import os
import re
import json
import time
import logging
import requests
import unicodedata
import gzip
import io
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET

# ─── LOGGING Y CONFIGURACIÓN ─────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("curador_maestro")

XTREAM_URL   = os.environ.get("XTREAM_URL", "").rstrip('/')
XTREAM_USER  = os.environ.get("XTREAM_USER", "")
XTREAM_PASS  = os.environ.get("XTREAM_PASS", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CX    = os.environ.get("GOOGLE_CX", "")
RAPIDAPI_HOST = "sofasport.p.rapidapi.com"

ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")
ARCHIVO_JSON = "eventos_hoy.json"

LLAVES_ENV = [val for key, val in os.environ.items() if key.startswith("RAPIDAPI_KEY_") and val]
LLAVES_API = list(set(LLAVES_ENV))
indice_llave_actual = 0

# ─── URLS DE EPG (Puedes cambiarlas si caen) ─────────────────────────────────
URL_EPG_EUROPA = "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml"
# Asumimos un XML genérico o gz para América. Reemplaza con tu fuente real.
URL_EPG_AMERICA = "https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz"

# ─── DICCIONARIOS Y CONSTANTES ───────────────────────────────────────────────
DEPORTES_MAP = {"Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 11, "Béisbol": 64, "Hockey": 4, "Voleibol": 23, "Rugby": 12, "Fútbol Americano": 63}
DURACION_POR_DEPORTE = {"Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210, "Béisbol": 210, "Hockey": 150, "Combate": 180, "Deportes": 180, "Ciclismo": 300}

CANALES_AMERICA = ["WIN SPORTS", "DSPORTS", "TYC SPORTS", "TNT SPORTS", "CLARO SPORTS", "TUDN", "ESPN"]
CANALES_EUROPA  = ["EUROSPORT", "GOL", "DAZN"]

PALABRAS_BASURA = ["REPETICIÓN", "REPETICION", "NOTICIAS", "RESUMEN", "PREVIO", "SAQUE LARGO", "MEDIO TIEMPO", "MAGAZINE", "FÚTBOL TOTAL", "SPORTIA", "DOMINGOL", "LÍBERO", "SPORTSCENTER", "STUDIO", "ANALISIS", "ENTREVISTA"]
BANDERAS_VERDES = ["hoy", "previa", "transmisión", "alineaciones", "dónde ver", "recibe a", "se enfrentan", "vivo", "directo"]
BANDERAS_ROJAS  = ["goles", "resumen", "resultado", "derrotó", "empató", "ayer", "polémica por", "venció", "crónica"]

# ─── FUNCIONES DE LIMPIEZA Y UTILIDAD ────────────────────────────────────────
def crear_id_seguro(texto: str) -> str:
    import hashlib
    return hashlib.md5(texto.encode('utf-8')).hexdigest()[:12]

def calcular_similitud(t1: str, t2: str) -> float:
    t1 = re.sub(r'[^A-Z0-9\s]', '', str(t1).upper())
    t2 = re.sub(r'[^A-Z0-9\s]', '', str(t2).upper())
    return SequenceMatcher(None, t1, t2).ratio() * 100

def es_basura(titulo: str) -> bool:
    t = titulo.upper()
    return any(pb in t for pb in PALABRAS_BASURA)

def parse_xmltv_time(time_str: str) -> str:
    """Convierte 20260510200000 -0500 a formato UTC ISO 8601."""
    try:
        dt_obj = datetime.strptime(time_str[:14], "%Y%m%d%H%M%S")
        offset_str = time_str[15:]
        sign = 1 if offset_str == '+' else -1
        hours, mins = int(offset_str[1:3]), int(offset_str[3:5])
        tz = timezone(timedelta(hours=sign*hours, minutes=sign*mins))
        return dt_obj.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ─── FASE 1: FILTRO XTREAM Y RANKING DE CALIDAD ──────────────────────────────
def clasificar_calidad(nombre: str) -> int:
    n = nombre.upper()
    if any(x in n for x in ['4K', 'FHD', '1080', 'HEVC', '50FPS', '60FPS']): return 3
    if any(x in n for x in ['HD', '720']): return 2
    return 1 # SD o sin etiqueta

def es_espn_latam(nombre: str) -> bool:
    n = nombre.upper()
    if "ESPN" not in n: return False
    # Filtro Gringo (Lista Negra)
    if re.search(r'\b(US|USA|UK|NCAA|COLLEGE|ESPNU|SEC|ACCN|ESPN\+)\b', n): return False
    # Filtro Latam (Lista Blanca)
    return bool(re.search(r'\bESPN(\s?[2-7]| PREMIUM| EXTRA)?\b', n))

def mapear_canales_xtream() -> dict:
    """Escanea Xtream y agrupa calidades filtrando basura."""
    log.info("🔍 Escaneando Xtream y rankeando calidades...")
    api_url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    mapa = {c: [] for c in CANALES_AMERICA + CANALES_EUROPA}
    
    try:
        r = requests.get(api_url, timeout=30)
        if r.status_code != 200: return mapa
        
        for s in r.json():
            nombre = s.get("name", "").strip()
            nombre_upper = nombre.upper()
            url_video = f"{XTREAM_URL}/{XTREAM_USER}/{XTREAM_PASS}/{s.get('stream_id')}.ts"
            
            # Match lógico
            canal_asignado = None
            if "ESPN" in nombre_upper:
                if es_espn_latam(nombre): canal_asignado = "ESPN"
            else:
                for c in mapa.keys():
                    if c in nombre_upper and c != "ESPN":
                        canal_asignado = c
                        break
            
            if canal_asignado:
                mapa[canal_asignado].append({
                    "nombre": nombre,
                    "url": url_video,
                    "score": clasificar_calidad(nombre)
                })
        
        # Ordenar por calidad y dejar solo las 2 mejores
        for c in mapa:
            mapa[c].sort(key=lambda x: x["score"], reverse=True)
            mapa[c] = [{"nombre": x["nombre"], "url": x["url"]} for x in mapa[c][:2]]
            
        return mapa
    except Exception as e:
        log.error(f"Error Xtream: {e}")
        return mapa

# ─── FASE 2: EL VALIDADOR DE GOOGLE (SOLUCIÓN AMERICANA) ─────────────────────
def validar_con_google(titulo: str) -> bool:
    """Usa Custom Search para saber si el evento es hoy. Devuelve True si es EN VIVO."""
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        log.warning("Faltan llaves de Google. Se asume FALSO por seguridad.")
        return False
        
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY, "cx": GOOGLE_CX,
        "q": f'"{titulo}" (hoy OR previa OR vivo)',
        "dateRestrict": "d", # Solo últimas 24 horas
        "hl": "es"
    }
    
    try:
        time.sleep(1) # Cuidar Rate Limit
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200: return False
        
        items = r.json().get("items", [])
        if not items: return False # Si internet no habla de esto, es repetición
        
        texto_global = " ".join([i.get("snippet", "") + i.get("title", "") for i in items]).lower()
        
        puntos_verdes = sum(1 for b in BANDERAS_VERDES if b in texto_global)
        puntos_rojos  = sum(1 for b in BANDERAS_ROJAS if b in texto_global)
        
        return puntos_verdes > puntos_rojos
    except:
        return False

# ─── FASE 3: PARSEO XMLTV (EXTRACCIÓN Y ENRUTAMIENTO) ────────────────────────
def extraer_eventos_epg(mapa_xtream: dict) -> list:
    log.info("📡 Descargando y procesando guías EPG (XMLTV)...")
    eventos_aprobados = []
    
    # Función interna para procesar un XML de EPG
    def procesar_xml(url_xml, region):
        try:
            r = requests.get(url_xml, stream=True, timeout=20)
            if r.status_code != 200: return
            
            f = gzip.GzipFile(fileobj=io.BytesIO(r.content)) if url_xml.endswith(".gz") else io.BytesIO(r.content)
            
            for event, elem in ET.iterparse(f, events=("end",)):
                if elem.tag == "programme":
                    canal_id = elem.attrib.get("channel", "").upper()
                    
                    # Buscar si el canal nos importa
                    canal_logico = next((c for c in (CANALES_EUROPA if region == "EU" else CANALES_AMERICA) if c.replace(" ", "") in canal_id), None)
                    
                    if canal_logico and mapa_xtream.get(canal_logico): # Solo procesar si tenemos el canal HOY en Xtream
                        titulo_elem = elem.find("title")
                        desc_elem = elem.find("desc")
                        titulo = titulo_elem.text if titulo_elem is not None else ""
                        desc = desc_elem.text if desc_elem is not None else ""
                        
                        if es_basura(titulo): 
                            elem.clear()
                            continue
                            
                        hora_str = elem.attrib.get("start", "")
                        
                        # --- ENRUTADOR ---
                        es_valido = False
                        if region == "EU":
                            if "DIRECTO" in titulo.upper() or "VIVO" in titulo.upper() or "DIRECTO" in desc.upper():
                                es_valido = True
                        else:
                            # Tubería Americana: Validar con Google
                            titulo_limpio = re.sub(r'\b(HD|SD|FHD|4K|VIVO|DIRECTO)\b', '', titulo, flags=re.IGNORECASE).strip()
                            es_valido = validar_con_google(titulo_limpio)
                        
                        if es_valido:
                            cat = "Fútbol" if " vs " in titulo.lower() else "Deportes"
                            eventos_aprobados.append({
                                "id": f"epg_{crear_id_seguro(titulo+hora_str)}",
                                "titulo": titulo.replace("DIRECTO", "").strip(" -:"),
                                "torneo": canal_logico,
                                "categoria": cat,
                                "hora_utc": parse_xmltv_time(hora_str),
                                "duracion_min": 120, # Default si no hay 'stop'
                                "logo_local": "", "logo_visitante": "", "banner": "",
                                "tier": 2,
                                "fuentes": mapa_xtream[canal_logico]
                            })
                    elem.clear() # Liberar memoria RAM
        except Exception as e:
            log.error(f"Error procesando {region}: {e}")

    procesar_xml(URL_EPG_EUROPA, "EU")
    procesar_xml(URL_EPG_AMERICA, "AM")
    
    return eventos_aprobados

# ─── FASE 4: SOFASCORE Y FUSIÓN FINAL ────────────────────────────────────────
def hacer_peticion_segura(url: str, params: dict):
    global LLAVES_API, indice_llave_actual
    if not LLAVES_API: return None
    intentos = 0
    while intentos < len(LLAVES_API) * 2 and LLAVES_API:
        llave = LLAVES_API[indice_llave_actual]
        try:
            time.sleep(1.5)
            r = requests.get(url, headers={"x-rapidapi-key": llave, "x-rapidapi-host": RAPIDAPI_HOST}, params=params, timeout=15)
            if r.status_code == 200: return r
            if r.status_code == 429:
                time.sleep(2)
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
            elif r.status_code == 403:
                LLAVES_API.pop(indice_llave_actual)
                if not LLAVES_API: return None
                indice_llave_actual = indice_llave_actual % len(LLAVES_API)
            intentos += 1
        except:
            indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
            intentos += 1
    return None

def main():
    log.info("=== Iniciando Pipeline Maestro EPG V9 ===")
    
    # 1. Obtener Xtream pre-mapeado
    mapa_xtream = mapear_canales_xtream()
    
    # 2. Descargar API SofaScore
    eventos_finales = []
    log.info("Descargando SofaScore...")
    for deporte, sport_id in DEPORTES_MAP.items():
        url = f"https://{RAPIDAPI_HOST}/v1/events/schedule/date"
        r = hacer_peticion_segura(url, {"date": FECHA_HOY, "sport_id": sport_id, "timezone": -5})
        if not r: continue
        for ev in r.json().get("data", []):
            try:
                unix = ev.get("startTimestamp")
                if not unix: continue
                titulo = f"{ev.get('homeTeam', {}).get('name', '')} vs {ev.get('awayTeam', {}).get('name', '')}"
                if " vs " not in titulo: titulo = ev.get("name", "")
                
                eventos_finales.append({
                    "id": str(ev.get("id")),
                    "titulo": titulo,
                    "torneo": ev.get("tournament", {}).get("name", ""),
                    "categoria": deporte,
                    "hora_utc": datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": DURACION_POR_DEPORTE.get(deporte, 150),
                    "logo_local": f"https://api.sofascore.app/api/v1/team/{ev.get('homeTeam', {}).get('id')}/image",
                    "logo_visitante": f"https://api.sofascore.app/api/v1/team/{ev.get('awayTeam', {}).get('id')}/image" if ev.get('awayTeam') else "",
                    "tier": 2,
                    "fuentes": [] # Se llenarán en el cruce de cubo temporal si lo deseas, o las fusionamos.
                })
            except: continue

    # 3. Extraer y Validar eventos EPG
    eventos_epg = extraer_eventos_epg(mapa_xtream)
    log.info(f"Eventos EPG aprobados (EU + Google Validados): {len(eventos_epg)}")

    # 4. Motor de Fusión (Deduplicación)
    for e_epg in eventos_epg:
        match = False
        for e_sofa in eventos_finales:
            # Si se parecen > 75%, fusionamos la fuente de IPTV al evento de SofaScore
            if calcular_similitud(e_epg["titulo"], e_sofa["titulo"]) > 75.0:
                match = True
                urls_existentes = [f["url"] for f in e_sofa["fuentes"]]
                for fuente in e_epg["fuentes"]:
                    if fuente["url"] not in urls_existentes:
                        e_sofa["fuentes"].append(fuente)
                break
        
        # Si es un deporte de nicho, lo añadimos como evento nuevo
        if not match:
            eventos_finales.append(e_epg)

    # 5. Ordenar cronológicamente
    eventos_finales.sort(key=lambda x: x["hora_utc"])

    # 6. Guardar JSON
    with open(ARCHIVO_JSON, "w", encoding="utf-8") as f:
        json.dump(eventos_finales, f, ensure_ascii=False, indent=2)
        
    log.info(f"✅ ¡Proceso Terminado! Total de eventos listos: {len(eventos_finales)}")

if __name__ == "__main__":
    main()
