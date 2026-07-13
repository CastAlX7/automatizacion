from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests


class AirtableTool:
    BASE_URL = "https://api.airtable.com/v0"

    def __init__(self, pat=None, base_id=None, table_name=None):
        self.pat        = pat        or os.getenv("AIRTABLE_PAT", "")
        self.base_id    = base_id    or os.getenv("AIRTABLE_BASE_ID", "")
        self.table_name = table_name or os.getenv("AIRTABLE_TABLE_NAME", "Autos Aptos")

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.pat}", "Content-Type": "application/json"}

    @property
    def _table_url(self):
        return f"{self.BASE_URL}/{self.base_id}/{self.table_name}"

    def is_configured(self):
        return bool(self.pat and self.base_id)

    # ── Leer ──────────────────────────────────────────────────────────────────
    def get_approved_cars(self, max_records: int = 100) -> list[dict[str, Any]]:
        """Devuelve registros con '_id' incluido para poder actualizarlos."""
        if not self.is_configured():
            return []
        params = {
            "maxRecords": max_records,
            "sort[0][field]": "Fecha Análisis",
            "sort[0][direction]": "desc",
        }
        resp = requests.get(self._table_url, headers=self._headers, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        result = []
        for record in resp.json().get("records", []):
            fields = record["fields"]
            fields["_id"] = record["id"]
            result.append(fields)
        return result

    # ── Crear ─────────────────────────────────────────────────────────────────
    def save_car(self, car_data: dict[str, Any], inspection_data: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if not self.is_configured():
            return None

        price_num = None
        try:
            cleaned = str(car_data.get("price", "") or "").replace("$", "").replace("S/", "").replace(",", "").strip()
            if cleaned:
                price_num = float(cleaned)
        except (ValueError, AttributeError):
            pass

        obs = inspection_data or {}
        fields: dict[str, Any] = {
            "Título":               car_data.get("title", "Sin título"),
            "Precio Publicado":     price_num,
            "Precio Mercado":       car_data.get("precio_mercado"),
            "Precio Venta Sugerido": car_data.get("precio_venta"),
            "Estado":               car_data.get("condition", ""),
            "URL":                  car_data.get("url", ""),
            "Imagen":               car_data.get("image_url", ""),
            "Observaciones":        obs.get("observaciones") or obs.get("resultado_inspeccion") or "",
            "Razón":                obs.get("resultado_inspeccion") or "",
            "Fecha Análisis":       datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "Ciudad":               car_data.get("city", ""),
            "Teléfono":             car_data.get("whatsapp_number", ""),
            "Pipeline":             "Encontrado",
        }
        fields = {k: v for k, v in fields.items() if v is not None and v != ""}

        resp = requests.post(self._table_url, headers=self._headers, json={"fields": fields}, timeout=10)

        # Si Pipeline no existe aún, reintenta sin él
        if resp.status_code == 422 and "Pipeline" in resp.text:
            fields.pop("Pipeline", None)
            resp = requests.post(self._table_url, headers=self._headers, json={"fields": fields}, timeout=10)

        return resp.json() if resp.status_code in (200, 201) else None

    # ── Actualizar ────────────────────────────────────────────────────────────
    def update_car(self, record_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        """PATCH: actualiza campos específicos de un registro."""
        if not self.is_configured():
            return None
        clean = {k: v for k, v in fields.items() if not k.startswith("_") and v is not None}
        resp = requests.patch(
            f"{self._table_url}/{record_id}",
            headers=self._headers,
            json={"fields": clean},
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else None

    # ── Setup campos avanzados ────────────────────────────────────────────────
    def setup_advanced_fields(self) -> list[str]:
        """Crea Pipeline, campos financieros y Notas si no existen."""
        if not self.is_configured():
            return ["❌ Airtable no configurado"]

        schema_url = f"{self.BASE_URL}/meta/bases/{self.base_id}/tables/{self.table_name}/fields"
        new_fields = [
            {
                "name": "Pipeline",
                "type": "singleSelect",
                "options": {
                    "choices": [
                        {"name": "Encontrado",  "color": "blueLight2"},
                        {"name": "Contactando", "color": "yellowLight2"},
                        {"name": "Negociando",  "color": "orangeLight2"},
                        {"name": "Comprado",    "color": "purpleLight2"},
                        {"name": "Vendido",     "color": "greenLight2"},
                    ]
                },
            },
            {"name": "Precio Compra Real",  "type": "currency", "options": {"precision": 0, "symbol": "$"}},
            {"name": "Precio Venta Real",   "type": "currency", "options": {"precision": 0, "symbol": "$"}},
            {"name": "Ganancia Real",       "type": "currency", "options": {"precision": 0, "symbol": "$"}},
            {"name": "Teléfono",            "type": "phoneNumber"},
            {"name": "Notas",               "type": "multilineText"},
        ]

        results = []
        for f in new_fields:
            resp = requests.post(schema_url, headers=self._headers, json=f)
            if resp.status_code in (200, 201):
                results.append(f"✅ {f['name']} creado")
            else:
                err_type = resp.json().get("error", {}).get("type", "")
                if "DUPLICATE" in err_type.upper():
                    results.append(f"→ {f['name']} ya existe")
                else:
                    results.append(f"❌ {f['name']}: {err_type or resp.text[:60]}")
        return results
