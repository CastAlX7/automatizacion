# Anymotor — Sistema Multiagente de Venta de Autos Usados

## Descripción del proyecto
Sistema multiagente en Python que automatiza el "flipping" de autos usados en Lima, Perú: detecta autos baratos en Facebook Marketplace, evalúa con IA si conviene comprarlos para revender con ganancia, genera el anuncio de reventa, atiende a los clientes potenciales y cierra la negociación. Construido sobre **LangChain + LangGraph**, con Streamlit como interfaz web y un bot de Telegram como interfaz conversacional alternativa.

## Arquitectura

El pipeline de adquisición→publicación es un **`StateGraph`** de LangGraph con ramificación condicional; CRM y Cierre se invocan por turno porque dependen de entradas humanas (mensajes del cliente, ofertas), no de un paso más del pipeline automático.

```
                     ┌────────────────────┐
                     │     Orchestrator    │
                     │ (agentes + grafo)   │
                     └──────────┬──────────┘
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │  CarSaleState (Pydantic)      │
                 │  compartido entre nodos       │
                 └──────────────┬────────────────┘
                                │
                                ▼
                 ┌──────────────────────────────┐
                 │  AcquisitionAgent (nodo)      │
                 │  ChatGroq.with_structured_output│
                 └──────────────┬────────────────┘
                                │
                  apto_venta?   │  no
                     sí         ▼
                      │   ┌───────────┐
                      │   │ rejected  │ → END
                      │   └───────────┘
                      ▼
                 ┌──────────────────────────────┐
                 │  PublicationAgent (nodo)      │ → END
                 └──────────────────────────────┘

     (invocados por turno, fuera del grafo anterior)
                 ┌──────────────────────────────┐
                 │  CRMChatbotAgent               │
                 │  mini-StateGraph propio,       │
                 │  memoria persistida por        │
                 │  thread_id (SqliteSaver)       │
                 └──────────────────────────────┘
                 ┌──────────────────────────────┐
                 │  SalesClosingAgent             │
                 │  regla de negocio determinista │
                 │  (oferta ≥ 85% precio mercado)  │
                 └──────────────────────────────┘

     (interfaz conversacional alternativa)
                 ┌──────────────────────────────┐
                 │  TelegramBotAgent               │
                 │  agente ReAct (langchain.agents.│
                 │  create_agent) + 5 tools        │
                 │  + memoria persistida por chat   │
                 └──────────────────────────────┘
```

Coordinación entre agentes: **estado compartido explícito** (`CarSaleState`, Pydantic) + **checkpointing de LangGraph** (`AsyncSqliteSaver`, un único archivo `data/checkpoints.sqlite`) — reemplaza el event bus / historial en memoria de la primera entrega, que se perdía en cada reinicio del proceso.

## Tecnologías usadas
- Python 3.12
- **LangChain** + **LangGraph** (`StateGraph`, checkpointing, `create_agent` para el agente ReAct)
- **Groq** (`langchain-groq` / `ChatGroq`, modelo `llama-3.3-70b-versatile`; visión con `llama-4-scout` para fotos)
- Streamlit (interfaz web) + python-telegram (bot vía HTTP, long polling)
- Pydantic (estado del grafo y schemas de salida estructurada)
- Playwright (scraping de Facebook Marketplace)
- Airtable (persistencia de oportunidades), CallMeBot (alertas WhatsApp)
- fpdf2 (generación de contratos PDF)
- pytest + pytest-asyncio (mocks de LLM, sin gastar tokens)

## Instalación paso a paso
1. Instalar Python 3.12+ y crear un entorno virtual (recomendado).
2. Instalar dependencias:
   ```
   pip install -r requirements.txt
   playwright install chromium
   ```
3. Configurar variables de entorno:
   - Copiar `.env.example` a `.env`
   - Establecer `GROQ_API_KEY` (gratis en [console.groq.com](https://console.groq.com))
   - Establecer `APP_USER` y `APP_PASSWORD` (login de la app — **obligatorio**, sin esto no se puede entrar)
   - El resto (Airtable, Telegram, WhatsApp) es opcional; sin ellos la app funciona con menos features.

## Cómo ejecutar
```
streamlit run app.py
```
Abre en `http://localhost:8501`.

## Cómo ejecutar los tests
```
python -m pytest tests/ -v --tb=short
```
Todos los tests mockean la salida estructurada del LLM (`agent.llm` / `ChatGroq`) — no gastan tokens ni requieren una API key real.

## Estructura del proyecto
- `agents/`: los 4 agentes especializados (nodos de LangGraph) + el orquestador + el bot de Telegram (agente ReAct)
- `shared/`: `graph_state.py` (estado compartido Pydantic), `checkpointing.py` (checkpointer async compartido), `event_bus.py` (constantes de nombre de evento, solo para el log de `CarSaleState.events`)
- `tools/`: scraper de Facebook Marketplace, integración Airtable/WhatsApp/Telegram, generación de PDF
- `data/`: datos de ejemplo + `checkpoints.sqlite` (generado en runtime, no se versiona)
- `tests/`: suite pytest (mocks de LLM; incluye edge cases y el agente ReAct del bot)

## Métricas de evaluación
- Tasa de aprobación del pipeline (adquisición → publicación)
- Tiempo total de ejecución por corrida
- Cobertura de casos adversariales (`tests/test_edge_cases.py`): año faltante, score límite, negociación agotada (3 intentos), mensaje vacío, pipeline rechazado
- Robustez: reintentos ante respuestas inválidas del LLM (3 intentos por agente)
- Persistencia: conversaciones y estado de pipeline sobreviven a reinicios del proceso (verificado releyendo el checkpointer directamente en los tests)

## Autores
- Autor/a: (completar)

---
*Proyecto: Automatización Inteligente de Procesos — UPAO 2026-10*
