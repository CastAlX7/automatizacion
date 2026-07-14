from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF

_MAX_WORD_LEN = 40


def _safe_text(text: Any, max_word_len: int = _MAX_WORD_LEN) -> str:
    """Sanea texto (típicamente generado por el LLM) antes de escribirlo en el PDF.

    fpdf2 no puede envolver un token sin espacios más ancho que la página y
    lanza FPDFException("Not enough horizontal space to render a single
    character"); inserta espacios cada max_word_len caracteres en cualquier
    'palabra' larga para garantizar puntos de corte. También descarta
    caracteres fuera de Latin-1 (ej. emojis), que la fuente core Helvetica
    no soporta y hacen fallar la generación.
    """
    text = str(text) if text is not None else ""
    text = text.encode("latin-1", errors="ignore").decode("latin-1")
    words = text.split(" ")
    wrapped = [
        " ".join(re.findall(f".{{1,{max_word_len}}}", w))
        if len(w) > max_word_len
        else w
        for w in words
    ]
    return " ".join(wrapped)


def generate_contract_pdf(output_path: str | Path, contract: dict[str, Any]) -> str:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    def line(text: str, height: int = 8) -> None:
        # new_x/new_y explícitos: multi_cell por defecto deja el cursor en el
        # margen DERECHO (XPos.RIGHT), no lo devuelve al izquierdo — sin esto,
        # la siguiente línea calcula un ancho casi nulo y fpdf2 lanza
        # "Not enough horizontal space to render a single character".
        pdf.multi_cell(0, height, _safe_text(text), new_x="LMARGIN", new_y="NEXT")

    line("Contrato de Compraventa (Resumen)", height=10)
    pdf.ln(2)

    line(f"Fecha: {contract.get('fecha') or datetime.now().date().isoformat()}")
    line(f"Vendedor: {contract.get('vendedor', '')}")
    line(f"Comprador: {contract.get('comprador', '')}")
    line(f"Vehiculo: {contract.get('vehiculo', '')}")
    line(f"Precio: {contract.get('precio', '')}")
    line(f"Forma de pago: {contract.get('forma_pago', '')}")
    pdf.ln(4)

    clausulas = contract.get("clausulas") or []
    if isinstance(clausulas, list):
        line("Clausulas:")
        for c in clausulas:
            line(f"- {c}", height=6)

    pdf.output(str(out))
    return str(out)
