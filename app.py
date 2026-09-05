"""
app.py
Servicio Web / API para monitoreo de avisos de ciclones tropicales de CFE (CONAGUA/SMN).
Monitoreo automático cada 15 min, generación de reportes Word (.docx),
notificaciones por Correo Electrónico, Telegram y WhatsApp, y Asistente Centinela Bot.
"""

import os
import time
import json
import logging
import threading
import requests as http_requests
from flask import Flask, jsonify, request, send_from_directory, render_template_string, Response
from smn_scraper import get_active_cyclones, fetch_cyclone_data
from report_generator import generate_word_report
from email_sender import send_cyclone_email, send_whatsapp_disconnected_alert
from telegram_sender import send_cyclone_telegram, handle_incoming_telegram_update
from whatsapp_sender import send_cyclone_whatsapp
from teams_sender import send_cyclone_teams
from centinela_bot import handle_incoming_whatsapp_message, handle_incoming_teams_message, get_azure_blob_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)

REPORTS_DIR = os.getenv("REPORTS_DIR", "reportes_generados")
STATE_FILE = os.getenv("STATE_FILE", "state_processed.json")
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
OPENWA_SERVER_URL = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085")

os.makedirs(REPORTS_DIR, exist_ok=True)
last_wa_alert_time = 0


def load_processed_state():
    """Carga los IDs y avisos previamente procesados."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_processed_state(state):
    """Guarda los IDs procesados."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def check_whatsapp_health():
    """Verifica si WhatsApp sigue conectado. Si se desvinculó, envía correo de alerta."""
    global last_wa_alert_time
    if not os.getenv("WHATSAPP_TO"):
        return
        
    try:
        r = http_requests.get(f"{OPENWA_SERVER_URL}/status", timeout=5)
        data = r.json()
        if not data.get("isReady"):
            now = time.time()
            if now - last_wa_alert_time > 14400:
                logging.warning("[WHATSAPP] Sesión desvinculada detectada. Enviando correo de alerta...")
                send_whatsapp_disconnected_alert()
                last_wa_alert_time = now
    except Exception as e:
        logging.debug(f"No se pudo consultar estado de WhatsApp: {e}")


def run_cycle_check(force=False):
    """
    Ejecuta el ciclo de verificación contra SMN para Pacífico y Atlántico.
    Genera el Word y envía notificaciones a Correo, Telegram y WhatsApp.
    """
    state = load_processed_state()
    cyclones = get_active_cyclones()
    generated = []

    check_whatsapp_health()

    for c in cyclones:
        aviso_id = str(c["aviso_id"])
        cuenca = c["cuenca"]
        basin_key = c["basin_key"]
        label = c["label"]
        state_key = f"{basin_key}_{aviso_id}"

        if not force and state_key in state:
            logging.info(f"Aviso {label} en {cuenca} (ID: {aviso_id}) ya procesado anteriormente.")
            continue

        logging.info(f"Procesando nuevo aviso detectado: {label} en {cuenca} (ID: {aviso_id})")
        data = fetch_cyclone_data(aviso_id, basin_key=basin_key)
        if data:
            doc_path = generate_word_report(data, output_dir=REPORTS_DIR)
            filename = os.path.basename(doc_path)

            email_sent = send_cyclone_email(data, doc_path)
            telegram_sent = send_cyclone_telegram(data, doc_path)
            whatsapp_sent = send_cyclone_whatsapp(data, doc_path)
            teams_sent = send_cyclone_teams(data, doc_path)

            state[state_key] = {
                "label": label,
                "cuenca": cuenca,
                "filename": filename,
                "email_sent": email_sent,
                "telegram_sent": telegram_sent,
                "whatsapp_sent": whatsapp_sent,
                "teams_sent": teams_sent,
                "timestamp": str(os.path.getmtime(doc_path))
            }
            generated.append({
                "aviso_id": aviso_id,
                "cuenca": cuenca,
                "label": label,
                "filename": filename,
                "email_sent": email_sent,
                "telegram_sent": telegram_sent,
                "whatsapp_sent": whatsapp_sent,
                "teams_sent": teams_sent
            })

    save_processed_state(state)
    return generated, len(cyclones)


def background_monitor_worker():
    """Hilo en segundo plano que revisa automáticamente el SMN cada N minutos."""
    logging.info(f"Iniciando monitor en segundo plano (revisión cada {POLL_INTERVAL_MINUTES} minutos)...")
    while True:
        try:
            logging.info("Ejecutando revisión periódica automática del SMN...")
            generated, total = run_cycle_check(force=False)
            logging.info(f"Revisión completada: {len(generated)} nuevos reportes de {total} activos.")
        except Exception as e:
            logging.error(f"Error en revisión automática: {e}")
        time.sleep(POLL_INTERVAL_MINUTES * 60)


monitor_thread = threading.Thread(target=background_monitor_worker, daemon=True)
monitor_thread.start()


@app.route("/")
def index():
    """Panel de control visual."""
    state = load_processed_state()
    files = [f for f in os.listdir(REPORTS_DIR) if f.endswith((".docx", ".docm"))]
    files.sort(key=lambda x: os.path.getmtime(os.path.join(REPORTS_DIR, x)), reverse=True)
    smtp_configured = bool(os.getenv("SMTP_USER") and os.getenv("EMAIL_TO"))
    telegram_configured = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
    whatsapp_configured = bool(os.getenv("WHATSAPP_TO"))
    teams_configured = True

    html = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>CFE - Sistema de Avisos de Ciclón Tropical & Centinela</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            body { background: #f4f6f9; font-family: 'Segoe UI', sans-serif; }
            .hero-card { background: linear-gradient(135deg, #1E5B4F, #123730); color: white; border-radius: 12px; padding: 2rem; margin-bottom: 2rem; }
            .badge-pacifico { background-color: #0d6efd; color: white; }
            .badge-atlantico { background-color: #0dcaf0; color: #000; }
        </style>
    </head>
    <body class="p-4">
        <div class="container">
            <div class="hero-card shadow">
                <h2>🌀 CFE Hidrometeorología &mdash; Avisos de Ciclón & Centinela Bot</h2>
                <p class="lead mb-0">Monitoreo 24/7, Word (.docx) y notificaciones automáticas por <strong>Correo</strong>, <strong>Telegram</strong> y <strong>WhatsApp</strong></p>
            </div>

            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h5 class="card-title">Acciones</h5>
                            <p class="card-text small">Revisión cada 15 min:</p>
                            <a href="/check?force=true" class="btn btn-success mb-2 w-100 btn-sm">⚡ Forzar Notificaciones</a>
                            <a href="/check" class="btn btn-outline-primary w-100 btn-sm">🔍 Verificar Nuevos</a>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h5 class="card-title">📧 Correo</h5>
                            {% if smtp_configured %}
                            <span class="badge bg-success">🟢 Activo</span>
                            <p class="text-muted small mt-1 mb-0">{{ os.getenv('EMAIL_TO') }}</p>
                            {% else %}
                            <span class="badge bg-warning text-dark">🟡 No configurado</span>
                            {% endif %}
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h5 class="card-title">✈️ Telegram</h5>
                            {% if telegram_configured %}
                            <span class="badge bg-success">🟢 Activo</span>
                            <p class="text-muted small mt-1 mb-0">Canal: {{ os.getenv('TELEGRAM_CHAT_ID') }}</p>
                            {% else %}
                            <span class="badge bg-warning text-dark">🟡 No configurado</span>
                            {% endif %}
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h5 class="card-title">💬 WhatsApp & Centinela</h5>
                            {% if whatsapp_configured %}
                            <span class="badge bg-success">🟢 Activo</span>
                            <p class="text-muted small mt-1 mb-0">Destino: {{ os.getenv('WHATSAPP_TO') }}</p>
                            {% else %}
                            <span class="badge bg-warning text-dark">🟡 Sin Destinatario</span>
                            {% endif %}
                            <p class="mt-2 mb-0"><a href="/qr" class="btn btn-sm btn-outline-success w-100">📱 Ver/Escanear QR</a></p>
                        </div>
                    </div>
                </div>
                <div class="col-md-12 mt-3">
                    <div class="card shadow-sm">
                        <div class="card-body d-flex justify-content-between align-items-center">
                            <div>
                                <h5 class="card-title mb-1">👥 Microsoft Teams</h5>
                                <p class="text-muted small mb-0">Canal: <strong>Avisos Ciclones</strong> • Adaptive Cards 1.4 con fotos satelitales y conos</p>
                            </div>
                            <span class="badge bg-success fs-6">🟢 Conectado</span>
                        </div>
                    </div>
                </div>
            </div>

            <div class="card shadow-sm">
                <div class="card-header bg-white d-flex justify-content-between align-items-center">
                    <h5 class="mb-0">📄 Reportes Word Disponibles para Descarga</h5>
                    <span class="badge bg-secondary">{{ files|length }} reportes</span>
                </div>
                <div class="card-body p-0">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light">
                            <tr>
                                <th>Cuenca</th>
                                <th>Nombre del Archivo</th>
                                <th class="text-end">Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for f in files %}
                            <tr>
                                <td>
                                    {% if 'Atlántico' in f %}
                                    <span class="badge badge-atlantico">Atlántico</span>
                                    {% else %}
                                    <span class="badge badge-pacifico">Pacífico</span>
                                    {% endif %}
                                </td>
                                <td><strong>{{ f }}</strong></td>
                                <td class="text-end">
                                    <a href="/download/{{ f }}" class="btn btn-sm btn-primary">⬇️ Descargar Word</a>
                                </td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="3" class="text-center py-4 text-muted">Aún no hay reportes generados.</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html, files=files, state=state, smtp_configured=smtp_configured, telegram_configured=telegram_configured, whatsapp_configured=whatsapp_configured, teams_configured=teams_configured, os=os)


@app.route("/api/whatsapp/webhook", methods=["POST"])
def whatsapp_incoming_webhook():
    """Recibe mensajes entrantes de WhatsApp y los procesa con Centinela Bot."""
    payload = request.get_json(force=True, silent=True)
    if payload:
        threading.Thread(target=handle_incoming_whatsapp_message, args=(payload,), daemon=True).start()
    return jsonify({"status": "received"}), 200



@app.route("/logout")
@app.route("/logout/")
def proxy_logout():
    """Cierra la sesión de WhatsApp y redirige a escanear un nuevo QR."""
    openwa_url = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085").rstrip("/")
    try:
        http_requests.get(f"{openwa_url}/logout", timeout=10)
    except Exception:
        pass
    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head><meta http-equiv="refresh" content="2;url=/qr"></head>
        <body style="font-family: sans-serif; text-align: center; padding: 50px;">
            <h3>🔄 Sesión cerrada con éxito.</h3>
            <p>Generando nuevo código QR para vincular tu otro número...</p>
        </body>
        </html>
    ''')

@app.route("/qr")
@app.route("/qr/")
def proxy_qr():
    """Proxy transparente al servidor de WhatsApp para mostrar la pantalla del QR."""
    openwa_url = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085").rstrip("/")
    try:
        r = http_requests.get(f"{openwa_url}/qr", timeout=10)
        return Response(r.content, status=r.status_code, content_type=r.headers.get("content-type", "text/html"))
    except Exception as e:
        return f"""
        <!DOCTYPE html>
        <html>
        <head><title>Iniciando WhatsApp...</title><meta http-equiv="refresh" content="5"></head>
        <body style="font-family: 'Segoe UI', sans-serif; text-align: center; padding: 50px; background: #f0f2f5;">
            <div style="background: white; border-radius: 12px; padding: 30px; display: inline-block; box-shadow: 0 4px 12px rgba(0,0,0,0.1); max-width: 400px;">
                <h3 style="color: #1E5B4F;">Iniciando servidor de WhatsApp...</h3>
                <p style="color: #666;">Por favor espera unos segundos mientras carga el código QR.</p>
                <p style="font-size: 12px; color: #999;">Esta página se recarga automáticamente cada 5 segundos.</p>
            </div>
        </body>
        </html>
        """, 200


@app.route("/groups")
@app.route("/groups/")
def proxy_groups():
    """Proxy para listar los grupos de WhatsApp disponibles."""
    openwa_url = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085").rstrip("/")
    try:
        r = http_requests.get(f"{openwa_url}/groups", timeout=10)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/status")
@app.route("/status/")
def proxy_status():
    """Proxy para consultar el estado del cliente WhatsApp Baileys."""
    openwa_url = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085").rstrip("/")
    try:
        r = http_requests.get(f"{openwa_url}/status", timeout=10)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/check", methods=["GET", "POST"])
def check():
    force = request.args.get("force", "false").lower() == "true"
    # Ejecutar en segundo plano para evitar timeouts de 30s en peticiones HTTP
    threading.Thread(target=run_cycle_check, kwargs={"force": force}, daemon=True).start()
    return jsonify({
        "status": "triggered",
        "message": "Revisión y envío de avisos SMN iniciado en segundo plano exitosamente."
    }), 200


@app.route("/api/cyclones", methods=["GET"])
def api_cyclones():
    cyclones = get_active_cyclones()
    results = []
    for c in cyclones:
        data = fetch_cyclone_data(c["aviso_id"], basin_key=c["basin_key"], download_images=False)
        if data:
            results.append(data)
    return jsonify(results)


@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(REPORTS_DIR, filename, as_attachment=True)


@app.route("/api/teams/webhook", methods=["POST"])
@app.route("/api/teams/centinela", methods=["POST"])
def teams_centinela_webhook():
    """Webhook para recibir comandos de Microsoft Teams (Outgoing Webhook y Workflows)."""
    # Verificación de seguridad informativa con el Token HMAC de Teams
    teams_tokens_raw = os.getenv("TEAMS_OUTGOING_TOKEN", "").strip()
    if teams_tokens_raw:
        import hmac
        import hashlib
        import base64
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            try:
                received_sig = auth_header.split(" ")[-1]
                allowed_tokens = [t.strip() for t in teams_tokens_raw.split(",") if t.strip()]
                match = any(
                    hmac.compare_digest(
                        received_sig,
                        base64.b64encode(hmac.new(base64.b64decode(tok), request.get_data(), hashlib.sha256).digest()).decode("utf-8")
                    )
                    for tok in allowed_tokens
                )
                if match:
                    logging.info("[TEAMS] Petición autenticada con firma HMAC válida.")
                else:
                    logging.info("[TEAMS] Petición recibida de un webhook secundario (sin token específico).")
            except Exception as e:
                logging.debug(f"[TEAMS] Validación HMAC omitida: {e}")

    payload = request.get_json(silent=True) or {}
    server_base_url = os.getenv("SERVER_PUBLIC_URL", request.host_url).rstrip("/")
    response_card = handle_incoming_teams_message(payload, server_base_url=server_base_url)
    return jsonify(response_card), 200


@app.route("/api/telegram/webhook", methods=["POST"])
@app.route("/api/telegram/centinela", methods=["POST"])
def telegram_centinela_webhook():
    """Webhook para recibir mensajes interactivos dirigidos a Centinela desde el Bot de Telegram."""
    payload = request.get_json(silent=True) or {}
    server_base_url = os.getenv("SERVER_PUBLIC_URL", request.host_url).rstrip("/")
    if payload:
        threading.Thread(target=handle_incoming_telegram_update, args=(payload,), kwargs={"server_base_url": server_base_url}, daemon=True).start()
    return jsonify({"status": "received"}), 200


@app.route("/media/azure/<container>/<blob_name>", methods=["GET"])
def media_azure_blob(container, blob_name):
    """Sirve imágenes y archivos descargados dinámicamente de Azure Blob Storage."""
    data = get_azure_blob_bytes(container, blob_name)
    if not data:
        return "Blob not found", 404
    mimetype = "image/png"
    if blob_name.endswith(".jpg") or blob_name.endswith(".jpeg"):
        mimetype = "image/jpeg"
    elif blob_name.endswith(".txt"):
        mimetype = "text/plain; charset=utf-8"
    return Response(data, mimetype=mimetype)


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
