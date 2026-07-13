from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.crm_chatbot_agent import CRMChatbotAgent, CRMResult
from shared.graph_state import CarSaleState


@pytest_asyncio.fixture
async def checkpointer():
    conn = await aiosqlite.connect(":memory:")
    try:
        yield AsyncSqliteSaver(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_qualified_lead(checkpointer):
    agent = CRMChatbotAgent(api_key="test", checkpointer=checkpointer)
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=CRMResult(
                respuesta_cliente="Claro, coordinemos.",
                lead_calificado=True,
                siguiente_accion="agendar_cita",
                resumen_intencion="quiere ver el auto",
            )
        )
    )

    state = CarSaleState(
        car_data={"marca": "Toyota", "modelo": "Corolla", "año": 2019, "km": 45000}
    )
    out = await agent.handle_message(
        "¿Cuánto es lo menos que acepta? Quiero ir a verlo mañana", state
    )
    assert out["lead_calificado"] is True


@pytest.mark.asyncio
async def test_unqualified_lead(checkpointer):
    agent = CRMChatbotAgent(api_key="test", checkpointer=checkpointer)
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=CRMResult(
                respuesta_cliente="El precio es fijo por ahora.",
                lead_calificado=False,
                motivo_descarte="oferta_irreal",
                siguiente_accion="seguir_conversacion",
                resumen_intencion="solo tiene 3000",
            )
        )
    )

    state = CarSaleState(
        car_data={"marca": "Toyota", "modelo": "Corolla", "año": 2019, "km": 45000}
    )
    out = await agent.handle_message("¿Puede ser más barato? Solo tengo 3000", state)
    assert out["lead_calificado"] is False


@pytest.mark.asyncio
async def test_conversation_history(checkpointer):
    agent = CRMChatbotAgent(api_key="test", checkpointer=checkpointer)
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=CRMResult(
                respuesta_cliente="OK",
                lead_calificado=False,
                motivo_descarte="",
                siguiente_accion="seguir_conversacion",
                resumen_intencion="pregunta",
            )
        )
    )

    state = CarSaleState(
        car_data={"marca": "Toyota", "modelo": "Corolla", "año": 2019, "km": 45000}
    )
    await agent.handle_message("¿Sigue disponible?", state)
    await agent.handle_message("¿Tiene financiamiento?", state)
    assert state.lead_data["consultas"] == [
        "¿Sigue disponible?",
        "¿Tiene financiamiento?",
    ]

    # La persistencia real vive en el checkpointer (por thread_id = car_id),
    # no solo en el dict en memoria — se puede verificar releyendo el estado del grafo.
    graph = agent._builder.compile(checkpointer=checkpointer)
    snapshot = await graph.aget_state({"configurable": {"thread_id": state.car_id}})
    assert snapshot.values["consultas"] == [
        "¿Sigue disponible?",
        "¿Tiene financiamiento?",
    ]
