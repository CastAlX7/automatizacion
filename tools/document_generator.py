from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF


def generate_contract_pdf(output_path: str | Path, contract: dict[str, Any]) -> str:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    pdf.cell(0, 10, "Contrato de Compraventa (Resumen)", ln=1)
    pdf.ln(2)

    pdf.cell(0, 8, f"Fecha: {contract.get('fecha') or datetime.now().date().isoformat()}", ln=1)
    pdf.cell(0, 8, f"Vendedor: {contract.get('vendedor', '')}", ln=1)
    pdf.cell(0, 8, f"Comprador: {contract.get('comprador', '')}", ln=1)
    pdf.cell(0, 8, f"Vehiculo: {contract.get('vehiculo', '')}", ln=1)
    pdf.cell(0, 8, f"Precio: {contract.get('precio', '')}", ln=1)
    pdf.cell(0, 8, f"Forma de pago: {contract.get('forma_pago', '')}", ln=1)
    pdf.ln(4)

    clausulas = contract.get("clausulas") or []
    if isinstance(clausulas, list):
        pdf.cell(0, 8, "Clausulas:", ln=1)
        for c in clausulas:
            pdf.multi_cell(0, 6, f"- {c}")

    pdf.output(str(out))
    return str(out)
