import os
import json
import re
import time
import logging
import requests
from datetime import datetime, timezone
from difflib import SequenceMatcher

# ─── CONFIGURACIÓN DE LOGS Y ENTORNO ─────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("inyector")

XTREAM_URL   = os.environ.get("XTREAM_URL", "http://tu-servidor.com")
XTREAM_USER  = os.environ.get("XTREAM_USER", "usuario")
XTREAM_PASS  = os.environ.get("XTREAM_PASS", "password")

ARCHIVO_EVENTOS = "eventos_hoy.json"

# ─── FIRMAS DE CANALES (Búsqueda dinámica en Xtream) ─────────────────────────
# Si la lista cambia, el script buscará canales que contengan estas palabras.
FIRMAS_CANALES = {
    "Win Sports": ["WIN SPORTS", "WIN SPORT", "WIN+"],
    "DSports": ["DSPORTS", "DIRECTV SPORTS", "D SPORTS", "DTV SPORTS"],
    "TyC Sports": ["TYC SPORTS", "TYC"],
    "Eurosport": ["EUROSPORT"]
}

# ─── FUNCIONES DE LIMPIEZA Y AYUDA ───────────────────────────────────────────
def calcular_similitud(texto1: str, texto2: str) -> float:
    t1 = re.sub(r'[^A-Z0-9\s]', '', str(texto1).upper())
    t2 = re.sub(r'[^A-Z0-9\s]', '', str(texto2).upper())
    return SequenceMatcher(None, t1, t2).ratio() * 100

def es_basura(titulo: str) -> bool:
    """Filtro estricto para descartar repeticiones y programas de estudio."""
    t = titulo.upper()
    palabras_basura = [
        "REPETICIÓN", "REPETICION", "NOTICIAS", "RESUMEN", "PREVIO", 
        "SAQUE LARGO", "MEDIO TIEMPO", "LA RUTA DEL MUNDIAL", "DESPIERTA WIN",
        "PLANETA FÚTBOL", "MAGAZINE", "FÚTBOL TOTAL", "DE FÚTBOL SE HABLA ASÍ",
        "PUEDE PASAR", "SUPERFÚTBOL", "DALE AL MEDIO", "SPORTIA", "DOMINGOL",
        "LÍBERO", "PRESIÓN ALTA"
    ]
    # Si el título es solo el nombre del canal (muy común en los JSON como marcador)
    if t.strip() in ["DSPORTS", "WIN SPORTS", "EUROSPORT"]: return True
    
    return any(pb in t for pb in palabras_basura)

def crear_id_seguro(titulo: str) -> str:
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return f"local_{hash_obj.hexdigest()[:10]}"

# ─── FASE 1: OBTENER URLS ACTIVAS DE XTREAM ──────────────────────────────────
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
                    break # Encontrado, pasamos al siguiente stream
                    
        for c, urls in mapa_urls.items():
            log.info(f"   └─ {c}: Encontradas {len(urls)} calidades/enlaces activos.")
        return mapa_urls
    except Exception as e:
        log.error(f"Error escaneando Xtream: {e}")
        return mapa_urls

# ─── FASE 2: SCRAPERS DE LOS 4 CANALES ───────────────────────────────────────
headers_web = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def extraer_tyc() -> list:
    log.info("📡 Extrayendo TyC Sports (HTML Regex)...")
    eventos = []
    try:
        html = requests.get("https://play.tycsports.com/proximos-eventos.html", headers=headers_web, timeout=15).text
        # Busca: data-start="1777985700000" data-end="1777991400000" data-eventname="Sportivo Luqueño..."
        patron = r'data-start="(\d+)"\s+data-end="(\d+)"\s+data-eventname="([^"]+)"'
        coincidencias = re.findall(patron, html)
        
        for start_ms, end_ms, titulo in coincidencias:
            if es_basura(titulo): continue
            
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

def extraer_eurosport() -> list:
    log.info("📡 Extrayendo Eurosport (HTML Next.js Regex)...")
    eventos = []
    try:
        html = requests.get("https://www.eurosport.es/watch/schedule.shtml", headers=headers_web, timeout=15).text
        patron = r'"programName":"([^"]+)".*?"startTime":"([^"]+)".*?"endTime":"([^"]+)"'
        coincidencias = re.findall(patron, html)
        vistos = set()
        
        for titulo, inicio, fin in coincidencias:
            if es_basura(titulo) or titulo in vistos: continue
            vistos.add(titulo)
            
            try:
                inicio_dt = datetime.strptime(inicio, "%Y-%m-%dT%H:%M:%S.000Z")
                fin_dt = datetime.strptime(fin, "%Y-%m-%dT%H:%M:%S.000Z")
                duracion = int((fin_dt - inicio_dt).total_seconds() / 60)
                
                eventos.append({
                    "titulo": titulo.strip(), "canal": "Eurosport",
                    "hora_utc": inicio_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": abs(duracion)
                })
            except: continue
    except Exception as e: log.error(f"Error Eurosport: {e}")
    return eventos

def extraer_dsports() -> list:
    log.info("📡 Extrayendo DSports (API Automática)...")
    eventos = []
    
    # Usamos EXACTAMENTE la URL que tú investigaste y encontraste
    url_dsports = "https://epg.tbxapis.com/v0/epg/external/entries"
    
    try:
        # El script hace la conexión a internet automáticamente
        respuesta = requests.get(url_dsports, timeout=15)
        
        if respuesta.status_code != 200:
            log.error(f"Error accediendo a la API de DSports. Código: {respuesta.status_code}")
            return eventos
            
        data = respuesta.json()
            
        # El JSON empieza con "data" y luego "programs"
        for prog in data.get("data", {}).get("programs", []):
            titulo = prog.get("Title", "")
            if es_basura(titulo): continue
            
            try:
                # Reemplazamos +00:00 por Z para estandarizar con tu app
                hora_limpia = prog.get("StartDate").replace("+00:00", "Z")
                # Parseamos el tiempo para calcular la duración
                inicio = datetime.fromisoformat(prog.get("StartDate"))
                fin = datetime.fromisoformat(prog.get("EndDate"))
                duracion = int((fin - inicio).total_seconds() / 60)
                
                eventos.append({
                    "titulo": titulo.strip(), 
                    "canal": "DSports", 
                    "hora_utc": hora_limpia, 
                    "duracion_min": duracion
                })
            except Exception as e:
                continue # Si hay error en la hora de un evento, saltamos al siguiente
                
    except Exception as e: 
        log.error(f"Error en la conexión con DSports: {e}")
        
    return eventos

def extraer_winplay() -> list:
    log.info("📡 Extrayendo Win Sports (API JSON)...")
    eventos = []
    try:
        # Generar fecha actual para la URL dinámica
        ahora = datetime.now(timezone.utc)
        start_date = f"{ahora.strftime('%Y-%m-%d')}T00:00:00.000Z"
        end_date = f"{ahora.strftime('%Y-%m-%d')}T23:59:59.000Z"
        # URL basada en tu captura
        url = f"https://unity.tbxapis.com/v0/sections/679cf2e59f9c761c5888766a/components/67b4dc7c7bcf686c2de6142d/items/6761b6ab55adef022ee97d166a561d048728db7c786b53b0941d0dd9_eses_p0_es.json?pageSize=25&page=1&fromEpg={start_date}&toEpg={end_date}"
        
        datos = requests.get(url, timeout=15).json()
        for canal in datos.get("result", []):
            for prog in canal.get("content", {}).get("epg", []):
                titulo = prog.get("programName", "")
                if es_basura(titulo): continue
                
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
    log.info("=== INICIANDO INYECTOR EPG LIGERO ===")
    
    # 1. Cargar el archivo pesado (SofaScore)
    if not os.path.exists(ARCHIVO_EVENTOS):
        log.error(f"No existe {ARCHIVO_EVENTOS}. Ejecuta primero el curador principal.")
        return
        
    with open(ARCHIVO_EVENTOS, "r", encoding="utf-8") as f:
        eventos_app = json.load(f)
        
    urls_disponibles = obtener_urls_xtream()
    
    # 2. Recolectar de la web
    eventos_web = extraer_tyc() + extraer_eurosport() + extraer_winplay() + extraer_dsports()
    log.info(f"Total eventos deportivos válidos raspados de la web: {len(eventos_web)}")
    
    # 3. Fusión
    nuevos_creados = 0
    fuentes_añadidas = 0
    
    for ew in eventos_web:
        canal = ew["canal"]
        fuentes_iptv = urls_disponibles.get(canal, [])
        
        # Si Xtream no nos da este canal hoy, ignoramos el evento
        if not fuentes_iptv: continue
            
        match_encontrado = False
        
        # Buscar si el evento ya existe en tu app
        for ea in eventos_app:
            if calcular_similitud(ew["titulo"], ea["titulo"]) > 75.0:
                match_encontrado = True
                # Inyectar fuentes si no existen ya
                urls_actuales = [f["url"] for f in ea["fuentes"]]
                for fuente_nueva in fuentes_iptv:
                    if fuente_nueva["url"] not in urls_actuales:
                        ea["fuentes"].append(fuente_nueva)
                        fuentes_añadidas += 1
                break
                
        # Si no existe, es evento LOCAL exclusivo. Lo creamos.
        if not match_encontrado:
            # Determinamos categoría simple
            cat = "Ciclismo" if "ETAPA" in ew["titulo"].upper() else "Fútbol" if " VS " in ew["titulo"].upper() else "Deportes"
            
            nuevo_evento = {
                "id": crear_id_seguro(ew["titulo"] + ew["hora_utc"]),
                "titulo": ew["titulo"],
                "torneo": canal, # Usamos el canal como torneo para darle contexto
                "categoria": cat,
                "hora_utc": ew["hora_utc"],
                "duracion_min": ew["duracion_min"],
                "logo_local": "", "logo_visitante": "", "banner": "",
                "tier": 2,
                "fuentes": fuentes_iptv
            }
            eventos_app.append(nuevo_evento)
            nuevos_creados += 1
            
    # Ordenar cronológicamente para que la app lo lea bien
    eventos_app.sort(key=lambda x: x.get("hora_utc", ""))
    
    # 4. Guardar archivo final
    with open(ARCHIVO_EVENTOS, "w", encoding="utf-8") as f:
        json.dump(eventos_app, f, ensure_ascii=False, indent=2)
        
    log.info("=== INYECCIÓN FINALIZADA ===")
    log.info(f"Eventos nuevos exclusivos creados: {nuevos_creados}")
    log.info(f"Nuevas calidades de video inyectadas a eventos existentes: {fuentes_añadidas}")

if __name__ == "__main__":
    inyectar_eventos()
