import os
import re
import json
import time
import logging
import requests
import unicodedata
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse

# ─── LOGGING ESTRUCTURADO ────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("curador")

# ─── CONFIGURACIÓN DE ENTORNO Y LLAVES DINÁMICAS ─────────────────────────────
XTREAM_URL   = os.environ.get("XTREAM_URL")
XTREAM_USER  = os.environ.get("XTREAM_USER")
XTREAM_PASS  = os.environ.get("XTREAM_PASS")
RAPIDAPI_HOST = "sofasport.p.rapidapi.com"

ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY     = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")
ARCHIVO_CACHE = "agenda_api_v7.json"
HORAS_CACHE   = 4

# Lector Dinámico: Atrapa cualquier variable que empiece con "RAPIDAPI_KEY_"
LLAVES_ENV = [val for key, val in os.environ.items() if key.startswith("RAPIDAPI_KEY_") and val]
LLAVES_API = list(set(LLAVES_ENV)) # Elimina duplicados
indice_llave_actual = 0

if not LLAVES_API:
    log.warning("⚠️ No se han detectado llaves de RapidAPI en las variables de entorno.")
else:
    log.info(f"✅ Se han cargado {len(LLAVES_API)} llaves de RapidAPI para la rotación.")

# ─── DICCIONARIOS Y CONFIGURACIÓN ────────────────────────────────────────────
SOFA_IMG_EQUIPO = "https://api.sofascore.app/api/v1/team/{id}/image"
SOFA_IMG_TORNEO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image/dark"

DEPORTES_MAP = {
    "Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 11,
    "Béisbol": 64, "Hockey": 4, "Voleibol": 23, "Rugby": 12,
    "Fútbol Americano": 63
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210,
    "Béisbol": 210, "Hockey": 150, "Combate": 180, "Deportes": 180,
    "Ciclismo": 300
}

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE", 
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE"
}

CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "CARRERA", "GP ", "MOTOGP", "RALLY", "INDYCAR", "SPRINT"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS", "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS", "RANGERS", "PADRES", "GIANTS", "MARINERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS", "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS", "ENDESA", "EUROLEAGUE"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS", "CHAMPIONS", "LEAGUE", "SUDAMERICANA", "COPA", "BUNDESLIGA", "LIGUE 1"],
    "Combate": ["WWE", "SMACKDOWN", "RAW", "AEW", "LUCHA", "WRESTLING", "WRESTLEMANIA", "BOXEO", "BOXING", "PESAJE", "FIGHT", "COMBATE", "RING", "UFC", "MMA", "BELLATOR", "ONE CHAMPIONSHIP", "FIGHT PASS"],
    "Deportes": ["PGA", "LIV", "GOLF", "MASTERS", "OPEN", "NFL", "FOOTBALL", "SUPER BOWL", "NCAA", "RUGBY", "DERBY", "KENTUCKY"],
    "Hockey": ["NHL", "SABRES", "BRUINS", "CANADIENS", "LIGHTNING", "HOCKEY", "STANLEY"],
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "WIMBLEDON", "OPEN", "PADEL", "PING PONG"],
    "Ciclismo": ["CICLISMO", "CYCLING", "TOUR DE FRANCE", "TOUR COLOMBIA", "VUELTA ESPAÑA", "VUELTA A ESPAÑA", "GIRO DE ITALIA", "GIRO D ITALIA", "UCI", "ETAPA", "BERNAL", "CARAPAZ", "NAIRO", "COLOMBIA ES PASION"]
}

LOGOS_RESCATE = {
    "Motor": "https://img.icons8.com/color/512/f1-car.png",
    "Béisbol": "https://img.icons8.com/color/512/baseball.png",
    "Baloncesto": "https://img.icons8.com/color/512/basketball.png",
    "Fútbol": "https://img.icons8.com/color/512/football2.png",
    "Combate": "https://img.icons8.com/color/512/boxing-glove.png",
    "Deportes": "https://img.icons8.com/color/512/stadium.png",
    "Hockey": "https://img.icons8.com/color/512/ice-hockey.png",
    "Tenis": "https://img.icons8.com/color/512/tennis.png",
    "Ciclismo": "https://img.icons8.com/color/512/cycling.png"
}

# ─── UTILIDADES DE FECHA Y TEXTO ─────────────────────────────────────────────
def obtener_variaciones_fecha_hoy() -> list:
    """Genera dinámicamente múltiples formatos de la fecha actual para atrapar cualquier proveedor."""
    dt = datetime.now(ZONA_COLOMBIA)
    d, m = dt.strftime("%d"), dt.strftime("%m")
    
    meses_es = {"01":"ENE", "02":"FEB", "03":"MAR", "04":"ABR", "05":"MAY", "06":"JUN", "07":"JUL", "08":"AGO", "09":"SEP", "10":"OCT", "11":"NOV", "12":"DIC"}
    meses_en = {"01":"JAN", "02":"FEB", "03":"MAR", "04":"APR", "05":"MAY", "06":"JUN", "07":"JUL", "08":"AUG", "09":"SEP", "10":"OCT", "11":"NOV", "12":"DEC"}
    
    m_es, m_en = meses_es[m], meses_en[m]
    
    return [
        f"{d}/{m}", f"{d}-{m}", f"{d} {m}", f"{d}.{m}",
        f"{m}/{d}", f"{m}-{d}", f"{m} {d}", f"{m}.{d}",
        f"{d} {m_es}", f"{m_es} {d}", f"{d} {m_en}", f"{m_en} {d}"
    ]

def limpiar_nombre_categoria(nombre: str) -> str:
    nombre = nombre.upper()
    nombre = re.sub(r'[^\w\s\/\-\.]', ' ', nombre) 
    return ' '.join(nombre.split())

def limpiar_texto_para_match(texto: str) -> str:
    if not texto: return ""
    texto = str(texto).upper()
    
    texto = re.sub(r'\(.*?\)', '', texto)
    texto = re.sub(r'\[.*?\]', '', texto)
    texto = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', texto)
    texto = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|OPC\.\d+|OP\d+|PEA \d+|PARA\+ \d+|VIVO|LIVE|COMPACTO|REPETICION|RESUMEN)\b', '', texto)
    
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto) 
    
    for corto, largo in TRADUCTOR_JERGA.items():
        texto = re.sub(rf"\b{corto}\b", largo, texto)
        
    palabras = [p for p in texto.split() if len(p) > 2 and p not in ["THE", "AND", "DEL", "LAS", "LOS", "VS", "EN", "EL", "DE", "LA"]]
    return " ".join(palabras)

def calcular_similitud_interseccion(xtream_limpio: str, api_limpio: str) -> float:
    if not xtream_limpio or not api_limpio: return 0.0
    words_x = set(xtream_limpio.split())
    words_a = set(api_limpio.split())
    if not words_x or not words_a: return 0.0
    
    comunes = words_x.intersection(words_a)
    if len(comunes) >= 2: return 85.0
    if len(comunes) == 1 and len(list(comunes)) >= 5:
        if SequenceMatcher(None, xtream_limpio, api_limpio).ratio() * 100 > 40.0:
            return 70.0
            
    ratio_difuso = SequenceMatcher(None, xtream_limpio, api_limpio).ratio() * 100
    if ratio_difuso > 65.0: return ratio_difuso
    return 0.0

def adivinar_categoria_y_logo(texto: str):
    texto_upper = texto.upper()
    for categoria, keywords in CATEGORIAS_RESCATE.items():
        if any(kw in texto_upper for kw in keywords):
            return categoria, LOGOS_RESCATE[categoria]
    return "Deportes", LOGOS_RESCATE["Deportes"]

def calcular_hora_utc_falsa(nombre_canal: str) -> str:
    match = re.search(r'\b(\d{1,2}):(\d{2})\b\s*(PM)?', nombre_canal, re.IGNORECASE)
    if match:
        try:
            h, m = int(match.group(1)), int(match.group(2))
            pm = match.group(3)
            if pm and h < 12: h += 12
            dt_evento = datetime.now(ZONA_COLOMBIA).replace(hour=h, minute=m, second=0, microsecond=0)
            return dt_evento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except: pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def crear_id_seguro(titulo: str) -> str:
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return hash_obj.hexdigest()[:12]

# ─── RED Y CACHÉ ────────────────────
def hacer_peticion_segura(url: str, params: dict):
    global LLAVES_API, indice_llave_actual
    if not LLAVES_API: return None
        
    intentos = 0
    max_intentos = len(LLAVES_API) * 2 
    backoff = 2
    
    while intentos < max_intentos and LLAVES_API:
        llave = LLAVES_API[indice_llave_actual]
        try:
            time.sleep(1.5)
            r = requests.get(url, headers={"x-rapidapi-key": llave, "x-rapidapi-host": RAPIDAPI_HOST}, params=params, timeout=15)
            
            if r.status_code == 200:
                return r
            elif r.status_code == 429:
                log.warning(f"Llave {indice_llave_actual + 1} dio 429. Esperando {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
                intentos += 1
                continue
            elif r.status_code == 403:
                log.error(f"🚨 Llave {indice_llave_actual + 1} AGOTADA (403).")
                LLAVES_API.pop(indice_llave_actual)
                if not LLAVES_API: return None
                indice_llave_actual = indice_llave_actual % len(LLAVES_API)
                continue
            else:
                return r
        except requests.exceptions.RequestException as e:
            indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
            intentos += 1
            time.sleep(2)
    return None

def obtener_agenda_maestra() -> list:
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            try:
                with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                    agenda = json.load(f)
                    if agenda: return agenda
            except Exception: pass

    log.info("Descargando Agenda Maestra de SofaScore...")
    eventos_api = []
    
    for deporte, sport_id in DEPORTES_MAP.items():
        log.info(f"Consultando agenda de {deporte}...")
        url = f"https://{RAPIDAPI_HOST}/v1/events/schedule/date"
        params = {"date": FECHA_HOY, "sport_id": sport_id, "timezone": -5}
        
        r_agenda = hacer_peticion_segura(url, params)
        if not r_agenda: continue

        if r_agenda.status_code == 404:
            continue
            
        data = r_agenda.json().get("data", [])
        
        for ev in data:
            try:
                unix_time = ev.get("startTimestamp")
                if not unix_time: continue
                dt_utc = datetime.fromtimestamp(unix_time, timezone.utc)
                torneo = ev.get("tournament", {}).get("name", "")
                home_team = ev.get("homeTeam", {})
                away_team = ev.get("awayTeam", {})
                eq_local = home_team.get("name", "")
                eq_visit = away_team.get("name", "")
                
                titulo = f"{eq_local} vs {eq_visit}" if eq_local and eq_visit else ev.get("name", ev.get("description", ""))
                firma_texto = limpiar_texto_para_match(f"{torneo} {titulo}")

                if eq_local:
                    logo_local = SOFA_IMG_EQUIPO.format(id=home_team.get("id"))
                else:
                    logo_local = SOFA_IMG_TORNEO.format(id=ev.get("tournament", {}).get("uniqueTournament", {}).get("id"))
                    
                logo_visitante = SOFA_IMG_EQUIPO.format(id=away_team.get("id")) if eq_visit else ""

                eventos_api.append({
                    "id": str(ev.get("id")),
                    "titulo": titulo, "torneo": torneo, "categoria": deporte,
                    "firma_texto": firma_texto,
                    "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": DURACION_POR_DEPORTE.get(deporte, 150),
                    "logo_local": logo_local, "logo_visitante": logo_visitante,
                    "tier": 2
                })
            except Exception: continue

    if eventos_api:
        with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
            json.dump(eventos_api, f, ensure_ascii=False, indent=2)
    return eventos_api

# ─── XTREAM ────────────────────────────────
def procesar_cubo_a() -> list:
    log.info("Analizando servidor Xtream a través del puente...")
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    cubo_a = []
    
    fechas_hoy = obtener_variaciones_fecha_hoy()
    palabras_principales = ["HOY", "TODAY", "DAILY", "DIARIO", "DIA", "VIVO", "LIVE"]
    palabras_contexto = ["EVENTOS", "EVENTS", "AGENDA", "PARTIDOS", "CARTELERA", "CALENDARIO", "PPV"]
    
    try:
        url_cat_original = f"{api_url}&action=get_live_categories"
        r_cat = requests.get("https://mi-dashboard-tv.onrender.com/api/puente_xtream", params={"url": url_cat_original}, timeout=45)
        categoria_id_hoy = None
        
        if r_cat.status_code == 200:
            for cat in r_cat.json():
                nombre_cat = cat.get("category_name", "")
                nombre_limpio = limpiar_nombre_categoria(nombre_cat)
                
                if any(f in nombre_limpio for f in fechas_hoy):
                    categoria_id_hoy = cat.get("category_id")
                    break
                    
                tiene_principal = any(p in nombre_limpio for p in palabras_principales)
                tiene_contexto = any(c in nombre_limpio for c in palabras_contexto)
                if tiene_principal and tiene_contexto:
                    categoria_id_hoy = cat.get("category_id")
                    break
        
        if categoria_id_hoy:
            url_streams = f"{api_url}&action=get_live_streams&category_id={categoria_id_hoy}"
        else:
            url_streams = f"{api_url}&action=get_live_streams"
            
        r_str = requests.get("https://mi-dashboard-tv.onrender.com/api/puente_xtream", params={"url": url_streams}, timeout=60)
        if r_str.status_code != 200: return []
        
        for s in r_str.json():
            nombre_canal = s.get("name", "").strip()
            
            if not categoria_id_hoy:
                 match_fecha = re.search(r'\b(\d{1,2})[-/](\d{1,2})\b', nombre_canal)
                 if match_fecha:
                     d_str, m_str = match_fecha.group(1), match_fecha.group(2)
                     if not any(f in f"{d_str}/{m_str}" or f in f"{m_str}/{d_str}" for f in fechas_hoy):
                         continue 

            if re.search(r'\b\d{1,2}:\d{2}\b', nombre_canal) or any(kw in nombre_canal.upper() for kw in ["PEA ", "PARA+", "LMB ", "MLB ", "UFC "]):
                texto_limpio = limpiar_texto_para_match(nombre_canal)
                if len(texto_limpio) > 4:
                    cubo_a.append({
                        "id_xtream": str(s.get("stream_id")),
                        "nombre_ui": nombre_canal,
                        "texto_limpio": texto_limpio
                    })
                    
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []

# ─── RESOLUCIÓN DE HOST DE MEDIA ─────────────────────────────────────────────
def detectar_base_media_m3u() -> str:
    """Lee una muestra pequeña del M3U y detecta su host real de media."""
    base_api = XTREAM_URL.rstrip("/")
    url_m3u = (
        f"{base_api}/get.php?username={XTREAM_USER}"
        f"&password={XTREAM_PASS}&type=m3u&output=ts"
    )
    candidatos = []

    try:
        with requests.get(
            url_m3u,
            headers={"Range": "bytes=0-65535"},
            stream=True,
            timeout=(15, 30)
        ) as respuesta:
            if respuesta.status_code not in (200, 206):
                log.warning(
                    f"No se pudo leer la muestra M3U: HTTP {respuesta.status_code}. "
                    f"Se conserva XTREAM_URL como base de media."
                )
                return base_api

            for linea_bytes in respuesta.iter_lines(chunk_size=8192):
                linea = linea_bytes.decode("utf-8", errors="ignore").strip()
                if not linea.startswith(("http://", "https://")):
                    continue

                parsed = urlparse(linea)
                partes = [parte for parte in parsed.path.split("/") if parte]
                if len(partes) < 3:
                    continue

                usuario, password = partes[-3], partes[-2]
                stream_id = partes[-1].split("?")[0]
                if usuario == XTREAM_USER and password == XTREAM_PASS and stream_id:
                    candidatos.append(f"{parsed.scheme}://{parsed.netloc}")

                if len(candidatos) >= 3:
                    break

        if len(candidatos) >= 3 and len(set(candidatos)) == 1:
            base_media = candidatos[0]
            if base_media != base_api:
                log.info(f"Base de media detectada desde M3U: {base_media}")
            else:
                log.info("El M3U confirma XTREAM_URL como base de media.")
            return base_media

        log.warning(
            "No se obtuvo una muestra M3U consistente. "
            "Se conserva XTREAM_URL como base de media."
        )
        return base_api

    except requests.exceptions.RequestException as e:
        log.warning(
            f"No se pudo detectar la base de media desde M3U: {e}. "
            "Se conserva XTREAM_URL como base de media."
        )
        return base_api

# ─── ORQUESTADOR ───────────────────────────────────
def main():
    log.info(f"=== Iniciando Curador Base (SofaScore + Xtream) ===")
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()
    
    if not cubo_a: 
        log.warning("No se detectaron eventos temporales en Xtream para procesar.")
        return

    resultados_finales = []
    base_media = detectar_base_media_m3u()

    for canal in cubo_a:
        texto_canal = canal["texto_limpio"]
        url_video = f"{base_media}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}"
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": url_video}
        
        match_encontrado = False
        mejor_evento, mejor_puntaje = None, 0
        
        if agenda_api:
            for ev in agenda_api:
                puntaje = calcular_similitud_interseccion(texto_canal, ev["firma_texto"])
                if puntaje > mejor_puntaje:
                    mejor_puntaje, mejor_evento = puntaje, ev
            
            if mejor_puntaje > 55.0 and mejor_evento:
                match_encontrado = True
                evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
                if evento_existente:
                    if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                        evento_existente["fuentes"].append(fuente_limpia)
                else:
                    evento_clon = mejor_evento.copy()
                    evento_clon["fuentes"] = [fuente_limpia]
                    if "firma_texto" in evento_clon: del evento_clon["firma_texto"]
                    resultados_finales.append(evento_clon)

        if not match_encontrado:
            titulo_base = canal["nombre_ui"]
            titulo_base = re.sub(r'\(.*?\)|\[.*?\]', '', titulo_base)
            titulo_base = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', titulo_base)
            titulo_base = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER)\b', '', titulo_base, flags=re.IGNORECASE)
            
            partes = [p.strip() for p in re.split(r'[▫\|☻✨]+', titulo_base) if len(p.strip()) > 2]
            
            if partes:
                parte_vs = next((p for p in partes if ' vs ' in p.lower() or ' vs. ' in p.lower()), None)
                if parte_vs:
                    titulo_base = parte_vs
                else:
                    titulo_base = " - ".join(partes)
            
            titulo_base = titulo_base.strip(" -[]").title()
            
            if not titulo_base: continue
            
            evento_existente = next((item for item in resultados_finales if item["titulo"] == titulo_base and item["id"].startswith("rescate_")), None)
            
            if evento_existente:
                if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(canal["nombre_ui"])
                hora_utc_calculada = calcular_hora_utc_falsa(canal["nombre_ui"])
                
                resultados_finales.append({
                    "id": f"rescate_{crear_id_seguro(titulo_base)}", 
                    "titulo": titulo_base,
                    "torneo": "Evento Especial",
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_calculada,
                    "duracion_min": DURACION_POR_DEPORTE.get(categoria_adivinada, 240),
                    "logo_local": logo_adivinado,
                    "logo_visitante": "",
                    "banner": logo_adivinado,
                    "tier": 2,
                    "fuentes": [fuente_limpia]
                })

    resultados_finales.sort(key=lambda x: x["hora_utc"])

    try:
        with open("eventos_hoy.json", "w", encoding="utf-8") as f:
            json.dump(resultados_finales, f, ensure_ascii=False, indent=2)
        log.info(f"¡Proceso Terminado! Total de eventos listos: {len(resultados_finales)}")
    except Exception as e:
        log.error(f"Error crítico guardando el archivo JSON: {e}")

if __name__ == "__main__":
    main()
