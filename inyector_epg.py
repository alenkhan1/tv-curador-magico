import os
import re
import json
import gzip
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher

# ─── CONFIGURACIÓN DE LOGS Y ENTORNO ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("inyector_maestro")

XTREAM_URL   = os.environ.get("XTREAM_URL", "http://tu-servidor.com")
XTREAM_USER  = os.environ.get("XTREAM_USER", "usuario")
XTREAM_PASS  = os.environ.get("XTREAM_PASS", "password")

ARCHIVO_EVENTOS = "eventos_hoy.json"

# ─── MAPEOS DE CANALES ───────────────────────────────────────────────────────
# XMLTV procesará los canales latinos.
MAPEO_XML_CANALES = {
    "Win.Sports+.co": "Win Sports +",
    "DSPORTS.+(DTS+).co": "DSports +",
    "DSPORTS.2.(DTS2).co": "DSports 2",
    "TyC.Sports.International(TYCS).co": "TyC Sports"
}

# Movistar API procesará los canales europeos (Precisión 100% En Vivo)
CANALES_MOVISTAR = {
    "Eurosport 1": "ESP",
    "Eurosport 2": "ESP2"
}

FIRMAS_XTREAM = {
    "Win Sports +": ["WIN SPORTS+", "WIN SPORTS +", "WIN+"],
    "DSports +": ["DSPORTS+", "DSPORTS +", "DIRECTV SPORTS+"],
    "DSports 2": ["DSPORTS 2", "DSPORTS2", "DIRECTV SPORTS 2"],
    "TyC Sports": ["TYC SPORTS", "TYC"],
    "Eurosport 1": ["EUROSPORT 1", "EUROSPORT 1HD", "EUROSPORT HD", "EUROSPORT ESPAÑA"],
    "Eurosport 2": ["EUROSPORT 2", "EUROSPORT 2HD"]
}

URLS_XMLTV = [
    "https://epgshare01.online/epgshare01/epg_ripper_CO1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz"
]

# ─── FUNCIONES DE AYUDA Y FILTROS ────────────────────────────────────────────
def parsear_tiempo_xml(tiempo_str: str) -> datetime:
    t_str = tiempo_str[:20]
    dt = datetime.strptime(t_str, "%Y%m%d%H%M%S %z")
    return dt.astimezone(timezone.utc)

def pasa_filtros_logicos(titulo: str, subtitulo: str, descripcion: str, categoria: str, inicio_dt: datetime, fin_dt: datetime) -> bool:
    ahora_dt = datetime.now(timezone.utc)
    texto_completo = f"{titulo} {subtitulo} {descripcion}".upper()
    cat_upper = categoria.upper()

    if fin_dt <= ahora_dt: return False
    if inicio_dt.date() > ahora_dt.date(): return False

    duracion_min = (fin_dt - inicio_dt).total_seconds() / 60
    if duracion_min < 65: return False

    if any(c in cat_upper for c in ["TALK", "NEWS", "MAGAZINE", "DOCUMENTARY", "NOTICIAS", "BUSINESS", "WEATHER"]):
        return False

    basura = [
        "REPETICIÓN", "REPETICION", "CLÁSICOS", "CLASICOS", "TEMPORADA", 
        "PARTE", "RESUMEN", "ESPECIAL", "NOTICIAS", "SAQUE LARGO", "PREVIO", 
        "MEDIO TIEMPO", "LA RUTA", "SPORTIA", "DOMINGOL", "LÍBERO", "PLANETA",
        "PUEDE PASAR", "FÚTBOL TOTAL", "NOTICIAS LITE", "NOTICIAS NITE"
    ]
    if any(b in texto_completo for b in basura): return False

    es_duelo = " VS " in texto_completo or " VS. " in texto_completo or " - " in titulo
    es_evento_especial = any(kw in texto_completo for kw in [
        "ETAPA", "FINAL", "GRAN PREMIO", "RONDA", "CLASIFICACIÓN", 
        "SESIÓN", "CHAMPIONSHIP", "TORNEO", "VUELTA"
    ])
    
    if not (es_duelo or es_evento_especial): return False

    return True

def crear_id_seguro(titulo: str, hora: str) -> str:
    import hashlib
    hash_obj = hashlib.md5((titulo + hora).encode('utf-8'))
    return f"local_{hash_obj.hexdigest()[:10]}"

def calcular_similitud(texto1: str, texto2: str) -> float:
    t1 = re.sub(r'[^A-Z0-9\s]', '', str(texto1).upper())
    t2 = re.sub(r'[^A-Z0-9\s]', '', str(texto2).upper())
    return SequenceMatcher(None, t1, t2).ratio() * 100

# ─── FASE 1: XTREAM ──────────────────────────────────────────────────────────
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

# ─── FASE 2: EXTRACCIÓN XMLTV (Canales Latinos) ──────────────────────────────
def descargar_y_procesar_xmltv() -> list:
    log.info("📡 Fase 2A: Procesando EPGShare01 (XMLTV)...")
    eventos_validos = []
    ids_interes = list(MAPEO_XML_CANALES.keys())

    for url_gz in URLS_XMLTV:
        try:
            r = requests.get(url_gz, timeout=20)
            xml_content = gzip.decompress(r.content)
            root = ET.fromstring(xml_content)
            
            for programme in root.findall('programme'):
                canal_xml = programme.get('channel')
                
                if canal_xml not in ids_interes:
                    continue
                    
                nombre_logico = MAPEO_XML_CANALES[canal_xml]
                
                title_node = programme.find('title')
                sub_node = programme.find('sub-title')
                desc_node = programme.find('desc')
                cat_node = programme.find('category')
                
                titulo = title_node.text if title_node is not None and title_node.text else ""
                subtitulo = sub_node.text if sub_node is not None and sub_node.text else ""
                descripcion = desc_node.text if desc_node is not None and desc_node.text else ""
                categoria = cat_node.text if cat_node is not None and cat_node.text else ""
                
                start_str = programme.get('start')
                end_str = programme.get('stop')
                
                try:
                    inicio_dt = parsear_tiempo_xml(start_str)
                    fin_dt = parsear_tiempo_xml(end_str)
                except Exception: continue

                if pasa_filtros_logicos(titulo, subtitulo, descripcion, categoria, inicio_dt, fin_dt):
                    duracion_min = int((fin_dt - inicio_dt).total_seconds() / 60)
                    
                    titulo_final = titulo.strip()
                    if subtitulo and subtitulo.upper() not in titulo.upper():
                        titulo_final = f"{titulo_final} - {subtitulo.strip()}"
                        
                    eventos_validos.append({
                        "titulo": titulo_final,
                        "canal": nombre_logico,
                        "hora_utc": inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "duracion_min": duracion_min
                    })
                    
        except Exception as e:
            log.error(f"Error procesando XMLTV {url_gz}: {e}")
            
    return eventos_validos

# ─── FASE 2.5: EXTRACCIÓN MOVISTAR (Canales Europeos) ────────────────────────
def extraer_eventos_movistar() -> list:
    log.info("📡 Fase 2B: Extrayendo canales desde API Movistar+ (Solo En Vivo)...")
    eventos_validos = []
    
    ahora = datetime.now(timezone.utc)
    fecha_movistar = ahora.strftime("%Y-%m-%dT00:00:00")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    for nombre_canal, id_movistar in CANALES_MOVISTAR.items():
        url = f"https://soteroc-pf.cdn.sve.video.telefonicaservices.com/service/contents/webplayer/CASUAL/epg?from={fecha_movistar}&span=1&channel={id_movistar}&network=movistarplus&v=10&mdrm=true&tlsstream=true&demarcation=0&startover=U7D"
        
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200: continue
            
            programas = r.json()
            for prog in programas:
                tags = prog.get("Tags", "") 
                
                # Filtro absoluto: Solo directo
                if "DI" in tags or "DIRECTO" in str(tags).upper():
                    titulo = prog.get("Title", "Sin Título")
                    inicio_ms = prog.get("StartTime", 0)
                    fin_ms = prog.get("EndTime", 0)
                    
                    inicio_dt = datetime.fromtimestamp(inicio_ms / 1000, timezone.utc)
                    fin_dt = datetime.fromtimestamp(fin_ms / 1000, timezone.utc)
                    duracion_min = int((fin_dt - inicio_dt).total_seconds() / 60)
                    
                    eventos_validos.append({
                        "titulo": titulo.strip(),
                        "canal": nombre_canal,
                        "hora_utc": inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "duracion_min": abs(duracion_min)
                    })
        except Exception as e:
            log.error(f"Error procesando {nombre_canal} en API Movistar: {e}")
            
    return eventos_validos

# ─── FASE 3: MOTOR DE FUSIÓN ─────────────────────────────────────────────────
def inyectar_eventos():
    log.info("=== INICIANDO INYECTOR EPG MAESTRO ===")
    
    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error(f"No existe {ARCHIVO_EVENTOS}. Ejecuta primero el curador.")
        return
        
    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        eventos_app = json.load(f)
        
    urls_disponibles = obtener_urls_xtream()
    
    # 1. Recolectamos eventos de ambas fuentes
    eventos_xml = descargar_y_procesar_xmltv()
    eventos_movistar = extraer_eventos_movistar()
    
    eventos_totales_web = eventos_xml + eventos_movistar
    log.info(f"Total eventos reales en vivo listos para procesar: {len(eventos_totales_web)}")
    
    nuevos_creados = 0
    fuentes_añadidas = 0
    
    # 2. Fusión contra los eventos de tu App
    for ev_web in eventos_totales_web:
        canal = ev_web["canal"]
        fuentes_iptv = urls_disponibles.get(canal, [])
        
        if not fuentes_iptv: continue
            
        match_encontrado = False
        
        for ea in eventos_app:
            if calcular_similitud(ev_web["titulo"], ea["titulo"]) > 75.0:
                match_encontrado = True
                urls_actuales = [f["url"] for f in ea["fuentes"]]
                for fuente_nueva in fuentes_iptv:
                    if fuente_nueva["url"] not in urls_actuales:
                        ea["fuentes"].append(fuente_nueva)
                        fuentes_añadidas += 1
                break
                
        if not match_encontrado:
            t_up = ev_web["titulo"].upper()
            cat = "Ciclismo" if any(kw in t_up for kw in ["ETAPA", "VUELTA", "TOUR", "GIRO"]) else "Tenis" if "ATP" in t_up or "WTA" in t_up else "Fútbol" if " VS " in t_up or "-" in t_up else "Deportes"
            
            nuevo_evento = {
                "id": crear_id_seguro(ev_web["titulo"], ev_web["hora_utc"]),
                "titulo": ev_web["titulo"],
                "torneo": canal,
                "categoria": cat,
                "hora_utc": ev_web["hora_utc"],
                "duracion_min": ev_web["duracion_min"],
                "logo_local": "", "logo_visitante": "", "banner": "",
                "tier": 2,
                "fuentes": fuentes_iptv
            }
            eventos_app.append(nuevo_evento)
            nuevos_creados += 1
            
    eventos_app.sort(key=lambda x: x.get("hora_utc", ""))
    
    with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
        json.dump(eventos_app, f, ensure_ascii=False, indent=2)
        
    log.info("=== INYECCIÓN FINALIZADA ===")
    log.info(f"Nuevos eventos exclusivos inyectados: {nuevos_creados}")
    log.info(f"Nuevas fuentes añadidas a eventos globales: {fuentes_añadidas}")

if __name__ == "__main__":
    inyectar_eventos()
