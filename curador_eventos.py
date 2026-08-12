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

# ─── CONFIGURACIÓN DE ENTORNO ────────────────────────────────────────────────
XTREAM_URL = os.environ.get("XTREAM_URL")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")

# TheSportsDB: llave gratuita "123" (limite por MINUTO, no mensual -> sin rotacion necesaria)
THESPORTSDB_KEY = os.environ.get("THESPORTSDB_KEY", "123")
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}"

ZONA_COLOMBIA = timezone(timedelta(hours=-5))
FECHA_HOY = datetime.now(ZONA_COLOMBIA).strftime("%Y-%m-%d")
ARCHIVO_CACHE = "agenda_api_v8.json"
HORAS_CACHE = 4

# ─── DEPORTES A CONSULTAR (nombres oficiales de TheSportsDB) ────────────────
# Se mantiene cobertura amplia, incluyendo motor (interes explicito del usuario)
DEPORTES_MAP = {
    "Fútbol": "Soccer",
    "Baloncesto": "Basketball",
    "Tenis": "Tennis",
    "Motor": "Motorsport",
    "Béisbol": "Baseball",
    "Hockey": "Ice Hockey",
    "Voleibol": "Volleyball",
    "Rugby": "Rugby",
    "Fútbol Americano": "American Football",
    "Combate": "Fighting",
    "Ciclismo": "Cycling",
}

DURACION_POR_DEPORTE = {
    "Fútbol": 120, "Baloncesto": 150, "Tenis": 180, "Motor": 210,
    "Béisbol": 210, "Hockey": 150, "Combate": 180, "Deportes": 180,
    "Ciclismo": 300, "Voleibol": 150, "Rugby": 150, "Fútbol Americano": 210,
}

TRADUCTOR_JERGA = {
    "F1": "FORMULA 1", "MOTO GP": "MOTOGP", "UCL": "CHAMPIONS LEAGUE",
    "LIV": "LIV GOLF", "PGA": "PGA TOUR", "PREMIER": "PREMIER LEAGUE",
    "MLB": "MAJOR LEAGUE BASEBALL", "NBA": "NATIONAL BASKETBALL ASSOCIATION",
    "NFL": "NATIONAL FOOTBALL LEAGUE", "NHL": "NATIONAL HOCKEY LEAGUE",
    "UFC": "ULTIMATE FIGHTING CHAMPIONSHIP", "BKFC": "BARE KNUCKLE FIGHTING CHAMPIONSHIP",
    "USAC": "UNITED STATES AUTO CLUB",
}

CATEGORIAS_RESCATE = {
    "Motor": ["F1", "F2", "F3", "FORMULA", "NASCAR", "CARRERA", "GP ", "MOTOGP", "RALLY", "INDYCAR", "SPRINT", "USAC", "SILVER CROWN", "IRC", "IMSA", "WRC"],
    "Béisbol": ["MLB", "LMB", "BEISBOL", "BASEBALL", "DIAMONDBACKS", "CUBS", "YANKEES", "RED SOX", "DODGERS", "PIRATES", "REDS", "ASTROS", "RANGERS", "PADRES", "GIANTS", "MARINERS"],
    "Baloncesto": ["NBA", "WNBA", "BASKETBALL", "LAKERS", "CELTICS", "BULLS", "CAVALIERS", "PISTONS", "MAGIC", "RAPTORS", "ENDESA", "EUROLEAGUE"],
    "Fútbol": ["LIGA", "SERIE A", "PREMIER", "FUTBOL", "SOCCER", "NWSL", "MLS", "CHAMPIONS", "LEAGUE", "SUDAMERICANA", "COPA", "BUNDESLIGA", "LIGUE 1", "LEAGUES CUP"],
    "Combate": ["WWE", "SMACKDOWN", "RAW", "AEW", "LUCHA", "WRESTLING", "WRESTLEMANIA", "BOXEO", "BOXING", "PESAJE", "FIGHT", "COMBATE", "RING", "UFC", "MMA", "BELLATOR", "ONE CHAMPIONSHIP", "FIGHT PASS", "BKFC", "MVPW"],
    "Deportes": ["PGA", "LIV", "GOLF", "MASTERS", "OPEN", "NCAA", "DERBY", "KENTUCKY", "NATACION", "SWIMMING", "ATLETISMO", "AJEDREZ", "CHESS"],
    "Fútbol Americano": ["NFL", "FOOTBALL", "SUPER BOWL"],
    "Hockey": ["NHL", "SABRES", "BRUINS", "CANADIENS", "LIGHTNING", "HOCKEY", "STANLEY"],
    "Tenis": ["ATP", "WTA", "TENIS", "TENNIS", "WIMBLEDON", "OPEN", "PADEL", "PING PONG"],
    "Ciclismo": ["CICLISMO", "CYCLING", "TOUR DE FRANCE", "TOUR COLOMBIA", "VUELTA ESPAÑA", "VUELTA A ESPAÑA", "GIRO DE ITALIA", "GIRO D ITALIA", "UCI", "ETAPA", "BERNAL", "CARAPAZ", "NAIRO", "COLOMBIA ES PASION"],
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
    "Ciclismo": "https://img.icons8.com/color/512/cycling.png",
    "Fútbol Americano": "https://img.icons8.com/color/512/football2.png",
}

# Ruido de proveedor IPTV: numeracion de feed, calidad, idioma, marcas
RUIDO_CANAL = [
    r"\bPPV\b", r"\bLIVE\s*EVENT\s*\d*\b", r"\bLIVE\s*\d+\b",
    r"\bHD\b", r"\bSD\b", r"\bFHD\b", r"\b4K\b", r"\bOP\d+\b",
    r"\bENG\b", r"\bESP\b", r"\bGER\b", r"\bPEA\s*\d+\b", r"\bPARA\+\s*\d+\b",
    r"\bVIP\b", r"\bAM\b", r"\bUS\|", r"^US\|", r"\bREPETICION\b", r"\bRESUMEN\b",
]

# ─── UTILIDADES DE FECHA Y TEXTO ─────────────────────────────────────────────
def obtener_variaciones_fecha_hoy() -> list:
    dt = datetime.now(ZONA_COLOMBIA)
    d, m = dt.strftime("%d"), dt.strftime("%m")
    meses_es = {"01":"ENE","02":"FEB","03":"MAR","04":"ABR","05":"MAY","06":"JUN","07":"JUL","08":"AGO","09":"SEP","10":"OCT","11":"NOV","12":"DIC"}
    meses_en = {"01":"JAN","02":"FEB","03":"MAR","04":"APR","05":"MAY","06":"JUN","07":"JUL","08":"AUG","09":"SEP","10":"OCT","11":"NOV","12":"DEC"}
    m_es, m_en = meses_es[m], meses_en[m]
    return [
        f"{d}/{m}", f"{d}-{m}", f"{d} {m}", f"{d}.{m}",
        f"{m}/{d}", f"{m}-{d}", f"{m} {d}", f"{m}.{d}",
        f"{d} {m_es}", f"{m_es} {d}", f"{d} {m_en}", f"{m_en} {d}",
        f"{m_en} {int(d)}",
    ]

def limpiar_nombre_categoria(nombre: str) -> str:
    nombre = nombre.upper()
    nombre = re.sub(r'[^\w\s\/\-\.]', ' ', nombre)
    return ' '.join(nombre.split())

def normalizar_base(texto: str) -> str:
    """Mayusculas, sin tildes, sin caracteres raros. Base para toda extraccion."""
    if not texto: return ""
    t = str(texto).upper()
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return t

def extraer_hora_evento(texto_original: str):
    """
    Extrae la hora del evento sin asumir posicion fija.
    Soporta: HH:MM (24h), HH:MM AM/PM, y variantes con fecha pegada ("Aug 6 12:35 PM").
    Devuelve datetime en zona Colombia o None si no encuentra nada.
    """
    t = texto_original
    meses_en_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}

    # Formato "Aug 6 12:35 PM" o "Aug 6 2:20 PM"
    m = re.search(r'\b([A-Za-z]{3,})\s+(\d{1,2})\s+(\d{1,2}):(\d{2})\s*([AP]M)?\b', t, re.IGNORECASE)
    if m:
        mes_txt = m.group(1)[:3].lower()
        if mes_txt in meses_en_map:
            try:
                dia = int(m.group(2)); h = int(m.group(3)); mi = int(m.group(4))
                pm = m.group(5)
                if pm and pm.upper() == "PM" and h < 12: h += 12
                if pm and pm.upper() == "AM" and h == 12: h = 0
                dt = datetime.now(ZONA_COLOMBIA).replace(month=meses_en_map[mes_txt], day=dia, hour=h, minute=mi, second=0, microsecond=0)
                return dt
            except Exception:
                pass

    # Formato HH:MM con AM/PM opcional (24h o 12h), en cualquier posicion
    for m in re.finditer(r'\b(\d{1,2}):(\d{2})\b\s*([AaPp][Mm])?', t):
        try:
            h, mi = int(m.group(1)), int(m.group(2))
            ampm = m.group(3)
            if ampm and ampm.upper() == "PM" and h < 12: h += 12
            if ampm and ampm.upper() == "AM" and h == 12: h = 0
            if 0 <= h <= 23 and 0 <= mi <= 59:
                dt = datetime.now(ZONA_COLOMBIA).replace(hour=h, minute=mi, second=0, microsecond=0)
                return dt
        except Exception:
            continue
    return None

def extraer_enfrentamiento(texto: str):
    """
    Detecta patron de enfrentamiento binario: 'X vs Y', 'X vs. Y', 'X v Y', 'X @ Y'.
    Devuelve (participante_a, participante_b) o (None, None) si no aplica
    (deportes individuales/de evento: natacion, ciclismo, ajedrez, etc.)
    """
    patrones = [
        r'^(.*?)\s+vs\.?\s+(.*?)$',
        r'^(.*?)\s+@\s+(.*?)$',
        r'^(.*?)\s+\bv\b\s+(.*?)$',
    ]
    for patron in patrones:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            a, b = m.group(1).strip(), m.group(2).strip()
            if len(a) > 1 and len(b) > 1:
                return a, b
    return None, None

def limpiar_ruido_canal(texto: str) -> str:
    """Quita numeracion de feed, calidad, marcas de proveedor. No toca fecha/hora todavia."""
    t = texto
    for patron in RUIDO_CANAL:
        t = re.sub(patron, ' ', t, flags=re.IGNORECASE)
    # Separadores variados de proveedores: | ▫ • - (cuando actuan como separador, no como guion de nombre)
    t = re.sub(r'[▫•‣●]+', ' | ', t)
    return t

def limpiar_texto_para_match(texto: str) -> str:
    """Normaliza un bloque de texto (torneo, nombre de evento o participante) a bolsa de palabras comparable."""
    if not texto: return ""
    t = normalizar_base(texto)
    t = re.sub(r'\(.*?\)', '', t)
    t = re.sub(r'\[.*?\]', '', t)
    t = re.sub(r'\b\d{1,2}:\d{2}\b(\s*[AP]M)?(\s*ET)?', '', t)
    t = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|OPC\.\d+|OP\d+|PEA\s*\d+|PARA\+\s*\d+|VIVO|LIVE|COMPACTO|REPETICION|RESUMEN|PPV)\b', '', t)
    t = re.sub(r"[^A-Z0-9\s]", " ", t)
    for corto, largo in TRADUCTOR_JERGA.items():
        t = re.sub(rf"\b{corto}\b", largo, t)
    palabras = [p for p in t.split() if len(p) > 2 and p not in ["THE","AND","DEL","LAS","LOS","VS","EN","EL","DE","LA"]]
    return " ".join(palabras)

def descomponer_canal(nombre_canal: str) -> dict:
    """
    Extraccion universal, agnostica a proveedor/orden de campos.
    Devuelve: hora_dt, participante_a, participante_b, contexto_limpio (torneo o nombre de evento)
    """
    texto = limpiar_ruido_canal(nombre_canal)
    hora_dt = extraer_hora_evento(texto)

    # Quitamos el token de hora encontrado para no contaminar el resto del texto
    texto_sin_hora = re.sub(r'\b\d{1,2}:\d{2}\b\s*([AaPp][Mm])?', ' ', texto)
    texto_sin_hora = re.sub(r'\b[A-Za-z]{3,}\s+\d{1,2}\b', ' ', texto_sin_hora)  # "Aug 6"
    texto_sin_hora = re.sub(r'[|]+', ' | ', texto_sin_hora)

    participante_a, participante_b = extraer_enfrentamiento(texto_sin_hora)

    if participante_a and participante_b:
        # El "contexto" (torneo/liga) es lo que sobra fuera del enfrentamiento
        resto = texto_sin_hora.replace(participante_a, '').replace(f"vs {participante_b}", '').replace(f"vs. {participante_b}", '').replace(participante_b, '')
        contexto = limpiar_texto_para_match(resto)
        return {
            "hora_dt": hora_dt,
            "participante_a": limpiar_texto_para_match(participante_a),
            "participante_b": limpiar_texto_para_match(participante_b),
            "contexto": contexto,
            "texto_completo": limpiar_texto_para_match(texto_sin_hora),
        }
    else:
        # Deporte individual/evento unico: todo el residuo es el "nombre de evento"
        return {
            "hora_dt": hora_dt,
            "participante_a": None,
            "participante_b": None,
            "contexto": limpiar_texto_para_match(texto_sin_hora),
            "texto_completo": limpiar_texto_para_match(texto_sin_hora),
        }

def buscar_evento_rescate_duplicado(resultados_finales, canal_info, titulo_base, hora_dt):
    """
    Busca si ya existe un evento de rescate que representa el MISMO partido/evento,
    aunque el titulo textual sea distinto (ej: "Amistoso Newcastle Vs Everton" vs
    "World Cup Newcastle Vs Everton" son el mismo partido bajo dos categorias
    distintas del proveedor Xtream). El match ya NO depende de titulo_base exacto:
    compara el PAR de participantes (si existen) + proximidad horaria (<=90 min).
    Si no hay participantes (evento individual), cae de vuelta al match por
    titulo exacto, igual que antes.
    """
    tiene_participantes = canal_info["participante_a"] and canal_info["participante_b"]

    for item in resultados_finales:
        if not item["id"].startswith("rescate_"):
            continue

        if tiene_participantes and item.get("_participante_a") and item.get("_participante_b"):
            set_nuevo = {canal_info["participante_a"], canal_info["participante_b"]}
            set_existente = {item["_participante_a"], item["_participante_b"]}
            if set_nuevo != set_existente:
                continue

            if hora_dt and item.get("_hora_dt_rescate"):
                diff_min = abs((hora_dt - item["_hora_dt_rescate"]).total_seconds()) / 60.0
                if diff_min > 90:
                    continue

            return item

        elif not tiene_participantes and item["titulo"] == titulo_base:
            return item

    return None

def calcular_similitud_universal(canal_info: dict, evento_api: dict) -> float:
    """
    Combina proximidad horaria + similitud de texto.
    Funciona con o sin enfrentamiento binario (universal para todo deporte).
    """
    puntaje = 0.0

    # Señal 1: proximidad horaria (peso alto, es la señal mas confiable y universal)
    if canal_info["hora_dt"] and evento_api.get("hora_utc"):
        try:
            hora_evento_api = datetime.strptime(evento_api["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            hora_canal_utc = canal_info["hora_dt"].astimezone(timezone.utc)
            diff_min = abs((hora_evento_api - hora_canal_utc).total_seconds()) / 60.0
            if diff_min <= 15: puntaje += 40
            elif diff_min <= 30: puntaje += 30
            elif diff_min <= 60: puntaje += 15
            elif diff_min <= 120: puntaje += 5
        except Exception:
            pass

    # Señal 2: similitud de texto
    firma_api = evento_api.get("firma_texto", "")

    if canal_info["participante_a"] and canal_info["participante_b"]:
        # Enfrentamiento: comparamos ambos participantes contra la firma completa de la API
        words_a = set(canal_info["participante_a"].split())
        words_b = set(canal_info["participante_b"].split())
        words_api = set(firma_api.split())
        comunes_a = len(words_a.intersection(words_api))
        comunes_b = len(words_b.intersection(words_api))
        if comunes_a >= 1 and comunes_b >= 1:
            puntaje += 45
        elif comunes_a >= 1 or comunes_b >= 1:
            puntaje += 20
        ratio_txt = SequenceMatcher(None, canal_info["texto_completo"], firma_api).ratio() * 100
        puntaje += ratio_txt * 0.15
    else:
        # Evento individual: similitud directa de contexto contra firma de la API
        ratio_txt = SequenceMatcher(None, canal_info["contexto"], firma_api).ratio() * 100
        puntaje += ratio_txt * 0.6

    return min(puntaje, 100.0)

def adivinar_categoria_y_logo(texto: str):
    texto_upper = normalizar_base(texto)
    for categoria, keywords in CATEGORIAS_RESCATE.items():
        if any(kw in texto_upper for kw in keywords):
            return categoria, LOGOS_RESCATE.get(categoria, LOGOS_RESCATE["Deportes"] if "Deportes" in LOGOS_RESCATE else "")
    return "Deportes", LOGOS_RESCATE.get("Deportes", "")

def crear_id_seguro(titulo: str) -> str:
    import hashlib
    hash_obj = hashlib.md5(titulo.encode('utf-8'))
    return hash_obj.hexdigest()[:12]

# ─── THESPORTSDB: AGENDA MAESTRA ─────────────────────────────────────────────
def obtener_agenda_maestra() -> list:
    if os.path.exists(ARCHIVO_CACHE):
        tiempo_modificacion = os.path.getmtime(ARCHIVO_CACHE)
        if (time.time() - tiempo_modificacion) < (HORAS_CACHE * 3600):
            try:
                with open(ARCHIVO_CACHE, "r", encoding="utf-8") as f:
                    agenda = json.load(f)
                if agenda: return agenda
            except Exception:
                pass

    log.info("Descargando Agenda Maestra de TheSportsDB...")
    eventos_api = []

    for deporte_es, deporte_api in DEPORTES_MAP.items():
        log.info(f"Consultando agenda de {deporte_es}...")
        url = f"{THESPORTSDB_BASE}/eventsday.php"
        params = {"d": FECHA_HOY, "s": deporte_api}
        try:
            time.sleep(1.2)  # margen prudente bajo el limite de 30-100 req/min
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                log.warning(f"Rate limit alcanzado en {deporte_es}, esperando 5s...")
                time.sleep(5)
                r = requests.get(url, params=params, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json().get("events") or []
        except requests.exceptions.RequestException as e:
            log.warning(f"Error consultando {deporte_es}: {e}")
            continue

        for ev in data:
            try:
                fecha_str = ev.get("dateEvent")
                hora_str = ev.get("strTime") or "00:00:00"
                if not fecha_str: continue
                dt_naive = datetime.strptime(f"{fecha_str} {hora_str[:8]}", "%Y-%m-%d %H:%M:%S")
                dt_utc = dt_naive.replace(tzinfo=timezone.utc)  # TheSportsDB entrega strTime en UTC

                torneo = ev.get("strLeague", "") or ""
                eq_local = ev.get("strHomeTeam", "") or ""
                eq_visit = ev.get("strAwayTeam", "") or ""

                if eq_local and eq_visit:
                    titulo = f"{eq_local} vs {eq_visit}"
                else:
                    titulo = ev.get("strEvent", "") or ev.get("strFilename", "") or torneo

                firma_texto = limpiar_texto_para_match(f"{torneo} {titulo}")

                eventos_api.append({
                    "id": str(ev.get("idEvent")),
                    "titulo": titulo, "torneo": torneo, "categoria": deporte_es,
                    "firma_texto": firma_texto,
                    "hora_utc": dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duracion_min": DURACION_POR_DEPORTE.get(deporte_es, 150),
                    "logo_local": ev.get("strHomeTeamBadge", "") or ev.get("strThumb", "") or "",
                    "logo_visitante": ev.get("strAwayTeamBadge", "") or "",
                    "tier": 2,
                })
            except Exception:
                continue

    if eventos_api:
        with open(ARCHIVO_CACHE, "w", encoding="utf-8") as f:
            json.dump(eventos_api, f, ensure_ascii=False, indent=2)
    return eventos_api

# ─── XTREAM ───────────────────────────────────────────────────────────────
def procesar_cubo_a() -> list:
    log.info("Analizando servidor Xtream a través del puente...")
    base_url = XTREAM_URL.rstrip('/')
    api_url = f"{base_url}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
    cubo_a = []

    fechas_hoy = obtener_variaciones_fecha_hoy()
    palabras_principales = ["HOY", "TODAY", "DAILY", "DIARIO", "DIA", "VIVO", "LIVE", "EVENTS", "EVENTOS"]
    palabras_contexto = ["EVENTOS", "EVENTS", "AGENDA", "PARTIDOS", "CARTELERA", "CALENDARIO", "PPV", "SPORT"]

    try:
        # 1) Categorías: se usan solo para IDENTIFICAR cuáles category_id son "de hoy".
        # NUNCA se usan para filtrar la llamada a get_live_streams (ver nota mas abajo).
        url_cat_original = f"{api_url}&action=get_live_categories"
        r_cat = requests.get("https://mi-dashboard-tv.onrender.com/api/puente_xtream", params={"url": url_cat_original}, timeout=45)
        categorias_hoy_ids = set()

        if r_cat.status_code == 200:
            for cat in r_cat.json():
                nombre_cat = cat.get("category_name", "")
                nombre_limpio = limpiar_nombre_categoria(nombre_cat)

                match_fecha_directa = any(f in nombre_limpio for f in fechas_hoy)
                tiene_principal = any(p in nombre_limpio for p in palabras_principales)
                tiene_contexto = any(c in nombre_limpio for c in palabras_contexto)

                if match_fecha_directa or (tiene_principal and tiene_contexto):
                    categorias_hoy_ids.add(str(cat.get("category_id")))
            log.info(f"Categorias candidatas a 'hoy' detectadas: {len(categorias_hoy_ids)}")
        else:
            log.warning(f"get_live_categories devolvio HTTP {r_cat.status_code}, se continua sin filtro de categoria.")

        # 2) IMPORTANTE: se pide get_live_streams SIN category_id.
        # El filtro server-side &category_id=X de este panel Xtream demostro devolver
        # [] (vacio) con HTTP 200 incluso cuando esa categoria SI tiene streams activos
        # (confirmado manualmente). Filtrar por category_id en la URL es INSEGURO en este
        # proveedor: es mas confiable traer el catalogo completo una sola vez y filtrar en
        # Python contra 'categorias_hoy_ids'. Esto tambien reduce N llamadas a 1 sola.
        url_streams = f"{api_url}&action=get_live_streams"
        r_str = requests.get("https://mi-dashboard-tv.onrender.com/api/puente_xtream", params={"url": url_streams}, timeout=60)

        if r_str.status_code != 200:
            log.error(f"get_live_streams devolvio HTTP {r_str.status_code}. Abortando cubo_a.")
            return []

        streams_totales = r_str.json()
        log.info(f"Total streams live recibidos del panel (sin filtrar): {len(streams_totales)}")

        vistos = set()
        for s in streams_totales:
            stream_id = str(s.get("stream_id"))
            if stream_id in vistos: continue
            vistos.add(stream_id)

            nombre_canal = s.get("name", "").strip()
            if not nombre_canal: continue

            cat_id_stream = str(s.get("category_id"))

            # Si detectamos categorias "de hoy", el stream debe pertenecer a una de ellas
            # O contener una hora/keyword reconocible (red de seguridad si el mapeo de
            # categorias fallo o cambio de id).
            pertenece_a_categoria_hoy = (not categorias_hoy_ids) or (cat_id_stream in categorias_hoy_ids)

            tiene_hora = extraer_hora_evento(nombre_canal) is not None
            tiene_kw_evento = any(kw in nombre_canal.upper() for kw in ["PPV", "LIVE EVENT", "PEA ", "PARA+"])

            if not categorias_hoy_ids:
                match_fecha = re.search(r'\b(\d{1,2})[-/](\d{1,2})\b', nombre_canal)
                if match_fecha:
                    d_str, m_str = match_fecha.group(1), match_fecha.group(2)
                    if not any(f in f"{d_str}/{m_str}" or f in f"{m_str}/{d_str}" for f in fechas_hoy):
                        continue

            if pertenece_a_categoria_hoy and (tiene_hora or tiene_kw_evento):
                cubo_a.append({
                    "id_xtream": stream_id,
                    "nombre_ui": nombre_canal,
                })

        log.info(f"Eventos candidatos detectados en cubo_a: {len(cubo_a)}")
        return cubo_a
    except Exception as e:
        log.error(f"Error procesando Xtream: {e}")
        return []

# ─── RESOLUCIÓN DE HOST DE MEDIA ─────────────────────────────────────────────
def detectar_base_media_m3u() -> str:
    base_api = XTREAM_URL.rstrip("/")
    url_m3u = f"{base_api}/get.php?username={XTREAM_USER}&password={XTREAM_PASS}&type=m3u&output=ts"
    candidatos = []
    try:
        with requests.get(url_m3u, headers={"Range": "bytes=0-65535"}, stream=True, timeout=(15, 30)) as respuesta:
            if respuesta.status_code not in (200, 206):
                log.warning(f"No se pudo leer la muestra M3U: HTTP {respuesta.status_code}. Se conserva XTREAM_URL como base de media.")
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

            log.warning("No se obtuvo una muestra M3U consistente. Se conserva XTREAM_URL como base de media.")
            return base_api
    except requests.exceptions.RequestException as e:
        log.warning(f"No se pudo detectar la base de media desde M3U: {e}. Se conserva XTREAM_URL como base de media.")
        return base_api

# ─── ORQUESTADOR ─────────────────────────────────────────────────────────────
def main():
    log.info("=== Iniciando Curador Base (TheSportsDB + Xtream) ===")
    agenda_api = obtener_agenda_maestra()
    cubo_a = procesar_cubo_a()

    if not cubo_a:
        log.warning("No se detectaron eventos temporales en Xtream para procesar.")
        return

    resultados_finales = []
    base_media = detectar_base_media_m3u()

    for canal in cubo_a:
        canal_info = descomponer_canal(canal["nombre_ui"])
        url_video = f"{base_media}/{XTREAM_USER}/{XTREAM_PASS}/{canal['id_xtream']}"
        fuente_limpia = {"nombre": canal["nombre_ui"], "url": url_video}

        match_encontrado = False
        mejor_evento, mejor_puntaje = None, 0

        if agenda_api:
            for ev in agenda_api:
                puntaje = calcular_similitud_universal(canal_info, ev)
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
            # Bloque de rescate: sin match en agenda, se construye el evento desde el propio canal
            if canal_info["participante_a"] and canal_info["participante_b"]:
                titulo_base = f"{canal_info['participante_a'].title()} Vs {canal_info['participante_b'].title()}"
            else:
                titulo_original = re.sub(r'\b(HD|SD|FHD|4K|ENG|ESP|GER|PPV)\b', '', canal["nombre_ui"], flags=re.IGNORECASE)
                titulo_original = re.sub(r'\bLIVE\s*EVENT\s*\d*\b', '', titulo_original, flags=re.IGNORECASE)
                titulo_base = titulo_original.strip(" -|▫:").title()

            if not titulo_base: continue

            hora_dt_rescate = canal_info["hora_dt"] or datetime.now(ZONA_COLOMBIA)
            hora_dt_rescate_utc = hora_dt_rescate.astimezone(timezone.utc)

            evento_existente = buscar_evento_rescate_duplicado(resultados_finales, canal_info, titulo_base, hora_dt_rescate_utc)

            if evento_existente:
                if not any(f["url"] == url_video for f in evento_existente["fuentes"]):
                    evento_existente["fuentes"].append(fuente_limpia)
            else:
                categoria_adivinada, logo_adivinado = adivinar_categoria_y_logo(canal["nombre_ui"])
                hora_utc_calculada = hora_dt_rescate_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

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
                    "fuentes": [fuente_limpia],
                    "_participante_a": canal_info["participante_a"],
                    "_participante_b": canal_info["participante_b"],
                    "_hora_dt_rescate": hora_dt_rescate_utc,
                })

    resultados_finales.sort(key=lambda x: x["hora_utc"])

    for ev in resultados_finales:
        ev.pop("_participante_a", None)
        ev.pop("_participante_b", None)
        ev.pop("_hora_dt_rescate", None)

    try:
        with open("eventos_hoy.json", "w", encoding="utf-8") as f:
            json.dump(resultados_finales, f, ensure_ascii=False, indent=2)
        log.info(f"¡Proceso Terminado! Total de eventos listos: {len(resultados_finales)}")
    except Exception as e:
        log.error(f"Error crítico guardando el archivo JSON: {e}")

if __name__ == "__main__":
    main()
