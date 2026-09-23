"""
whatsapp_sender.py
Módulo para el envío automático de Avisos de Ciclones Tropicales CFE
a números o grupos de WhatsApp a través del servidor Open-WA.
"""

import os
import logging
import requests

OPENWA_SERVER_URL = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085")
WHATSAPP_TO = os.getenv("WHATSAPP_TO", "")  # Número ej: "5219611234567" o Group ID "120363023456789@g.us"


def send_cyclone_whatsapp(cyclone_data, docx_path):
    """
    Envía las imágenes de satélite/cono y el archivo Word al chat o grupo de WhatsApp via Open-WA.
    """
    openwa_url = os.getenv("OPENWA_SERVER_URL", OPENWA_SERVER_URL).rstrip("/")
    target_to = os.getenv("WHATSAPP_TO", WHATSAPP_TO).strip()

    if not target_to:
        logging.info("[WHATSAPP] Envío por WhatsApp no configurado (falta variable WHATSAPP_TO).")
        return False

    sistema = cyclone_data.get("sistema", "Ciclón Tropical")
    cuenca = cyclone_data.get("cuenca", "Océano")
    titular = cyclone_data.get("titular", "")
    cond = cyclone_data.get("condiciones", {})
    proximo = cyclone_data.get("proximo_aviso", "")
    img_sat_path = cyclone_data.get("img_sat_path")
    img_tray_path = cyclone_data.get("img_tray_path")

    caption = (
        f"🌀 *AVISO DE CICLÓN TROPICAL — CFE*\n"
        f"🌊 *{cuenca}*\n\n"
        f"📍 *{sistema}*\n"
        f"*{titular}*\n\n"
        f"📊 *Condiciones Actuales:*\n"
        f"• *Hora:* {cond.get('hora_local_gmt', '--')}\n"
        f"• *Ubicación:* Lat {cond.get('latitud_norte', '--')}°N, Lon {cond.get('longitud_oeste', '--')}°O\n"
        f"• *Distancia:* {cond.get('distancia_costa', '--')}\n"
        f"• *Desplazamiento:* {cond.get('desplazamiento', '--')}\n"
        f"• *Vientos:* Sost: {cond.get('vientos_sostenidos', '--')} km/h | Rachas: {cond.get('vientos_rachas', '--')} km/h\n"
        f"• *Presión:* {cond.get('presion_minima', '--')} hPa\n"
        f"• *Lluvia:* {cond.get('pronostico_lluvia', 'Sin efectos')}\n\n"
        f"🔔 _{proximo}_\n\n"
        f"📎 *Reporte oficial Word (.docx) adjunto abajo*"
    )

    # Si hay múltiples destinatarios separados por comas
    recipients = [r.strip() for r in target_to.split(",") if r.strip()]
    success_count = 0

    for recipient in recipients:
        try:
            payload = {
                "to": recipient,
                "caption": caption,
                "satPath": os.path.abspath(img_sat_path) if img_sat_path and os.path.exists(img_sat_path) else None,
                "trayPath": os.path.abspath(img_tray_path) if img_tray_path and os.path.exists(img_tray_path) else None,
                "docxPath": os.path.abspath(docx_path) if docx_path and os.path.exists(docx_path) else None
            }

            res = requests.post(f"{openwa_url}/sendCycloneNotice", json=payload, timeout=40)
            if res.status_code == 200 and res.json().get("ok"):
                logging.info(f"[WHATSAPP] Aviso enviado exitosamente a {recipient}")
                success_count += 1
            else:
                logging.error(f"[WHATSAPP] Error al enviar a {recipient}: {res.text}")
        except Exception as e:
            logging.error(f"[WHATSAPP] Excepción al conectar con servidor Open-WA: {e}")

    return success_count > 0
