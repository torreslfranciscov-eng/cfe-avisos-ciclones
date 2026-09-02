"""
smn_scraper.py
Extractor de Avisos de Ciclón Tropical desde el portal de CONAGUA / SMN.
Soporta Océano Pacífico y Océano Atlántico.
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://smn.conagua.gob.mx/tools/GUI/PortalLaravel/public/"

BASINS = {
    "pacifico": {
        "name": "Océano Pacífico",
        "url": urljoin(BASE_URL, "WebAviso")
    },
    "atlantico": {
        "name": "Océano Atlántico",
        "url": urljoin(BASE_URL, "WebAvisoAtlan")
    }
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}


def get_active_cyclones(basin_key=None):
    """
    Consulta WebAviso para Pacífico y/o Atlántico y obtiene la lista de avisos activos.
    """
    basins_to_check = [basin_key] if basin_key else list(BASINS.keys())
    all_cyclones = []

    for key in basins_to_check:
        basin_info = BASINS.get(key)
        if not basin_info:
            continue

        try:
            response = requests.get(basin_info["url"], headers=HEADERS, timeout=20, verify=False)
            response.raise_for_status()
        except Exception as e:
            print(f"[ERROR] No se pudo conectar a {basin_info['name']}: {e}")
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        buttons = soup.find_all(class_="aviso-btn")

        for btn in buttons:
            aviso_id = btn.get("data-aviso-id")
            btn_text = btn.get_text(strip=True)
            if aviso_id:
                all_cyclones.append({
                    "aviso_id": aviso_id,
                    "basin_key": key,
                    "cuenca": basin_info["name"],
                    "basin_url": basin_info["url"],
                    "label": btn_text,
                    "is_active": "active-aviso" in btn.get("class", [])
                })

    return all_cyclones


def fetch_cyclone_data(aviso_id, basin_key="pacifico", download_images=True, output_dir="temp_images"):
    """
    Extrae la información completa de un aviso específico por su aviso_id y cuenca.
    """
    basin_info = BASINS.get(basin_key, BASINS["pacifico"])
    url = f"{basin_info['url']}?searchText={aviso_id}"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=20, verify=False)
        response.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Error al consultar aviso {aviso_id} en {basin_info['name']}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    
    # 1. Sistema Ciclónico
    sistema_text = ""
    for elem in soup.find_all(string=re.compile(r"Sistema ciclónico:", re.I)):
        parent_text = elem.parent.get_text(strip=True)
        sistema_text = re.sub(r"^Sistema ciclónico:\s*", "", parent_text, flags=re.I).strip()
        sistema_text = re.sub(r"Emisión:.*$", "", sistema_text).strip()
        if sistema_text:
            break

    # 2. Titular (Síntesis)
    titular_text = ""
    sintesis_box = soup.find(class_=re.compile(r"sintesis-box|sintesis", re.I))
    if sintesis_box:
        titular_text = sintesis_box.get_text(strip=True)
        titular_text = re.sub(r"^Síntesis\s*", "", titular_text, flags=re.I).strip()
    
    if not titular_text:
        for p in soup.find_all(["p", "h3", "h4", "h5"]):
            t = p.get_text(strip=True)
            if any(k in t.upper() for k in ["CONTINÚA", "SE MANTIENE", "HURACÁN", "TORMENTA", "DEPRESIÓN", "DEBILITADO", "INTENSIFICADO"]) and len(t) < 200:
                titular_text = t
                break

    # 3. Situación Actual
    situacion_actual_text = ""
    for card in soup.find_all(class_=re.compile(r"card", re.I)):
        if "Situación Actual" in card.get_text():
            for p in card.find_all("p"):
                pt = p.get_text(strip=True)
                if pt and not pt.startswith("Condiciones") and not pt.startswith("Hora local") and not pt.startswith("Sistema"):
                    situacion_actual_text = pt
                    break
        if situacion_actual_text:
            break

    # Fallback para situación actual
    if not situacion_actual_text:
        for p in soup.find_all("p"):
            t = p.get_text(strip=True)
            if any(k in t.lower() for k in ["continúa siendo", "se mantiene como", "su centro se localiza", "se desplaza hacia", "se ha debilitado", "se ubica en"]):
                situacion_actual_text = t
                break

    # 4. Próximo aviso
    proximo_aviso_text = ""
    for elem in soup.find_all(string=re.compile(r"EL SIGUIENTE AVISO SE EMITIRÁ", re.I)):
        proximo_aviso_text = elem.strip()
        if proximo_aviso_text:
            break

    # 5. Tabla de Condiciones Actuales
    condiciones = {
        "hora_local_gmt": "",
        "latitud_norte": "",
        "longitud_oeste": "",
        "distancia_costa": "",
        "desplazamiento": "",
        "vientos_sostenidos": "",
        "vientos_rachas": "",
        "presion_minima": "",
        "pronostico_lluvia": "",
        "comentarios_adicionales": "",
        "recomendaciones": "",
    }

    for tbl in soup.find_all("table"):
        text = tbl.get_text()
        if "Condiciones Actuales" in text or "Hora local" in text:
            rows = tbl.find_all("tr")
            for r in rows:
                cols = [td.get_text(strip=True) for td in r.find_all(["td", "th"])]
                if not cols:
                    continue
                row_label = cols[0].lower()
                
                if "hora local" in row_label and len(cols) > 1:
                    condiciones["hora_local_gmt"] = cols[1]
                elif "ubicación del centro" in row_label:
                    for c in cols[1:]:
                        if "latitud" in c.lower():
                            lat_match = re.search(r'[\d\.]+', c)
                            if lat_match:
                                condiciones["latitud_norte"] = lat_match.group(0)
                        if "longitud" in c.lower():
                            lon_match = re.search(r'[\d\.]+', c)
                            if lon_match:
                                condiciones["longitud_oeste"] = lon_match.group(0)
                elif "distancia" in row_label and len(cols) > 1:
                    condiciones["distancia_costa"] = cols[1]
                elif "desplazamiento" in row_label and len(cols) > 1:
                    condiciones["desplazamiento"] = cols[1]
                elif "vientos" in row_label:
                    for c in cols[1:]:
                        if "sostenidos" in c.lower():
                            sost_match = re.search(r'[\d]+', c)
                            if sost_match:
                                condiciones["vientos_sostenidos"] = sost_match.group(0)
                        elif "rachas" in c.lower():
                            rach_match = re.search(r'[\d]+', c)
                            if rach_match:
                                condiciones["vientos_rachas"] = rach_match.group(0)
                        elif "/" in c:
                            parts = c.split("/")
                            if len(parts) >= 2:
                                s_match = re.search(r'[\d]+', parts[0])
                                r_match = re.search(r'[\d]+', parts[1])
                                if s_match: condiciones["vientos_sostenidos"] = s_match.group(0)
                                if r_match: condiciones["vientos_rachas"] = r_match.group(0)
                elif "presión" in row_label and len(cols) > 1:
                    pres_match = re.search(r'[\d]+', cols[1])
                    if pres_match:
                        condiciones["presion_minima"] = pres_match.group(0)
                    else:
                        condiciones["presion_minima"] = cols[1]
                elif "lluvia" in row_label and len(cols) > 1:
                    condiciones["pronostico_lluvia"] = cols[1]
                elif "comentarios" in row_label and len(cols) > 1:
                    condiciones["comentarios_adicionales"] = cols[1]
                elif "recomendaciones" in row_label and len(cols) > 1:
                    condiciones["recomendaciones"] = cols[1]

    # 6. Imágenes (Satélite y Trayectoria)
    img_sat_url = None
    img_tray_url = None
    
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "ImgSat" in src:
            img_sat_url = urljoin(BASE_URL, src)
        elif "ImgTray" in src:
            img_tray_url = urljoin(BASE_URL, src)

    img_sat_path = None
    img_tray_path = None
    
    if download_images:
        os.makedirs(output_dir, exist_ok=True)
        if img_sat_url:
            sat_filename = f"sat_{basin_key}_{aviso_id}.jpg"
            img_sat_path = os.path.join(output_dir, sat_filename)
            try:
                res_img = requests.get(img_sat_url, headers=HEADERS, timeout=20, verify=False)
                if res_img.status_code == 200:
                    with open(img_sat_path, "wb") as f:
                        f.write(res_img.content)
            except Exception as e:
                print(f"[WARN] No se pudo descargar imagen satélite: {e}")
                img_sat_path = None
                
        if img_tray_url:
            tray_filename = f"tray_{basin_key}_{aviso_id}.jpg"
            img_tray_path = os.path.join(output_dir, tray_filename)
            try:
                res_img = requests.get(img_tray_url, headers=HEADERS, timeout=20, verify=False)
                if res_img.status_code == 200:
                    with open(img_tray_path, "wb") as f:
                        f.write(res_img.content)
            except Exception as e:
                print(f"[WARN] No se pudo descargar imagen trayectoria: {e}")
                img_tray_path = None

    # Extraer nombre limpio y número de aviso
    nombre_limpio = sistema_text
    numero_aviso = ""
    
    num_match = re.search(r'(\d+)$', sistema_text)
    if num_match:
        numero_aviso = num_match.group(1)
        nombre_limpio = re.sub(r'\s*\d+$', '', sistema_text).strip()

    return {
        "aviso_id": aviso_id,
        "basin_key": basin_key,
        "cuenca": basin_info["name"],
        "sistema": sistema_text,
        "nombre_limpio": nombre_limpio,
        "numero_aviso": numero_aviso,
        "titular": titular_text,
        "situacion_actual": situacion_actual_text,
        "condiciones": condiciones,
        "proximo_aviso": proximo_aviso_text,
        "img_sat_url": img_sat_url,
        "img_tray_url": img_tray_url,
        "img_sat_path": img_sat_path,
        "img_tray_path": img_tray_path,
    }


if __name__ == "__main__":
    print("=== Buscando ciclones activos en Océano Pacífico y Océano Atlántico ===")
    cyclones = get_active_cyclones()
    print(f"Total de ciclones activos detectados: {len(cyclones)}")
    for c in cyclones:
        print(f"\nProcesando: {c['label']} en {c['cuenca']} (ID: {c['aviso_id']})")
        data = fetch_cyclone_data(c["aviso_id"], basin_key=c["basin_key"])
        if data:
            print("Titular:", data["titular"])
            print("Situación:", data["situacion_actual"])
            print("Condiciones:", data["condiciones"])
            print("Próximo aviso:", data["proximo_aviso"])
