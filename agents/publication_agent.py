from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from shared.event_bus import PUBLISHED
from shared.graph_state import CarSaleState
from tools.listing_publisher import build_mock_urls

SYSTEM_PROMPT = """Eres el Agente de Publicación de un sistema de venta de autos usados. Tu rol es:
1. Generar descripciones atractivas y verídicas para anuncios de autos usados
2. Adaptar el tono según la plataforma: Facebook Marketplace (casual), MercadoLibre (técnico y detallado), Instagram (corto e impactante)
3. Destacar las mejores características del auto sin mentir
4. Incluir siempre: precio, año, km, características principales, estado del auto, forma de contacto"""


class PublicationResult(BaseModel):
    descripcion_facebook: str
    descripcion_mercadolibre: str
    descripcion_instagram: str
    titulo_anuncio: str
    precio_publicar: float = 0
    tags_seo: list[str] = Field(default_factory=list)


class PublicationAgent:
    """LangGraph node: genera el anuncio multi-plataforma para un auto ya aprobado."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self.llm = ChatGroq(
            model=model, api_key=api_key, temperature=0.4
        ).with_structured_output(PublicationResult)

    async def __call__(self, state: CarSaleState) -> dict[str, Any]:
        user_content = json.dumps(
            {
                "car_id": state.car_id,
                "car_data": state.car_data,
                "inspection_data": state.inspection_data,
            },
            ensure_ascii=False,
        )
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        result: PublicationResult | None = None
        last_error: Exception | None = None
        for _ in range(3):
            try:
                result = await self.llm.ainvoke(messages)
                break
            except Exception as e:
                last_error = e
                result = None

        if result is None:
            return {
                "publication_data": {
                    "error": f"No se pudo obtener respuesta estructurada de Groq: {last_error}"
                },
                "status": "published",
                "events": [PUBLISHED],
            }

        urls = build_mock_urls(state.car_id)
        publication_data = {
            "descripcion_generada": {
                "facebook": result.descripcion_facebook,
                "mercadolibre": result.descripcion_mercadolibre,
                "instagram": result.descripcion_instagram,
            },
            "titulo_anuncio": result.titulo_anuncio,
            "precio_publicar": result.precio_publicar,
            "tags_seo": result.tags_seo,
            "urls_publicadas": urls,
            "plataformas": ["facebook_marketplace", "mercadolibre", "instagram"],
        }

        return {
            "publication_data": publication_data,
            "status": "published",
            "events": [PUBLISHED],
        }
