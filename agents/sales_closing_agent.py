from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel

from shared.event_bus import NEGOTIATION_FAILED, SALE_COMPLETED
from shared.graph_state import CarSaleState
from tools.document_generator import generate_contract_pdf

SYSTEM_PROMPT = """Eres el Agente de Cierre de Venta de un sistema de venta de autos usados. Tu rol es:
1. Redactar la respuesta al cliente y, si corresponde, el resumen del contrato de compraventa.
2. La aceptación o rechazo de la oferta YA fue decidida por una regla de negocio (se te informa
   en 'oferta_aceptable' del input) — no la vuelvas a evaluar, solo redacta en consecuencia.
3. Si la oferta es aceptable: redacta 'resumen_contrato' con todos los datos necesarios.
4. Si no es aceptable: redacta una 'contraoferta' razonable (nunca menor a 'min_aceptable')."""


class ResumenContrato(BaseModel):
    vendedor: str = ""
    comprador: str = ""
    vehiculo: str = ""
    precio: float = 0
    forma_pago: str = ""
    fecha: str = ""
    clausulas: list[str] = []


class SalesClosingResult(BaseModel):
    contraoferta: float | None = None
    resumen_contrato: ResumenContrato | None = None
    mensaje_cliente: str


class SalesClosingAgent:
    """LangGraph node: evalúa la oferta contra la regla de negocio y cierra o contraoferta."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self.llm = ChatGroq(
            model=model, api_key=api_key, temperature=0.3
        ).with_structured_output(SalesClosingResult)

    async def negotiate(self, offer: float, state: CarSaleState) -> dict[str, Any]:
        offer = float(offer)
        precio_mercado = float(state.car_data.get("precio_mercado") or 0.0)
        min_aceptable = precio_mercado * 0.85 if precio_mercado > 0 else 0.0
        oferta_aceptable = offer >= min_aceptable if min_aceptable > 0 else False

        user_content = json.dumps(
            {
                "car_id": state.car_id,
                "offer": offer,
                "precio_mercado": precio_mercado,
                "min_aceptable": min_aceptable,
                "oferta_aceptable": oferta_aceptable,
                "attempt": state.negotiation_attempts + 1,
                "car_data": state.car_data,
                "lead_data": state.lead_data,
            },
            ensure_ascii=False,
        )
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        result: SalesClosingResult | None = None
        last_error: Exception | None = None
        for _ in range(3):
            try:
                result = await self.llm.ainvoke(messages)
                break
            except Exception as e:
                last_error = e
                result = None

        if result is None:
            state.sale_data["error"] = (
                f"No se pudo obtener respuesta estructurada de Groq: {last_error}"
            )
            return {"error": state.sale_data["error"], "venta_completada": False}

        state.status = "negotiating"
        state.negotiation_attempts += 1

        if oferta_aceptable:
            precio_final = offer
            resumen = result.resumen_contrato or ResumenContrato()
            resumen.vehiculo = resumen.vehiculo or (
                f"{state.car_data.get('marca', '')} {state.car_data.get('modelo', '')} "
                f"{state.car_data.get('año') or state.car_data.get('anio')}"
            )
            resumen.precio = resumen.precio or precio_final
            resumen.fecha = (
                resumen.fecha or datetime.now(timezone.utc).date().isoformat()
            )

            pdf_path = generate_contract_pdf(
                output_path=f"contracts/{state.car_id}.pdf",
                contract=resumen.model_dump(),
            )

            state.sale_data = {
                "precio_final": precio_final,
                "forma_pago": resumen.forma_pago,
                "contrato_generado": True,
                "venta_completada": True,
                "contrato_pdf": pdf_path,
            }
            state.status = "sold"
            state.events.append(SALE_COMPLETED)
            state.updated_at = datetime.now(timezone.utc)

            return {
                "oferta_aceptable": True,
                "precio_final": precio_final,
                "resumen_contrato": resumen.model_dump(),
                "mensaje_cliente": result.mensaje_cliente,
                "venta_completada": True,
            }

        contraoferta = (
            result.contraoferta
            if result.contraoferta and result.contraoferta >= min_aceptable
            else max(min_aceptable, offer)
        )

        if state.negotiation_attempts >= 3:
            state.sale_data = {
                "precio_final": None,
                "forma_pago": None,
                "contrato_generado": False,
                "venta_completada": False,
            }
            state.events.append(NEGOTIATION_FAILED)

        return {
            "oferta_aceptable": False,
            "contraoferta": contraoferta,
            "mensaje_cliente": result.mensaje_cliente,
            "venta_completada": False,
        }
