import os
import json
import re
import time
import logging
import requests
import gzip
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

# ─── CONFIGURACIÓN DE LOGS Y ENTORNO ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("inyector")

XTREAM_URL   = os.environ.get("XTREAM_URL", "http://tu-servidor.com")
XTREAM_USER  = os.environ.get("XTREAM_USER", "usuario")
XTREAM_PASS  = os.environ.get("XTREAM_PASS", "password")

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
GOOGLE_CX = os.environ.get("GOOGLE_CX")

ARCHIVO_EVENTOS = "eventos_hoy.json"

# ─── URLS DE EPG LIGERAS Y ESPECÍFICAS ───────────────────────────────────────
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
URLS_EPG_AMERICA = [
    "https://epgshare01.online/epgshare01/epg_ripper_CO1.xml.gz", # Colombia (Win Sports, DSports)
    "https://epgshare01.online/epgshare01/epg_ripper_AR1.xml.gz"  # Argentina (TyC Sports, DSports)
]

# ─── FIRMAS DE CANALES EN XTREAM ─────────────────────────────────────────────
FIRMAS_CANALES = {
    "Win Sports": ["WIN SPORTS", "WIN SPORT", "WIN+"],
    "DSports": ["DSPORTS", "DIRECTV SPORTS", "D SPORTS", "DTV SPORTS"],
    "TyC Sports": ["TYC SPORTS", "TYC"],
    "Eurosport": ["EUROSPORT"]
}

# ─── FUNCIONES DE AYUDA Y LIMPIEZA ───────────────────────────────────────────
def calcular_similitud(texto1: str, texto2: str) -> float:
    t1 = re.sub(r'[^A-Z0-9\s]', '', str(texto1).upper())
    t2 = re.sub(r'[^A-Z0-9\s]', '', str(texto2).upper())
    return SequenceMatcher(None, t1, t2).ratio() * 100

def es_basura(titulo: str) -> bool:
    t = titulo.upper()
    palabras_basura = [
        "REPETICIÓN", "REPETICION", "NOTICIAS", "RESUMEN", "PREVIO", 
        "SAQUE LARGO", "MEDIO TIEMPO", "LA RUTA DEL MUNDIAL", "DESPIERTA WIN",
        "PLANETA FÚTBOL", "MAGAZINE", "FÚTBOL TOTAL", "DE FÚTBOL SE HABLA ASÍ",
        "PUEDE PASAR", "SUPERFÚTBOL", "DALE AL MEDIO", "SPORTIA", "DOMINGOL",
        "LÍBERO", "PRESIÓN ALTA", "CONEXIÓN", "SPORT CENTER", "SPORTSCENTER"
    ]
    if t.strip() in ["DSPORTS", "WIN SPORTS", "EUROSPORT", "TYC SPORTS"]: return True
    return any(pb in t for pb in palabras_basura)

def parse_xmltv_time(time_str: str) -> datetime:
    """Convierte '20260510200000 -0500' a objeto datetime UTC."""
    try:
        dt_obj = datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        offset_str = time_str[15:]
        if len(offset_str) >= 5:
            sign = 1 if offset_str[0] == '+' else -1
            hours, mins = int(offset_str[1:3]), int(offset_str[3:5])
            tz = timezone(timedelta(hours=sign*hours, minutes=sign*mins))
        else:
            tz = timezone.utc
        return dt_obj.replace(tzinfo=tz).astimezone(timezone.utc)
    except:
        return datetime.now(timezone.utc)

def crear_id_seguro(titulo: str) -> str:
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return f"local_{hash_obj.hexdigest()[:10]}"

# ─── VALIDADOR DE GOOGLE (LA SOLUCIÓN AMERICANA) ─────────────────────────────
def es_en_vivo_google(titulo: str) -> bool:
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        log.warning("⚠️ Faltan llaves de Google. Se descarta evento por seguridad.")
        return False

    titulo_limpio = re.sub(r'\b(HD|SD|FHD|4K|VIVO|DIRECTO|REPETICION|RESUMEN)\b', '', titulo, flags=re.IGNORECASE).strip()
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX,
        "q": f'"{titulo_limpio}" (hoy OR previa OR vivo)',
        "dateRestrict": "d",
        "hl": "es"
    }
    
    try:
        time.sleep(1) # Cuidar rate limit de Google
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200: 
            return False
            
        items = r.json().get("items", [])
        if not items:
            log.info(f"[-] Descartado (Cero noticias web hoy): {titulo}")
            return False
            
        texto_global = " ".join([i.get("snippet", "") + " " + i.get("title", "") for i in items]).lower()
        
        banderas_verdes = ["hoy", "previa", "transmisión", "transmision", "alineaciones", "dónde ver", "donde ver", "recibe a", "se enfrentan", "vivo", "directo"]
        banderas_rojas = ["goles", "resumen", "resultado", "derrotó", "derroto", "empató", "empato", "ayer", "polémica", "venció", "vencio"]
        
        puntos_verdes = sum(1 for b in banderas_verdes if b in texto_global)
        puntos_rojos  = sum(1 for b in banderas_rojas if b in texto_global)
        
        es_vivo = puntos_verdes > puntos_rojos
        if es_vivo:
            log.info(f"[+] GOOGLE APRUEBA (En Vivo): {titulo}")
        else:
            log.info(f"[-] GOOGLE RECHAZA (Repetición/Pasado): {titulo}")
            
        return es_vivo
    except Exception as e:
        log.error(f"Error conectando a Google: {e}")
        return False

# ─── CONEXIÓN XTREAM ─────────────────────────────────────────────────────────
def obtener_urls_xtream() -> dict:
    log.info("🔍 Fase 1: Escaneando servidor Xtream para URLs de Win, TyC, DSports, Eurosport...")
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    
    mapa_urls = {canal: [] for canal in FIRMAS_CANALES.keys()}
    try:
        r = requests.get(api_url, timeout=30)
        if r.status_code != 200: return mapa_urls
        
        streams = r.json()
        for s in streams:
            nombre = s.get("name", "").upper()
            stream_id = str(s.get("stream_id"))
            url_video = f"{base_url}/{XTREAM_USER}/{XTREAM_PASS}/{stream_id}.ts"
            
            for canal_logico, firmas in FIRMAS_CANALES.items():
                if any(firma in nombre for firma in firmas):
                    mapa_urls[canal_logico].append({
                        "nombre": s.get("name", "").strip(),
                        "url": url_video
                    })
                    break
        return mapa_urls
    except Exception as e:
        return mapa_urls

# ─── PARSEO LIGERO DE EPG ────────────────────────────────────────────────────
def extraer_eventos_epg() -> list:
    eventos_aprobados = []
    
    # 1. PROCESAR EUROPA (EPG_dobleM) -> Lógica de confianza (DIRECTO)
    log.info("📡 Extrayendo Vía Europea (EPG_dobleM)...")
    try:
        r_eu = requests.get(URL_EPG_EUROPA, timeout=15)
        if r_eu.status_code == 200:
            root_eu = ET.fromstring(r_eu.content)
            for elem in root_eu.findall("programme"):
                canal_id = elem.attrib.get("channel", "")
                
                # Solo analizamos Eurosport 1 y 2
                if canal_id in ["Eurosport1.es", "Eurosport2.es"]:
                    titulo_elem = elem.find("title")
                    desc_elem = elem.find("desc")
                    titulo = titulo_elem.text if titulo_elem is not None else ""
                    desc = desc_elem.text if desc_elem is not None else ""
                    
                    if "DIRECTO" in titulo.upper() or "DIRECTO" in desc.upper():
                        titulo_limpio = titulo.replace("DIRECTO", "").replace("()", "").strip(" -:")
                        if es_basura(titulo_limpio): continue
                        
                        dt_utc = parse_xmltv_time(elem.attrib.get("start", ""))
                        hora_stop = elem.attrib.get("stop", "")
                        duracion = 120
                        if hora_stop:
                            dt_stop = parse_xmltv_time(hora_stop)
                            duracion = int((dt_stop - dt_utc).total_seconds() / 60)
                            
                        eventos_aprobados.append({
                            "titulo": titulo_limpio, 
                            "canal": "Eurosport",
                            "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "duracion_min": abs(duracion)
                        })
    except Exception as e: log.error(f"Error procesando Europa: {e}")

    # 2. PROCESAR AMÉRICA (epgshare01) -> Lógica de validación (Google)
    for url_am in URLS_EPG_AMERICA:
        region_nombre = "Colombia" if "CO1" in url_am else "Argentina"
        log.info(f"📡 Extrayendo Vía Americana ({region_nombre} - epgshare01)...")
        try:
            r_am = requests.get(url_am, stream=True, timeout=20)
            if r_am.status_code == 200:
                f = gzip.GzipFile(fileobj=io.BytesIO(r_am.content))
                
                for event, elem in ET.iterparse(f, events=("end",)):
                    if elem.tag == "programme":
                        canal_id = elem.attrib.get("channel", "").upper()
                        
                        canal_logico = None
                        if "WINSPORT" in canal_id: canal_logico = "Win Sports"
                        elif "TYCSPORT" in canal_id: canal_logico = "TyC Sports"
                        elif "DSPORT" in canal_id or "DIRECTVSPORT" in canal_id: canal_logico = "DSports"
                        
                        if canal_logico:
                            titulo_elem = elem.find("title")
                            titulo = titulo_elem.text if titulo_elem is not None else ""
                            
                            if not es_basura(titulo):
                                # ¡AQUÍ ENTRA GOOGLE!
                                if es_en_vivo_google(titulo):
                                    dt_utc = parse_xmltv_time(elem.attrib.get("start", ""))
                                    hora_stop = elem.attrib.get("stop", "")
                                    duracion = 120
                                    if hora_stop:
                                        dt_stop = parse_xmltv_time(hora_stop)
                                        duracion = int((dt_stop - dt_utc).total_seconds() / 60)
                                        
                                    eventos_aprobados.append({
                                        "titulo": titulo.strip(), 
                                        "canal": canal_logico,
                                        "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                        "duracion_min": abs(duracion)
                                    })
                        elem.clear() # Liberar memoria
        except Exception as e: log.error(f"Error procesando {region_nombre}: {e}")

    return eventos_aprobados

# ─── FASE 3: MOTOR DE INYECCIÓN ──────────────────────────────────────────────
def inyectar_eventos():
    log.info("=== INICIANDO INYECTOR DUAL (EPG + GOOGLE) ===")
    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error(f"No existe {ARCHIVO_EVENTOS}. Ejecuta primero el curador.")
        return
        
    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        eventos_app = json.load(f)
        
    urls_disponibles = obtener_urls_xtream()
    eventos_aprobados = extraer_eventos_epg()
    
    log.info(f"Total eventos inyectables (Europa Confianza + América Google): {len(eventos_aprobados)}")
    
    nuevos_creados = 0
    fuentes_añadidas = 0
    
    for ew in eventos_aprobados:
        canal = ew["canal"]
        fuentes_iptv = urls_disponibles.get(canal, [])
        
        if not fuentes_iptv: 
            # Si no tienes links de Xtream de ese canal hoy, no inyectamos el evento
            continue
            
        match_encontrado = False
        for ea in eventos_app:
            if calcular_similitud(ew["titulo"], ea["titulo"]) > 75.0:
                match_encontrado = True
                urls_actuales = [f["url"] for f in ea["fuentes"]]
                for fuente_nueva in fuentes_iptv:
                    if fuente_nueva["url"] not in urls_actuales:
                        ea["fuentes"].append(fuente_nueva)
                        fuentes_añadidas += 1
                break
                
        if not match_encontrado:
            cat = "Ciclismo" if "ETAPA" in ew["titulo"].upper() else "Fútbol" if " VS " in ew["titulo"].upper() else "Deportes"
            nuevo_evento = {
                "id": crear_id_seguro(ew["titulo"] + ew["hora_utc"]),
                "titulo": ew["titulo"],
                "torneo": canal,
                "categoria": cat,
                "hora_utc": ew["hora_utc"],
                "duracion_min": ew["duracion_min"],
                "logo_local": "", "logo_visitante": "", "banner": "",
                "tier": 2,
                "fuentes": fuentes_iptv
            }
            eventos_app.append(nuevo_evento)
            nuevos_creados += 1
            
    eventos_app.sort(key=lambda x: x.get("hora_utc", ""))
    
    with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
        json.dump(eventos_app, f, ensure_ascii=False, indent=2)
        
    log.info(f"Eventos exclusivos inyectados: {nuevos_creados}")
    log.info(f"Fuentes añadidas a SofaScore: {fuentes_añadidas}")
    log.info("=== INYECCIÓN FINALIZADA ===")

if __name__ == "__main__":
    inyectar_eventos()
