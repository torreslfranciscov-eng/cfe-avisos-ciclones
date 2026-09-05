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


def send_telegram_text(chat_id, text, parse_mode="HTML"):
    """Envía un mensaje de texto formateado a Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }
        res = requests.post(url, json=payload, timeout=20)
        return res.json().get("ok", False)
    except Exception as e:
        logging.error(f"[TELEGRAM] Error enviando texto a {chat_id}: {e}")
        return False


def send_telegram_photo(chat_id, photo_bytes, caption="", filename="imagen.png"):
    """Envía una foto en bytes a Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        files = {"photo": (filename, photo_bytes, "image/png")}
        data = {
            "chat_id": chat_id,
            "caption": caption[:1024],
            "parse_mode": "HTML"
        }
        res = requests.post(url, data=data, files=files, timeout=30)
        return res.json().get("ok", False)
    except Exception as e:
        logging.error(f"[TELEGRAM] Error enviando foto a {chat_id}: {e}")
        return False


def handle_incoming_telegram_update(payload, server_base_url="https://cfe-avisos-ciclones.onrender.com"):
    """
    Procesa mensajes entrantes del bot de Telegram para Centinela SPH Grijalva.
    """
    from centinela_bot import get_azure_blob_bytes, get_azure_blob_text, ask_deepseek, get_cyclones_summary, AZURE_CONTAINER

    msg = payload.get("message") or payload.get("channel_post") or {}
    chat_id = msg.get("chat", {}).get("id")
    raw_text = (msg.get("text") or "").strip()
    user_name = msg.get("from", {}).get("first_name", "Ingeniero(a)")

    if not chat_id or not raw_text:
        return

    cmd = raw_text.strip().lower()

    menu_text = (
        f"🤖 <b>Centinela SPH Grijalva &mdash; Menú de Consultas</b>\n\n"
        f"Hola <b>{user_name}</b>, estoy a tu disposición con la información técnica del Sistema Hidroeléctrico del Río Grijalva:\n\n"
        f"1️⃣ <b>1</b> - Reporte de Unidades Generadoras\n"
        f"2️⃣ <b>2</b> - Power Monitoring en tiempo real\n"
        f"3️⃣ <b>3</b> - Gráfica de Potencia Actual\n"
        f"4️⃣ <b>4</b> - Condición de Embalses (Niveles/Cotas)\n"
        f"5️⃣ <b>5</b> - Aportaciones por Cuenca Propia\n"
        f"7️⃣ <b>7</b> - Reporte de Disponibilidad\n"
        f"8️⃣ <b>8</b> - 🌀 <b>Avisos de Ciclón Tropical (SMN / CONAGUA)</b>\n"
        f"11️⃣ <b>11</b> - Reporte de Lluvias 24h (6am a 6am)\n"
        f"12️⃣ <b>12</b> - Reporte de Lluvias Parcial\n"
        f"6️⃣ <b>6</b> o pregunta directa - 🤖 Consulta Técnica con IA\n\n"
        f"💡 <i>Escribe el número de la opción o envía tu pregunta.</i>"
    )

    # 1. Menú principal
    if cmd in ["/start", "/help", "/menu", "menu", "menú", "hola", "inicio", "0", "start"]:
        send_telegram_text(chat_id, menu_text)
        return

    # 2. Reporte de Unidades
    if cmd in ["1", "01", "/unidades", "unidades"]:
        img = get_azure_blob_bytes(AZURE_CONTAINER, "9c8a7f42-3d91-4e01-a3fa-0d2e5b1c6f7d.png")
        if img:
            send_telegram_photo(chat_id, img, "📊 <b>Reporte de Unidades Generadoras</b> — CFE SPH Grijalva")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener el Reporte de Unidades en este momento.")

    # 3. Power Monitoring
    elif cmd in ["2", "02", "/power", "power", "monitoring"]:
        img = get_azure_blob_bytes(AZURE_CONTAINER, "6f3b2c91-91df-41b6-9a1e-c3f0d0c8e24a.png")
        if img:
            send_telegram_photo(chat_id, img, "📊 <b>Power Monitoring en Tiempo Real</b> — CFE SPH Grijalva")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener la captura de Power Monitoring.")

    # 4. Gráfica de Potencia
    elif cmd in ["3", "03", "/potencia", "potencia"]:
        img = get_azure_blob_bytes(AZURE_CONTAINER, "b7e1f9c3-8a2d-4f5d-9c3a-7f1f6e7a2c01.png")
        if img:
            send_telegram_photo(chat_id, img, "📊 <b>Gráfica de Potencia Actual (MW)</b> — CFE SPH Grijalva")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener la gráfica de potencia.")

    # 5. Embalses
    elif cmd in ["4", "04", "/embalses", "embalses", "presas", "niveles"]:
        img = get_azure_blob_bytes(AZURE_CONTAINER, "e1a5f734-9c2e-4b3b-8d5a-6f7e1d2c9b8f.png")
        if img:
            send_telegram_photo(chat_id, img, "📊 <b>Condición de los Embalses</b> (Niveles, Cotas y Almacenamiento)")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener la condición de embalses.")

    # 6. Cuenca
    elif cmd in ["5", "05", "/cuenca", "cuenca", "aportaciones"]:
        img = get_azure_blob_bytes(AZURE_CONTAINER, "d42f3e19-b89c-4f02-90d4-3e7f4a6d2c01.png")
        if img:
            send_telegram_photo(chat_id, img, "📊 <b>Aportaciones por Cuenca Propia</b> (m³/s)")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener las aportaciones por cuenca.")

    # 7. Disponibilidad
    elif cmd in ["7", "07", "/disponibilidad", "disponibilidad"]:
        report_text = get_azure_blob_text("reporte-unidades", "telegram_report.txt")
        if report_text:
            send_telegram_text(chat_id, f"📋 <b>Reporte de Disponibilidad Hidroeléctrica:</b>\n\n<pre>{report_text}</pre>")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener el reporte de disponibilidad.")

    # 8. Reporte de Ciclones Tropicales
    elif cmd in ["8", "08", "/ciclon", "/ciclones", "ciclon", "ciclón", "ciclones", "huracan", "huracán", "tormenta"]:
        summary = get_cyclones_summary(server_base_url=server_base_url)
        send_telegram_text(chat_id, summary["text_tg"])
        if summary.get("has_active") and summary.get("cyclones"):
            for c in summary["cyclones"]:
                tray_path = c.get("img_tray_path")
                sat_path = c.get("img_sat_path")
                img_path = tray_path if (tray_path and os.path.exists(tray_path)) else (sat_path if (sat_path and os.path.exists(sat_path)) else None)
                if img_path:
                    try:
                        with open(img_path, "rb") as f_img:
                            send_telegram_photo(chat_id, f_img.read(), f"🗺️ <b>Cono de Trayectoria:</b> {c.get('sistema', 'Ciclón Tropical')}")
                    except Exception as e:
                        logging.debug(f"[TELEGRAM] Error al enviar foto de ciclón: {e}")

    # 9. Lluvias 24h
    elif cmd in ["11", "/lluvias24", "lluvias 24", "lluvia 24h"]:
        img = get_azure_blob_bytes(AZURE_CONTAINER, "reporte_lluvia_1_1_638848218556433423.png")
        if img:
            send_telegram_photo(chat_id, img, "🌧️ <b>Reporte de Lluvias 24 Horas (6am a 6am)</b> — CFE SPH Grijalva")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener el reporte de lluvias 24h.")

    # 10. Lluvias Parcial
    elif cmd in ["12", "/lluviasparcial", "lluvia parcial", "parcial"]:
        img = get_azure_blob_bytes(AZURE_CONTAINER, "reporte_lluvia_1_2_638848218556433423.png")
        if img:
            send_telegram_photo(chat_id, img, "🌧️ <b>Reporte de Lluvias Parcial (6am a hora actual)</b> — CFE SPH Grijalva")
        else:
            send_telegram_text(chat_id, "⚠️ No se pudo obtener el reporte de lluvias parcial.")

    # 11. Consulta de Inteligencia Artificial
    else:
        question = raw_text
        if cmd.startswith("6"):
            question = raw_text[1:].strip() or "¿Cómo se encuentra la operación del sistema Grijalva?"
        elif cmd.startswith("/ia "):
            question = raw_text[4:].strip()

        send_telegram_text(chat_id, "🤖 <i>Analizando tu consulta técnica con IA...</i>")
        ai_reply = ask_deepseek(question)
        send_telegram_text(chat_id, f"🤖 <b>Análisis Técnico Centinela:</b>\n\n{ai_reply}")
