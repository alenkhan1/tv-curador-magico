import os
import json
import re
import time
import logging
import requests
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

FIRMAS_CANALES = {
    "Win Sports": ["WIN SPORTS", "WIN SPORT", "WIN+"],
    "DSports": ["DSPORTS", "DIRECTV SPORTS", "D SPORTS", "DTV SPORTS"],
    "TyC Sports": ["TYC SPORTS", "TYC"],
    "Eurosport": ["EUROSPORT"]
}

# ─── FUNCIONES DE LIMPIEZA Y VALIDADOR GOOGLE ────────────────────────────────
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
        "LÍBERO", "PRESIÓN ALTA"
    ]
    if t.strip() in ["DSPORTS", "WIN SPORTS", "EUROSPORT"]: return True
    return any(pb in t for pb in palabras_basura)

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
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200: return False
            
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

def crear_id_seguro(titulo: str) -> str:
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return f"local_{hash_obj.hexdigest()[:10]}"

def obtener_urls_xtream() -> dict:
    log.info("🔍 Fase 1: Escaneando servidor Xtream para URLs dinámicas...")
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

# ─── FASE 2: SCRAPERS LOCALES + VALIDACIÓN GOOGLE ────────────────────────────
headers_web = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def extraer_tyc() -> list:
    log.info("📡 Extrayendo TyC Sports...")
    eventos = []
    try:
        html = requests.get("https://play.tycsports.com/proximos-eventos.html", headers=headers_web, timeout=15).text
        patron = r'data-start="(\d+)"\s+data-end="(\d+)"\s+data-eventname="([^"]+)"'
        coincidencias = re.findall(patron, html)
        
        for start_ms, end_ms, titulo in coincidencias:
            if es_basura(titulo): continue
            if not es_en_vivo_google(titulo): continue # <--- Validación Google
            
            inicio_dt = datetime.fromtimestamp(int(start_ms) / 1000, timezone.utc)
            fin_dt = datetime.fromtimestamp(int(end_ms) / 1000, timezone.utc)
            duracion = int((fin_dt - inicio_dt).total_seconds() / 60)
            
            eventos.append({
                "titulo": titulo.strip(), "canal": "TyC Sports",
                "hora_utc": inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duracion_min": duracion
            })
    except Exception as e: log.error(f"Error TyC: {e}")
    return eventos

def extraer_eurosport_epg() -> list:
    log.info("📡 Extrayendo Eurosport 1 y 2 (EPG_dobleM)...")
    eventos = []
    try:
        url_xml = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
        r = requests.get(url_xml, timeout=20)
        if r.status_code != 200: return eventos
        
        root = ET.fromstring(r.content)
        for elem in root.findall("programme"):
            canal_id = elem.attrib.get("channel", "")
            
            # FILTRO ESTRICTO: Solo estos dos canales
            if canal_id in ["Eurosport1.es", "Eurosport2.es"]:
                titulo_elem = elem.find("title")
                desc_elem = elem.find("desc")
                titulo = titulo_elem.text if titulo_elem is not None else ""
                desc = desc_elem.text if desc_elem is not None else ""
                
                # FILTRO VIVO: Debe decir explícitamente DIRECTO
                if "DIRECTO" in titulo.upper() or "DIRECTO" in desc.upper():
                    titulo_limpio = titulo.replace("DIRECTO", "").replace("()", "").strip(" -:")
                    if es_basura(titulo_limpio): continue
                    
                    hora_str = elem.attrib.get("start", "")
                    try:
                        dt_obj = datetime.strptime(hora_str[:14], "%Y%m%d%H%M%S")
                        offset_str = hora_str[15:]
                        sign = 1 if offset_str == '+' else -1
                        hours, mins = int(offset_str[1:3]), int(offset_str[3:5])
                        tz = timezone(timedelta(hours=sign*hours, minutes=sign*mins))
                        dt_utc = dt_obj.replace(tzinfo=tz).astimezone(timezone.utc)
                        
                        duracion = 120
                        hora_stop = elem.attrib.get("stop", "")
                        if hora_stop:
                            dt_stop = datetime.strptime(hora_stop[:14], "%Y%m%d%H%M%S").replace(tzinfo=tz).astimezone(timezone.utc)
                            duracion = int((dt_stop - dt_utc).total_seconds() / 60)
                            
                        eventos.append({
                            "titulo": titulo_limpio, 
                            "canal": "Eurosport",
                            "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "duracion_min": abs(duracion)
                        })
                    except: continue
    except Exception as e: log.error(f"Error Eurosport EPG: {e}")
    return eventos

def extraer_dsports() -> list:
    log.info("📡 Extrayendo DSports...")
    eventos = []
    # Lógica base intacta, cuando pongas el JSON aquí le agregas el filtro:
    # if es_basura(titulo): continue
    # if not es_en_vivo_google(titulo): continue
    return eventos

def extraer_winplay() -> list:
    log.info("📡 Extrayendo Win Sports...")
    eventos = []
    try:
        ahora = datetime.now(timezone.utc)
        start_date = f"{ahora.strftime('%Y-%m-%d')}T00:00:00.000Z"
        end_date = f"{ahora.strftime('%Y-%m-%d')}T23:59:59.000Z"
        url = f"https://unity.tbxapis.com/v0/sections/679cf2e59f9c761c5888766a/components/67b4dc7c7bcf686c2de6142d/items/6761b6ab55adef022ee97d166a561d048728db7c786b53b0941d0dd9_eses_p0_es.json?pageSize=25&page=1&fromEpg={start_date}&toEpg={end_date}"
        
        datos = requests.get(url, timeout=15).json()
        for canal in datos.get("result", []):
            for prog in canal.get("content", {}).get("epg", []):
                titulo = prog.get("programName", "")
                
                if es_basura(titulo): continue
                if not es_en_vivo_google(titulo): continue # <--- Validación Google
                
                try:
                    inicio_dt = datetime.strptime(prog.get("startTime"), "%Y-%m-%dT%H:%M:%S.000Z")
                    fin_dt = datetime.strptime(prog.get("endTime"), "%Y-%m-%dT%H:%M:%S.000Z")
                    duracion = int((fin_dt - inicio_dt).total_seconds() / 60)
                    
                    eventos.append({
                        "titulo": titulo.strip(), "canal": "Win Sports",
                        "hora_utc": inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "duracion_min": duracion
                    })
                except: continue
    except Exception as e: log.error(f"Error WinPlay: {e}")
    return eventos

# ─── FASE 3: MOTOR DE FUSIÓN ─────────────────────────────────────────────────
def inyectar_eventos():
    log.info("=== INICIANDO INYECTOR EPG LIGERO Y SEGURO ===")
    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error(f"No existe {ARCHIVO_EVENTOS}. Ejecuta primero el curador.")
        return
        
    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        eventos_app = json.load(f)
        
    urls_disponibles = obtener_urls_xtream()
    eventos_web = extraer_tyc() + extraer_eurosport_epg() + extraer_winplay() + extraer_dsports()
    log.info(f"Total eventos inyectables aprobados: {len(eventos_web)}")
    
    nuevos_creados = 0
    fuentes_añadidas = 0
    
    for ew in eventos_web:
        canal = ew["canal"]
        fuentes_iptv = urls_disponibles.get(canal, [])
        if not fuentes_iptv: continue
            
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
