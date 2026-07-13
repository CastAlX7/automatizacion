from __future__ import annotations

import os
import urllib.parse
import requests


class WhatsAppTool:
    """
    Notificaciones via CallMeBot (gratis, sin Twilio).
    Setup: guarda +34 644 44 23 23 como contacto y envíale
    "I allow callmebot to send me messages" por WhatsApp.
    Recibirás tu API key de respuesta.
    """

    API_URL = "https://api.callmebot.com/whatsapp.php"

    def __init__(self, phone: str | None = None, api_key: str | None = None):
        self.phone   = phone   or os.getenv("WHATSAPP_PHONE", "")
        self.api_key = api_key or os.getenv("WHATSAPP_APIKEY", "")

    def is_configured(self) -> bool:
        return bool(self.phone and self.api_key)

    def send(self, message: str) -> bool:
        if not self.is_configured():
            return False
        params = {
            "phone":  self.phone,
            "text":   message,
            "apikey": self.api_key,
        }
        try:
            resp = requests.get(self.API_URL, params=params, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def send_deal_alert(self, car_data: dict, analysis_data: dict) -> bool:
        title  = car_data.get("title", "Auto sin título")
        price  = car_data.get("price", "?")
        market = analysis_data.get("precio_mercado")
        venta  = analysis_data.get("precio_venta")
        city   = car_data.get("city", "")
        url    = car_data.get("url", "")

        ganancia = None
        try:
            pub_num = float(str(price).replace("$", "").replace("S/", "").replace(",", "").strip())
            if market and market > pub_num:
                ganancia = market - pub_num
        except (ValueError, AttributeError):
            pass

        lines = [
            "🚗 *ANYMOTOR — Nueva oportunidad*",
            "",
            f"*{title}*",
            f"💰 Precio publicado: {price}",
        ]
        if market:
            lines.append(f"📊 Valor de mercado: ${market:,.0f}")
        if venta:
            lines.append(f"🎯 Precio máximo de compra: ${venta:,.0f}")
        if ganancia:
            lines.append(f"✨ Ganancia estimada: ${ganancia:,.0f}")
        if city:
            lines.append(f"📍 {city}")
        if url:
            lines += ["", url]

        return self.send("\n".join(lines))
