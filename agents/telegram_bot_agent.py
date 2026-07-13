"""Anymotor Telegram Bot Agent — agente ReAct de LangGraph con memoria persistida."""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from datetime import datetime
from typing import Literal

import requests
from langchain.agents import create_agent

# Avoid UnicodeEncodeError on Windows consoles stuck on a legacy codepage (cp1252)
# when logging Spanish text (tildes, emoji) to stdout.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field

from agents.orchestrator import Orchestrator
from shared.checkpointing import checkpointer_scope
from tools.airtable_tool import AirtableTool
from tools.seen_listings import is_seen, mark_seen

BOT_MODEL = "llama-3.3-70b-versatile"
_PIPELINE_STATES = ("Encontrado", "Contactando", "Negociando", "Comprado", "Vendido")


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[AnyBot {ts}] {msg}", flush=True)


SYSTEM_PROMPT = """Eres el agente inteligente de Anymotor, una herramienta de flipping de autos usados en Perú.
Ayudas al usuario a encontrar, analizar y gestionar autos para comprar y revender con ganancia.

CAPACIDADES:
- buscar_autos: busca y analiza autos en Facebook Marketplace (Lima, Trujillo, Arequipa)
- ver_pipeline: muestra los autos guardados por estado en la base de datos
- analizar_url: analiza un listing específico de Facebook Marketplace por URL
- actualizar_estado: cambia el estado de un deal en la base de datos
- resumen: estadísticas de ganancias y pipeline

CONTEXTO DEL MERCADO PERUANO:
- Tipo de cambio: S/3.75 = $1 USD
- Margen mínimo rentable: 18-20% sobre el precio de compra
- Sweet spot Lima: $4,000-$15,000
- Mejores modelos para flip: Toyota Yaris, Corolla, Hyundai Accent, Kia Rio, Suzuki Swift

REGLAS CRÍTICAS — DEBES SEGUIRLAS SIEMPRE:

1. CANTIDAD: `cantidad` = número de autos a analizar (1-5 máximo absoluto).
   - "busca 1 Hilux" → cantidad=1
   - "busca 3 autos" → cantidad=3
   - "busca 20 autos" → cantidad=5, informa al usuario del límite

2. PRECIO — regla más importante:
   - Si el usuario pide un MODELO ESPECÍFICO y NO menciona precio → omite precio_min y precio_max (deja en 0 = sin filtro). Ejemplo: "busca una Hilux en Trujillo" → precio_min=0, precio_max=0
   - Si el usuario busca SIN modelo específico → usa precio_min=3000, precio_max=15000
   - Si el usuario menciona un rango explícito ("menos de 10000", "entre 5k y 8k") → úsalo exactamente
   - Modelos caros que SIEMPRE van sin filtro de precio: Hilux, Land Cruiser, RAV4, Fortuner, Ranger, Frontier, L200, Outlander, Tucson, Sportage (pueden costar $15,000-$40,000)

3. NUNCA llames buscar_autos más de UNA vez por mensaje. Si ya buscaste, responde con lo que encontraste.

4. Si buscar_autos no encuentra resultados → responde al usuario directamente. NO busques de nuevo con otros parámetros.

4b. Si el usuario pide "lista los resultados", "muéstrame lo que encontraste" o similar → los resultados ya están en el historial de conversación anterior. NO vuelvas a llamar buscar_autos. Responde usando el historial.

5b. Cuando el usuario diga "acepta", "acepta todas", "acepta las oportunidades" → usa actualizar_estado con nuevo_estado="Contactando" para CADA auto encontrado. El titulo_parcial debe ser una parte EXACTA del título que apareció en los resultados anteriores (cópialo del historial). Puedes llamar actualizar_estado múltiples veces seguidas, una por auto.

5. Cuando el usuario pegue una URL de Facebook Marketplace → usa analizar_url.
6. Cuando pregunte por sus autos, pipeline o negociaciones → usa ver_pipeline UNA VEZ. Su resultado se envía directamente al usuario; no repitas la llamada.
7. Cuando pregunte por estadísticas o ganancias → usa resumen UNA VEZ. Su resultado se envía directamente al usuario; no repitas la llamada.
8. NUNCA encadenes varias herramientas en la misma respuesta a menos que el usuario lo pida explícitamente.
8b. Si el usuario pregunta por un auto ESPECÍFICO del pipeline que ya se mostró ("cuánto vale el Nissan Sentra", "dime más del Ford Explorer") → usa la información del historial de conversación para responder. NO llames ver_pipeline de nuevo.
9. Responde siempre en español, directo y conciso.
10. Usa emojis con moderación."""


class BuscarAutosArgs(BaseModel):
    ciudad: Literal["lima", "trujillo", "arequipa"] = Field(
        description="Ciudad peruana donde buscar"
    )
    modelo: str = Field(
        default="",
        description="Modelo a buscar (ej: 'Toyota Corolla'). Vacío = cualquier auto.",
    )
    precio_min: int = Field(
        default=0,
        description=(
            "Precio mínimo en USD. USA 0 para buscar sin límite mínimo. Omite o usa 0 cuando el "
            "usuario pide un modelo específico sin mencionar precio."
        ),
    )
    precio_max: int = Field(
        default=0,
        description=(
            "Precio máximo en USD. USA 0 para buscar sin límite máximo. Omite o usa 0 cuando el "
            "usuario pide un modelo específico sin mencionar precio. Modelos caros (Hilux, RAV4, "
            "SUVs) suelen costar más de $15,000 — no les pongas límite."
        ),
    )
    cantidad: int = Field(default=3, description="Autos a analizar, 1-5 (defecto: 3)")


class VerPipelineArgs(BaseModel):
    estado: Literal[
        "todos", "Encontrado", "Contactando", "Negociando", "Comprado", "Vendido"
    ] = Field(default="todos", description="Estado a filtrar. 'todos' para ver todos.")


class AnalizarUrlArgs(BaseModel):
    url: str = Field(description="URL completa del listing de Facebook Marketplace")


class ActualizarEstadoArgs(BaseModel):
    titulo_parcial: str = Field(
        description="Parte del título del auto para identificarlo (ej: 'Corolla 2018')"
    )
    nuevo_estado: Literal[
        "Encontrado", "Contactando", "Negociando", "Comprado", "Vendido"
    ] = Field(description="Nuevo estado del pipeline")


class ResumenArgs(BaseModel):
    pass


class TelegramBotAgent:
    """Bot de Telegram (long-polling) respaldado por un agente ReAct de LangGraph."""

    def __init__(
        self,
        token: str,
        groq_key: str,
        allowed_users: list[str] | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.token = token
        self.groq_key = groq_key
        self.allowed_users = [
            str(u).strip() for u in (allowed_users or []) if str(u).strip()
        ]
        self.airtable = AirtableTool()
        self.model = ChatGroq(model=BOT_MODEL, api_key=groq_key, temperature=0.25)
        self._checkpointer_override = checkpointer
        self._offset = 0
        self._running = False

    # ── Telegram API helpers ──────────────────────────────────────────────────

    def _post(self, method: str, **payload) -> dict:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                json=payload,
                timeout=10,
            )
            return r.json()
        except Exception:
            return {}

    def send_message(self, chat_id: int, text: str) -> None:
        for chunk in _split_text(text, 4000):
            self._post(
                "sendMessage", chat_id=chat_id, text=chunk, parse_mode="Markdown"
            )

    def _typing(self, chat_id: int) -> None:
        self._post("sendChatAction", chat_id=chat_id, action="typing")

    def _get_updates(self) -> list[dict]:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"offset": self._offset, "timeout": 25},
                timeout=30,
            )
            if r.status_code == 200:
                return r.json().get("result", [])
        except Exception:
            pass
        return []

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _is_authorized(self, user_id: int) -> bool:
        if not self.allowed_users:
            return False
        return str(user_id) in self.allowed_users

    # ── Main polling loop ─────────────────────────────────────────────────────

    def run_forever(self) -> None:
        self._running = True
        print(
            f"[AnyBot] Iniciado. Usuarios autorizados: {self.allowed_users or 'ninguno'}"
        )
        while self._running:
            try:
                for upd in self._get_updates():
                    self._offset = upd["update_id"] + 1
                    self._dispatch(upd)
            except Exception as e:
                print(f"[AnyBot] Error en polling: {e}")
                time.sleep(5)

    def _dispatch(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg or not msg.get("text"):
            return
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        username = (
            msg["from"].get("username") or msg["from"].get("first_name") or str(user_id)
        )
        text = msg["text"].strip()
        _log(f"MSG  [{username}] → {text[:120]}")
        threading.Thread(
            target=self._handle, args=(chat_id, user_id, username, text), daemon=True
        ).start()

    # ── Message handler ───────────────────────────────────────────────────────

    def _handle(self, chat_id: int, user_id: int, username: str, text: str) -> None:
        if not self._is_authorized(user_id):
            _log(f"DENY [{username}] id={user_id} — no autorizado")
            self.send_message(
                chat_id,
                "🔒 No tienes acceso a este bot.\n"
                "Pide al administrador que agregue tu ID a la lista de usuarios autorizados en Anymotor.",
            )
            return

        if text == "/start":
            _log(f"CMD  [{username}] /start")
            self.send_message(
                chat_id,
                (
                    "👋 *¡Hola! Soy el agente de Anymotor.*\n\n"
                    "Puedo ayudarte a:\n"
                    "• 🔍 Buscar autos en Lima, Trujillo o Arequipa\n"
                    "• 📋 Ver tu pipeline de deals\n"
                    "• 🔗 Analizar un auto (pega la URL de Facebook)\n"
                    "• ✏️ Actualizar el estado de un deal\n"
                    "• 📊 Ver tu resumen de ganancias\n\n"
                    "Escríbeme lo que necesitas en lenguaje normal. ¿Qué buscamos hoy?"
                ),
            )
            return

        if text == "/reset":
            _log(f"CMD  [{username}] /reset")
            asyncio.run(self._reset_thread(str(chat_id)))
            self.send_message(chat_id, "✅ Conversación reiniciada.")
            return

        if text == "/id":
            self.send_message(chat_id, f"Tu Telegram ID es: `{user_id}`")
            return

        self._typing(chat_id)
        response = asyncio.run(self._run_agent(chat_id, username, text))
        _log(f"RESP [{username}] ← {response[:120].replace(chr(10), ' ')}")
        self.send_message(chat_id, response)

    async def _reset_thread(self, thread_id: str) -> None:
        if self._checkpointer_override is not None:
            await self._checkpointer_override.setup()
            await self._checkpointer_override.adelete_thread(thread_id)
        else:
            async with checkpointer_scope() as checkpointer:
                await checkpointer.adelete_thread(thread_id)

    # ── Agente ReAct (LangGraph) ──────────────────────────────────────────────

    async def _run_agent(self, chat_id: int, username: str, text: str) -> str:
        tools = self._build_tools(chat_id)
        config = {"configurable": {"thread_id": str(chat_id)}, "recursion_limit": 20}

        try:
            if self._checkpointer_override is not None:
                agent = create_agent(
                    model=self.model,
                    tools=tools,
                    system_prompt=SYSTEM_PROMPT,
                    checkpointer=self._checkpointer_override,
                )
                result = await agent.ainvoke(
                    {"messages": [HumanMessage(content=text)]}, config=config
                )
            else:
                async with checkpointer_scope() as checkpointer:
                    agent = create_agent(
                        model=self.model,
                        tools=tools,
                        system_prompt=SYSTEM_PROMPT,
                        checkpointer=checkpointer,
                    )
                    result = await agent.ainvoke(
                        {"messages": [HumanMessage(content=text)]}, config=config
                    )
        except Exception as e:
            _log(f"ERR  [{username}] error del agente: {e}")
            return f"⚠️ Error al conectar con la IA: {e}"

        for message in reversed(result["messages"]):
            if isinstance(message, AIMessage) and message.content:
                return message.content
        return "No pude generar una respuesta."

    def _build_tools(self, chat_id: int) -> list:
        """Construye las tools por request — closures que capturan chat_id para
        poder avisar al usuario ('Buscando...') antes de operaciones lentas."""

        @tool("buscar_autos", args_schema=BuscarAutosArgs)
        async def buscar_autos(
            ciudad: str,
            modelo: str = "",
            precio_min: int = 0,
            precio_max: int = 0,
            cantidad: int = 3,
        ) -> str:
            """Busca y analiza autos en Facebook Marketplace de una ciudad peruana. Tarda 1-3 minutos. SOLO puede llamarse UNA VEZ por turno."""
            if not modelo and precio_min == 0 and precio_max == 0:
                precio_min, precio_max = 3000, 15000
            sin_filtro = precio_min == 0 and precio_max == 0
            self.send_message(
                chat_id,
                f"🔍 Buscando{' ' + modelo if modelo else ''} en Facebook Marketplace"
                f"{' (sin filtro de precio)' if sin_filtro else ''}... esto tarda 1-2 minutos ⏳",
            )
            return await self._tool_buscar_autos(
                ciudad=ciudad,
                modelo=modelo,
                precio_min=precio_min,
                precio_max=precio_max,
                cantidad=min(cantidad, 5),
            )

        @tool("ver_pipeline", args_schema=VerPipelineArgs)
        async def ver_pipeline(estado: str = "todos") -> str:
            """Muestra los autos guardados en Airtable, opcionalmente filtrados por estado del pipeline."""
            return self._tool_ver_pipeline(estado)

        @tool("analizar_url", args_schema=AnalizarUrlArgs)
        async def analizar_url(url: str) -> str:
            """Analiza un auto específico a partir de su URL de Facebook Marketplace."""
            self.send_message(chat_id, "🔎 Analizando el auto... un momento ⏳")
            return await self._tool_analizar_url(url)

        @tool("actualizar_estado", args_schema=ActualizarEstadoArgs)
        async def actualizar_estado(titulo_parcial: str, nuevo_estado: str) -> str:
            """Actualiza el estado del pipeline de un auto guardado en Airtable."""
            return self._tool_actualizar_estado(titulo_parcial, nuevo_estado)

        @tool("resumen", args_schema=ResumenArgs)
        async def resumen() -> str:
            """Muestra estadísticas generales: autos por estado, ganancia potencial y real."""
            return self._tool_resumen()

        return [buscar_autos, ver_pipeline, analizar_url, actualizar_estado, resumen]

    # ── Implementación: buscar_autos ──────────────────────────────────────────

    async def _tool_buscar_autos(
        self,
        ciudad: str = "lima",
        modelo: str = "",
        precio_min: int = 0,
        precio_max: int = 0,
        cantidad: int = 3,
    ) -> str:
        from tools.scraper_tool import FacebookScraper

        scraper = FacebookScraper(
            city=ciudad, min_price=precio_min, max_price=precio_max, query=modelo
        )
        autos = await scraper.scrape_cars(cantidad)
        orch = Orchestrator(api_key=self.groq_key)

        aptos: list[tuple[dict, object]] = []
        rechazados = 0
        for auto in autos:
            if is_seen(auto.get("url", "")):
                continue
            try:
                state = await orch.run_acquisition(car_data=auto)
                mark_seen(auto.get("url", ""), auto.get("title", ""))
                if state.car_data.get("apto_venta"):
                    aptos.append((auto, state))
                else:
                    rechazados += 1
            except Exception:
                rechazados += 1

        total = len(autos)
        if total == 0:
            return (
                "No se encontraron autos. Facebook puede estar bloqueando el acceso temporalmente. "
                "Intenta en unos minutos o prueba otra ciudad."
            )
        if not aptos:
            return (
                f"Se analizaron {total} auto(s) en {ciudad.title()} pero ninguno es rentable.\n"
                "Prueba con otro modelo o amplía el rango de precio."
            )

        lines = [
            f"✅ *{len(aptos)} oportunidad(es) encontrada(s)* de {total} analizado(s) en {ciudad.title()}:\n"
        ]
        for auto, state in aptos:
            cd = state.car_data
            pm = cd.get("precio_mercado") or 0
            pv = cd.get("precio_venta") or 0
            gan = cd.get("ganancia_est") or 0
            pct = cd.get("margen_pct") or 0
            lines.append(f"🚗 *{auto.get('title', '?')}*")
            lines.append(
                f"   💵 Publicado: {auto.get('price', '?')}  |  Mercado: ${pm:,.0f}"
            )
            lines.append(
                f"   🎯 Máx. pagar: ${pv:,.0f}  |  💰 Ganancia: ${gan:,.0f} ({pct:.0f}%)"
            )
            if auto.get("url"):
                lines.append(f"   🔗 {auto['url']}")
            if auto.get("whatsapp_number"):
                lines.append(f"   📱 +{auto['whatsapp_number']}")
            lines.append("")
        if rechazados:
            lines.append(f"_({rechazados} auto(s) descartados por no ser rentables)_")
        return "\n".join(lines)

    # ── Implementación: ver_pipeline ──────────────────────────────────────────

    def _tool_ver_pipeline(self, estado: str = "todos") -> str:
        if not self.airtable.is_configured():
            return "⚠️ Airtable no está configurado. Conéctalo en la app web (pestaña Configuración)."

        cars = self.airtable.get_approved_cars(max_records=100)
        if not cars:
            return "📭 No tienes autos guardados aún."

        if estado != "todos":
            cars = [c for c in cars if c.get("Pipeline", "Encontrado") == estado]
            if not cars:
                return f"No tienes autos en estado *{estado}*."

        emojis = {
            "Encontrado": "🔵",
            "Contactando": "🟡",
            "Negociando": "🟠",
            "Comprado": "🟣",
            "Vendido": "🟢",
        }
        groups: dict[str, list] = {}
        for c in cars:
            groups.setdefault(c.get("Pipeline", "Encontrado"), []).append(c)

        lines = [f"📋 *Pipeline* ({len(cars)} auto(s)):\n"]
        for stage in _PIPELINE_STATES:
            grp = groups.get(stage, [])
            if not grp:
                continue
            lines.append(f"{emojis.get(stage, '•')} *{stage}* ({len(grp)})")
            for c in grp:
                title = str(c.get("Título", "?"))[:50]
                pub = c.get("Precio Publicado") or 0
                merc = c.get("Precio Mercado") or 0
                gan_c = max(merc - pub, 0)
                suffix = f" — +${gan_c:,.0f}" if gan_c else ""
                lines.append(f"  • {title}{suffix}")
            lines.append("")
        return "\n".join(lines)

    # ── Implementación: analizar_url ──────────────────────────────────────────

    async def _tool_analizar_url(self, url: str) -> str:
        if not re.search(r"facebook\.com/marketplace/item/\d+", url):
            return "⚠️ La URL no parece ser un listing válido de Facebook Marketplace."

        from tools.scraper_tool import scrape_item_url

        car_data = await scrape_item_url(url)
        if not car_data:
            return "No pude acceder al listing. Puede que Facebook esté bloqueando el acceso."

        orch = Orchestrator(api_key=self.groq_key)
        try:
            state = await orch.run_acquisition(car_data=car_data)
        except Exception as e:
            return f"Error al analizar el listing: {e}"
        mark_seen(url, car_data.get("title", ""))

        cd = state.car_data
        pm = cd.get("precio_mercado") or 0
        pv = cd.get("precio_venta") or 0
        gan = cd.get("ganancia_est") or 0
        pct = cd.get("margen_pct") or 0
        apto = bool(cd.get("apto_venta"))

        icon = "✅" if apto else "❌"
        lines = [
            f"{icon} *{car_data.get('title', '?')}*",
            "",
            f"💵 Publicado: {car_data.get('price', '?')}",
            f"📊 Valor mercado: ${pm:,.0f}",
            f"🎯 Máx. a pagar: ${pv:,.0f}",
            f"💰 Ganancia est.: ${gan:,.0f} ({pct:.0f}%)",
            "",
        ]
        red_flags = cd.get("red_flags", [])
        green_flags = cd.get("green_flags", [])
        if red_flags:
            lines.append("⚠️ *Alertas:*")
            lines.extend(f"  • {f}" for f in red_flags[:4])
            lines.append("")
        if green_flags:
            lines.append("✅ *Puntos a favor:*")
            lines.extend(f"  • {f}" for f in green_flags[:4])
            lines.append("")
        obs = (
            state.inspection_data.get("observaciones")
            or state.inspection_data.get("resultado_inspeccion")
            or ""
        )
        if obs:
            lines.append(f"📋 _{obs[:350]}{'...' if len(obs) > 350 else ''}_")
        if car_data.get("whatsapp_number") and apto:
            lines.append(f"\n📱 WhatsApp vendedor: +{car_data['whatsapp_number']}")
        return "\n".join(lines)

    # ── Implementación: actualizar_estado ─────────────────────────────────────

    def _tool_actualizar_estado(self, titulo_parcial: str, nuevo_estado: str) -> str:
        if not self.airtable.is_configured():
            return "⚠️ Airtable no está configurado."
        if not titulo_parcial:
            return "Necesito el nombre (o parte del nombre) del auto para buscarlo."

        cars = self.airtable.get_approved_cars(max_records=100)
        matches = [
            c
            for c in cars
            if titulo_parcial.lower() in str(c.get("Título", "")).lower()
        ]

        if not matches:
            return (
                f"No encontré ningún auto que coincida con *{titulo_parcial}*.\n"
                "Verifica el nombre o usa más palabras del título."
            )
        if len(matches) > 1:
            titles = "\n".join(f"• {c.get('Título', '?')}" for c in matches[:5])
            return (
                f"Encontré {len(matches)} coincidencias. Sé más específico:\n{titles}"
            )

        car = matches[0]
        result = self.airtable.update_car(
            car.get("_id", ""), {"Pipeline": nuevo_estado}
        )
        if result:
            return f"✅ *{car.get('Título', '?')}* → *{nuevo_estado}*"
        return "❌ No pude actualizar el registro. Revisa la configuración de Airtable."

    # ── Implementación: resumen ────────────────────────────────────────────────

    def _tool_resumen(self) -> str:
        if not self.airtable.is_configured():
            return "⚠️ Airtable no está configurado."

        cars = self.airtable.get_approved_cars(max_records=500)
        if not cars:
            return "📭 Aún no tienes datos. Empieza buscando autos desde la app o desde aquí."

        total = len(cars)
        pipeline: dict[str, int] = {}
        gan_pot = 0.0
        gan_real = 0.0

        for c in cars:
            stage = c.get("Pipeline", "Encontrado")
            pipeline[stage] = pipeline.get(stage, 0) + 1
            pub = c.get("Precio Publicado") or 0
            merc = c.get("Precio Mercado") or 0
            gan_pot += max(merc - pub, 0)
            gan_real += c.get("Ganancia Real") or 0

        emojis = {
            "Encontrado": "🔵",
            "Contactando": "🟡",
            "Negociando": "🟠",
            "Comprado": "🟣",
            "Vendido": "🟢",
        }
        lines = [f"📊 *Resumen Anymotor* ({total} autos)\n"]
        for stage in _PIPELINE_STATES:
            count = pipeline.get(stage, 0)
            if count:
                lines.append(f"{emojis.get(stage, '•')} {stage}: {count}")

        lines.append(f"\n💰 Ganancia potencial: *${gan_pot:,.0f}*")
        if gan_real > 0:
            lines.append(f"✅ Ganancia real: *${gan_real:,.0f}*")
            vendidos = pipeline.get("Vendido", 0)
            if vendidos:
                lines.append(
                    f"📈 Promedio por auto vendido: *${gan_real / vendidos:,.0f}*"
                )
        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks
