from unittest.mock import AsyncMock

import pytest

from agents.publication_agent import PublicationAgent, PublicationResult
from shared.graph_state import CarSaleState


@pytest.mark.asyncio
async def test_description_generated():
    agent = PublicationAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=PublicationResult(
                descripcion_facebook="FB",
                descripcion_mercadolibre="ML",
                descripcion_instagram="IG #auto",
                titulo_anuncio="Toyota Corolla 2019",
                precio_publicar=12500,
                tags_seo=["toyota", "corolla"],
            )
        )
    )

    state = CarSaleState(
        car_data={
            "marca": "Toyota",
            "modelo": "Corolla",
            "año": 2019,
            "km": 45000,
            "precio_venta": 11900,
        },
        inspection_data={"score_fisico": 82},
    )
    out = await agent(state)
    desc = out["publication_data"]["descripcion_generada"]
    assert desc["facebook"]
    assert desc["mercadolibre"]
    assert desc["instagram"]


@pytest.mark.asyncio
async def test_urls_generated():
    agent = PublicationAgent(api_key="test")
    agent.llm = AsyncMock(
        ainvoke=AsyncMock(
            return_value=PublicationResult(
                descripcion_facebook="FB",
                descripcion_mercadolibre="ML",
                descripcion_instagram="IG",
                titulo_anuncio="Anuncio",
                precio_publicar=12000,
                tags_seo=[],
            )
        )
    )

    state = CarSaleState(
        car_data={
            "marca": "Toyota",
            "modelo": "Corolla",
            "año": 2019,
            "km": 45000,
            "precio_venta": 11900,
        },
        inspection_data={"score_fisico": 82},
    )
    out = await agent(state)
    urls = out["publication_data"].get("urls_publicadas")
    assert isinstance(urls, list)
    assert len(urls) == 3
