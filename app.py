from __future__ import annotations

import asyncio
import io
import os
import re
import urllib.parse

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv, set_key

from agents.orchestrator import Orchestrator
from tools.airtable_tool import AirtableTool
from tools.scraper_tool import PERU_CITIES, FacebookScraper, extract_phone_number
from tools.seen_listings import clear_seen, count_seen, is_seen, mark_seen

# ─── Telegram bot (one instance shared across all Streamlit sessions) ──────────
@st.cache_resource
def _start_telegram_bot(token: str, groq_key: str, allowed_str: str):
    """Starts the Telegram bot in a background daemon thread, once per process."""
    import threading
    from agents.telegram_bot_agent import TelegramBotAgent
    allowed = [u.strip() for u in allowed_str.split(",") if u.strip()]
    bot = TelegramBotAgent(token=token, groq_key=groq_key, allowed_users=allowed)
    t = threading.Thread(target=bot.run_forever, daemon=True)
    t.start()
    return bot
from tools.telegram_tool import TelegramNotifier
from tools.whatsapp_tool import WhatsAppTool

# Resolve .env relative to this file, not the process's CWD — `streamlit run`
# may be launched from a different working directory than this file's folder.
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH)

# ─── Auth ──────────────────────────────────────────────────────────────────────
_AUTH_USER = os.getenv("APP_USER")
_AUTH_PASS = os.getenv("APP_PASSWORD")

def _check_auth() -> bool:
    return st.session_state.get("_authenticated") is True

def _login_page():
    st.set_page_config(page_title="Anymotor — Login", page_icon="🔐", layout="centered")
    col = st.columns([1, 2, 1])[1]
    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.image("https://img.icons8.com/emoji/96/automobile-emoji.png", width=80)
        st.title("Anymotor")
        st.markdown("##### Ingresa tus credenciales para continuar")
        st.markdown("---")
        user = st.text_input("Usuario", placeholder="usuario")
        pwd  = st.text_input("Contraseña", type="password", placeholder="••••••••")
        if st.button("Entrar", type="primary", width="stretch"):
            if user == _AUTH_USER and pwd == _AUTH_PASS:
                st.session_state["_authenticated"] = True
                st.rerun()
            else:
                st.error("Usuario o contraseña incorrectos.")

if not _check_auth():
    _login_page()
    st.stop()

# ─── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Anymotor",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
  .stApp { background-color: #f0f2f6; }

  .am-header {
    background: linear-gradient(135deg, #0d1b2a 0%, #1b2a4a 55%, #1a3a6b 100%);
    color: white; padding: 1.6rem 2rem; border-radius: 16px; margin-bottom: 1.4rem;
  }
  .am-title { font-size: 2rem; font-weight: 800; margin: 0 0 4px 0; letter-spacing: -0.5px; }
  .am-sub   { font-size: 0.95rem; color: #93c5fd; margin: 0; }

  .dot-ok   { color: #10b981; font-weight: 700; }
  .dot-warn { color: #f59e0b; font-weight: 700; }
  .dot-err  { color: #ef4444; font-weight: 700; }

  .profit-pill {
    background: linear-gradient(135deg, #10b981, #059669);
    color: white; padding: 7px 18px; border-radius: 30px;
    font-size: 1.05rem; font-weight: 700; display: inline-block; margin: 6px 0 10px 0;
  }
  .stat-box {
    background: white; border-radius: 14px; padding: 1.2rem 0.8rem;
    text-align: center; border: 1px solid #e2e8f0; box-shadow: 0 2px 8px rgba(0,0,0,0.05);
  }
  .stat-num   { font-size: 1.9rem; font-weight: 800; color: #1e3a5f; }
  .stat-label { font-size: 0.82rem; color: #64748b; font-weight: 500; margin-top: 2px; }

  .pub-box {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
    padding: 1rem 1.2rem; font-size: 0.93rem; line-height: 1.6; white-space: pre-wrap;
  }
  .pipeline-badge {
    display: inline-block; padding: 3px 12px; border-radius: 20px;
    font-size: 0.78rem; font-weight: 600;
  }
  .empty-state { text-align: center; padding: 3rem 1rem; color: #94a3b8; }
  .empty-state .icon { font-size: 3.5rem; }
  .empty-state h3 { color: #334155; margin: 0.5rem 0; }
  .empty-state p  { max-width: 420px; margin: 0 auto; line-height: 1.6; }

  #MainMenu, footer { visibility: hidden; }
  .stTabs [data-baseweb="tab"] { border-radius: 8px 8px 0 0; padding: 8px 22px; }
  hr.am-sep { border: none; border-top: 1px solid #e2e8f0; margin: 0.6rem 0; }
</style>
""", unsafe_allow_html=True)

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _secret_input(label: str, current: str | None, key: str, hint: str = "") -> str:
    """Password input that never pre-fills the actual value.
    Shows 'Configurado' placeholder when a value already exists.
    Returns the new value typed by the user, or "" if unchanged."""
    ph = "✓ Configurado — escribe para reemplazar" if current else (hint or "Pega tu clave aquí")
    return st.text_input(label, value="", type="password", placeholder=ph, key=key)


def _cfg_status(current: str | None) -> None:
    """Inline indicator: green check if configured, warning if missing."""
    if current:
        st.caption("🟢 Credencial guardada")
    else:
        st.caption("🔴 Sin configurar")
PIPELINE_COLORS = {
    "Encontrado":  "#dbeafe",
    "Contactando": "#fef9c3",
    "Negociando":  "#ffedd5",
    "Comprado":    "#ede9fe",
    "Vendido":     "#dcfce7",
}
PIPELINE_TEXT = {
    "Encontrado":  "#1e40af",
    "Contactando": "#854d0e",
    "Negociando":  "#9a3412",
    "Comprado":    "#5b21b6",
    "Vendido":     "#166534",
}
PIPELINE_OPTIONS = ["Encontrado", "Contactando", "Negociando", "Comprado", "Vendido"]


def parse_price(s) -> float | None:
    try:
        return float(str(s or "").replace("$", "").replace("S/", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def profit_info(pub_str, market: float | None):
    pub = parse_price(pub_str)
    if pub and market and market > pub:
        g = market - pub
        return g, (g / market) * 100
    return None, None


def pipeline_badge(status: str) -> str:
    bg = PIPELINE_COLORS.get(status, "#f1f5f9")
    tc = PIPELINE_TEXT.get(status, "#334155")
    return (
        f'<span class="pipeline-badge" '
        f'style="background:{bg};color:{tc};">{status}</span>'
    )


def whatsapp_link(phone: str, title: str, offer_price: float | None) -> str:
    """Builds a wa.me URL with a pre-written offer message in Spanish."""
    price_text = f"${offer_price:,.0f}" if offer_price else "un buen precio"
    msg = (
        f"Hola, vi tu publicación de *{title}*. "
        f"¿Sigue disponible? Te ofrezco {price_text} al contado. "
        f"¿Podemos coordinar para verlo? Gracias 🙌"
    )
    return f"https://wa.me/{phone}?text={urllib.parse.quote(msg)}"


def save_env(key: str, value: str):
    set_key(_ENV_PATH, key, value)


def _run_async(coro):
    """Runs a coroutine to completion in a fresh event loop, closing it afterwards.

    Streamlit re-executes this script top-to-bottom on every interaction, outside
    any asyncio context — each call here must get its own loop and clean up after
    itself (the async checkpointer connections are opened/closed per call too,
    see shared/checkpointing.py, precisely so they never outlive a closed loop)."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(None)
        loop.close()


# ─── Session state ─────────────────────────────────────────────────────────────
if "compare_ids" not in st.session_state:
    st.session_state.compare_ids = set()

# ─── Services ──────────────────────────────────────────────────────────────────
groq_key       = os.getenv("GROQ_API_KEY")
groq_model     = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
telegram_chat  = os.getenv("TELEGRAM_CHAT_ID")
telegram_allowed = os.getenv("TELEGRAM_ALLOWED_USERS", telegram_chat or "")
airtable   = AirtableTool()
whatsapp   = WhatsAppTool()

# Start Telegram bot agent if credentials are available
if telegram_token and groq_key:
    _start_telegram_bot(telegram_token, groq_key, telegram_allowed)

# ─── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="am-header">
  <div class="am-title">🚗 Anymotor</div>
  <div class="am-sub">Detecta autos baratos para revender y calcula tu ganancia — automáticamente</div>
</div>
""", unsafe_allow_html=True)

# ─── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    if st.button("🔒 Cerrar sesión", width="stretch"):
        st.session_state["_authenticated"] = False
        st.rerun()
    st.divider()
    st.markdown("#### ⚙️ Estado del sistema")

    def _dot(ok, label_ok, label_fail):
        cls = "dot-ok" if ok else "dot-err"
        label = label_ok if ok else label_fail
        st.markdown(f'<span class="{cls}">●</span> {label}', unsafe_allow_html=True)

    _dot(groq_key,                                        "IA lista",               "Sin IA — ve a Configuración")
    _dot(airtable.is_configured(),                        "Base de datos activa",   "Sin base de datos")
    _dot(telegram_token and telegram_chat,                "Telegram activo",        "Sin Telegram")
    _dot(telegram_token and groq_key and telegram_allowed,"Agente bot activo",      "Bot sin configurar")
    _dot(whatsapp.is_configured(),                        "WhatsApp activo",        "Sin WhatsApp")

    st.divider()
    st.markdown("#### 🔍 Buscar ofertas")
    query = st.text_input("Modelo de auto", value="", placeholder="ej. Toyota Corolla 2015")

    _CITIES = {"Lima": "lima", "Trujillo": "trujillo", "Arequipa": "arequipa"}
    city = _CITIES[st.selectbox("Ciudad", list(_CITIES.keys()))]
    st.caption("💡 Deja vacío el modelo para ver todos los autos disponibles.")

    st.markdown("#### 💵 Segmento de precio")
    _PRESETS = {
        "🚗 Económico  ($2k – $8k)":   (2000,  8000),
        "🚙 Estándar   ($8k – $18k)":  (8000,  18000),
        "🛻 Premium    ($18k – $40k)":  (18000, 40000),
        "✏️ Personalizado":             None,
    }
    preset_label = st.radio("Segmento", list(_PRESETS.keys()), index=0, label_visibility="hidden")
    preset_val   = _PRESETS[preset_label]
    if preset_val:
        min_price, max_price = preset_val
    else:
        pc1, pc2  = st.columns(2)
        min_price = pc1.number_input("Desde ($)", value=3000, step=500)
        max_price = pc2.number_input("Hasta ($)", value=15000, step=500)

    max_items = st.slider("Autos a revisar", 1, 10, 3, help="Aprox. 30s por auto")
    st.divider()
    search_btn = st.button("🔍 Buscar Ofertas Ahora", type="primary",
                           width="stretch", disabled=not groq_key)

# ─── Tabs ──────────────────────────────────────────────────────────────────────
tab_search, tab_manual, tab_db, tab_dash, tab_cfg = st.tabs([
    "🔍 Buscar Ofertas",
    "➕ Agregar Manual",
    "📊 Mis Oportunidades",
    "📈 Dashboard",
    "⚙️ Configuración",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — BUSCAR OFERTAS
# ══════════════════════════════════════════════════════════════════════════════
with tab_search:
    if not search_btn:
        st.markdown("""
        <div class="empty-state">
          <div class="icon">🔍</div>
          <h3>Todo listo para buscar</h3>
          <p>Configura el modelo, la ciudad y el rango de precio en el panel izquierdo,
          luego presiona <strong>Buscar Ofertas Ahora</strong>.<br><br>
          La IA analizará cada publicación y te dirá cuánto puedes ganar con cada auto.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        pb     = st.progress(0, text="Abriendo Facebook Marketplace...")
        banner = st.empty()

        scraper = FacebookScraper(city=city, min_price=min_price,
                                  max_price=max_price, query=query)
        autos_scraped = _run_async(scraper.scrape_cars(max_items))
        pb.progress(30, text=f"Encontrados {len(autos_scraped)} autos. Analizando con IA...")

        if not autos_scraped:
            pb.empty()
            st.warning("No se encontraron autos. Facebook puede estar bloqueando el acceso "
                       "— prueba otra ciudad o modelo.")
        else:
            orchestrator   = Orchestrator(api_key=groq_key, model=groq_model)
            aptos_count    = 0
            ganancia_total = 0.0
            ya_vistos      = 0

            for idx, auto in enumerate(autos_scraped, 1):
                pb.progress(30 + int(idx / len(autos_scraped) * 65),
                            text=f"Analizando auto {idx} de {len(autos_scraped)}...")

                # Skip listings already analyzed in a previous run
                if is_seen(auto.get("url", "")):
                    ya_vistos += 1
                    continue

                try:
                    state = _run_async(orchestrator.run_acquisition(car_data=auto))
                except Exception as e:
                    st.error(f"Error al analizar '{auto.get('title','?')}': {e}")
                    mark_seen(auto.get("url", ""), auto.get("title", ""))
                    continue

                mark_seen(auto.get("url", ""), auto.get("title", ""))

                is_apto = (getattr(state, "status", None) in ("acquired", "published")
                           or state.car_data.get("apto_venta") is True)
                pm  = state.car_data.get("precio_mercado")
                pv  = state.car_data.get("precio_venta")
                gan, pct_gan = profit_info(auto.get("price", ""), pm)

                if is_apto:
                    label = (f"✅ **¡Buena oferta!** — Ganancia estimada: **${gan:,.0f}** ({pct_gan:.0f}%)"
                             if gan else "✅ **¡Buena oferta!** — Precio por debajo del mercado")
                    st.success(label)
                else:
                    st.error("❌ **No es rentable** — El precio es demasiado alto para revender con ganancia.")

                col_img, col_body = st.columns([1, 3], gap="medium")
                with col_img:
                    if auto.get("image_url"):
                        st.image(auto["image_url"], width="stretch")
                    else:
                        st.markdown('<div style="background:#f1f5f9;border-radius:10px;'
                                    'text-align:center;font-size:3rem;padding:1.5rem;">🚗</div>',
                                    unsafe_allow_html=True)

                with col_body:
                    title = auto.get("title", "Sin título").replace("$", "")
                    st.markdown(f"**{title}**")

                    if is_apto and gan:
                        aptos_count   += 1
                        ganancia_total += gan
                        margen = state.car_data.get("margen_pct")
                        pill = f"💰 +${gan:,.0f} de ganancia"
                        if margen:
                            pill += f" ({margen:.0f}%)"
                        st.markdown(f'<span class="profit-pill">{pill}</span>',
                                    unsafe_allow_html=True)

                    # Precio publicado con moneda detectada
                    moneda   = state.car_data.get("moneda_detectada") or auto.get("currency", "")
                    pub_usd  = state.car_data.get("precio_pub_usd")
                    price_display = auto.get("price", "?")
                    if moneda == "PEN" and pub_usd:
                        price_display += f" (≈ ${pub_usd:,.0f} USD)"

                    m1, m2, m3 = st.columns(3)
                    m1.metric("Precio publicado",      price_display)
                    m2.metric("Valor real de mercado", f"${pm:,.0f}" if pm else "—")
                    if is_apto:
                        m3.metric("Precio máximo de compra", f"${pv:,.0f}" if pv else "—")
                    else:
                        limit = pm * 0.82 if pm else None
                        m3.metric("Precio que sería rentable", f"${limit:,.0f}" if limit else "—")

                    # Meta info: condition, km, transmission, fuel, currency note
                    meta_parts = []
                    if auto.get("condition"):     meta_parts.append(f"🔧 {auto['condition']}")
                    if auto.get("kilometraje"):   meta_parts.append(f"🛣️ {auto['kilometraje']:,} km")
                    if auto.get("transmision"):   meta_parts.append(f"⚙️ {auto['transmision']}")
                    if auto.get("combustible"):   meta_parts.append(f"⛽ {auto['combustible']}")
                    if moneda:                    meta_parts.append(f"💱 {moneda}")
                    if meta_parts:
                        st.caption("  ·  ".join(meta_parts))
                    # Links: Facebook + WhatsApp contact
                    link_cols = st.columns([1, 1, 4])
                    if auto.get("url"):
                        link_cols[0].markdown(f"[🔗 Ver en Facebook]({auto['url']})")
                    wa_num = auto.get("whatsapp_number")
                    if wa_num and is_apto:
                        wa_url = whatsapp_link(wa_num, auto.get("title", ""), pv)
                        link_cols[1].markdown(
                            f'<a href="{wa_url}" target="_blank" style="'
                            f'background:#25D366;color:white;padding:5px 14px;'
                            f'border-radius:20px;font-size:0.85rem;font-weight:600;'
                            f'text-decoration:none;">💬 Enviar oferta</a>',
                            unsafe_allow_html=True,
                        )

                    # Red flags / green flags
                    red_flags   = state.car_data.get("red_flags", [])
                    green_flags = state.car_data.get("green_flags", [])
                    if red_flags or green_flags:
                        fg1, fg2 = st.columns(2)
                        if red_flags:
                            fg1.markdown("**⚠️ Alertas:**\n" + "\n".join(f"- {f}" for f in red_flags))
                        if green_flags:
                            fg2.markdown("**✅ Puntos a favor:**\n" + "\n".join(f"- {f}" for f in green_flags))

                    obs = (state.inspection_data.get("observaciones")
                           or state.inspection_data.get("resultado_inspeccion") or "")
                    if obs:
                        with st.expander("📋 Ver análisis completo de la IA"):
                            st.write(obs)

                    if is_apto:
                        pub = state.publication_data or {}
                        with st.expander("📣 Texto listo para publicar"):
                            if pub.get("titulo"):
                                st.markdown(f"**Título:** {pub['titulo']}")
                            if pub.get("descripcion"):
                                st.markdown("**Descripción:**")
                                st.markdown(f'<div class="pub-box">{pub["descripcion"]}</div>',
                                            unsafe_allow_html=True)
                            if pub.get("precio_venta"):
                                st.markdown(f"**Precio sugerido de venta:** ${pub['precio_venta']:,.0f}")
                            if not any(pub.get(k) for k in ("titulo", "descripcion")):
                                st.json(pub)

                        if airtable.is_configured():
                            saved = airtable.save_car(
                                car_data={**auto, "precio_mercado": pm, "precio_venta": pv},
                                inspection_data=state.inspection_data,
                            )
                            if saved:
                                st.toast("💾 Guardado en tu base de datos")

                        if telegram_token and telegram_chat:
                            TelegramNotifier(bot_token=telegram_token,
                                             chat_id=telegram_chat).send_deal_alert(auto, state.car_data)

                        if whatsapp.is_configured():
                            whatsapp.send_deal_alert(auto, state.car_data)

                st.markdown('<hr class="am-sep">', unsafe_allow_html=True)

            pb.progress(100, text="¡Listo!")
            analizados = len(autos_scraped) - ya_vistos
            omitidos_txt = f" · {ya_vistos} ya analizados antes (omitidos)" if ya_vistos else ""
            if aptos_count > 0:
                banner.success(
                    f"🎉 **Búsqueda completada** — {aptos_count} de {analizados} "
                    f"autos son buenas ofertas. Ganancia potencial total: **${ganancia_total:,.0f}**"
                    f"{omitidos_txt}"
                )
            elif analizados == 0:
                banner.info(
                    f"Todos los autos encontrados ({ya_vistos}) ya fueron analizados anteriormente. "
                    f"Intenta buscar en otra ciudad o ampliar el rango de precio."
                )
            else:
                banner.warning(
                    f"Búsqueda completada — {analizados} autos analizados pero ninguno "
                    f"tiene margen suficiente con este rango de precio. "
                    f"Prueba ampliar el presupuesto o buscar otro modelo."
                    f"{omitidos_txt}"
                )

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — AGREGAR MANUAL
# ══════════════════════════════════════════════════════════════════════════════
with tab_manual:
    st.subheader("➕ Agregar un auto manualmente")
    st.caption("¿Encontraste un auto en un grupo de WhatsApp, de un amigo, o en otra plataforma? "
               "Agrégalo aquí y la IA calculará si es buena oportunidad.")

    with st.form("form_manual"):
        fc1, fc2 = st.columns(2)
        m_title  = fc1.text_input("Título / Descripción corta *", placeholder="ej. Toyota Corolla 2018")
        m_price  = fc2.text_input("Precio publicado *", placeholder="ej. $8500 o S/12000")
        fc3, fc4, fc5 = st.columns(3)
        m_year   = fc3.number_input("Año", min_value=1990, max_value=2026, value=2015)
        m_city   = fc4.text_input("Ciudad", placeholder="lima")
        m_cond   = fc5.selectbox("Condición", ["", "Usado - Buen estado", "Usado - Como nuevo",
                                               "Usado - Con detalles", "Nuevo"])
        m_url    = st.text_input("URL de la publicación (opcional)", placeholder="https://...")
        m_img    = st.text_input("URL de la foto (opcional)", placeholder="https://...")
        m_desc   = st.text_area("Descripción del vendedor (opcional)",
                                placeholder="Pega aquí lo que dice el vendedor sobre el auto...")
        submitted = st.form_submit_button("🤖 Analizar con IA", type="primary")

    if submitted:
        if not m_title or not m_price:
            st.error("El título y el precio son obligatorios.")
        elif not groq_key:
            st.error("Configura la IA en la pestaña Configuración antes de analizar.")
        else:
            manual_phone = extract_phone_number(f"{m_desc} {m_url}")
            car_data = {
                "title":          m_title,
                "price":          m_price,
                "año":            m_year,
                "city":           m_city,
                "condition":      m_cond,
                "url":            m_url,
                "image_url":      m_img,
                "description":    m_desc,
                "whatsapp_number": manual_phone,
                "raw_data":       f"Año: {m_year} | Condición: {m_cond} | {m_desc}",
            }
            with st.spinner("Analizando con IA..."):
                orch2 = Orchestrator(api_key=groq_key, model=groq_model)
                try:
                    state2 = _run_async(orch2.run_acquisition(car_data=car_data))
                except Exception as e:
                    st.error(f"Error al analizar: {e}")
                    state2 = None

            if state2:
                is_apto2 = (getattr(state2, "status", None) in ("acquired", "published")
                            or state2.car_data.get("apto_venta") is True)
                pm2  = state2.car_data.get("precio_mercado")
                pv2  = state2.car_data.get("precio_venta")
                gan2, pct2 = profit_info(m_price, pm2)

                if is_apto2:
                    st.success("✅ **¡Buena oferta!**")
                    if gan2:
                        st.markdown(f'<span class="profit-pill">💰 +${gan2:,.0f} de ganancia ({pct2:.0f}%)</span>',
                                    unsafe_allow_html=True)
                    r1, r2, r3 = st.columns(3)
                    r1.metric("Precio publicado",      m_price)
                    r2.metric("Valor real de mercado", f"${pm2:,.0f}" if pm2 else "—")
                    r3.metric("Precio máximo de compra", f"${pv2:,.0f}" if pv2 else "—")

                    if manual_phone:
                        wa_url2 = whatsapp_link(manual_phone, m_title, pv2)
                        st.markdown(
                            f'<a href="{wa_url2}" target="_blank" style="'
                            f'background:#25D366;color:white;padding:7px 18px;'
                            f'border-radius:20px;font-size:0.9rem;font-weight:600;'
                            f'text-decoration:none;display:inline-block;margin:8px 0;">'
                            f'💬 Enviar oferta por WhatsApp</a>',
                            unsafe_allow_html=True,
                        )

                    obs2 = (state2.inspection_data.get("observaciones")
                            or state2.inspection_data.get("resultado_inspeccion") or "")
                    if obs2:
                        with st.expander("📋 Ver análisis completo"):
                            st.write(obs2)

                    if airtable.is_configured():
                        s2 = airtable.save_car(
                            car_data={**car_data, "precio_mercado": pm2, "precio_venta": pv2},
                            inspection_data=state2.inspection_data,
                        )
                        if s2:
                            st.toast("💾 Guardado en tu base de datos")
                else:
                    st.error("❌ **No es rentable**")
                    razon2 = (state2.inspection_data.get("resultado_inspeccion")
                              or state2.inspection_data.get("observaciones")
                              or "No cumple con el margen mínimo de ganancia.")
                    st.info(f"📋 {razon2}")
                    if pm2:
                        st.caption(f"💡 Precio rentable máximo: ${pm2 * 0.82:,.0f}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — MIS OPORTUNIDADES
# ══════════════════════════════════════════════════════════════════════════════
with tab_db:
    if not airtable.is_configured():
        st.markdown("""
        <div class="empty-state">
          <div class="icon">🗄️</div>
          <h3>Base de datos no conectada</h3>
          <p>Ve a la pestaña <strong>Configuración</strong> para conectar Airtable.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        with st.spinner("Cargando base de datos..."):
            cars = airtable.get_approved_cars()

        if not cars:
            st.markdown("""
            <div class="empty-state">
              <div class="icon">📭</div>
              <h3>Aún no tienes oportunidades guardadas</h3>
              <p>Cuando la IA detecte una buena oferta en <strong>Buscar Ofertas</strong>
              o <strong>Agregar Manual</strong>, se guardará aquí automáticamente.</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            # ── Stats ──────────────────────────────────────────────────────
            total = len(cars)
            gan_pot = sum(
                max((c.get("Precio Mercado") or 0) - (c.get("Precio Publicado") or 0), 0)
                for c in cars
            )
            gan_real = sum(c.get("Ganancia Real") or 0 for c in cars)
            vendidos = sum(1 for c in cars if c.get("Pipeline") == "Vendido")

            s1, s2, s3, s4 = st.columns(4)
            s1.markdown(f'<div class="stat-box"><div class="stat-num">{total}</div>'
                        f'<div class="stat-label">Oportunidades guardadas</div></div>', unsafe_allow_html=True)
            s2.markdown(f'<div class="stat-box"><div class="stat-num">${gan_pot:,.0f}</div>'
                        f'<div class="stat-label">Ganancia potencial total</div></div>', unsafe_allow_html=True)
            s3.markdown(f'<div class="stat-box"><div class="stat-num">${gan_real:,.0f}</div>'
                        f'<div class="stat-label">Ganancia real obtenida</div></div>', unsafe_allow_html=True)
            s4.markdown(f'<div class="stat-box"><div class="stat-num">{vendidos}</div>'
                        f'<div class="stat-label">Autos vendidos</div></div>', unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # ── Controles ──────────────────────────────────────────────────
            tb1, tb2, tb3 = st.columns([1, 1, 4])

            if tb1.button("🔄 Actualizar", key="refresh_db"):
                st.rerun()

            # Exportar
            df_export = pd.DataFrame([{k: v for k, v in c.items() if not k.startswith("_")}
                                       for c in cars])
            csv_bytes = df_export.to_csv(index=False).encode("utf-8")
            tb2.download_button("📥 Exportar CSV", csv_bytes,
                                "anymotor_oportunidades.csv", "text/csv", key="csv_dl")

            # Comparar
            selected_cars = [c for c in cars if c.get("_id") in st.session_state.compare_ids]
            if len(selected_cars) >= 2:
                with st.expander(f"⚖️ Comparando {len(selected_cars)} autos", expanded=True):
                    cols = st.columns(len(selected_cars))
                    for ci, (col, car) in enumerate(zip(cols, selected_cars)):
                        pub   = car.get("Precio Publicado") or 0
                        merc  = car.get("Precio Mercado") or 0
                        gan_c = max(merc - pub, 0)
                        pct_c = (gan_c / merc * 100) if merc > 0 else 0
                        col.markdown(f"**{str(car.get('Título','?'))[:35]}**")
                        col.metric("Precio publicado",  f"${pub:,.0f}" if pub else "—")
                        col.metric("Valor de mercado",  f"${merc:,.0f}" if merc else "—")
                        col.metric("Ganancia estimada", f"${gan_c:,.0f} ({pct_c:.0f}%)" if gan_c else "—")
                        col.caption(f"Pipeline: {car.get('Pipeline','Encontrado')}")
                    if st.button("Limpiar selección", key="clear_cmp"):
                        st.session_state.compare_ids = set()
                        st.rerun()

            st.markdown('<hr class="am-sep">', unsafe_allow_html=True)

            # ── Car cards ──────────────────────────────────────────────────
            for car in cars:
                rec_id = car.get("_id", "")
                pub    = car.get("Precio Publicado") or 0
                merc   = car.get("Precio Mercado") or 0
                venta  = car.get("Precio Venta Sugerido") or 0
                gan_c  = max(merc - pub, 0)
                pct_c  = (gan_c / merc * 100) if merc > 0 else 0
                title  = str(car.get("Título", "Sin título")).replace("$", "")
                status = car.get("Pipeline", "Encontrado")

                col_chk, col_img, col_body = st.columns([0.3, 1, 3], gap="small")

                with col_chk:
                    checked = st.checkbox("Comparar", key=f"cmp_{rec_id}",
                                          value=rec_id in st.session_state.compare_ids,
                                          help="Seleccionar para comparar",
                                          label_visibility="hidden")
                    if checked:
                        st.session_state.compare_ids.add(rec_id)
                    else:
                        st.session_state.compare_ids.discard(rec_id)

                with col_img:
                    if car.get("Imagen"):
                        st.image(car["Imagen"], width="stretch")
                    else:
                        st.markdown('<div style="background:#f1f5f9;border-radius:10px;'
                                    'text-align:center;font-size:2.5rem;padding:1rem;">🚗</div>',
                                    unsafe_allow_html=True)

                with col_body:
                    header_c, badge_c = st.columns([3, 1])
                    header_c.markdown(f"**{title}**")
                    badge_c.markdown(pipeline_badge(status), unsafe_allow_html=True)

                    if gan_c > 0:
                        st.markdown(f'<span class="profit-pill">💰 +${gan_c:,.0f} ({pct_c:.0f}%)</span>',
                                    unsafe_allow_html=True)

                    m1, m2, m3 = st.columns(3)
                    m1.metric("Precio publicado",       f"${pub:,.0f}"   if pub   else "—")
                    m2.metric("Valor de mercado",        f"${merc:,.0f}"  if merc  else "—")
                    m3.metric("Precio máx. de compra",  f"${venta:,.0f}" if venta else "—")

                    meta = st.columns(3)
                    if car.get("Estado"):  meta[0].caption(f"🔧 {car['Estado']}")
                    if car.get("Ciudad"):  meta[1].caption(f"📍 {car['Ciudad']}")
                    if car.get("Fecha Análisis"): meta[2].caption(f"📅 {car['Fecha Análisis']}")

                    db_link_cols = st.columns([1, 1, 4])
                    if car.get("URL"):
                        db_link_cols[0].markdown(f"[🔗 Ver publicación]({car['URL']})")
                    if car.get("Teléfono"):
                        wa_url_db = whatsapp_link(car["Teléfono"], title, venta or merc * 0.82 or None)
                        db_link_cols[1].markdown(
                            f'<a href="{wa_url_db}" target="_blank" style="'
                            f'background:#25D366;color:white;padding:4px 12px;'
                            f'border-radius:20px;font-size:0.82rem;font-weight:600;'
                            f'text-decoration:none;">💬 Enviar oferta</a>',
                            unsafe_allow_html=True,
                        )

                    # Actualizar pipeline
                    with st.expander("✏️ Actualizar estado y finanzas"):
                        pc1, pc2 = st.columns([2, 1])
                        new_status = pc1.selectbox(
                            "Estado del deal",
                            PIPELINE_OPTIONS,
                            index=PIPELINE_OPTIONS.index(status) if status in PIPELINE_OPTIONS else 0,
                            key=f"pipe_{rec_id}",
                        )

                        upd_fields: dict = {"Pipeline": new_status}

                        # Campos financieros reales
                        if new_status in ("Comprado", "Vendido"):
                            fc1r, fc2r = st.columns(2)
                            compra_real = fc1r.number_input(
                                "Precio que pagué (real)", value=float(car.get("Precio Compra Real") or 0),
                                step=100.0, key=f"compra_{rec_id}",
                            )
                            venta_real = fc2r.number_input(
                                "Precio que vendí (real)", value=float(car.get("Precio Venta Real") or 0),
                                step=100.0, key=f"venta_{rec_id}",
                            )
                            if compra_real > 0:
                                upd_fields["Precio Compra Real"] = compra_real
                            if venta_real > 0:
                                upd_fields["Precio Venta Real"] = venta_real
                            if compra_real > 0 and venta_real > 0:
                                gan_real_val = venta_real - compra_real
                                upd_fields["Ganancia Real"] = gan_real_val
                                color = "#10b981" if gan_real_val > 0 else "#ef4444"
                                st.markdown(
                                    f'<p style="font-size:1.1rem;font-weight:700;color:{color};">'
                                    f'Ganancia real: ${gan_real_val:,.0f}</p>',
                                    unsafe_allow_html=True,
                                )

                        notes_val = st.text_area("Notas", value=car.get("Notas", ""),
                                                 key=f"notes_{rec_id}", height=80,
                                                 placeholder="Ej: vendedor dispuesto a negociar, revisa frenos...")
                        if notes_val:
                            upd_fields["Notas"] = notes_val

                        if pc2.button("💾 Guardar", key=f"save_{rec_id}"):
                            result = airtable.update_car(rec_id, upd_fields)
                            if result:
                                st.toast("✅ Actualizado")
                                st.rerun()
                            else:
                                st.error("No se pudo guardar. ¿Corriste el setup de campos avanzados?")

                st.markdown('<hr class="am-sep">', unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab_dash:
    st.subheader("📈 Dashboard de rendimiento")

    if not airtable.is_configured():
        st.markdown("""
        <div class="empty-state">
          <div class="icon">📊</div>
          <h3>Conecta Airtable para ver tu dashboard</h3>
          <p>Ve a <strong>Configuración</strong> y añade tus credenciales de Airtable.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        with st.spinner("Cargando datos..."):
            dash_cars = airtable.get_approved_cars(max_records=500)

        if not dash_cars:
            st.info("Aún no hay datos. Empieza buscando ofertas para ver estadísticas aquí.")
        else:
            df = pd.DataFrame(dash_cars)

            # Normalise numeric columns — Airtable omits fields that are empty,
            # so the column may not exist at all in the DataFrame.
            for _col in ["Precio Mercado", "Precio Publicado", "Precio Venta Sugerido",
                         "Precio Compra Real", "Precio Venta Real", "Ganancia Real"]:
                if _col not in df.columns:
                    df[_col] = 0
                df[_col] = pd.to_numeric(df[_col], errors="coerce").fillna(0)
            for _col in ["Título", "Pipeline", "Ciudad", "Fecha Análisis"]:
                if _col not in df.columns:
                    df[_col] = ""

            # KPIs
            total_d      = len(df)
            gan_pot_d    = sum(max((r.get("Precio Mercado") or 0) - (r.get("Precio Publicado") or 0), 0) for r in dash_cars)
            gan_real_d   = df["Ganancia Real"].sum()
            vendidos_d   = int((df["Pipeline"] == "Vendido").sum())

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Oportunidades encontradas", total_d)
            k2.metric("Ganancia potencial",  f"${gan_pot_d:,.0f}")
            k3.metric("Ganancia real",        f"${gan_real_d:,.0f}")
            k4.metric("Autos vendidos",       vendidos_d)

            st.markdown("<br>", unsafe_allow_html=True)

            ch1, ch2 = st.columns(2)

            # Gráfica 1 — Estado del pipeline
            with ch1:
                st.markdown("**Estado del pipeline**")
                pipeline_counts = (
                    df["Pipeline"].replace("", "Encontrado").fillna("Encontrado")
                    .value_counts().reset_index()
                )
                pipeline_counts.columns = ["Estado", "Cantidad"]
                color_map = {
                    "Encontrado": "#3b82f6", "Contactando": "#f59e0b",
                    "Negociando": "#f97316", "Comprado": "#8b5cf6", "Vendido": "#10b981",
                }
                fig1 = px.pie(
                    pipeline_counts, values="Cantidad", names="Estado",
                    color="Estado", color_discrete_map=color_map,
                    hole=0.4,
                )
                fig1.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300,
                                   showlegend=True, legend=dict(orientation="h"))
                st.plotly_chart(fig1, width="stretch")

            # Gráfica 2 — Top 8 oportunidades por ganancia potencial
            with ch2:
                st.markdown("**Top oportunidades por ganancia potencial**")
                df["Ganancia Potencial"] = (
                    df["Precio Mercado"].fillna(0) - df["Precio Publicado"].fillna(0)
                ).clip(lower=0)
                top8 = df.nlargest(8, "Ganancia Potencial")[["Título", "Ganancia Potencial"]].copy()
                top8["Título"] = top8["Título"].str[:25] + "…"
                fig2 = px.bar(top8, x="Ganancia Potencial", y="Título", orientation="h",
                              color="Ganancia Potencial",
                              color_continuous_scale=["#bfdbfe", "#1d4ed8"])
                fig2.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=300,
                                   coloraxis_showscale=False, yaxis_title="", xaxis_title="Ganancia ($)")
                st.plotly_chart(fig2, width="stretch")

            # Gráfica 3 — Autos encontrados por fecha
            fecha_valid = df["Fecha Análisis"].replace("", pd.NA).dropna()
            if not fecha_valid.empty:
                st.markdown("**Autos encontrados por fecha**")
                by_date = fecha_valid.value_counts().sort_index().reset_index()
                by_date.columns = ["Fecha", "Cantidad"]
                fig3 = px.area(by_date, x="Fecha", y="Cantidad",
                               color_discrete_sequence=["#3b82f6"])
                fig3.update_layout(margin=dict(t=10, b=10), height=240,
                                   xaxis_title="", yaxis_title="Autos encontrados")
                st.plotly_chart(fig3, width="stretch")

            # Gráfica 4 — Ganancia real vs potencial (si hay datos)
            if (df["Ganancia Real"] > 0).any():
                st.markdown("**Ganancia real vs potencial**")
                df_cmp = df[df["Ganancia Real"] > 0][
                    ["Título", "Ganancia Potencial", "Ganancia Real"]
                ].copy()
                df_cmp["Título"] = df_cmp["Título"].str[:20] + "…"
                df_melt = df_cmp.melt("Título", var_name="Tipo", value_name="Ganancia")
                fig4 = px.bar(df_melt, x="Título", y="Ganancia", color="Tipo", barmode="group",
                              color_discrete_map={"Ganancia Potencial": "#93c5fd", "Ganancia Real": "#10b981"})
                fig4.update_layout(margin=dict(t=10, b=10), height=280, xaxis_title="")
                st.plotly_chart(fig4, width="stretch")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
with tab_cfg:
    st.subheader("⚙️ Configuración")
    st.caption("Todos los cambios se guardan directamente en tu archivo .env.")

    with st.expander("🤖 Inteligencia Artificial (Groq)", expanded=not bool(groq_key)):
        st.markdown("La IA analiza los autos y calcula si son buenas ofertas.")
        _cfg_status(groq_key)
        new_groq = _secret_input("GROQ API Key", groq_key, "cfg_groq")
        if st.button("Guardar IA", key="save_groq"):
            if new_groq:
                save_env("GROQ_API_KEY", new_groq)
                st.toast("✅ Guardado. Recarga la página para aplicar.")
            else:
                st.toast("ℹ️ No se escribió ningún valor — se conserva el actual.")

    with st.expander("🗄️ Base de datos Airtable", expanded=not airtable.is_configured()):
        st.markdown("Donde se guardan todos los autos aptos que encuentres.")
        _cfg_status(airtable.pat)
        new_pat  = _secret_input("Personal Access Token (PAT)", airtable.pat, "cfg_pat")
        new_base = st.text_input("Base ID (ej. appXXXXXXXXXXXXXX)",    value=airtable.base_id or "",    key="cfg_base")
        new_tbl  = st.text_input("ID de tabla (ej. tblXXXXXXXXXXXXXX)", value=airtable.table_name or "", key="cfg_tbl")
        if st.button("Guardar Airtable", key="save_at"):
            if new_pat:
                save_env("AIRTABLE_PAT", new_pat)
            save_env("AIRTABLE_BASE_ID",    new_base)
            save_env("AIRTABLE_TABLE_NAME", new_tbl)
            st.toast("✅ Guardado. Recarga la página para aplicar.")

    with st.expander("🗂️ Cache de listings analizados"):
        n = count_seen()
        st.markdown(
            f"Anymotor recuerda **{n} publicaciones** ya analizadas y las omite "
            f"automáticamente en búsquedas futuras para no repetir trabajo."
        )
        if st.button("🗑️ Limpiar cache", key="clear_cache", disabled=n == 0):
            clear_seen()
            st.toast("✅ Cache limpiado. Todos los autos serán analizados de nuevo.")
            st.rerun()

    with st.expander("🔧 Campos avanzados en Airtable"):
        st.markdown(
            "Crea los campos de **Pipeline**, **Precio Compra Real**, **Precio Venta Real**, "
            "**Ganancia Real** y **Notas** en tu tabla de Airtable. "
            "Esto es necesario para el seguimiento de deals y el Dashboard."
        )
        if st.button("⚙️ Configurar campos avanzados", type="primary", key="setup_fields"):
            if not airtable.is_configured():
                st.error("Primero configura Airtable arriba.")
            else:
                with st.spinner("Creando campos..."):
                    results = airtable.setup_advanced_fields()
                for r in results:
                    st.write(r)

    with st.expander("🤖 Agente Telegram (Bot inteligente)", expanded=not bool(telegram_allowed)):
        st.markdown(
            "El agente escucha tu Telegram y puede buscar autos, consultar tu pipeline, "
            "analizar URLs y actualizar deals — todo desde el chat.\n\n"
            "**Comandos disponibles:** `/start` · `/reset` · `/id`\n\n"
            "Agrega los **Telegram User IDs** autorizados (uno por línea o separados por coma). "
            "Escribe `/id` en el bot para conocer tu ID."
        )
        new_allowed = st.text_area(
            "Usuarios autorizados (Telegram User IDs)",
            value=telegram_allowed,
            placeholder="1092840016\n987654321",
            height=80,
            key="cfg_allowed",
        )
        if st.button("Guardar usuarios autorizados", key="save_allowed"):
            clean = ",".join(u.strip() for u in re.split(r"[,\n]+", new_allowed) if u.strip())
            save_env("TELEGRAM_ALLOWED_USERS", clean)
            st.toast("✅ Guardado. Reinicia la app para aplicar.")

        if telegram_token and groq_key:
            st.success("✅ Bot activo — escribe al bot de Telegram para empezar.")
        elif not telegram_token:
            st.warning("Configura el Token de Telegram abajo para activar el bot.")
        else:
            st.warning("Configura la API Key de IA para activar el bot.")

    with st.expander("💬 Notificaciones Telegram"):
        st.markdown("Recibe un mensaje en Telegram cada vez que se detecte una buena oferta.")
        _cfg_status(telegram_token)
        new_tg_token = _secret_input("Bot Token", telegram_token, "cfg_tg_t")
        new_tg_chat  = st.text_input("Chat ID", value=telegram_chat or "", key="cfg_tg_c")
        if st.button("Guardar Telegram", key="save_tg"):
            if new_tg_token:
                save_env("TELEGRAM_BOT_TOKEN", new_tg_token)
            save_env("TELEGRAM_CHAT_ID", new_tg_chat)
            st.toast("✅ Guardado. Recarga la página para aplicar.")

    with st.expander("📱 Notificaciones WhatsApp"):
        st.markdown("""
        Recibe alertas por WhatsApp usando el servicio gratuito **CallMeBot**.

        **Cómo activarlo (una sola vez):**
        1. Guarda este número en tus contactos: **+34 644 44 23 23** (CallMeBot)
        2. Envíale este mensaje por WhatsApp: `I allow callmebot to send me messages`
        3. Recibirás tu API key de respuesta en segundos
        """)
        _cfg_status(whatsapp.api_key)
        new_wa_phone  = st.text_input("Tu número de WhatsApp (con código de país, ej. +51999888777)",
                                      value=whatsapp.phone or "", key="cfg_wa_phone")
        new_wa_key    = _secret_input("API Key de CallMeBot", whatsapp.api_key, "cfg_wa_key")

        col_save_wa, col_test_wa = st.columns(2)
        if col_save_wa.button("Guardar WhatsApp", key="save_wa"):
            save_env("WHATSAPP_PHONE", new_wa_phone)
            if new_wa_key:
                save_env("WHATSAPP_APIKEY", new_wa_key)
            st.toast("✅ Guardado. Recarga la página para aplicar.")

        # For the test, use the new key if provided, otherwise fall back to stored key
        effective_wa_key = new_wa_key or whatsapp.api_key or ""
        if col_test_wa.button("🧪 Enviar mensaje de prueba", key="test_wa"):
            test_wa = WhatsAppTool(phone=new_wa_phone, api_key=effective_wa_key)
            if test_wa.is_configured():
                ok = test_wa.send("🚗 Anymotor: ¡Notificaciones de WhatsApp activadas correctamente!")
                if ok:
                    st.success("✅ Mensaje enviado. Revisa tu WhatsApp.")
                else:
                    st.error("❌ No se pudo enviar. Verifica el número y la API key.")
            else:
                st.error("Completa el número y la API key antes de probar.")
