"""
telegram_sender.py
Módulo para el envío automático de Avisos de Ciclones Tropicales CFE
a un Canal o Grupo de Telegram con el archivo Word (.docx) adjunto y fotos.
"""

import os
import logging
import requests


def send_cyclone_telegram(cyclone_data, docx_path):
    """
    Envía la notificación del ciclón y el documento Word adjunto a un canal/chat de Telegram.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        logging.info("[TELEGRAM] Envío por Telegram no configurado (falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID).")
        return False

    sistema = cyclone_data.get("sistema", "Ciclón Tropical")
    cuenca = cyclone_data.get("cuenca", "Océano")
    titular = cyclone_data.get("titular", "")
    situacion = cyclone_data.get("situacion_actual", "")
    cond = cyclone_data.get("condiciones", {})
    proximo = cyclone_data.get("proximo_aviso", "")
    filename = os.path.basename(docx_path)

    # Mensaje formateado en HTML para Telegram
    caption = (
        f"🌀 <b>AVISO DE CICLÓN TROPICAL &mdash; CFE</b>\n"
        f"🌊 <b>{cuenca}</b>\n\n"
        f"📍 <b>{sistema}</b>\n"
        f"<b>{titular}</b>\n\n"
        f"📊 <b>Condiciones Actuales:</b>\n"
        f"• <b>Hora:</b> {cond.get('hora_local_gmt', '--')}\n"
        f"• <b>Ubicación:</b> Lat {cond.get('latitud_norte', '--')}°N, Lon {cond.get('longitud_oeste', '--')}°O\n"
        f"• <b>Distancia:</b> {cond.get('distancia_costa', '--')}\n"
        f"• <b>Desplazamiento:</b> {cond.get('desplazamiento', '--')}\n"
        f"• <b>Vientos:</b> Sost: {cond.get('vientos_sostenidos', '--')} km/h | Rachas: {cond.get('vientos_rachas', '--')} km/h\n"
        f"• <b>Presión:</b> {cond.get('presion_minima', '--')} hPa\n"
        f"• <b>Lluvia:</b> {cond.get('pronostico_lluvia', 'Sin efectos')}\n\n"
        f"🔔 <i>{proximo}</i>\n\n"
        f"📎 <b>Reporte Word oficial adjunto abajo.</b>"
    )

    base_url = f"https://api.telegram.org/bot{bot_token}"

    try:
        # Enviar documento Word (.docx) con el caption
        if os.path.exists(docx_path):
            with open(docx_path, "rb") as f:
                doc_url = f"{base_url}/sendDocument"
                data = {
                    "chat_id": chat_id,
                    "caption": caption[:1024],  # Límite de caption en Telegram
                    "parse_mode": "HTML"
                }
                files = {"document": (filename, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
                res = requests.post(doc_url, data=data, files=files, timeout=30)
                res_json = res.json()
                if res_json.get("ok"):
                    logging.info(f"[TELEGRAM] Reporte enviado con éxito al canal/chat {chat_id}")
                    return True
                else:
                    logging.error(f"[TELEGRAM] Error de Telegram API: {res_json}")
                    return False
        else:
            # Si no hay doc, enviar solo texto
            msg_url = f"{base_url}/sendMessage"
            res = requests.post(msg_url, json={"chat_id": chat_id, "text": caption, "parse_mode": "HTML"}, timeout=20)
            return res.json().get("ok", False)

    except Exception as e:
        logging.error(f"[TELEGRAM] Excepción al enviar mensaje a Telegram: {e}")
        return False
