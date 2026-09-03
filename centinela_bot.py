"""
centinela_bot.py
Módulo Centinela CFE SPH Grijalva para responder automáticamente a usuarios
en WhatsApp sustituyendo UltraMsg con nuestro propio servidor WhatsApp Baileys.
"""

import os
import time
import hmac
import base64
import hashlib
import datetime
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

AZURE_ACCOUNT = os.getenv("AZURE_STORAGE_ACCOUNT", "reportegeneracion")
AZURE_KEY = os.getenv("AZURE_STORAGE_KEY", "")
AZURE_CONTAINER = os.getenv("AZURE_STORAGE_CONTAINER", "unidades")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# Estado en memoria
mensajes_procesados = {}
usuarios_en_modo_ia = {}


def get_azure_blob_bytes(container, blob_name):
    """Descarga un blob directamente desde Azure Blob Storage usando la API REST con SharedKey."""
    key = AZURE_KEY or os.getenv("AZURE_KEY", "")
    if not key:
        logging.error("[AZURE] AZURE_STORAGE_KEY no configurada.")
        return None
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        version = '2020-10-02'

        canonicalized_headers = f'x-ms-date:{now_utc}\nx-ms-version:{version}'
        canonicalized_resource = f'/{AZURE_ACCOUNT}/{container}/{blob_name}'
        string_to_sign = f'GET\n\n\n\n\n\n\n\n\n\n\n\n{canonicalized_headers}\n{canonicalized_resource}'

        decoded_key = base64.b64decode(key)
        signature = base64.b64encode(hmac.new(decoded_key, string_to_sign.encode('utf-8'), hashlib.sha256).digest()).decode('utf-8')

        headers = {
            'x-ms-date': now_utc,
            'x-ms-version': version,
            'Authorization': f'SharedKey {AZURE_ACCOUNT}:{signature}'
        }

        url = f'https://{AZURE_ACCOUNT}.blob.core.windows.net/{container}/{blob_name}'
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 200:
            return r.content
        else:
            logging.error(f"[AZURE] Error {r.status_code} al descargar {blob_name}: {r.text}")
            return None
    except Exception as e:
        logging.error(f"[AZURE] Excepción al descargar {blob_name}: {e}")
        return None


def get_azure_blob_text(container, blob_name):
    """Descarga un archivo de texto de Azure Blob."""
    data = get_azure_blob_bytes(container, blob_name)
    if data:
        try:
            return data.decode('utf-8')
        except Exception:
            return data.decode('latin-1', errors='ignore')
    return ""


def send_wa_text(to_jid, text):
    """Envía un mensaje de texto a través del servidor local Baileys."""
    openwa_url = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085").rstrip("/")
    try:
        requests.post(f"{openwa_url}/sendText", json={"to": to_jid, "text": text}, timeout=15)
    except Exception as e:
        logging.error(f"[CENTINELA] Error al enviar texto a {to_jid}: {e}")


def send_wa_image_base64(to_jid, image_bytes, caption, filename="imagen.png"):
    """Envía una imagen a través del servidor local Baileys."""
    openwa_url = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085").rstrip("/")
    try:
        b64 = base64.b64encode(image_bytes).decode('utf-8')
        requests.post(f"{openwa_url}/sendBase64Image", json={
            "to": to_jid,
            "base64": b64,
            "caption": caption,
            "filename": filename
        }, timeout=25)
    except Exception as e:
        logging.error(f"[CENTINELA] Error al enviar imagen a {to_jid}: {e}")


def get_menu_text():
    return (
        "✅ *Hola, soy el Centinela, tu asistente desarrollado por SPH Grijalva.*\n"
        "Estoy aquí para brindarte la siguiente información:\n\n"
        "Por favor, selecciona una opción enviando el número correspondiente:\n\n"
        "1️⃣ Reporte de Unidades\n"
        "2️⃣ Power Monitoring\n"
        "3️⃣ Gráfica de Potencia Actual\n"
        "4️⃣ Condición de los Embalses\n"
        "5️⃣ Aportaciones por Cuenca Propia de Embalse\n"
        "7️⃣ Reporte de Disponibilidad\n"
        "11️⃣ Reporte de lluvias 24h (6am a 6am)\n"
        "12️⃣ Reporte de lluvias parcial\n"
        "6️⃣ 🤖 Consultar con IA\n\n"
        "💡 *Tip:* En modo IA, escribe 'volver' para regresar aquí."
    )


def is_volver(msg):
    if not msg:
        return False
    return msg.strip().lower() in ["volver", "menu", "menú", "inicio", "salir", "0"]


def process_with_ai(from_jid, question):
    """Procesa una pregunta con DeepSeek / OpenAI."""
    api_key = DEEPSEEK_API_KEY or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        send_wa_text(from_jid, "⚠️ El motor de IA no tiene clave configurada en las variables de entorno.")
        return
        
    send_wa_text(from_jid, "🤖 *Analizando tu consulta técnica con IA...*\nEsto puede tardar unos segundos.")
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        prompt = f"""
Actúa como un ingeniero hidroeléctrico experto del sistema de presas del río Grijalva (Angostura, Chicoasén, Malpaso, Peñitas) de CFE.
Pregunta del usuario: {question}

Responde de forma técnica, clara y concisa (máximo 3 párrafos).
Usa unidades correctas: msnm, m³/s, hm³, MW.
Finaliza firmando como: *Centinela SPH Grijalva*.
"""
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Eres el Centinela SPH Grijalva, experto en operación hidroeléctrica de CFE."},
                {"role": "user", "content": prompt}
            ]
        }
        res = requests.post("https://api.deepseek.com/v1/chat/completions", json=payload, headers=headers, timeout=30)
        if res.status_code == 200:
            ai_text = res.json()["choices"][0]["message"]["content"]
            reply = f"🤖 *Análisis Técnico Centinela:*\n\n{ai_text}\n\n💡 _Escribe 'volver' para regresar al menú principal._"
            send_wa_text(from_jid, reply)
        else:
            send_wa_text(from_jid, "⚠️ No fue posible conectar con el motor de IA en este momento.\nEscribe 'volver' para regresar al menú.")
    except Exception as e:
        send_wa_text(from_jid, "❌ Ocurrió un error al procesar tu consulta con IA. Escribe 'volver' para regresar al menú.")


def handle_incoming_whatsapp_message(payload):
    """
    Controlador principal equivalente al WhatsappController de C# para procesar
    todos los comandos del menú de Centinela.
    """
    from_jid = payload.get("from")
    text = (payload.get("text") or "").strip()
    msg_id = payload.get("messageId", "")

    if not from_jid or not text:
        return

    # 1. Anti-duplicados (10 segundos)
    now = time.time()
    if msg_id and msg_id in mensajes_procesados and (now - mensajes_procesados[msg_id]) < 10:
        return
    if msg_id:
        mensajes_procesados[msg_id] = now

    # 2. Verificar comando volver
    if is_volver(text):
        usuarios_en_modo_ia[from_jid] = False
        send_wa_text(from_jid, "✅ *Has vuelto al menú principal*\n\n" + get_menu_text())
        return

    # 3. Verificar si el usuario está en modo IA
    if usuarios_en_modo_ia.get(from_jid, False):
        process_with_ai(from_jid, text)
        return

    # 4. Menú de opciones (1, 2, 3, 4, 5, 6, 7, 11, 12)
    if text == "1":
        img = get_azure_blob_bytes(AZURE_CONTAINER, "9c8a7f42-3d91-4e01-a3fa-0d2e5b1c6f7d.png")
        if img:
            send_wa_image_base64(from_jid, img, "📊 *Reporte de Unidades actualizado.*", "reporte_unidades.png")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener el Reporte de Unidades en este momento.")

    elif text == "2":
        img = get_azure_blob_bytes(AZURE_CONTAINER, "6f3b2c91-91df-41b6-9a1e-c3f0d0c8e24a.png")
        if img:
            send_wa_image_base64(from_jid, img, "📊 *Captura del Power Monitoring.*", "power_monitoring.png")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener la captura de Power Monitoring.")

    elif text == "3":
        img = get_azure_blob_bytes(AZURE_CONTAINER, "b7e1f9c3-8a2d-4f5d-9c3a-7f1f6e7a2c01.png")
        if img:
            send_wa_image_base64(from_jid, img, "📊 *Gráfica de potencia.*", "grafica_potencia.png")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener la gráfica de potencia.")

    elif text == "4":
        img = get_azure_blob_bytes(AZURE_CONTAINER, "e1a5f734-9c2e-4b3b-8d5a-6f7e1d2c9b8f.png")
        if img:
            send_wa_image_base64(from_jid, img, "📊 *Condición de embalses.*", "condicion_embalses.png")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener la condición de embalses.")

    elif text == "5":
        img = get_azure_blob_bytes(AZURE_CONTAINER, "d42f3e19-b89c-4f02-90d4-3e7f4a6d2c01.png")
        if img:
            send_wa_image_base64(from_jid, img, "📊 *Aportaciones por cuenca propia.*", "aportaciones_cuenca.png")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener las aportaciones por cuenca.")

    elif text == "11":
        img = get_azure_blob_bytes(AZURE_CONTAINER, "reporte_lluvia_1_1_638848218556433423.png")
        if img:
            send_wa_image_base64(from_jid, img, "📊 *CFE SPH Grijalva - Reporte de lluvias 24 horas de 6am a 6am.*", "lluvias_24h.png")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener el reporte de lluvias 24 horas.")

    elif text == "12":
        img = get_azure_blob_bytes(AZURE_CONTAINER, "reporte_lluvia_1_2_638848218556433423.png")
        if img:
            send_wa_image_base64(from_jid, img, "📊 *CFE SPH Grijalva - Reporte de lluvias parcial de 6am a hora actual.*", "lluvias_parcial.png")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener el reporte de lluvias parcial.")

    elif text == "7":
        report_text = get_azure_blob_text("reporte-unidades", "telegram_report.txt")
        if report_text:
            send_wa_text(from_jid, f"📊 *Reporte de Disponibilidad*\n\n{report_text}")
        else:
            send_wa_text(from_jid, "⚠️ No se pudo obtener el reporte de disponibilidad.")

    elif text == "6":
        usuarios_en_modo_ia[from_jid] = True
        msg_ia = (
            "🤖 *¡Hola! Ahora estás hablando con el Centinela de SPH potenciado con IA*\n\n"
            "Puedo ayudarte con:\n"
            "• 📊 Análisis de datos\n"
            "• ❓ Preguntas técnicas\n"
            "• 🔍 Consultas sobre operación\n"
            "• 💡 Recomendaciones\n\n"
            "💬 *Escribe tu pregunta*\n\n"
            "💡 _Escribe 'volver' para regresar al menú principal._"
        )
        send_wa_text(from_jid, msg_ia)

    else:
        # Responder con el menú principal
        send_wa_text(from_jid, get_menu_text())
