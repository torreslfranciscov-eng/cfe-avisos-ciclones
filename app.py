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
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from flask import Flask, jsonify, request, send_from_directory, render_template_string, Response, send_file
from smn_scraper import get_active_cyclones, fetch_cyclone_data
from report_generator import generate_word_report
from email_sender import send_cyclone_email, send_whatsapp_disconnected_alert
from telegram_sender import send_cyclone_telegram, handle_incoming_telegram_update
from whatsapp_sender import send_cyclone_whatsapp
from teams_sender import send_cyclone_teams
from centinela_bot import handle_incoming_whatsapp_message, handle_incoming_teams_message, get_azure_blob_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)


@app.after_request
def add_teams_embed_headers(response):
    """Permite que Teams embeba el dashboard como Tab (iframe) dentro de un canal."""
    response.headers['Content-Security-Policy'] = (
        "frame-ancestors 'self' https://*.teams.microsoft.com https://teams.microsoft.com "
        "https://*.cloud.microsoft https://teams.cloud.microsoft "
        "https://*.microsoft.com https://*.office.com https://*.office365.com https://*.sharepoint.com;"
    )
    response.headers.pop('X-Frame-Options', None)
    return response

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


def background_telegram_polling_worker():
    """Hilo en segundo plano para procesar comandos interactivos de Centinela vía Telegram sin requerir webhook HTTPS."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        logging.info("[TELEGRAM CENTINELA] Sin TELEGRAM_BOT_TOKEN, polling interactivo omitido.")
        return
    offset = None
    server_base_url = os.getenv("SERVER_PUBLIC_URL", "http://20.102.124.195:10000").rstrip("/")
    logging.info("[TELEGRAM CENTINELA] Iniciando polling interactivo de comandos...")
    while True:
        try:
            params = {
                "timeout": 20,
                "allowed_updates": json.dumps(["message", "callback_query"])
            }
            if offset:
                params["offset"] = offset
            r = http_requests.get(f"https://api.telegram.org/bot{bot_token}/getUpdates", params=params, timeout=25)
            if r.status_code == 200:
                data = r.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    threading.Thread(
                        target=handle_incoming_telegram_update,
                        args=(update,),
                        kwargs={"server_base_url": server_base_url},
                        daemon=True
                    ).start()
        except Exception as e:
            logging.debug(f"[TELEGRAM CENTINELA] Error en polling: {e}")
            time.sleep(5)
        time.sleep(1)


monitor_thread = threading.Thread(target=background_monitor_worker, daemon=True)
monitor_thread.start()

telegram_thread = threading.Thread(target=background_telegram_polling_worker, daemon=True)
telegram_thread.start()


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


@app.route("/dashboard")
def teams_dashboard():
    """Dashboard operativo dedicado para embeber como Tab fijo en Microsoft Teams."""
    from datetime import datetime, timezone, timedelta
    from centinela_bot import generate_blob_sas_url
    zona_mx = timezone(timedelta(hours=-6))
    ahora = datetime.now(zona_mx).strftime("%d/%m/%Y %H:%M CST")

    # Verificar ciclones activos
    try:
        from smn_scraper import get_active_cyclones
        ciclones = get_active_cyclones() or []
    except Exception:
        ciclones = []

    # Cargar registros de guardia capturados
    capturas_file = os.path.join(os.path.dirname(__file__), "capturas_guardia.json")
    capturas = []
    if os.path.exists(capturas_file):
        try:
            with open(capturas_file, "r", encoding="utf-8") as f:
                capturas = json.load(f)
        except Exception:
            capturas = []

    # Calcular KPIs a partir de la última captura o valores agregados
    kpi_mw = "—"
    kpi_unidades = "—"
    kpi_aportacion = "—"
    kpi_central = "Sin registros recientes"

    if capturas:
        ultima = capturas[-1]
        kpi_mw = ultima.get("generacion") or "—"
        kpi_unidades = ultima.get("unidades") or "—"
        kpi_aportacion = ultima.get("aportacion") or "—"
        kpi_central = f"Último reporte: {ultima.get('central', 'Central')} ({ultima.get('timestamp', '')})"

    # Generar URLs seguras SAS para reportes gráficos
    server_base = request.host_url.rstrip("/")
    img_unidades = generate_blob_sas_url("unidades", "9c8a7f42-3d91-4e01-a3fa-0d2e5b1c6f7d.png") or f"{server_base}/media/azure/unidades/9c8a7f42-3d91-4e01-a3fa-0d2e5b1c6f7d.png"
    img_power = generate_blob_sas_url("unidades", "6f3b2c91-91df-41b6-9a1e-c3f0d0c8e24a.png") or f"{server_base}/media/azure/unidades/6f3b2c91-91df-41b6-9a1e-c3f0d0c8e24a.png"
    img_embalses = generate_blob_sas_url("unidades", "e1a5f734-9c2e-4b3b-8d5a-6f7e1d2c9b8f.png") or f"{server_base}/media/azure/unidades/e1a5f734-9c2e-4b3b-8d5a-6f7e1d2c9b8f.png"
    img_lluvias = generate_blob_sas_url("unidades", "reporte_lluvia_1_1_638848218556433423.png") or f"{server_base}/media/azure/unidades/reporte_lluvia_1_1_638848218556433423.png"

    # Construir filas de la bitácora
    filas_html = ""
    if capturas:
        for c in reversed(capturas[-25:]):  # Últimos 25 registros
            filas_html += f"""
            <tr>
                <td style="white-space:nowrap;font-weight:600;color:var(--accent-green);">{c.get('timestamp', '—')}</td>
                <td style="font-weight:500;">{c.get('usuario', '—')}</td>
                <td><span class="badge badge-blue">{c.get('central', '—')}</span></td>
                <td style="text-align:right;">{c.get('nivel', '—')}</td>
                <td style="text-align:right;color:var(--accent-cyan);">{c.get('aportacion', '—')}</td>
                <td style="text-align:right;">{c.get('extraccion', '—')}</td>
                <td style="text-align:right;">{c.get('turbinado', '—')}</td>
                <td style="text-align:right;font-weight:700;color:var(--accent-green);">{c.get('generacion', '—')}</td>
                <td>{c.get('unidades', '—')}</td>
                <td style="color:var(--text-secondary);font-size:0.85rem;">{c.get('observaciones', '—')}</td>
            </tr>
            """
    else:
        filas_html = """
        <tr>
            <td colspan="10" style="text-align:center;padding:2.5rem;color:var(--text-muted);">
                📋 No hay tomas de datos de guardia registradas aún hoy.<br>
                <span style="font-size:0.85rem;color:var(--text-secondary);">
                    Para registrar una guardia desde Teams, escribe <b>@centinelaSph 9</b> en el canal de Cuenca Grijalva.
                </span>
            </td>
        </tr>
        """

    html = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Centinela Dashboard — SPH Grijalva</title>
        <script src="https://res.cdn.office.net/teams-js/2.22.0/js/MicrosoftTeams.min.js"></script>
        <script>
            document.addEventListener('DOMContentLoaded', function() {
                if (window.microsoftTeams) {
                    try { microsoftTeams.app.initialize(); } catch(e) {}
                }
            });
        </script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-primary: #0b131e;
                --bg-card: #152232;
                --bg-card-hover: #1c2d42;
                --accent-green: #10b981;
                --accent-blue: #3b82f6;
                --accent-amber: #f59e0b;
                --accent-red: #ef4444;
                --accent-cyan: #06b6d4;
                --text-primary: #f1f5f9;
                --text-secondary: #94a3b8;
                --text-muted: #64748b;
                --border: #223449;
            }
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                background: var(--bg-primary);
                color: var(--text-primary);
                font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif;
                min-height: 100vh;
            }
            .dashboard { max-width: 1400px; margin: 0 auto; padding: 1.5rem; }

            /* Header */
            .header {
                display: flex; justify-content: space-between; align-items: center;
                margin-bottom: 1.5rem; padding-bottom: 1rem;
                border-bottom: 1px solid var(--border);
            }
            .header-left { display: flex; align-items: center; gap: 1rem; }
            .header-logo { font-size: 2.2rem; }
            .header h1 { font-size: 1.4rem; font-weight: 700; letter-spacing: -0.02em; }
            .header .sub { color: var(--text-secondary); font-size: 0.8rem; font-weight: 400; }
            .header-right { text-align: right; }
            .header-time { color: var(--text-secondary); font-size: 0.85rem; margin-top: 0.2rem; }
            .header-live {
                display: inline-flex; align-items: center; gap: 0.4rem;
                color: var(--accent-green); font-size: 0.75rem; font-weight: 600;
                text-transform: uppercase; letter-spacing: 0.05em;
            }
            .pulse {
                width: 8px; height: 8px; border-radius: 50%;
                background: var(--accent-green);
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(16,185,129,0.4); }
                50% { opacity: 0.8; box-shadow: 0 0 0 6px rgba(16,185,129,0); }
            }

            /* Alerta de ciclón */
            .cyclone-alert {
                background: linear-gradient(135deg, #7f1d1d, #991b1b);
                border: 1px solid var(--accent-red);
                border-radius: 12px; padding: 1rem 1.5rem;
                margin-bottom: 1.5rem;
                display: flex; align-items: center; gap: 1rem;
                animation: alert-glow 3s ease-in-out infinite;
            }
            @keyframes alert-glow {
                0%, 100% { box-shadow: 0 0 15px rgba(239,68,68,0.2); }
                50% { box-shadow: 0 0 25px rgba(239,68,68,0.4); }
            }
            .cyclone-alert .icon { font-size: 2rem; }
            .cyclone-alert .text { font-weight: 600; }
            .cyclone-alert .detail { color: #fca5a5; font-size: 0.85rem; }
            .no-cyclone {
                background: var(--bg-card); border: 1px solid var(--border);
                border-radius: 12px; padding: 1rem 1.5rem;
                margin-bottom: 1.5rem;
                display: flex; align-items: center; gap: 1rem;
                color: var(--accent-green);
            }
            .no-cyclone .detail { color: var(--text-secondary); }

            /* Grid de cards */
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
            .card {
                background: var(--bg-card); border: 1px solid var(--border);
                border-radius: 12px; padding: 1.25rem;
                transition: all 0.2s ease;
                cursor: default;
            }
            .card:hover { background: var(--bg-card-hover); border-color: #3a4a5a; }
            .card-header {
                display: flex; justify-content: space-between; align-items: center;
                margin-bottom: 0.75rem;
            }
            .card-title {
                font-size: 0.75rem; text-transform: uppercase;
                letter-spacing: 0.06em; color: var(--text-secondary); font-weight: 600;
            }
            .card-icon { font-size: 1.3rem; }
            .card-value { font-size: 2rem; font-weight: 700; letter-spacing: -0.02em; }
            .card-label { color: var(--text-muted); font-size: 0.75rem; margin-top: 0.25rem; }
            .card-value.green { color: var(--accent-green); }
            .card-value.blue { color: var(--accent-blue); }
            .card-value.amber { color: var(--accent-amber); }
            .card-value.cyan { color: var(--accent-cyan); }

            /* Sección Bitácora */
            .section-header {
                display: flex; justify-content: space-between; align-items: center;
                margin: 2rem 0 1rem; flex-wrap: wrap; gap: 1rem;
            }
            .section-title {
                font-size: 1.1rem; font-weight: 700;
                padding-left: 0.5rem; border-left: 3px solid var(--accent-green);
                display: flex; align-items: center; gap: 0.5rem;
            }
            .btn-group { display: flex; gap: 0.5rem; }
            .btn {
                display: inline-flex; align-items: center; gap: 0.4rem;
                background: #1e3a5f; color: #fff; text-decoration: none;
                padding: 0.5rem 0.9rem; border-radius: 8px; font-size: 0.8rem; font-weight: 600;
                border: 1px solid #2b5282; transition: all 0.2s;
            }
            .btn:hover { background: #2563eb; border-color: #3b82f6; transform: translateY(-1px); }
            .btn-excel { background: #065f46; border-color: #047857; color: #ecfdf5; }
            .btn-excel:hover { background: #059669; border-color: #10b981; }

            /* Tabla Bitácora */
            .table-container {
                background: var(--bg-card); border: 1px solid var(--border);
                border-radius: 12px; overflow-x: auto; margin-bottom: 2rem;
            }
            table { width: 100%; border-collapse: collapse; font-size: 0.85rem; text-align: left; }
            th {
                background: #111b27; color: var(--text-secondary);
                padding: 0.85rem 1rem; font-weight: 600; text-transform: uppercase;
                font-size: 0.7rem; letter-spacing: 0.05em; border-bottom: 1px solid var(--border);
            }
            td { padding: 0.85rem 1rem; border-bottom: 1px solid rgba(255,255,255,0.03); }
            tr:hover td { background: rgba(255,255,255,0.02); }
            .badge {
                display: inline-block; padding: 0.2rem 0.5rem; border-radius: 6px;
                font-size: 0.75rem; font-weight: 600;
            }
            .badge-blue { background: rgba(59,130,246,0.15); color: #93c5fd; border: 1px solid rgba(59,130,246,0.3); }

            /* Sección de imágenes */
            .image-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 1rem; }
            .image-card {
                background: var(--bg-card); border: 1px solid var(--border);
                border-radius: 12px; overflow: hidden;
            }
            .image-card .label {
                padding: 0.75rem 1rem; font-size: 0.8rem;
                font-weight: 600; color: var(--text-secondary);
                text-transform: uppercase; letter-spacing: 0.04em;
                background: rgba(0,0,0,0.2);
            }
            .image-card img {
                width: 100%; height: auto; display: block;
                opacity: 0.95; transition: opacity 0.2s;
            }
            .image-card:hover img { opacity: 1; }

            /* Footer */
            .footer {
                text-align: center; padding: 1.5rem 0 0.5rem;
                color: var(--text-muted); font-size: 0.75rem;
                border-top: 1px solid var(--border); margin-top: 2rem;
            }

            /* Auto-refresh indicator */
            .refresh-bar {
                position: fixed; top: 0; left: 0; height: 2px;
                background: var(--accent-green);
                animation: refresh-progress 60s linear infinite;
                z-index: 999;
            }
            @keyframes refresh-progress { from { width: 0; } to { width: 100%; } }
        </style>
    </head>
    <body>
        <div class="refresh-bar"></div>
        <div class="dashboard">
            <!-- Header -->
            <div class="header">
                <div class="header-left">
                    <div class="header-logo">⚡</div>
                    <div>
                        <h1>Centinela SPH Grijalva</h1>
                        <span class="sub">Subgerencia de Producción Hidroeléctrica • CFE Generación</span>
                    </div>
                </div>
                <div class="header-right">
                    <div class="header-live"><div class="pulse"></div> EN VIVO</div>
                    <div class="header-time">""" + ahora + """</div>
                </div>
            </div>

            <!-- Alerta de ciclón -->
            """ + (
                ''.join([
                    f'<div class="cyclone-alert"><div class="icon">🌀</div><div><div class="text">⚠️ {c.get("nombre", "Ciclón Tropical")} — {c.get("cuenca", "")}</div><div class="detail">Sistema activo en vigilancia • Monitoreo continuo</div></div></div>'
                    for c in ciclones
                ]) if ciclones else
                '<div class="no-cyclone"><div class="icon">☀️</div><div><div class="text" style="font-weight:600">Sin ciclones tropicales activos</div><div class="detail">Pacífico y Atlántico en condiciones normales</div></div></div>'
            ) + """

            <!-- KPIs principales -->
            <div class="grid">
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">Generación Reportada</span>
                        <span class="card-icon">⚡</span>
                    </div>
                    <div class="card-value green">""" + str(kpi_mw) + """ <span style="font-size:1rem;color:var(--text-muted)">MW</span></div>
                    <div class="card-label">""" + kpi_central + """</div>
                </div>
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">Unidades en Servicio</span>
                        <span class="card-icon">🔌</span>
                    </div>
                    <div class="card-value blue">""" + str(kpi_unidades) + """</div>
                    <div class="card-label">Reportado en última guardia</div>
                </div>
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">Aportaciones a Embalses</span>
                        <span class="card-icon">💧</span>
                    </div>
                    <div class="card-value cyan">""" + str(kpi_aportacion) + """ <span style="font-size:1rem;color:var(--text-muted)">m³/s</span></div>
                    <div class="card-label">Gasto de entrada reportado</div>
                </div>
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">Vigilancia Ciclónica</span>
                        <span class="card-icon">🌀</span>
                    </div>
                    <div class="card-value """ + ("amber" if ciclones else "green") + '">'\
                    + (str(len(ciclones)) + " activo" + ("s" if len(ciclones) > 1 else "") if ciclones else "Normal") + """</div>
                    <div class="card-label">Pacífico + Atlántico / SMN</div>
                </div>
            </div>

            <!-- Bitácora de Guardias -->
            <div class="section-header">
                <div class="section-title">📋 Bitácora Institucional de Guardia SPH</div>
                <div class="btn-group">
                    <a href="/download/excel-guardia" class="btn btn-excel" download>📥 Descargar Excel (.xlsx)</a>
                    <a href="/download/csv-guardia" class="btn" download>📄 Descargar CSV UTF-8</a>
                </div>
            </div>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Fecha / Hora</th>
                            <th>Ingeniero(a)</th>
                            <th>Central</th>
                            <th style="text-align:right;">Nivel (msnm)</th>
                            <th style="text-align:right;">Aportación (m³/s)</th>
                            <th style="text-align:right;">Extracción (m³/s)</th>
                            <th style="text-align:right;">Turbinado (m³/s)</th>
                            <th style="text-align:right;">Generación (MW)</th>
                            <th>Unidades</th>
                            <th>Observaciones / Novedades</th>
                        </tr>
                    </thead>
                    <tbody>
                        """ + filas_html + """
                    </tbody>
                </table>
            </div>

            <!-- Reportes visuales -->
            <div class="section-header">
                <div class="section-title" style="border-color:var(--accent-blue);">📊 Monitoreo Visual en Tiempo Real</div>
            </div>
            <div class="image-grid">
                <div class="image-card">
                    <div class="label">⚡ Reporte de Unidades Generadoras</div>
                    <img src=\"""" + img_unidades + """\" alt="Unidades Generadoras" onerror="this.parentElement.innerHTML='<div style=\\'padding:3rem;text-align:center;color:var(--text-muted)\\'>Conectando con Azure Storage...</div>'">
                </div>
                <div class="image-card">
                    <div class="label">📊 Power Monitoring</div>
                    <img src=\"""" + img_power + """\" alt="Power Monitoring" onerror="this.parentElement.innerHTML='<div style=\\'padding:3rem;text-align:center;color:var(--text-muted)\\'>Conectando con Azure Storage...</div>'">
                </div>
                <div class="image-card">
                    <div class="label">🌊 Condición de Embalses</div>
                    <img src=\"""" + img_embalses + """\" alt="Condición de Embalses" onerror="this.parentElement.innerHTML='<div style=\\'padding:3rem;text-align:center;color:var(--text-muted)\\'>Conectando con Azure Storage...</div>'">
                </div>
                <div class="image-card">
                    <div class="label">🌧️ Reporte de Lluvias 24h</div>
                    <img src=\"""" + img_lluvias + """\" alt="Reporte de Lluvias" onerror="this.parentElement.innerHTML='<div style=\\'padding:3rem;text-align:center;color:var(--text-muted)\\'>Conectando con Azure Storage...</div>'">
                </div>
            </div>

            <div class="footer">
                Subgerencia de Producción Hidroeléctrica Grijalva • Gerencia de Ingeniería Civil • CFE Generación<br>
                Auto-sincronización con Microsoft Teams • Refresh cada 60s
            </div>
        </div>
        <script>
            // Auto-refresh cada 60 segundos
            setTimeout(() => location.reload(), 60000);
        </script>
    </body>
    </html>
    """
    return html


@app.route("/download/excel-guardia")
def download_excel_guardia():
    """Descarga la bitácora de guardias en formato Microsoft Excel (.xlsx)."""
    excel_path = os.path.join(os.path.dirname(__file__), "bitacora_guardias.xlsx")
    csv_path = os.path.join(os.path.dirname(__file__), "bitacora_guardias.csv")
    json_path = os.path.join(os.path.dirname(__file__), "capturas_guardia.json")

    # Si no existe el xlsx pero tenemos json o csv, generarlo dinámicamente
    if not os.path.exists(excel_path) and os.path.exists(json_path):
        try:
            import openpyxl
            with open(json_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Bitácora de Guardia SPH"
            ws.append(["Fecha / Hora CST", "Ingeniero(a)", "Central / Embalse", "Nivel (msnm)", "Aportación (m³/s)", "Extracción (m³/s)", "Gasto Turbinado (m³/s)", "Generación (MW)", "Unidades", "Observaciones"])
            for r in records:
                ws.append([
                    r.get("timestamp", ""),
                    r.get("usuario", ""),
                    r.get("central", ""),
                    r.get("nivel", ""),
                    r.get("aportacion", ""),
                    r.get("extraccion", ""),
                    r.get("turbinado", ""),
                    r.get("generacion", ""),
                    r.get("unidades", ""),
                    r.get("observaciones", "")
                ])
            wb.save(excel_path)
        except Exception as e:
            logging.error(f"Error generando Excel al vuelo: {e}")

    if os.path.exists(excel_path):
        return send_file(excel_path, as_attachment=True, download_name="Bitacora_Guardia_SPH_Grijalva.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    elif os.path.exists(csv_path):
        return send_file(csv_path, as_attachment=True, download_name="Bitacora_Guardia_SPH_Grijalva.csv", mimetype="text/csv")
    else:
        # Generar archivo vacío si no hay registros
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Bitácora de Guardia SPH"
        ws.append(["Fecha / Hora CST", "Ingeniero(a)", "Central / Embalse", "Nivel (msnm)", "Aportación (m³/s)", "Extracción (m³/s)", "Gasto Turbinado (m³/s)", "Generación (MW)", "Unidades", "Observaciones"])
        wb.save(excel_path)
        return send_file(excel_path, as_attachment=True, download_name="Bitacora_Guardia_SPH_Grijalva.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/download/csv-guardia")
def download_csv_guardia():
    """Descarga la bitácora de guardias en formato CSV con BOM UTF-8 (compatible con Excel)."""
    csv_path = os.path.join(os.path.dirname(__file__), "bitacora_guardias.csv")
    if not os.path.exists(csv_path):
        with open(csv_path, "w", encoding="utf-8-sig") as f:
            f.write("timestamp,usuario,central,nivel,aportacion,extraccion,turbinado,generacion,unidades,observaciones\n")
    return send_file(csv_path, as_attachment=True, download_name="Bitacora_Guardia_SPH_Grijalva.csv", mimetype="text/csv")


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


@app.route("/qr/data")
@app.route("/qr/data/")
@app.route("/qr-data")
def proxy_qr_data():
    """Proxy para consultar el código QR actual en JSON."""
    openwa_url = os.getenv("OPENWA_SERVER_URL", "http://localhost:8085").rstrip("/")
    try:
        r = http_requests.get(f"{openwa_url}/qr/data", timeout=10)
        return Response(r.content, status=r.status_code, content_type="application/json")
    except Exception as e:
        return jsonify({"isReady": False, "qr": None, "error": str(e)}), 500


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
    try:
        response_card = handle_incoming_teams_message(payload, server_base_url=server_base_url)
    except Exception as e:
        logging.error(f"[TEAMS] Excepción al procesar comando: {e}", exc_info=True)
        response_card = {
            "type": "message",
            "text": "⚠️ Centinela encontró un error al procesar tu solicitud. Intenta de nuevo o escribe 'menu'."
        }
    return jsonify(response_card), 200


@app.route("/api/teams/trigger-captura", methods=["GET", "POST"])
def trigger_teams_captura():
    """Lanza la publicación del formulario de captura de guardia directamente en el canal de Teams."""
    from teams_sender import send_captura_form_teams
    success = send_captura_form_teams()
    return jsonify({
        "success": success,
        "message": "Formulario de Captura Diaria enviado al canal de Teams" if success else "Error al enviar al canal de Teams (verifica TEAMS_WEBHOOK_URL)"
    })


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


@app.route("/media/cyclone/<path:filename>", methods=["GET"])
def media_cyclone(filename):
    """Sirve imágenes de satélite y trayectoria descargadas localmente."""
    temp_dir = os.path.abspath(os.getenv("TEMP_IMAGES_DIR", "temp_images"))
    file_path = os.path.join(temp_dir, filename)
    if not os.path.exists(file_path):
        return "Imagen no encontrada", 404
    mimetype = "image/jpeg"
    if filename.endswith(".png"):
        mimetype = "image/png"
    try:
        with open(file_path, "rb") as f:
            data = f.read()
        resp = Response(data, mimetype=mimetype)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp
    except Exception as e:
        return f"Error leyendo imagen: {e}", 500


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
