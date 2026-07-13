import requests
import os

class TelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        
    def send_deal_alert(self, car_data: dict, analysis: dict) -> bool:
        if not self.bot_token or not self.chat_id:
            print("Telegram credentials not configured.")
            return False
            
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        
        # Sanitizar el mensaje para evitar errores de Markdown
        def escape_markdown(text):
            if not text: return ""
            # Escapamos caracteres que suelen romper el parse_mode Markdown de Telegram
            return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

        message = f"🚗 *NUEVA BUENA OFERTA ENCONTRADA*\n\n"
        message += f"📌 *Título:* {escape_markdown(car_data.get('title'))}\n"
        message += f"💰 *Precio Publicado:* {escape_markdown(car_data.get('price'))}\n"
        message += f"📊 *Precio Mercado (estimado):* ${analysis.get('precio_mercado_sugerido', 'N/A')}\n"
        message += f"✅ *Por qué es buena oferta:* {escape_markdown(analysis.get('observaciones', 'N/A'))}\n\n"
        message += f"🔗 *Enlace:* {car_data.get('url')}"
        
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        try:
            import time
            for attempt in range(3):
                try:
                    response = requests.post(url, json=payload, timeout=15)
                    if response.status_code != 200:
                        import logging
                        logging.error(f"Telegram API Error: {response.text}")
                    response.raise_for_status()
                    return True
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, ConnectionResetError) as e:
                    import logging
                    logging.warning(f"Intento {attempt + 1} falló al enviar a Telegram: {e}")
                    if attempt == 2:
                        raise
                    time.sleep(2)
        except Exception as e:
            import logging
            logging.error(f"Failed to send Telegram message: {e}")
            return False
