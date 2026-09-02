"""
app.py
Servicio Web / API para monitoreo de avisos de ciclones tropicales de CFE (CONAGUA/SMN).
Monitoreo automático cada 15 min, generación de reportes Word (.docx),
envío automático por correo electrónico y canal de Telegram.
"""

import os
import time
import json
import logging
import threading
from flask import Flask, jsonify, request, send_from_directory, render_template_string
from smn_scraper import get_active_cyclones, fetch_cyclone_data
from report_generator import generate_word_report
from email_sender import send_cyclone_email
from telegram_sender import send_cyclone_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = Flask(__name__)

REPORTS_DIR = os.getenv("REPORTS_DIR", "reportes_generados")
STATE_FILE = os.getenv("STATE_FILE", "state_processed.json")
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))

os.makedirs(REPORTS_DIR, exist_ok=True)


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


def run_cycle_check(force=False):
    """
    Ejecuta el ciclo de verificación contra SMN para Pacífico y Atlántico.
    Genera el Word y envía notificaciones a Correo y Telegram.
    """
    state = load_processed_state()
    cyclones = get_active_cyclones()
    generated = []

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

            # 1. Envío por Correo Electrónico
            email_sent = send_cyclone_email(data, doc_path)

            # 2. Envío por Canal de Telegram
            telegram_sent = send_cyclone_telegram(data, doc_path)

            state[state_key] = {
                "label": label,
                "cuenca": cuenca,
                "filename": filename,
                "email_sent": email_sent,
                "telegram_sent": telegram_sent,
                "timestamp": str(os.path.getmtime(doc_path))
            }
            generated.append({
                "aviso_id": aviso_id,
                "cuenca": cuenca,
                "label": label,
                "filename": filename,
                "email_sent": email_sent,
                "telegram_sent": telegram_sent
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

    html = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>CFE - Sistema de Avisos de Ciclón Tropical</title>
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
                <h2>🌀 CFE Hidrometeorología &mdash; Avisos de Ciclón Tropical</h2>
                <p class="lead mb-0">Monitoreo 24/7, reportes Word (.docx), notificaciones por <strong>Correo</strong> y <strong>Telegram</strong></p>
            </div>

            <div class="row mb-4">
                <div class="col-md-4">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h5 class="card-title">Acciones</h5>
                            <p class="card-text">Monitoreo automático cada 15 min. Forzar verificación manual:</p>
                            <a href="/check?force=true" class="btn btn-success mb-2 w-100">⚡ Forzar Notificaciones Ahora</a>
                            <a href="/check" class="btn btn-outline-primary w-100">🔍 Verificar Nuevos</a>
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h5 class="card-title">📧 Correo Electrónico</h5>
                            {% if smtp_configured %}
                            <p class="card-text mb-1"><span class="badge bg-success">🟢 Activo</span></p>
                            <p class="text-muted small mb-0">Enviando a: <code>{{ os.getenv('EMAIL_TO') }}</code></p>
                            {% else %}
                            <p class="card-text mb-1"><span class="badge bg-warning text-dark">🟡 No configurado</span></p>
                            <p class="text-muted small mb-0">Faltan variables <code>SMTP_USER</code> y <code>EMAIL_TO</code>.</p>
                            {% endif %}
                        </div>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="card shadow-sm h-100">
                        <div class="card-body">
                            <h5 class="card-title">✈️ Canal de Telegram</h5>
                            {% if telegram_configured %}
                            <p class="card-text mb-1"><span class="badge bg-success">🟢 Activo</span></p>
                            <p class="text-muted small mb-0">Canal: <code>{{ os.getenv('TELEGRAM_CHAT_ID') }}</code></p>
                            {% else %}
                            <p class="card-text mb-1"><span class="badge bg-warning text-dark">🟡 No configurado</span></p>
                            <p class="text-muted small mb-0">Agrega <code>TELEGRAM_BOT_TOKEN</code> y <code>TELEGRAM_CHAT_ID</code>.</p>
                            {% endif %}
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
    return render_template_string(html, files=files, state=state, smtp_configured=smtp_configured, telegram_configured=telegram_configured, os=os)


@app.route("/check", methods=["GET", "POST"])
def check():
    force = request.args.get("force", "false").lower() == "true"
    generated, total_active = run_cycle_check(force=force)
    return jsonify({
        "status": "success",
        "total_active_cyclones": total_active,
        "new_reports_generated": len(generated),
        "details": generated
    })


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


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
