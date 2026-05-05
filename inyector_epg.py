import os
import re  # <--- Esta es la línea que faltaba
import json
import time
import gzip
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher

# ─── CONFIGURACIÓN DE LOGS Y ENTORNO ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("inyector_xmltv")

XTREAM_URL   = os.environ.get("XTREAM_URL", "http://tu-servidor.com")
XTREAM_USER  = os.environ.get("XTREAM_USER", "usuario")
XTREAM_PASS  = os.environ.get("XTREAM_PASS", "password")

ARCHIVO_EVENTOS = "eventos_hoy.json"

# ─── MAPEO ESTRICTO DE SUBCANALES ────────────────────────────────────────────
# 1. Relacionamos el ID del XML con un Nombre Lógico para nuestra App
MAPEO_XML_CANALES = {
    "Win.Sports+.co": "Win Sports +",                        #[cite: 12]
    "Win.Sports.co": "Win Sports Básico",                    #[cite: 12]
    "DSPORTS.+(DTS+).co": "DSports +",                       #[cite: 12]
    "DSPORTS.2.(DTS2).co": "DSports 2",                      #[cite: 12]
    "TyC.Sports.International(TYCS).co": "TyC Sports",       #[cite: 12]
    "Eurosport.es": "Eurosport 1",                           #[cite: 10]
    "Eurosport.2.es": "Eurosport 2"                          #[cite: 10]
}

# 2. Relacionamos el Nombre Lógico con la búsqueda en tu servidor Xtream
FIRMAS_XTREAM = {
    "Win Sports +": ["WIN SPORTS+", "WIN SPORTS +", "WIN+"],
    "Win Sports Básico": ["WIN SPORTS", "WIN SPORT"], # Se filtrará el "+" más abajo
    "DSports +": ["DSPORTS+", "DSPORTS +", "DIRECTV SPORTS+"],
    "DSports 2": ["DSPORTS 2", "DSPORTS2", "DIRECTV SPORTS 2"],
    "TyC Sports": ["TYC SPORTS", "TYC"],
    "Eurosport 1": ["EUROSPORT 1", "EUROSPORT 1HD", "EUROSPORT HD"],
    "Eurosport 2": ["EUROSPORT 2"]
}

URLS_XMLTV = [
    "https://epgshare01.online/epgshare01/epg_ripper_CO1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz"
]

# ─── FILTROS LÓGICOS INTELIGENTES ────────────────────────────────────────────
def pasa_filtros_logicos(titulo: str, categoria: str, inicio_dt: datetime, fin_dt: datetime) -> bool:
    ahora_dt = datetime.now(timezone.utc)
    t_upper = titulo.upper()
    cat_upper = categoria.upper()

    # 1. Filtro del Reloj Estricto: Si ya terminó o es de mañana, se descarta.
    if fin_dt <= ahora_dt: return False
    if inicio_dt.date() > ahora_dt.date(): return False

    # 2. Filtro de Duración: Si dura menos de 65 mins, es un resumen.
    duracion_min = (fin_dt - inicio_dt).total_seconds() / 60
    if duracion_min < 65: return False

    # 3. Filtro de Metadatos (Categoría)
    if any(c in cat_upper for c in ["TALK", "NEWS", "MAGAZINE", "DOCUMENTARY", "NOTICIAS"]):
        return False

    # 4. Diccionario Negativo (Basura y Relleno)
    basura = [
        "REPETICIÓN", "REPETICION", "CLÁSICOS", "CLASICOS", "TEMPORADA", 
        "PARTE", "RESUMEN", "ESPECIAL", "NOTICIAS", "SAQUE LARGO", "PREVIO", 
        "MEDIO TIEMPO", "LA RUTA", "SPORTIA", "DOMINGOL", "LÍBERO", "PLANETA"
    ]
    if any(b in t_upper for b in basura): return False

    # 5. Regla Competitiva ("VS" o palabras clave de carrera/etapa)
    es_duelo = " VS " in t_upper or " VS. " in t_upper
    es_carrera = any(kw in t_upper for kw in ["ETAPA", "FINAL", "GRAN PREMIO", "RONDA", "CLASIFICACIÓN"])
    
    if not (es_duelo or es_carrera): return False

    return True

def crear_id_seguro(titulo: str, hora: str) -> str:
    import hashlib
    hash_obj = hashlib.md5((titulo + hora).encode('utf-8'))
    return f"local_{hash_obj.hexdigest()[:10]}"

def calcular_similitud(texto1: str, texto2: str) -> float:
    t1 = re.sub(r'[^A-Z0-9\s]', '', str(texto1).upper())
    t2 = re.sub(r'[^A-Z0-9\s]', '', str(texto2).upper())
    return SequenceMatcher(None, t1, t2).ratio() * 100

# ─── FASE 1: OBTENER URLS ACTIVAS DE XTREAM ──────────────────────────────────
def obtener_urls_xtream() -> dict:
    log.info("🔍 Fase 1: Escaneando servidor Xtream para URLs dinámicas...")
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    
    mapa_urls = {canal: [] for canal in FIRMAS_XTREAM.keys()}
    
    try:
        r = requests.get(api_url, timeout=30)
        if r.status_code != 200: return mapa_urls
        
        streams = r.json()
        for s in streams:
            nombre = s.get("name", "").upper()
            url_video = f"{base_url}/{XTREAM_USER}/{XTREAM_PASS}/{s.get('stream_id')}.ts"
            
            for canal_logico, firmas in FIRMAS_XTREAM.items():
                if any(firma in nombre for firma in firmas):
                    # Protección para no meter Win+ en Win Básico
                    if canal_logico == "Win Sports Básico" and "+" in nombre:
                        continue 
                    mapa_urls[canal_logico].append({
                        "nombre": s.get("name", "").strip(),
                        "url": url_video
                    })
                    break 
                    
        for c, urls in mapa_urls.items():
            if urls: log.info(f"   └─ {c}: Encontradas {len(urls)} calidades activas.")
        return mapa_urls
    except Exception as e:
        log.error(f"Error escaneando Xtream: {e}")
        return mapa_urls

# ─── FASE 2: PROCESAMIENTO XMLTV ─────────────────────────────────────────────
def descargar_y_procesar_xmltv() -> list:
    log.info("📡 Fase 2: Descargando y procesando EPGShare01 (XMLTV)...")
    eventos_validos = []
    ids_interes = list(MAPEO_XML_CANALES.keys())

    for url_gz in URLS_XMLTV:
        log.info(f"   -> Descargando {url_gz.split('/')[-1]}...")
        try:
            r = requests.get(url_gz, timeout=20)
            xml_content = gzip.decompress(r.content)
            root = ET.fromstring(xml_content)
            
            for programme in root.findall('programme'):
                canal_xml = programme.get('channel')
                
                # Filtrado inmediato: Solo leemos los canales que mapeamos[cite: 10, 12]
                if canal_xml not in ids_interes:
                    continue
                    
                nombre_logico = MAPEO_XML_CANALES[canal_xml]
                
                # Extracción de datos del XML
                title_elem = programme.find('title')
                cat_elem = programme.find('category')
                
                titulo = title_elem.text if title_elem is not None else ""
                categoria = cat_elem.text if cat_elem is not None else ""
                
                # Manejo de tiempo XMLTV (Ej: "20260505190000 +0000")
                start_str = programme.get('start')
                end_str = programme.get('stop')
                
                try:
                    # Cortamos los primeros 14 caracteres y forzamos zona horaria UTC
                    inicio_dt = datetime.strptime(start_str[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                    fin_dt = datetime.strptime(end_str[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                except: continue

                # Aplicar nuestros filtros de inteligencia
                if pasa_filtros_logicos(titulo, categoria, inicio_dt, fin_dt):
                    duracion_min = int((fin_dt - inicio_dt).total_seconds() / 60)
                    eventos_validos.append({
                        "titulo": titulo.strip(),
                        "canal": nombre_logico,
                        "hora_utc": inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "duracion_min": duracion_min
                    })
                    
        except Exception as e:
            log.error(f"Error procesando XMLTV {url_gz}: {e}")
            
    return eventos_validos

# ─── FASE 3: MOTOR DE FUSIÓN ─────────────────────────────────────────────────
def inyectar_eventos():
    log.info("=== INICIANDO INYECTOR EPG V2 (XMLTV) ===")
    
    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error(f"No existe {ARCHIVO_EVENTOS}. Ejecuta primero el curador.")
        return
        
    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        eventos_app = json.load(f)
        
    urls_disponibles = obtener_urls_xtream()
    eventos_xml = descargar_y_procesar_xmltv()
    log.info(f"Total eventos reales en vivo filtrados: {len(eventos_xml)}")
    
    nuevos_creados = 0
    fuentes_añadidas = 0
    
    for ex in eventos_xml:
        canal = ex["canal"]
        fuentes_iptv = urls_disponibles.get(canal, [])
        
        # Si Xtream no nos da este canal hoy o se cayó, ignoramos el evento
        if not fuentes_iptv: continue
            
        match_encontrado = False
        
        # Buscar si el evento ya existe en tu app
        for ea in eventos_app:
            if calcular_similitud(ex["titulo"], ea["titulo"]) > 75.0:
                match_encontrado = True
                urls_actuales = [f["url"] for f in ea["fuentes"]]
                for fuente_nueva in fuentes_iptv:
                    if fuente_nueva["url"] not in urls_actuales:
                        ea["fuentes"].append(fuente_nueva)
                        fuentes_añadidas += 1
                break
                
        # Si no existe, es evento LOCAL/EXCLUSIVO. Lo creamos.
        if not match_encontrado:
            cat = "Ciclismo" if any(kw in ex["titulo"].upper() for kw in ["ETAPA", "VUELTA", "TOUR"]) else "Fútbol" if " VS " in ex["titulo"].upper() else "Deportes"
            
            nuevo_evento = {
                "id": crear_id_seguro(ex["titulo"], ex["hora_utc"]),
                "titulo": ex["titulo"],
                "torneo": canal, # El nombre del canal da contexto
                "categoria": cat,
                "hora_utc": ex["hora_utc"],
                "duracion_min": ex["duracion_min"],
                "logo_local": "", "logo_visitante": "", "banner": "",
                "tier": 2,
                "fuentes": fuentes_iptv
            }
            eventos_app.append(nuevo_evento)
            nuevos_creados += 1
            
    # Ordenar cronológicamente para mantener el orden en la app
    eventos_app.sort(key=lambda x: x.get("hora_utc", ""))
    
    with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
        json.dump(eventos_app, f, ensure_ascii=False, indent=2)
        
    log.info("=== INYECCIÓN FINALIZADA ===")
    log.info(f"Nuevos eventos exclusivos inyectados: {nuevos_creados}")
    log.info(f"Nuevas fuentes añadidas a eventos globales: {fuentes_añadidas}")

if __name__ == "__main__":
    inyectar_eventos()
