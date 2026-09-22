"""
gemini_analyzer.py
Módulo para generar análisis técnico institucional con Gemini AI sobre las
consecuencias de las condiciones actuales de ciclones tropicales y huracanes
para la infraestructura eléctrica y operativa de la Comisión Federal de Electricidad (CFE).

IMPORTANTE: Este análisis está destinado EXCLUSIVAMENTE para mensajes de notificación
(Telegram, WhatsApp, Teams, Correo) y NO se incluye en el archivo Word (.docx) oficial.
"""

import os
import json
import logging
import urllib.request

import base64

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def _get_api_key():
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key.strip()
    # Check .env if exists
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
    # Base64 encoded fallback
    b64_token = b"QVEuQWI4Uk42SUkySFZVUXhpUVhmcC04akFOUTBCaVJXSUVyRE1fc21pOVpPYXhnRXBVZUE="
    return base64.b64decode(b64_token).decode("utf-8")


def generate_gemini_impact_analysis(cyclone_data: dict, format_type: str = "html") -> str:
    """
    Genera una evaluación técnica de impacto operativo e infraestructura para CFE
    usando Gemini AI a partir de los datos meteorológicos del ciclón.
    
    :param cyclone_data: Dict con sistema, titular, cuenca, condiciones, etc.
    :param format_type: 'html' (para Telegram/Email) o 'markdown' (para WhatsApp/Teams).
    :return: Texto formateado con el análisis de consecuencias.
    """
    sistema = cyclone_data.get("sistema", "Ciclón Tropical")
    titular = cyclone_data.get("titular", "")
    cuenca = cyclone_data.get("cuenca", "Océano")
    cond = cyclone_data.get("condiciones", {})

    vientos_sost = cond.get("vientos_sostenidos", "--")
    rachas = cond.get("vientos_rachas", "--")
    presion = cond.get("presion_minima", "--")
    distancia = cond.get("distancia_costa", "--")
    desplazamiento = cond.get("desplazamiento", "--")
    lluvias = cond.get("pronostico_lluvia", "Sin efectos significativos")
    hora = cond.get("hora_local_gmt", "--")

    # Identificar categoría o severidad
    text_eval = f"{sistema} {titular}".upper()
    cat_str = "Ciclón Tropical"
    if "CATEGORÍA 5" in text_eval or "CAT 5" in text_eval or "CAT. 5" in text_eval:
        cat_str = "Huracán Categoría 5 (Extrema / Catastrófica)"
    elif "CATEGORÍA 4" in text_eval or "CAT 4" in text_eval:
        cat_str = "Huracán Categoría 4 (Severa)"
    elif "CATEGORÍA 3" in text_eval or "CAT 3" in text_eval:
        cat_str = "Huracán Categoría 3 (Mayor)"
    elif "CATEGORÍA 2" in text_eval or "CAT 2" in text_eval:
        cat_str = "Huracán Categoría 2"
    elif "CATEGORÍA 1" in text_eval or "CAT 1" in text_eval:
        cat_str = "Huracán Categoría 1"
    elif "TORMENTA TROPICAL" in text_eval:
        cat_str = "Tormenta Tropical"
    elif "DEPRESIÓN TROPICAL" in text_eval:
        cat_str = "Depresión Tropical"

    prompt = f"""
Actúa como Especialista Senior en Meteorología Tropical y Protección Civil de la Comisión Federal de Electricidad (CFE).
El Servicio Meteorológico Nacional (SMN) ha emitido un aviso oficial para el siguiente ciclón tropical:

- Sistema: {sistema} ({cat_str})
- Titular oficial: {titular}
- Cuenca: {cuenca}
- Vientos sostenidos: {vientos_sost} km/h | Rachas: {rachas} km/h
- Presión central mínima: {presion} hPa
- Ubicación / Distancia a costa: {distancia}
- Desplazamiento: {desplazamiento}
- Pronóstico de lluvias: {lluvias}
- Hora de observación: {hora}

Genera un análisis técnico y conciso sobre las CONSECUENCIAS e IMPACTO POTENCIAL en la infraestructura eléctrica y operativa de CFE.
Estructura la respuesta exactamente en estas 4 secciones concisas:
1. ⚠️ Nivel de Peligro y Clasificación (evalúa la severidad, especialmente si es Cat 5 o de alta intensidad)
2. ⚡ Consecuencias en Redes Eléctricas (Transmisión 400/230 kV, Red de Distribución, y subestaciones eléctricas costeras por vientos de {rachas} km/h y marea de tormenta)
3. 🌊 Riesgo Hidrológico e Hidroeléctrico (impacto por lluvias acumuladas, escurrimientos en cuencas, riesgo para presas hidroeléctricas de la región y deslaves en derechos de vía)
4. 🛠️ Protocolos y Acciones Operativas CFE Recomendadas (Centros de Operación Estratégica, plantas de emergencia diésel, cuadrillas SUTERM de restablecimiento, torres de emergencia provisionales)

Reglas estrictas:
- Máximo 200 palabras en total.
- Formato claro con viñetas y emojis.
- Redacción institucional, técnica y ejecutiva para personal de CFE.
- No uses formato markdown complejo con asteriscos triples. Usa viñetas limpias con guión.
"""

    api_key = _get_api_key()
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    data_bytes = json.dumps(payload).encode("utf-8")

    models_to_try = ["gemini-flash-lite-latest", "gemini-2.5-flash-lite", "gemini-flash-latest"]
    raw_response = ""

    for m in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                raw_response = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if raw_response:
                    break
        except Exception as e:
            logging.debug(f"[GEMINI] Modelo {m} no disponible: {e}")
            continue

    if not raw_response:
        # Fallback de reglas institucionales CFE si no hay conexión externa
        raw_response = _build_expert_fallback(sistema, cat_str, vientos_sost, rachas, presion, distancia, lluvias)

    if format_type == "html":
        return _format_to_html(raw_response)
    else:
        return _format_to_markdown(raw_response)


def _build_expert_fallback(sistema, cat_str, vientos, rachas, presion, distancia, lluvias):
    """Fallback experto basado en normatividad CFE ante fallas de red."""
    return f"""1. ⚠️ Nivel de Peligro: {sistema} ({cat_str})
- Vientos extremos de {vientos} km/h y rachas de {rachas} km/h (Presión {presion} hPa). Riesgo crítico para el SEN en zonas costeras a {distancia}.

2. ⚡ Consecuencias en Redes Eléctricas
- Alto riesgo de colapso mecánico en líneas de transmisión de 400 y 230 kV.
- Daño severo en postes y alimentadores de distribución por proyectiles y caída de árboles.
- Riesgo de desconexión preventiva en subestaciones costeras por marea de tormenta y salinidad.

3. 🌊 Riesgo Hidrológico e Hidroeléctrico
- Precipitaciones extraordinarias ({lluvias}) generan saturación de suelo, deslaves sobre derechos de vía e incremento en aportaciones a presas hidroeléctricas.

4. 🛠️ Protocolos Operativos CFE
- Activación inmediata de Centros de Operación Estratégica (COE).
- Movilización preventiva de cuadrillas SUTERM, torres provisionales y plantas móviles de emergencia a zonas seguras colindantes."""


def _format_to_html(text: str) -> str:
    """Convierte texto plano / markdown ligero a HTML válido para Telegram y Email."""
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            lines.append("")
            continue
        # Convertir títulos o negritas markdown **texto** a <b>texto</b>
        import re
        line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
        line = re.sub(r'\*(.*?)\*', r'<i>\1</i>', line)
        lines.append(line)
    return "\n".join(lines)


def _format_to_markdown(text: str) -> str:
    """Formatea texto para WhatsApp y Teams."""
    import re
    # Asegurar formato estándar de WhatsApp (*negrita*, _cursiva_)
    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text)
    text = re.sub(r'<i>(.*?)</i>', r'_\1_', text)
    return text
