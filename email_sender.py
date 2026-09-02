"""
email_sender.py
Módulo para el envío automático por correo electrónico de los reportes Word (.docx)
e imágenes satelitales y de cono de trayectoria de Avisos de Ciclón Tropical CFE.
"""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email import encoders


def send_cyclone_email(cyclone_data, docx_path):
    """
    Envía por correo electrónico la notificación del aviso junto con las imágenes
    incrustadas (satélite y trayectoria) y el archivo Word adjunto.
    """
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").replace(" ", "").strip()
    email_to_raw = os.getenv("EMAIL_TO", "").strip()

    if not smtp_user or not smtp_password or not email_to_raw:
        logging.info("[EMAIL] Envío por correo no configurado (falta SMTP_USER, SMTP_PASSWORD o EMAIL_TO).")
        return False

    recipients = [e.strip() for e in email_to_raw.split(",") if e.strip()]
    if not recipients:
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

    subject = f"🌀 Aviso Meteorológico CFE: {sistema} ({cuenca})"

    has_sat = img_sat_path and os.path.exists(img_sat_path)
    has_tray = img_tray_path and os.path.exists(img_tray_path)

    # HTML con imágenes incrustadas lado a lado
    images_html = ""
    if has_sat or has_tray:
        images_html = """
        <div class="section-title">Imágenes de Satélite y Trayectoria</div>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
            <tr>
        """
        if has_sat:
            images_html += """
                <td style="width: 50%; padding: 4px; text-align: center; vertical-align: top;">
                    <img src="cid:sat_img" style="max-width: 100%; height: auto; border-radius: 6px; border: 1px solid #ddd;" alt="Imagen Satelital">
                    <p style="font-size: 11px; color: #666; margin: 4px 0 0 0;">🛰️ Imagen Satelital</p>
                </td>
            """
        if has_tray:
            images_html += """
                <td style="width: 50%; padding: 4px; text-align: center; vertical-align: top;">
                    <img src="cid:tray_img" style="max-width: 100%; height: auto; border-radius: 6px; border: 1px solid #ddd;" alt="Cono de Trayectoria">
                    <p style="font-size: 11px; color: #666; margin: 4px 0 0 0;">🗺️ Cono de Trayectoria</p>
                </td>
            """
        images_html += """
            </tr>
        </table>
        """

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f4f6f9; color: #333; margin: 0; padding: 20px; }}
            .card {{ background-color: #ffffff; border-radius: 8px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); max-width: 680px; margin: auto; }}
            .header {{ border-bottom: 2px solid #1E5B4F; padding-bottom: 12px; margin-bottom: 16px; }}
            .header h2 {{ color: #1E5B4F; margin: 0 0 6px 0; font-size: 20px; }}
            .header p {{ color: #666; margin: 0; font-size: 13px; }}
            .titular {{ background: #fdf5f5; border-left: 4px solid #c0392b; padding: 12px; border-radius: 4px; font-weight: bold; color: #403152; margin-bottom: 16px; font-size: 14px; }}
            .section-title {{ color: #1E5B4F; font-size: 14px; font-weight: bold; margin-top: 16px; margin-bottom: 8px; text-transform: uppercase; }}
            .table-cond {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 16px; font-size: 13px; }}
            .table-cond th, .table-cond td {{ padding: 8px 10px; border: 1px solid #e0e0e0; text-align: left; }}
            .table-cond th {{ background-color: #f8f9fa; color: #1E5B4F; width: 35%; }}
            .footer {{ margin-top: 24px; padding-top: 12px; border-top: 1px solid #eee; font-size: 11px; color: #888; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="header">
                <h2>Comisión Federal de Electricidad &mdash; Hidrometeorología</h2>
                <p>Aviso de Ciclón Tropical en el {cuenca} &bull; CONAGUA / SMN</p>
            </div>

            <div class="titular">
                {titular}
            </div>

            <div class="section-title">Situación Actual</div>
            <p style="font-size: 13px; line-height: 1.5; margin: 0 0 16px 0;">{situacion}</p>

            {images_html}

            <div class="section-title">Condiciones Actuales</div>
            <table class="table-cond">
                <tr><th>Hora Local / GMT</th><td>{cond.get('hora_local_gmt', '--')}</td></tr>
                <tr><th>Ubicación</th><td>Lat: {cond.get('latitud_norte', '--')}°N, Lon: {cond.get('longitud_oeste', '--')}°O</td></tr>
                <tr><th>Distancia a Costa</th><td>{cond.get('distancia_costa', '--')}</td></tr>
                <tr><th>Desplazamiento</th><td>{cond.get('desplazamiento', '--')}</td></tr>
                <tr><th>Vientos / Rachas</th><td>Sostenidos: {cond.get('vientos_sostenidos', '--')} km/h | Rachas: {cond.get('vientos_rachas', '--')} km/h</td></tr>
                <tr><th>Presión Central</th><td>{cond.get('presion_minima', '--')} hPa</td></tr>
                <tr><th>Pronóstico de Lluvia</th><td>{cond.get('pronostico_lluvia', 'Sin efectos')}</td></tr>
            </table>

            <div style="margin-top: 16px; background: #eef7f4; padding: 10px; border-radius: 4px; font-size: 12px; color: #1E5B4F; font-weight: bold;">
                🔔 {proximo}
            </div>

            <div style="margin-top: 16px; font-size: 13px;">
                📎 <strong>Archivo adjunto:</strong> Se adjunta el reporte oficial completo en formato Word (<code>{filename}</code>).
            </div>

            <div class="footer">
                Departamento de Hidrometeorología &bull; Gerencia de Ingeniería Civil &bull; CFE Generación
            </div>
        </div>
    </body>
    </html>
    """

    # Usar multipart/related para permitir incrustar imágenes CID en el HTML
    msg = MIMEMultipart("related")
    msg["From"] = f"CFE Hidrometeorología <{smtp_user}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    # Parte HTML
    msg_alt = MIMEMultipart("alternative")
    msg.attach(msg_alt)
    msg_alt.attach(MIMEText(html_body, "html", "utf-8"))

    # Incrustar imagen Satélite (CID)
    if has_sat:
        try:
            with open(img_sat_path, "rb") as f:
                img_sat = MIMEImage(f.read())
                img_sat.add_header("Content-ID", "<sat_img>")
                img_sat.add_header("Content-Disposition", "inline", filename="satelite.jpg")
                msg.attach(img_sat)
        except Exception as e:
            logging.error(f"[EMAIL] Error al incrustar sat_img: {e}")

    # Incrustar imagen Trayectoria (CID)
    if has_tray:
        try:
            with open(img_tray_path, "rb") as f:
                img_tray = MIMEImage(f.read())
                img_tray.add_header("Content-ID", "<tray_img>")
                img_tray.add_header("Content-Disposition", "inline", filename="trayectoria.jpg")
                msg.attach(img_tray)
        except Exception as e:
            logging.error(f"[EMAIL] Error al incrustar tray_img: {e}")

    # Adjuntar archivo Word (.docx) con codificación RFC 2231 y MIME type oficial de Word
    if os.path.exists(docx_path):
        try:
            with open(docx_path, "rb") as f:
                part = MIMEBase(
                    "application",
                    "vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
                part.set_payload(f.read())
            encoders.encode_base64(part)
            
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=("utf-8", "", filename)
            )
            msg.attach(part)
        except Exception as e:
            logging.error(f"[EMAIL] Error al adjuntar archivo Word: {e}")

    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=20)
            server.starttls()
            
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, recipients, msg.as_string())
        server.quit()
        logging.info(f"[EMAIL] Notificación con imágenes y Word enviada a: {', '.join(recipients)}")
        return True
    except Exception as e:
        logging.error(f"[EMAIL] Error al enviar correo SMTP: {e}")
        return False
