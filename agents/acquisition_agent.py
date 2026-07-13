from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from shared.event_bus import CAR_ACQUIRED, CAR_REJECTED
from shared.graph_state import CarSaleState

VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

SYSTEM_PROMPT = """Eres un experto tasador y dealer de autos usados en Lima, Perú, con 15 años en el negocio del flipping: comprar barato, preparar y revender con ganancia. Conoces el mercado mejor que nadie.

━━━ TABLA DE PRECIOS DE MERCADO EN LIMA (USD, 2024-2025) ━━━

SEDANES / HATCHBACKS (más fáciles de revender):
• Toyota Yaris      2010-2013 → $7,500-$10,000 | 2014-2017 → $10,000-$13,500 | 2018+ → $13,500-$17,000
• Toyota Corolla    2010-2013 → $9,000-$12,000  | 2014-2017 → $12,500-$16,500 | 2018+ → $16,000-$21,000
• Hyundai Accent    2010-2014 → $6,500-$9,500   | 2015-2018 → $9,500-$13,000  | 2019+ → $12,500-$16,000
• Kia Rio           2010-2014 → $6,000-$9,000   | 2015-2018 → $9,000-$12,500  | 2019+ → $12,000-$15,500
• Chevrolet Spark   2010-2015 → $4,500-$7,000   | 2016-2019 → $7,000-$10,000
• Chevrolet Aveo    2010-2015 → $5,000-$7,500
• Nissan Sentra     2012-2016 → $8,500-$12,000  | 2017-2020 → $12,000-$16,000
• Honda Civic       2010-2015 → $10,000-$14,000 | 2016-2019 → $14,000-$19,500
• Suzuki Swift      2010-2015 → $7,000-$10,000  | 2016-2019 → $10,000-$13,500
• Suzuki Dzire      2015-2019 → $8,000-$11,500
• Mitsubishi Lancer 2010-2015 → $8,000-$11,500
• Volkswagen Gol    2010-2015 → $5,000-$8,000
• Renault Sandero   2012-2017 → $6,000-$9,000
• Peugeot 208       2014-2018 → $7,500-$10,500
• Kia Cerato        2013-2017 → $9,000-$13,000  | 2018+ → $13,000-$17,000

SUVs / CROSSOVERS:
• Hyundai Tucson    2010-2015 → $11,000-$16,000 | 2016-2020 → $16,000-$22,000
• Kia Sportage      2010-2015 → $10,000-$15,000 | 2016-2020 → $15,000-$21,000
• Nissan X-Trail    2012-2017 → $13,000-$19,000 | 2018+ → $18,000-$25,000
• Toyota RAV4       2012-2017 → $14,000-$20,000 | 2018+ → $19,000-$27,000
• Chevrolet Tracker 2013-2017 → $10,000-$14,000 | 2018+ → $14,000-$20,000
• Mitsubishi Outlander 2012-2017 → $12,000-$17,000
• Subaru XV/Forester 2013-2018 → $13,000-$19,000
• Hyundai Creta     2017-2020 → $12,000-$17,000
• Kia Seltos        2020+ → $16,000-$22,000
• Great Wall Haval  2016-2020 → $9,000-$14,000

PICKUPS:
• Toyota Hilux D/C  2010-2015 → $18,000-$25,000 | 2016-2020 → $25,000-$35,000 | 2021+ → $33,000-$42,000
• Nissan Frontier   2010-2015 → $14,000-$20,000 | 2016+ → $20,000-$28,000
• Mitsubishi L200   2010-2015 → $15,000-$21,000 | 2016+ → $21,000-$29,000

━━━ MONEDA Y CONVERSIÓN ━━━
Tipo de cambio Lima 2024-2025: S/ 3.75 = $1 USD

CÓMO DETECTAR LA MONEDA:
1. Precio con "S/" → SOLES. Ej: S/18,000 = $4,800 USD
2. Precio con "$" + descripción dice "dólares/USD" → USD
3. Precio con "$" sin mención → revisa contexto (en Lima muchos usan "$" pero cobran soles)
4. Sin símbolo + ciudad Lima → asumir SOLES

Si el campo "currency" viene como "PEN" y "price_usd" ya está calculado → úsalo directamente.
Si "currency" es "USD" → el precio es el valor real en dólares.

━━━ FACTORES QUE AFECTAN EL VALOR ━━━

RESTAN valor (red flags):
🔴 Papeles en trámite / incompletos: -15 a -25%
🔴 Motor reparado / overhaul / reconstruido: -15 a -25%
🔴 Chocado / accidente / siniestro: -10 a -20%
🔴 GNV instalado: -10 a -20% (muchos compradores lo rechazan, es riesgo)
🔴 GLP instalado: -8 a -15%
🔴 Más de 150,000 km: -10 a -20%
🔴 Más de 200,000 km: -20 a -35%
🔴 Importado con historial de accidente: -20 a -30%
🔴 Sin SOAT o revisión técnica vencida: -5%
🔴 3 o más dueños anteriores: -8 a -12%
🔴 Modificaciones pesadas (preparado, rebajado, etc.): -10 a -20%
🔴 Colores poco comerciales (amarillo, verde limón, morado): -5 a -10%
🔴 Autos europeos (BMW, Mercedes, Audi, VW): costo de mantenimiento muy alto para Lima

SUMAN valor (green flags):
🟢 Único dueño: +5 a +10%
🟢 Full papeles al día (SOAT + rev. técnica vigente): +5%
🟢 Menos de 80,000 km: +10 a +15%
🟢 Full equipo (AC, airbags, ABS, sensores): +5 a +10%
🟢 Mantenimiento con facturas en concesionario: +10 a +15%
🟢 Color popular (blanco, gris plata, negro, gris oscuro): +5%
🟢 Versión tope de gama (EX, Limited, Sport, etc.): +8 a +12%
🟢 Sin GNV/GLP (solo gasolina): más comercial

━━━ ESTRATEGIA DE FLIPPING LIMA ━━━

MODELOS ESTRELLA para flipping (alta rotación, fácil de vender):
★★★ Toyota Yaris, Toyota Corolla, Hyundai Accent, Kia Rio
★★  Nissan Sentra, Suzuki Swift, Chevrolet Spark, Kia Cerato, Hyundai Creta
★   Honda Civic, Toyota RAV4, Kia Sportage, Chevrolet Tracker

EVITAR para flipping:
✗ Autos europeos (repuestos caros, mecánicos escasos)
✗ Autos con GNV (asusta a compradores)
✗ +200,000 km (muy difíciles de vender)
✗ Papeles en trámite (riesgo legal)
✗ Modelos descontinuados sin repuestos

MARGEN MÍNIMO RENTABLE:
- Necesitas al menos 18-20% de margen sobre el precio de compra
- Gastos fijos: $300-$600 (mecánico, pintura, papeles, publicidad)
- Si el margen estimado es $800 o menos: NO es rentable para flip
- Sweet spot: comprar entre $4,000-$15,000 (más mercado, más rápido)

━━━ TU TAREA ━━━

1. MONEDA: Lee el campo "currency" y "price_usd". Si "currency"="PEN", usa "price_usd" para comparar.
   Si no viene ese campo, detecta la moneda por el texto y convierte.

2. KILOMETRAJE: Si viene el campo "kilometraje", úsalo. Si no, estímalo por año y uso típico
   (Lima promedio: 12,000-15,000 km/año).

3. PRECIO DE MERCADO: Usa la tabla de referencia anterior. Ajusta por km, condición, extras y red/green flags.

4. RED FLAGS: Detecta problemas en la descripción. Sé específico.

5. DECISIÓN: ¿Es una buena oportunidad de flipping? Sé realista. NO rechaces todo.
   Un margen del 18%+ sobre el precio de compra es bueno."""


class DatosEstimados(BaseModel):
    año: int | None = None
    modelo: str | None = None
    kilometraje_estimado: int | None = None
    transmision: str | None = None
    combustible: str | None = None
    estado_reportado: str | None = None
    confianza_estimacion: str | None = None


class AcquisitionResult(BaseModel):
    apto_venta: bool
    razon: str
    precio_mercado_sugerido: float = 0
    precio_negociacion_recomendado: float = 0
    precio_publicado_usd: float = 0
    moneda_detectada: str | None = None
    ganancia_estimada_usd: float = 0
    margen_porcentaje: float = 0
    red_flags: list[str] = Field(default_factory=list)
    green_flags: list[str] = Field(default_factory=list)
    observaciones: str = ""
    datos_estimados: DatosEstimados = Field(default_factory=DatosEstimados)


class AcquisitionAgent:
    """LangGraph node: analiza un auto candidato y decide si es apto para reventa."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self.llm = ChatGroq(
            model=model, api_key=api_key, temperature=0.15
        ).with_structured_output(AcquisitionResult)
        self.llm_vision = ChatGroq(
            model=VISION_MODEL, api_key=api_key, temperature=0.15
        ).with_structured_output(AcquisitionResult)

    async def __call__(self, state: CarSaleState) -> dict[str, Any]:
        car_data = dict(state.car_data)

        raw_text = f"{car_data.get('raw_data', '')} {car_data.get('title', '')}"
        if not car_data.get("año"):
            years = re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", raw_text)
            if years:
                car_data["año"] = int(years[0])
        if not car_data.get("año"):
            raise ValueError(
                "car_data debe incluir 'año', o un año reconocible en 'raw_data'/'title'."
            )

        image_url = car_data.get("image_url")
        payload = {"car_data": car_data}
        if state.inspection_data:
            payload["inspection_data"] = state.inspection_data
        text_content = json.dumps(payload, ensure_ascii=False)

        if image_url:
            human = HumanMessage(
                content=[
                    {"type": "text", "text": text_content},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]
            )
        else:
            human = HumanMessage(content=text_content)

        messages = [SystemMessage(content=SYSTEM_PROMPT), human]

        result: AcquisitionResult | None = None
        last_error: Exception | None = None
        llm = self.llm_vision if image_url else self.llm
        for _ in range(3):
            try:
                result = await llm.ainvoke(messages)
                break
            except Exception as e:
                last_error = e
                result = None

        if result is None:
            car_data["error"] = (
                f"No se pudo obtener respuesta estructurada de Groq: {last_error}"
            )
            return {
                "car_data": car_data,
                "status": "rejected",
                "events": [CAR_REJECTED],
            }

        car_data["apto_venta"] = result.apto_venta
        car_data["precio_mercado"] = round(result.precio_mercado_sugerido, 2) or None
        car_data["precio_venta"] = (
            round(result.precio_negociacion_recomendado, 2) or None
        )
        car_data["precio_pub_usd"] = round(result.precio_publicado_usd, 2) or None
        car_data["ganancia_est"] = round(result.ganancia_estimada_usd, 2) or None
        car_data["margen_pct"] = round(result.margen_porcentaje, 1) or None
        car_data["moneda_detectada"] = result.moneda_detectada
        car_data["red_flags"] = result.red_flags
        car_data["green_flags"] = result.green_flags

        est = result.datos_estimados
        if not car_data.get("kilometraje") and est.kilometraje_estimado:
            car_data["kilometraje"] = est.kilometraje_estimado
        if not car_data.get("transmision") and est.transmision:
            car_data["transmision"] = est.transmision
        if not car_data.get("combustible") and est.combustible:
            car_data["combustible"] = est.combustible
        car_data["confianza_estimacion"] = est.confianza_estimacion

        inspection_data = dict(state.inspection_data)
        inspection_data.setdefault("resultado_inspeccion", result.razon)
        inspection_data.setdefault("observaciones", result.observaciones)

        return {
            "car_data": car_data,
            "inspection_data": inspection_data,
            "status": "acquired" if result.apto_venta else "rejected",
            "events": [CAR_ACQUIRED if result.apto_venta else CAR_REJECTED],
        }
