"""
telegram_sender.py
Módulo para el envío automático de Avisos de Ciclones Tropicales CFE
a un Canal o Grupo de Telegram con imágenes (satélite y cono de trayectoria)
y el archivo Word (.docx) adjunto.
"""

import re
import os
import json
import logging
import requests


def safe_html_truncate(text, max_len=1024):
    """
    Trunca texto con formato HTML de manera segura sin romper etiquetas
    ni entidades y cerrando automáticamente cualquier etiqueta HTML abierta.
    """
    if not text or len(text) <= max_len:
        return text or ""

    cutoff = max_len - 25
    idx = text.rfind("\n", 0, cutoff)
    if idx == -1 or idx < cutoff // 2:
        idx = text.rfind(" ", 0, cutoff)
    if idx == -1:
        idx = cutoff
    truncated = text[:idx].strip()

    # Detectar etiquetas HTML abiertas y cerrarlas en orden inverso
    open_tags = []
    tag_regex = re.compile(r'<(/)?([a-zA-Z0-9]+)[^>]*>')
    for match in tag_regex.finditer(truncated):
        is_closing, tag_name = match.group(1), match.group(2).lower()
        if is_closing:
            if open_tags and open_tags[-1] == tag_name:
                open_tags.pop()
        else:
            open_tags.append(tag_name)

    for tag in reversed(open_tags):
        truncated += f"</{tag}>"

    return truncated


def send_cyclone_telegram(cyclone_data, docx_path):
    """
    Envía la notificación del ciclón con sus imágenes satelitales y de trayectoria
    seguido del documento Word adjunto a uno o varios canales/chats de Telegram.
    Soporta múltiples IDs o canales separados por coma en TELEGRAM_CHAT_ID.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    raw_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not raw_chat_id:
        logging.info("[TELEGRAM] Envío por Telegram no configurado (falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID).")
        return False

    chat_ids = [c.strip() for c in raw_chat_id.split(",") if c.strip()]
    if not chat_ids:
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

    sat_bytes = None
    if img_sat_path and os.path.exists(img_sat_path):
        try:
            with open(img_sat_path, "rb") as f:
                sat_bytes = f.read()
        except Exception as e:
            logging.error(f"[TELEGRAM] Error leyendo satélite {img_sat_path}: {e}")

    # Descarga directa de respaldo si el archivo satelital no está en disco
    img_sat_url = cyclone_data.get("img_sat_url")
    if not sat_bytes and img_sat_url:
        try:
            logging.info(f"[TELEGRAM] Descargando imagen satelital de respaldo desde {img_sat_url}...")
            r_sat = requests.get(img_sat_url, timeout=25, verify=False)
            if r_sat.status_code == 200 and len(r_sat.content) > 1000:
                sat_bytes = r_sat.content
        except Exception as e:
            logging.error(f"[TELEGRAM] Error descargando imagen satelital de respaldo: {e}")

    tray_bytes = None
    if img_tray_path and os.path.exists(img_tray_path):
        try:
            with open(img_tray_path, "rb") as f:
                tray_bytes = f.read()
        except Exception as e:
            logging.error(f"[TELEGRAM] Error leyendo trayectoria {img_tray_path}: {e}")

    # Descarga directa de respaldo si el cono de trayectoria no está en disco
    img_tray_url = cyclone_data.get("img_tray_url")
    if not tray_bytes and img_tray_url:
        try:
            logging.info(f"[TELEGRAM] Descargando imagen trayectoria de respaldo desde {img_tray_url}...")
            r_tray = requests.get(img_tray_url, timeout=25, verify=False)
            if r_tray.status_code == 200 and len(r_tray.content) > 1000:
                tray_bytes = r_tray.content
        except Exception as e:
            logging.error(f"[TELEGRAM] Error descargando imagen trayectoria de respaldo: {e}")

    doc_bytes = None
    if os.path.exists(docx_path):
        try:
            with open(docx_path, "rb") as f:
                doc_bytes = f.read()
        except Exception as e:
            logging.error(f"[TELEGRAM] Error leyendo docx {docx_path}: {e}")

    photo_caption = safe_html_truncate(caption, max_len=1024)
    any_success = False

    for target_chat in chat_ids:
        try:
            photos_sent = False

            # Caso 1: Si hay ambas fotos, enviar Álbum (sendMediaGroup)
            if sat_bytes and tray_bytes:
                media = [
                    {
                        "type": "photo",
                        "media": "attach://sat",
                        "caption": photo_caption,
                        "parse_mode": "HTML"
                    },
                    {
                        "type": "photo",
                        "media": "attach://tray"
                    }
                ]
                files = {
                    "sat": ("sat.jpg", sat_bytes, "image/jpeg"),
                    "tray": ("tray.jpg", tray_bytes, "image/jpeg")
                }
                try:
                    media_url = f"{base_url}/sendMediaGroup"
                    res_media = requests.post(
                        media_url,
                        data={"chat_id": target_chat, "media": json.dumps(media)},
                        files=files,
                        timeout=35
                    )
                    res_json = res_media.json()
                    if res_json.get("ok"):
                        photos_sent = True
                        logging.info(f"[TELEGRAM] Álbum de fotos de {sistema} enviado a {target_chat}")
                    else:
                        logging.warning(f"[TELEGRAM] sendMediaGroup falló ({res_json.get('description')}). Reintentando fotos individuales...")
                except Exception as e:
                    logging.warning(f"[TELEGRAM] Excepción en sendMediaGroup: {e}")

            # Caso 2: Si sendMediaGroup falló o solo hay 1 foto disponible
            if not photos_sent and (sat_bytes or tray_bytes):
                primary_photo = tray_bytes or sat_bytes
                secondary_photo = sat_bytes if tray_bytes else None

                try:
                    photo_url = f"{base_url}/sendPhoto"
                    files_p = {"photo": ("foto_principal.jpg", primary_photo, "image/jpeg")}
                    data_p = {"chat_id": target_chat, "caption": photo_caption, "parse_mode": "HTML"}
                    res_p = requests.post(photo_url, data=data_p, files=files_p, timeout=35).json()
                    if res_p.get("ok"):
                        photos_sent = True
                        logging.info(f"[TELEGRAM] Foto principal enviada a {target_chat}")
                    else:
                        logging.warning(f"[TELEGRAM] Error en sendPhoto: {res_p.get('description')}")

                    if secondary_photo and photos_sent:
                        files_s = {"photo": ("foto_secundaria.jpg", secondary_photo, "image/jpeg")}
                        data_s = {"chat_id": target_chat, "caption": "🛰️ <b>Imagen Satelital Oficial</b>", "parse_mode": "HTML"}
                        requests.post(photo_url, data=data_s, files=files_s, timeout=30)
                except Exception as e:
                    logging.error(f"[TELEGRAM] Excepción al enviar fotos individuales: {e}")

            # Caso 3: Si no se pudo enviar ninguna foto, enviar texto con sendMessage
            if not photos_sent:
                msg_url = f"{base_url}/sendMessage"
                res_msg = requests.post(msg_url, json={"chat_id": target_chat, "text": caption, "parse_mode": "HTML"}, timeout=20)
                logging.info(f"[TELEGRAM] Mensaje de texto enviado a {target_chat}: {res_msg.json().get('ok')}")

            # 2. Enviar el Documento Word (.docx) oficial adjunto
            if doc_bytes:
                doc_url = f"{base_url}/sendDocument"
                doc_data = {
                    "chat_id": target_chat,
                    "caption": f"📄 Reporte Oficial CFE: <b>{sistema}</b>",
                    "parse_mode": "HTML"
                }
                doc_files = {"document": (filename, doc_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
                res_doc = requests.post(doc_url, data=doc_data, files=doc_files, timeout=35)
                res_doc_json = res_doc.json()
                logging.info(f"[TELEGRAM] Word adjunto enviado a {target_chat}: {res_doc_json.get('ok')} ({res_doc_json.get('description', '')})")

            any_success = True
        except Exception as e:
            logging.error(f"[TELEGRAM] Error al enviar a destinatario {target_chat}: {e}")

    return any_success


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


def answer_telegram_callback_query(callback_query_id, text=None, show_alert=False):
    """Responde a un callback_query de Telegram para remover el estado de carga del botón."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token or not callback_query_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
        payload = {"callback_query_id": str(callback_query_id)}
        if text:
            payload["text"] = text
            payload["show_alert"] = show_alert
        requests.post(url, json=payload, timeout=10)
        return True
    except Exception as e:
        logging.debug(f"[TELEGRAM] Error respondiendo callback: {e}")
        return False


def handle_incoming_telegram_update(payload, server_base_url="https://cfe-avisos-ciclones.onrender.com"):
    """
    Procesa mensajes entrantes del bot de Telegram para Centinela SPH Grijalva (@wgrijalva_bot).
    Mantiene aislamiento absoluto: si recibe botones o comandos de Bitso, los redirige a @mibitso_bot.
    """
    from centinela_bot import get_azure_blob_bytes, get_azure_blob_text, ask_deepseek, get_cyclones_summary, AZURE_CONTAINER

    # 0. Interceptar toques de botones (callback_query)
    cb = payload.get("callback_query")
    if cb:
        cb_id = cb.get("id")
        cb_data = (cb.get("data") or "").strip()
        cb_msg = cb.get("message") or {}
        chat_id = cb_msg.get("chat", {}).get("id")

        # Si el botón proviene del bot de Bitso (ej. cmd_balance, cmd_positions, etc.)
        if any(k in cb_data for k in ["cmd_", "balance", "pos", "trade", "buy", "sell", "bitso", "chart", "stop", "resume", "stars"]):
            answer_telegram_callback_query(
                cb_id,
                text="⚠️ @wgrijalva_bot es exclusivo de Centinela CFE. Para Bitso usa @mibitso_bot",
                show_alert=True
            )
            if chat_id:
                send_telegram_text(
                    chat_id,
                    "⚠️ <b>Bot Exclusivo Centinela SPH Grijalva</b> (@wgrijalva_bot)\n\n"
                    "Este botón pertenece al sistema de trading autónomo de Bitso.\n\n"
                    "Para operar y consultar balances/posiciones en tiempo real, interactúa directamente con: 👉 <b>@mibitso_bot</b>."
                )
            return
        else:
            answer_telegram_callback_query(cb_id)
            return

    msg = payload.get("message") or payload.get("channel_post") or {}
    chat_id = msg.get("chat", {}).get("id")
    raw_text = (msg.get("text") or "").strip()
    user_name = msg.get("from", {}).get("first_name", "Ingeniero(a)")

    if not chat_id or not raw_text:
        return

    cmd = raw_text.strip().lower()

    # Redirigir comandos o consultas de trading/Bitso directamente a @mibitso_bot
    bitso_triggers = [
        "/balance", "balance", "/posiciones", "posiciones", "/pos", "pos",
        "/bitso", "bitso", "/trading", "trading", "/crypto", "crypto",
        "/capital", "capital", "/profit", "profit", "/pnl", "pnl",
        "/btc", "btc", "/eth", "eth", "/xrp", "xrp", "/sol", "sol",
        "/ordenes", "ordenes", "/orders", "orders", "/stop", "/resume"
    ]
    if cmd in bitso_triggers or any(cmd.startswith(x + " ") for x in ["/balance", "/posiciones", "/bitso", "/trading", "/capital", "/crypto"]):
        send_telegram_text(
            chat_id,
            "ℹ️ <b>Separación de Bots Activa</b>\n\n"
            "Este chat (<b>@wgrijalva_bot</b>) está reservado <u>únicamente</u> para el <b>Centinela del Sistema Hidroeléctrico del Grijalva</b> (Presas, Unidades Generadoras, Lluvias y Ciclones CFE).\n\n"
            "📈 Tu bot de Bitso con balance, compras/ventas y terminal autónoma está en:\n"
            "👉 <b>@mibitso_bot</b>\n\n"
            "<i>(Abre @mibitso_bot y escribe /start para ver tu panel de trading).</i>"
        )
        return

    menu_text = (
        f"🤖 <b>Centinela SPH Grijalva — Menú de Consultas</b>\n\n"
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
        f"💡 <i>Escribe el número de la opción o envía tu pregunta técnica.</i>"
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
                img_bytes = None
                if img_path:
                    try:
                        with open(img_path, "rb") as f_img:
                            img_bytes = f_img.read()
                    except Exception as e:
                        logging.debug(f"[TELEGRAM] Error al leer imagen de disco: {e}")

                if not img_bytes:
                    dl_url = c.get("img_tray_url") or c.get("img_sat_url")
                    if dl_url:
                        try:
                            r_dl = requests.get(dl_url, timeout=20, verify=False)
                            if r_dl.status_code == 200 and len(r_dl.content) > 1000:
                                img_bytes = r_dl.content
                        except Exception as e:
                            logging.debug(f"[TELEGRAM] Error al descargar imagen de ciclón para opción 8: {e}")

                if img_bytes:
                    try:
                        send_telegram_photo(chat_id, img_bytes, f"🗺️ <b>Cono de Trayectoria / Satélite:</b> {c.get('sistema', 'Ciclón Tropical')}")
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
