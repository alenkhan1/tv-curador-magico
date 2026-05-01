import os
import re
import json
import time
import logging
import requests
import unicodedata
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher

# ─── LOGGING ESTRUCTURADO ────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("curador")

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL   = os.environ.get("XTREAM_URL")
XTREAM_USER  = os.environ.get("XTREAM_USER")
XTREAM_PASS  = os.environ.get("XTREAM_PASS")
RAPIDAPI_HOST = "sofasport.p.rapidapi.com"

ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY     = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")
ARCHIVO_CACHE = "agenda_api_v7.json" # Nuevo cache para evitar datos corruptos
HORAS_CACHE   = 4

# Soporta múltiples llaves de RapidAPI
LLAVES_ENV = [
    os.environ.get("RAPIDAPI_KEY_1"), os.environ.get("RAPIDAPI_KEY_2"),
    os.environ.get("RAPIDAPI_KEY_3"), os.environ.get("RAPIDAPI_KEY_4"),
    os.environ.get("RAPIDAPI_KEY_5")
]
LLAVES_API = [k for k in LLAVES_ENV if k]
indice_llave_actual = 0

if not LLAVES_API:
    log.warning("⚠️ No se han detectado llaves de RapidAPI en las variables de entorno.")

# ─── DICCIONARIOS Y CONFIGURACIÓN ────────────────────────────────────────────
SOFA_IMG_EQUIPO = "https://api.sofascore.app/api/v1/team/{id}/image"
SOFA_IMG_TORNEO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image/dark"

# Mapeo de nombres de deportes a IDs de SofaScore
DEPORTES_MAP = {
    "Fútbol": 1, "Baloncesto": 2, "Tenis": 5, "Motor": 22, 
    "Béisbol": 64, "Boxeo": 9, "MMA": 30, "Golf": 18, 
    "Hockey": 4, "Voleibol": 23, "Rugby": 12, "Fútbol Americano": 14
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210, 
    "Béisbol": 210, "Rugby": 120, "Boxeo": 180, "Voleibol": 150, "MMA": 200, "Golf": 240, "Hockey": 150
}

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE", 
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE"
}

# Categorizador heurístico para eventos no encontrados en la API (Ruta B)
CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "CARRERA", "GP ", "MOTOGP", "RALLY", "INDYCAR"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS", "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS", "RANGERS", "PADRES", "GIANTS", "MARINERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS", "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS", "ENDESA", "EUROLEAGUE"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS", "CHAMPIONS", "LEAGUE", "SUDAMERICANA", "COPA", "BUNDESLIGA", "LIGUE 1"],
    "Lucha Libre": ["WWE", "SMACKDOWN", "RAW", "AEW", "LUCHA", "WRESTLING"],
    "Boxeo": ["BOXEO", "BOXING", "PESAJE", "FIGHT", "COMBATE"],
    "Hockey": ["NHL", "SABRES", "BRUINS", "CANADIENS", "LIGHTNING", "HOCKEY"],
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "WIMBLEDON", "OPEN", "PADEL", "PING PONG"],
    "Fútbol Americano": ["NFL", "FOOTBALL", "SUPER BOWL"]
}

LOGOS_RESCATE = {
    "Motor": "https://img.icons8.com/color/512/f1-car.png",
    "Béisbol": "https://img.icons8.com/color/512/baseball.png",
    "Baloncesto": "https://img.icons8.com/color/512/basketball.png",
    "Fútbol": "https://img.icons8.com/color/512/football2.png",
    "Lucha Libre": "https://img.icons8.com/color/512/wrestling.png",
    "Boxeo": "https://img.icons8.com/color/512/boxing-glove.png",
    "Hockey": "https://img.icons8.com/color/512/ice-hockey.png",
    "Tenis": "https://img.icons8.com/color/512/tennis.png",
    "Fútbol Americano": "https://img.icons8.com/color/512/american-football.png",
    "Deportes": "https://img.icons8.com/color/512/stadium.png"
}

# ─── UTILIDADES ──────────────────────────────────────────────────────────────
def limpiar_texto_para_match(texto: str) -> str:
    """Limpia la basura de IPTV (HD, horas, corchetes) para hacer match con la API."""
    if not texto: return ""
    texto = str(texto).upper()
    
    # 1. Quitar contenido entre paréntesis y corchetes (ej. "(On Board)", "[VIVO]")
    texto = re.sub(r'\(.*?\)', '', texto)
    texto = re.sub(r'\[.*?\]', '', texto)
    
    # 2. Quitar horas y zonas horarias
    texto = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', texto)
    
    # 3. Quitar etiquetas técnicas de IPTV y canales
    texto = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|OPC\.\d+|OP\d+|PEA \d+|PARA\+ \d+|VIVO|LIVE|COMPACTO|REPETICION|RESUMEN)\b', '', texto)
    
    # 4. Normalizar caracteres (quitar tildes y símbolos raros)
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = re.sub(r"[^A-Z0-9\s]", " ", texto) # Deja solo alfanuméricos
    
    # 5. Aplicar traducciones manuales (ej. F1 -> FORMULA 1)
    for corto, largo in TRADUCTOR_JERGA.items():
        texto = re.sub(rf"\b{corto}\b", largo, texto)
        
    # 6. Quitar preposiciones comunes que estorban en la comparación
    palabras = [p for p in texto.split() if len(p) > 2 and p not in ["THE", "AND", "DEL", "LAS", "LOS", "VS", "EN", "EL", "DE", "LA"]]
    return " ".join(palabras)

def calcular_similitud_interseccion(xtream_limpio: str, api_limpio: str) -> float:
    """Evalúa la coincidencia basándose en intersección de palabras clave y similitud difusa."""
    if not xtream_limpio or not api_limpio: return 0.0
    
    words_x = set(xtream_limpio.split())
    words_a = set(api_limpio.split())
    
    if not words_x or not words_a: return 0.0
    
    comunes = words_x.intersection(words_a)
    
    # MATCH FUERTE: Si comparten 2 o más palabras clave (Ej. "Red" y "Sox")
    if len(comunes) >= 2:
        return 85.0
    
    # MATCH MEDIO: Si comparten 1 palabra muy específica/larga (Ej. "Diamondbacks")
    if len(comunes) == 1 and len(list(comunes)[0]) >= 5:
        # Verificamos similitud fonética general para evitar falsos positivos
        ratio = SequenceMatcher(None, xtream_limpio, api_limpio).ratio() * 100
        if ratio > 40.0:
            return 70.0
            
    # MATCH DIFUSO (Fallback): Por si hay errores de tipeo ligeros
    ratio_difuso = SequenceMatcher(None, xtream_limpio, api_limpio).ratio() * 100
    if ratio_difuso > 65.0:
         return ratio_difuso
         
    return 0.0

def adivinar_categoria_y_logo(texto: str):
    """Asigna una categoría y un logo genérico a los eventos que no se encontraron en la API."""
    texto_upper = texto.upper()
    for categoria, keywords in CATEGORIAS_RESCATE.items():
        if any(kw in texto_upper for kw in keywords):
            return categoria, LOGOS_RESCATE[categoria]
    return "Deportes", LOGOS_RESCATE["Deportes"]

def calcular_hora_utc_falsa(nombre_canal: str) -> str:
    """Intenta extraer la hora del título del canal para simular una fecha UTC para la Ruta B."""
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
    """Crea un hash determinista para agrupar eventos con el mismo nombre en la Ruta B."""
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return hash_obj.hexdigest()[:12]

# ─── RED Y CACHÉ (API SOFASCORE) ─────────────────────────────────────────────
def hacer_peticion_segura(url: str, params: dict):
    """Realiza peticiones a RapidAPI con manejo inteligente de errores y rotación de llaves."""
    global indice_llave_actual
    if not LLAVES_API:
        return None
        
    intentos = 0
    max_intentos = len(LLAVES_API) * 2 # Permitimos probar cada llave un par de veces
    
    while intentos < max_intentos:
        llave = LLAVES_API[indice_llave_actual]
        try:
            time.sleep(1) # Pausa crucial para evitar saturar la API
            r = requests.get(url, headers={"x-rapidapi-key": llave, "x-rapidapi-host": RAPIDAPI_HOST}, params=params, timeout=15)
            
            if r.status_code == 200:
                return r
                
            elif r.status_code in [429, 403]: # Rate Limit (muy rápido) o Cuota agotada
                log.warning(f"Llave {indice_llave_actual + 1} dio error {r.status_code}. Rotando...")
                indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
                intentos += 1
                time.sleep(2) # Pausa de penalización
                continue
            else:
                # Errores 500, 404, etc. de la propia API, no de nuestras llaves
                log.error(f"Error HTTP {r.status_code} al consultar la API.")
                return None
                
        except requests.exceptions.RequestException as e:
            log.warning(f"Error de conexión en llave {indice_llave_actual + 1}: {e}. Rotando...")
            indice_llave_actual = (indice_llave_actual + 1) % len(LLAVES_API)
            intentos += 1
            time.sleep(2)
            
    log.error("Todas las llaves fallaron o se agotaron los intentos para esta solicitud.")
    return None

def obtener_agenda_maestra() -> list:
    """Descarga la agenda global del día de SofaScore con un método que evita baneos."""
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            try:
                with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                    agenda = json.load(f)
                    if agenda:
                        log.info(f"Cargando Agenda Maestra ({len(agenda)} eventos) desde Caché Local...")
                        return agenda
            except Exception as e:
                log.warning(f"Cache corrupto ({e}). Descargando nueva agenda...")

    log.info("Descargando Agenda Maestra de SofaScore (Método de Agenda Global)...")
    eventos_api = []
    
    # ESTRATEGIA NUEVA: En lugar de iterar por categoría, pedimos la agenda del DÍA por deporte
    for deporte, sport_id in DEPORTES_MAP.items():
        log.info(f"Consultando agenda de {deporte}...")
        
        # Endpoint que trae los eventos destacados/principales del día para ese deporte
        # Esto reduce drásticamente las peticiones en comparación con iterar liga por liga
        url = f"https://{RAPIDAPI_HOST}/v1/events/schedule/date"
        params = {"date": FECHA_HOY, "sport_id": sport_id, "timezone": -5}
        
        r_agenda = hacer_peticion_segura(url, params)
        
        if not r_agenda:
            log.warning(f"No se pudo descargar la agenda de {deporte} o no hay eventos hoy.")
            continue
            
        data = r_agenda.json().get("data", [])
        
        for ev in data:
            try:
                unix_time = ev.get("startTimestamp")
                if not unix_time: continue
                
                dt_utc = datetime.fromtimestamp(unix_time, timezone.utc)
                torneo = ev.get("tournament", {}).get("name", "")
                nombre_evento = ev.get("name", ev.get("description", ""))
                
                # Datos de los equipos (si aplica)
                home_team = ev.get("homeTeam", {})
                away_team = ev.get("awayTeam", {})
                eq_local = home_team.get("name", "")
                eq_visit = away_team.get("name", "")
                
                # Construir título representativo
                titulo = f"{eq_local} vs {eq_visit}" if eq_local and eq_visit else nombre_evento
                
                # Firma limpia para buscar coincidencias exactas y difusas
                firma_texto = limpiar_texto_para_match(f"{torneo} {titulo}")

                # Determinar logos
                if eq_local:
                    logo_local = SOFA_IMG_EQUIPO.format(id=home_team.get("id"))
                else:
                    logo_local = SOFA_IMG_TORNEO.format(id=ev.get("tournament", {}).get("uniqueTournament", {}).get("id"))
                    
                logo_visitante = SOFA_IMG_EQUIPO.format(id=away_team.get("id")) if eq_visit else ""

                eventos_api.append({
                    "id": str(ev.get("id")),
                    "titulo": titulo,
                    "torneo": torneo,
                    "categoria": deporte,
                    "firma_texto": firma_texto,
                    "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": DURACION_POR_DEPORTE.get(deporte, 150),
                    "logo_local": logo_local,
                    "logo_visitante": logo_visitante,
                    "tier": 2
                })
            except Exception as e:
                continue

    if eventos_api:
        log.info(f"Éxito: {len(eventos_api)} eventos descargados de la API.")
        try:
            with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
                json.dump(eventos_api, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.warning(f"No se pudo guardar el caché local: {e}")
    else:
        log.error("Fallo crítico: No se obtuvo ningún evento de la API.")
        
    return eventos_api

# ─── XTREAM (CUBO A) ─────────────────────────────────────────────────────────
def procesar_cubo_a() -> list:
    """Descarga la lista Xtream y filtra solo los canales que parecen ser transmisiones en vivo (Cubo A)."""
    log.info("Descargando Xtream y detectando canales de eventos temporales (Cubo A)...")
    
    # Asegurar URL limpia sin barras finales
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    
    try:
        r_str = requests.get(f"{api_url}&action=get_live_streams", timeout=30)
        
        if r_str.status_code != 200:
            log.error(f"Error conectando a Xtream: Código HTTP {r_str.status_code}")
            return []
            
        cubo_a = []
        data = r_str.json()
        
        for s in data:
            nombre_canal = s.get("name", "").strip()
            
            # Filtro: Buscamos un patrón de hora (ej. 14:00, 3:30 PM) o marcadores de canales temporales comunes
            if re.search(r'\b\d{1,2}:\d{2}\b', nombre_canal) or any(kw in nombre_canal.upper() for kw in ["PEA ", "PARA+", "LMB ", "MLB ", "UFC "]):
                texto_limpio = limpiar_texto_para_match(nombre_canal)
                
                # Ignoramos si después de limpiar no quedó casi nada (canal basura)
                if len(texto_limpio) > 4:
                    cubo_a.append({
                        "id_xtream": str(s.get("stream_id")),
                        "nombre_ui": nombre_canal,
                        "texto_limpio": texto_limpio
                    })
                    
        log.info(f"Cubo A listo: {len(cubo_a)} canales procesables detectados en Xtream.")
        return cubo_a
        
    except Exception as e:
        log.error(f"Error procesando lista Xtream: {e}")
        return []

# ─── ORQUESTADOR (MOTOR DE EMPAREJAMIENTO) ───────────────────────────────────
def main():
    log.info(f"=== Iniciando Curador Mágico V7 (Arquitectura Resiliente) ===")
    
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()
    
    if not cubo_a: 
        log.warning("No se detectaron eventos temporales en Xtream para procesar.")
        return

    resultados_finales = []
    base_url = XTREAM_URL.rstrip('/')

    for canal in cubo_a:
        texto_canal = canal["texto_limpio"]
        
        # Generar la URL de reproducción correcta (sin /live/)
        url_video = f"{base_url}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}.ts"
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": url_video}
        
        match_encontrado = False
        mejor_evento = None
        mejor_puntaje = 0
        
        # 1. RUTA A (Emparejamiento con Agenda SofaScore)
        if agenda_api:
            for ev in agenda_api:
                puntaje = calcular_similitud_interseccion(texto_canal, ev["firma_texto"])
                if puntaje > mejor_puntaje:
                    mejor_puntaje = puntaje
                    mejor_evento = ev
            
            # Si el nivel de confianza es aceptable
            if mejor_puntaje > 55.0 and mejor_evento:
                match_encontrado = True
                
                # Buscar si este evento ya se agregó al resultado final (para agrupar fuentes)
                evento_existente = next((item for item in resultados_finales if item["id"] == mejor_evento["id"]), None)
                
                if evento_existente:
                    # Prevenir duplicar la misma URL exacta en el arreglo de fuentes
                    if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                        evento_existente["fuentes"].append(fuente_limpia)
                else:
                    # Crear nuevo bloque de evento
                    evento_clon = mejor_evento.copy()
                    evento_clon["fuentes"] = [fuente_limpia]
                    # Limpiar metadata interna antes de exportar
                    if "firma_texto" in evento_clon: del evento_clon["firma_texto"]
                    resultados_finales.append(evento_clon)

        # 2. RUTA B (Modo Rescate para PPV, Lucha, o fallos de API)
        if not match_encontrado:
            # Limpiar el título para la UI de la app (Quita horas y tags de calidad)
            titulo_base = canal["nombre_ui"]
            titulo_base = re.sub(r'\(.*?\)', '', titulo_base)
            titulo_base = re.sub(r'\[.*?\]', '', titulo_base)
            titulo_base = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', titulo_base)
            titulo_base = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER)\b', '', titulo_base, flags=re.IGNORECASE)
            titulo_base = titulo_base.strip(" -|▫✨[]").title()
            
            if not titulo_base: continue
            
            # Buscar si ya rescatamos un evento con este título base (Agrupación de cámaras/audios)
            evento_existente = next((item for item in resultados_finales if item["titulo"] == titulo_base and item["id"].startswith("rescate_")), None)
            
            if evento_existente:
                if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(canal["nombre_ui"])
                hora_utc_calculada = calcular_hora_utc_falsa(canal["nombre_ui"])
                id_unico = crear_id_seguro(titulo_base)

                evento_rescate = {
                    "id": f"rescate_{id_unico}", 
                    "titulo": titulo_base,
                    "torneo": "Evento Especial",
                    "categoria": categoria_adivinada,
                    "hora_utc": hora_utc_calculada,
                    "duracion_min": DURACION_POR_DEPORTE.get(categoria_adivinada, 240), # Asegura que aparezca "En Vivo"
                    "logo_local": logo_adivinado,
                    "logo_visitante": "",
                    "banner": logo_adivinado,
                    "tier": 2,
                    "fuentes": [fuente_limpia]
                }
                resultados_finales.append(evento_rescate)

    # Ordenar cronológicamente para la App
    resultados_finales.sort(key=lambda x: x["hora_utc"])

    # Exportación segura del JSON
    try:
        with open("eventos_hoy.json", "w", encoding="utf-8") as f:
            json.dump(resultados_finales, f, ensure_ascii=False, indent=2)
        log.info(f"¡Proceso Terminado! Total de eventos finalizados: {len(resultados_finales)}")
    except Exception as e:
        log.error(f"Error crítico guardando el archivo JSON: {e}")

if __name__ == "__main__":
    main()
