from unittest.mock import AsyncMock

import pytest

from agents.sales_closing_agent import (
    ResumenContrato,
    SalesClosingAgent,
    SalesClosingResult,
)
from shared.graph_state import CarSaleState


@pytest.mark.asyncio
async def test_sale_completed():
    agent = SalesClosingAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=SalesClosingResult(
                resumen_contrato=ResumenContrato(
                    vendedor="Vendedor Demo",
                    comprador="Cliente Demo",
                    vehiculo="Toyota Corolla 2019",
                    precio=12000,
                    forma_pago="transferencia",
                    fecha="2026-05-20",
                    clausulas=["Se vende en el estado actual."],
                ),
                mensaje_cliente="Trato hecho.",
            )
        )
    )

    state = CarSaleState(
        car_data={
            "marca": "Toyota",
            "modelo": "Corolla",
            "año": 2019,
            "km": 45000,
            "precio_mercado": 14000,
        },
        lead_data={"nombre_cliente": "Cliente Demo"},
    )
    out = await agent.negotiate(offer=12000, state=state)
    assert out["venta_completada"] is True
    assert state.status == "sold"
