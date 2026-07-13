import aiosqlite
import pytest
import pytest_asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from unittest.mock import AsyncMock

from agents.acquisition_agent import AcquisitionAgent, AcquisitionResult
from agents.crm_chatbot_agent import CRMChatbotAgent, CRMResult
from agents.orchestrator import Orchestrator
from agents.sales_closing_agent import SalesClosingAgent, SalesClosingResult
from shared.event_bus import NEGOTIATION_FAILED
from shared.graph_state import CarSaleState


@pytest_asyncio.fixture
async def checkpointer():
    conn = await aiosqlite.connect(":memory:")
    try:
        yield AsyncSqliteSaver(conn)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_negotiation_max_attempts():
    agent = SalesClosingAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=SalesClosingResult(
                contraoferta=12000,
                mensaje_cliente="No",
            )
        )
    )

    state = CarSaleState(
        car_data={
            "precio_mercado": 14000,
            "marca": "Toyota",
            "modelo": "Corolla",
            "año": 2019,
        }
    )
    await agent.negotiate(offer=5000, state=state)
    await agent.negotiate(offer=6000, state=state)
    await agent.negotiate(offer=7000, state=state)
    assert state.negotiation_attempts == 3
    assert NEGOTIATION_FAILED in state.events


@pytest.mark.asyncio
async def test_car_border_score():
    agent = AcquisitionAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=AcquisitionResult(
                apto_venta=True,
                razon="Score en límite",
                precio_mercado_sugerido=12000,
                precio_negociacion_recomendado=10200,
                observaciones="OK",
            )
        )
    )
    state = CarSaleState(
        car_data={
            "marca": "Honda",
            "modelo": "Civic",
            "año": 2017,
            "km": 98000,
            "color": "Azul",
        }
    )
    result = await agent(state)
    assert result["car_data"]["apto_venta"] is True


@pytest.mark.asyncio
async def test_invalid_car_data():
    agent = AcquisitionAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(return_value=AcquisitionResult(apto_venta=True, razon="ok"))
    )
    state = CarSaleState(car_data={"marca": "Toyota", "modelo": "Corolla", "km": 45000})
    with pytest.raises(ValueError):
        await agent(state)


@pytest.mark.asyncio
async def test_empty_client_message(checkpointer):
    agent = CRMChatbotAgent(api_key="test", checkpointer=checkpointer)
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=CRMResult(
                respuesta_cliente="Hola, ¿en qué puedo ayudarte?",
                lead_calificado=False,
                motivo_descarte="",
                siguiente_accion="seguir_conversacion",
                resumen_intencion="saludo",
            )
        )
    )
    state = CarSaleState(
        car_data={"marca": "Toyota", "modelo": "Corolla", "año": 2019, "km": 45000}
    )
    out = await agent.handle_message("", state)
    assert "respuesta_cliente" in out


@pytest.mark.asyncio
async def test_full_pipeline_rejection(checkpointer):
    orch = Orchestrator(api_key="test", checkpointer=checkpointer)
    orch.acquisition_agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=AcquisitionResult(
                apto_venta=False,
                razon="Km excesivo",
                precio_mercado_sugerido=8000,
                precio_negociacion_recomendado=6800,
                observaciones="No apto",
            )
        )
    )

    summary = await orch.run_full_pipeline(
        car_data={"marca": "Ford", "modelo": "F-150", "año": 2012, "km": 215000},
        inspection_data={
            "defectos_encontrados": ["Motor con ruido"],
            "score_fisico": 38,
        },
        client_messages=["¿Está disponible?"],
        final_offer=3000,
    )
    assert summary["status"] == "rejected"
