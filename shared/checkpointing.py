from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "checkpoints.sqlite"


@asynccontextmanager
async def checkpointer_scope() -> AsyncIterator[AsyncSqliteSaver]:
    """Abre el checkpointer compartido (un único archivo .sqlite) para la duración
    de UNA sola unidad de trabajo async, y cierra la conexión al salir.

    No se cachea como singleton de proceso a propósito: Streamlit crea un event
    loop nuevo en cada interacción y el bot de Telegram uno por hilo/mensaje —
    una conexión aiosqlite abierta en un loop ya cerrado no es segura de reusar.
    Reabrir el archivo es barato (SQLite, WAL) y es el patrón que la propia
    librería documenta (`AsyncSqliteSaver.from_conn_string` como context manager).
    """
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(_DB_PATH))
    try:
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        yield saver
    finally:
        await conn.close()
