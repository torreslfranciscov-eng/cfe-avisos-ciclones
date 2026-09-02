"""
telegram_sender.py
Módulo para el envío automático de Avisos de Ciclones Tropicales CFE
a un Canal o Grupo de Telegram con imágenes (satélite y cono de trayectoria)
y el archivo Word (.docx) adjunto.
"""

import os
import json
import logging
import requests


def send_cyclone_telegram(cyclone_data, docx_path):
    """
    Envía la notificación del ciclón con sus imágenes satelitales y de trayectoria
    seguido del documento Word adjunto a un canal/chat de Telegram.
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
    img_sat_path = cyclone_data.get("img_sat_path")
    img_tray_path = cyclone_data.get("img_tray_path")
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
        f"📎 <b>Reporte Word oficial adjunto abajo ⬇️</b>"
    )

    base_url = f"https://api.telegram.org/bot{bot_token}"

    try:
        # 1. Enviar Álbum de Fotos (Satélite + Trayectoria) con el texto del aviso
        media = []
        files = {}
        
        has_sat = img_sat_path and os.path.exists(img_sat_path)
        has_tray = img_tray_path and os.path.exists(img_tray_path)

        if has_sat:
            files["sat"] = open(img_sat_path, "rb")
            media.append({
                "type": "photo",
                "media": "attach://sat",
                "caption": caption[:1024],
                "parse_mode": "HTML"
            })

        if has_tray:
            files["tray"] = open(img_tray_path, "rb")
            media.append({
                "type": "photo",
                "media": "attach://tray"
            })

        if media:
            media_url = f"{base_url}/sendMediaGroup"
            res_media = requests.post(
                media_url,
                data={"chat_id": chat_id, "media": json.dumps(media)},
                files=files,
                timeout=30
            )
            # Cerrar los archivos de imágenes
            for f in files.values():
                f.close()
                
            logging.info(f"[TELEGRAM] Fotos enviadas: {res_media.json().get('ok')}")
        else:
            # Si no hay fotos, enviar texto simple
            msg_url = f"{base_url}/sendMessage"
            requests.post(msg_url, json={"chat_id": chat_id, "text": caption, "parse_mode": "HTML"}, timeout=20)

        # 2. Enviar el Documento Word (.docx) oficial adjunto
        if os.path.exists(docx_path):
            with open(docx_path, "rb") as f_doc:
                doc_url = f"{base_url}/sendDocument"
                doc_data = {
                    "chat_id": chat_id,
                    "caption": f"📄 Reporte Oficial CFE: <b>{sistema}</b>",
                    "parse_mode": "HTML"
                }
                doc_files = {"document": (filename, f_doc, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
                res_doc = requests.post(doc_url, data=doc_data, files=doc_files, timeout=30)
                logging.info(f"[TELEGRAM] Word adjunto enviado: {res_doc.json().get('ok')}")

        return True

    except Exception as e:
        logging.error(f"[TELEGRAM] Error al enviar a Telegram: {e}")
        return False
