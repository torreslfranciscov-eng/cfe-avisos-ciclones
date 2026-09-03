"""
teams_sender.py
Módulo para el envío automático de notificaciones enriquecidas (Adaptive Cards)
a canales de Microsoft Teams para Avisos de Ciclón Tropical de CFE.
"""

import os
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_TEAMS_WEBHOOK = "https://defaultd57455e9c73f45d7ad9e430426491d.f9.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/506d5d93099843298e5d0d1ee10e9dfb/triggers/manual/paths/invoke?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=GjsXBNxPbcFQtm-hD61szTrl8M-EVxXXqB-FzN-7zuI"


def send_cyclone_teams(cyclone_data, docx_path=None):
    """
    Construye y envía una tarjeta adaptable (Adaptive Card 1.4) al webhook de Microsoft Teams
    con el resumen meteorológico, tabla de condiciones técnicas, imágenes satelitales/cono
    y enlaces directos para descarga.
    """
    webhook_url = os.getenv("TEAMS_WEBHOOK_URL", DEFAULT_TEAMS_WEBHOOK).strip()
    if not webhook_url:
        logging.info("[TEAMS] Webhook no configurado. Se omite el envío a Teams.")
        return False

    sistema = cyclone_data.get("sistema", "Ciclón Tropical")
    cuenca = cyclone_data.get("cuenca", "Océano")
    titular = cyclone_data.get("titular", "")
    situacion = cyclone_data.get("situacion_actual", "")
    cond = cyclone_data.get("condiciones", {})
    proximo = cyclone_data.get("proximo_aviso", "")
    img_sat_url = cyclone_data.get("img_sat_url")
    img_tray_url = cyclone_data.get("img_tray_url")
    basin_url = cyclone_data.get("basin_url", "https://smn.conagua.gob.mx")
    
    server_base_url = os.getenv("SERVER_PUBLIC_URL", "https://cfe-avisos-ciclones.onrender.com").rstrip("/")
    filename = os.path.basename(docx_path) if docx_path else ""
    download_url = f"{server_base_url}/download/{filename}" if filename else server_base_url

    # Construir elementos visuales de la Adaptive Card
    card_body = [
        # Encabezado institucional
        {
            "type": "Container",
            "style": "emphasis",
            "items": [
                {
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "width": "auto",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": "🌀",
                                    "size": "ExtraLarge"
                                }
                            ]
                        },
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": "Comisión Federal de Electricidad — Hidrometeorología",
                                    "weight": "Bolder",
                                    "size": "Medium",
                                    "color": "Dark"
                                },
                                {
                                    "type": "TextBlock",
                                    "text": f"Aviso de Ciclón Tropical en el {cuenca} • CONAGUA / SMN",
                                    "isSubtle": True,
                                    "spacing": "None",
                                    "size": "Small"
                                }
                            ]
                        }
                    ]
                }
            ]
        },
        # Título del Sistema
        {
            "type": "TextBlock",
            "text": f"🚨 {sistema.upper()} ({cuenca})",
            "weight": "Bolder",
            "size": "Large",
            "color": "Attention",
            "spacing": "Medium"
        },
        # Titular / Síntesis
        {
            "type": "Container",
            "style": "warning",
            "items": [
                {
                    "type": "TextBlock",
                    "text": titular or "Aviso meteorológico oficial de CFE.",
                    "wrap": True,
                    "weight": "Bolder",
                    "color": "Dark"
                }
            ]
        },
        # Situación Actual
        {
            "type": "TextBlock",
            "text": "**Situación Actual:**",
            "weight": "Bolder",
            "spacing": "Medium"
        },
        {
            "type": "TextBlock",
            "text": situacion or "Sin información adicional reportada.",
            "wrap": True,
            "spacing": "Small"
        }
    ]

    # Tabla de Condiciones Técnicas (FactSet)
    vientos_str = f"Sostenidos: {cond.get('vientos_sostenidos', '--')} km/h | Rachas: {cond.get('vientos_rachas', '--')} km/h"
    ubicacion_str = f"Lat: {cond.get('latitud_norte', '--')}°N, Lon: {cond.get('longitud_oeste', '--')}°O"
    
    card_body.append({
        "type": "TextBlock",
        "text": "**Condiciones Actuales:**",
        "weight": "Bolder",
        "spacing": "Medium"
    })
    
    card_body.append({
        "type": "FactSet",
        "facts": [
            {"title": "⏱️ Hora Local/GMT:", "value": cond.get("hora_local_gmt", "--")},
            {"title": "📍 Ubicación:", "value": ubicacion_str},
            {"title": "📏 Distancia a costa:", "value": cond.get("distancia_costa", "--")},
            {"title": "🧭 Desplazamiento:", "value": cond.get("desplazamiento", "--")},
            {"title": "💨 Vientos/Rachas:", "value": vientos_str},
            {"title": "🌀 Presión Central:", "value": f"{cond.get('presion_minima', '--')} hPa"},
            {"title": "🌧️ Pronóstico Lluvia:", "value": cond.get("pronostico_lluvia", "Sin efectos")}
        ]
    })

    # Imágenes satelitales y trayectoria (ColumnSet)
    image_columns = []
    if img_sat_url:
        image_columns.append({
            "type": "Column",
            "width": "stretch",
            "items": [
                {
                    "type": "TextBlock",
                    "text": "🛰️ **Imagen Satelital**",
                    "horizontalAlignment": "Center",
                    "size": "Small"
                },
                {
                    "type": "Image",
                    "url": img_sat_url,
                    "altText": "Imagen Satelital",
                    "size": "Auto"
                }
            ]
        })
    if img_tray_url:
        image_columns.append({
            "type": "Column",
            "width": "stretch",
            "items": [
                {
                    "type": "TextBlock",
                    "text": "🗺️ **Cono de Trayectoria**",
                    "horizontalAlignment": "Center",
                    "size": "Small"
                },
                {
                    "type": "Image",
                    "url": img_tray_url,
                    "altText": "Cono de Trayectoria",
                    "size": "Auto"
                }
            ]
        })

    if image_columns:
        card_body.append({
            "type": "ColumnSet",
            "columns": image_columns,
            "spacing": "Medium"
        })

    # Alerta de próximo aviso
    if proximo:
        card_body.append({
            "type": "Container",
            "style": "accent",
            "spacing": "Medium",
            "items": [
                {
                    "type": "TextBlock",
                    "text": f"🔔 **{proximo}**",
                    "wrap": True,
                    "color": "Accent"
                }
            ]
        })

    # Pie institucional
    card_body.append({
        "type": "TextBlock",
        "text": "Departamento de Hidrometeorología • Gerencia de Ingeniería Civil • CFE Generación",
        "isSubtle": True,
        "size": "Small",
        "horizontalAlignment": "Center",
        "spacing": "Medium"
    })

    # Acciones (Botones)
    actions = [
        {
            "type": "Action.OpenUrl",
            "title": "📄 Descargar Reporte Word (.docx)",
            "url": download_url
        },
        {
            "type": "Action.OpenUrl",
            "title": "🌐 Ver en Portal SMN / CONAGUA",
            "url": basin_url
        }
    ]

    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": card_body,
                    "actions": actions
                }
            }
        ]
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=20)
        if r.status_code in [200, 201, 202]:
            logging.info(f"[TEAMS] Aviso de {sistema} enviado con éxito a Microsoft Teams (Status: {r.status_code})")
            return True
        else:
            logging.error(f"[TEAMS] Error al enviar a Teams (Status {r.status_code}): {r.text}")
            return False
    except Exception as e:
        logging.error(f"[TEAMS] Excepción al enviar a Microsoft Teams: {e}")
        return False
