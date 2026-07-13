from __future__ import annotations

import json
import operator
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from shared.checkpointing import checkpointer_scope
from shared.graph_state import CarSaleState

SYSTEM_PROMPT = """Eres el Agente CRM de un sistema de venta de autos usados. Tu rol es:
1. Responder preguntas de clientes potenciales sobre el auto de forma amable y profesional
2. Calificar leads: un lead está calificado si muestra intención real de compra (pregunta por precio final, quiere ver el auto, pregunta por financiamiento o formas de pago)
3. Detectar leads no interesados: solo curiosidad, precios muy bajos irreales, o respuestas evasivas
4. Registrar el motivo de descarte si el lead no califica (para análisis futuro)"""


class CRMResult(BaseModel):
    respuesta_cliente: str
    lead_calificado: bool
    motivo_descarte: str | None = None
    siguiente_accion: str
    resumen_intencion: str


class CRMTurnState(BaseModel):
    """Estado del grafo de conversación CRM — persistido por thread_id (car_id)."""

    car_id: str = ""
    car_data: dict[str, Any] = Field(default_factory=dict)
    inspection_data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    # Cada turno solo agrega SU mensaje; el checkpointer reconstruye el resto.
    consultas: Annotated[list[str], operator.add] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class CRMChatbotAgent:
    """Nodo/agente CRM con memoria de conversación persistida (LangGraph + SqliteSaver)."""

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.llm = ChatGroq(
            model=model, api_key=api_key, temperature=0.4
        ).with_structured_output(CRMResult)
        self._checkpointer_override = checkpointer

        builder = StateGraph(CRMTurnState)
        builder.add_node("crm", self._turn)
        builder.set_entry_point("crm")
        builder.add_edge("crm", END)
        self._builder = builder

    async def _turn(self, state: CRMTurnState) -> dict[str, Any]:
        historial = [*state.consultas, state.message]
        user_content = json.dumps(
            {
                "car_id": state.car_id,
                "car_data": state.car_data,
                "inspection_data": state.inspection_data,
                "historial": historial,
            },
            ensure_ascii=False,
        )
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        result: CRMResult | None = None
        last_error: Exception | None = None
        for _ in range(3):
            try:
                result = await self.llm.ainvoke(messages)
                break
            except Exception as e:
                last_error = e
                result = None

        if result is None:
            parsed = {
                "respuesta_cliente": "¿Podrías detallar tu consulta? Estoy para ayudarte.",
                "lead_calificado": False,
                "motivo_descarte": "respuesta_llm_invalida",
                "siguiente_accion": "seguir_conversacion",
                "resumen_intencion": "sin_respuesta_llm",
                "error": str(last_error),
            }
        else:
            parsed = result.model_dump()

        return {"consultas": [state.message], "result": parsed}

    async def handle_message_persisted(
        self,
        message: str,
        thread_id: str,
        car_data: dict[str, Any],
        inspection_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Punto de entrada recomendado: conversación persistida por thread_id (car_id o chat_id)."""
        config = {"configurable": {"thread_id": thread_id}}
        turn = CRMTurnState(
            car_id=thread_id,
            car_data=car_data,
            inspection_data=inspection_data or {},
            message=(message or "").strip(),
        )

        if self._checkpointer_override is not None:
            graph = self._builder.compile(checkpointer=self._checkpointer_override)
            final_state = await graph.ainvoke(turn, config=config)
        else:
            async with checkpointer_scope() as checkpointer:
                graph = self._builder.compile(checkpointer=checkpointer)
                final_state = await graph.ainvoke(turn, config=config)

        return final_state["result"]

    async def handle_message(self, message: str, state: CarSaleState) -> dict[str, Any]:
        """Compatibilidad: usa CarSaleState.car_id como thread_id de la conversación."""
        parsed = await self.handle_message_persisted(
            message=message,
            thread_id=state.car_id,
            car_data=state.car_data,
            inspection_data=state.inspection_data,
        )
        consultas = state.lead_data.get("consultas")
        if not isinstance(consultas, list):
            consultas = []
        consultas.append((message or "").strip())
        state.lead_data["consultas"] = consultas
        state.lead_data["lead_calificado"] = bool(parsed.get("lead_calificado"))
        if not parsed.get("lead_calificado"):
            state.lead_data["motivo_descarte"] = parsed.get("motivo_descarte")
        return parsed
