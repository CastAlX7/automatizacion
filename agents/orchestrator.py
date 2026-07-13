from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from rich.console import Console

# Avoid UnicodeEncodeError on Windows consoles stuck on a legacy codepage (cp1252)
# when Rich prints Spanish text (tildes, emoji, arrows) to stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from agents.acquisition_agent import AcquisitionAgent
from agents.crm_chatbot_agent import CRMChatbotAgent
from agents.publication_agent import PublicationAgent
from agents.sales_closing_agent import SalesClosingAgent
from shared.checkpointing import checkpointer_scope
from shared.graph_state import CarSaleState


def _route_after_acquisition(state: CarSaleState) -> str:
    return "publicacion" if state.status == "acquired" else END


class Orchestrator:
    """Coordina el pipeline de venta de autos.

    Adquisición → Publicación es un StateGraph de LangGraph: flujo automático,
    secuencial, con ramificación condicional según si el auto resulta apto.
    CRM y Cierre se invocan por turno (dependen de mensajes/ofertas humanas,
    no de un paso más del pipeline automático).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.console = Console()

        self.acquisition_agent = AcquisitionAgent(api_key=api_key, model=model)
        self.publication_agent = PublicationAgent(api_key=api_key, model=model)
        self.crm_chatbot_agent = CRMChatbotAgent(api_key=api_key, model=model, checkpointer=checkpointer)
        self.sales_closing_agent = SalesClosingAgent(api_key=api_key, model=model)

        self._checkpointer_override = checkpointer

        builder = StateGraph(CarSaleState)
        builder.add_node("adquisicion", self.acquisition_agent)
        builder.add_node("publicacion", self.publication_agent)
        builder.set_entry_point("adquisicion")
        builder.add_conditional_edges("adquisicion", _route_after_acquisition)
        builder.add_edge("publicacion", END)
        self._builder = builder

    def _ts(self) -> str:
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    async def run_acquisition(
        self, car_data: dict[str, Any], inspection_data: dict[str, Any] | None = None
    ) -> CarSaleState:
        self.console.print(f"[yellow]{self._ts()}[/yellow] Pipeline: adquisición → publicación")
        initial = CarSaleState(
            car_data=dict(car_data),
            inspection_data=dict(inspection_data) if inspection_data else {},
        )
        config = {"configurable": {"thread_id": initial.car_id}}

        if self._checkpointer_override is not None:
            graph = self._builder.compile(checkpointer=self._checkpointer_override)
            result = await graph.ainvoke(initial, config=config)
        else:
            async with checkpointer_scope() as checkpointer:
                graph = self._builder.compile(checkpointer=checkpointer)
                result = await graph.ainvoke(initial, config=config)

        state = CarSaleState.model_validate(result)
        color = "green" if state.status != "rejected" else "red"
        self.console.print(f"[{color}]{self._ts()}[/{color}] Pipeline terminado: {state.status}")
        return state

    async def run_crm(self, message: str, state: CarSaleState) -> dict[str, Any]:
        self.console.print(f"[yellow]{self._ts()}[/yellow] CRM: mensaje recibido")
        return await self.crm_chatbot_agent.handle_message(message=message, state=state)

    async def run_closing(self, offer: float, state: CarSaleState) -> dict[str, Any]:
        self.console.print(f"[yellow]{self._ts()}[/yellow] Cierre: negociando")
        return await self.sales_closing_agent.negotiate(offer=offer, state=state)

    async def run_full_pipeline(
        self,
        car_data: dict[str, Any],
        inspection_data: dict[str, Any],
        client_messages: list[str],
        final_offer: float,
    ) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        state = await self.run_acquisition(car_data=car_data, inspection_data=inspection_data)

        if state.status == "rejected":
            total = (datetime.now(timezone.utc) - started).total_seconds()
            summary = state.to_summary()
            summary["tiempo_total_s"] = total
            return summary

        for msg in client_messages:
            reply = await self.run_crm(message=msg, state=state)
            if reply.get("lead_calificado"):
                self.console.print(f"[green]{self._ts()}[/green] Lead calificado: listo para cierre")
                break

        closing = await self.run_closing(offer=final_offer, state=state)
        total = (datetime.now(timezone.utc) - started).total_seconds()

        summary = state.to_summary()
        summary["tiempo_total_s"] = total
        summary["closing"] = closing
        return summary
