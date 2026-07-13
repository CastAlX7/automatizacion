from unittest.mock import AsyncMock

import pytest

from agents.acquisition_agent import AcquisitionAgent, AcquisitionResult
from shared.graph_state import CarSaleState


@pytest.mark.asyncio
async def test_car_approved():
    agent = AcquisitionAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=AcquisitionResult(
                apto_venta=True,
                razon="Cumple criterios",
                precio_mercado_sugerido=14000,
                precio_negociacion_recomendado=11900,
                observaciones="OK",
            )
        )
    )

    state = CarSaleState(
        car_data={
            "marca": "Toyota",
            "modelo": "Corolla",
            "año": 2019,
            "km": 45000,
            "color": "Blanco",
        }
    )
    result = await agent(state)
    assert result["car_data"]["apto_venta"] is True
    assert result["status"] == "acquired"


@pytest.mark.asyncio
async def test_car_rejected_km():
    agent = AcquisitionAgent(api_key="test")
    agent.llm = AsyncMock(
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

    state = CarSaleState(
        car_data={
            "marca": "Ford",
            "modelo": "F-150",
            "año": 2012,
            "km": 215000,
            "color": "Gris",
        }
    )
    result = await agent(state)
    assert result["car_data"]["apto_venta"] is False
    assert result["status"] == "rejected"


@pytest.mark.asyncio
async def test_price_suggestion():
    agent = AcquisitionAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=AcquisitionResult(
                apto_venta=True,
                razon="OK",
                precio_mercado_sugerido=10000,
                precio_negociacion_recomendado=8500,
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
    assert isinstance(result["car_data"].get("precio_mercado"), (int, float))
    assert result["car_data"]["precio_mercado"] > 0
