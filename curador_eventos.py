# -*- coding: utf-8 -*-
"""
CURADOR DE EVENTOS DEPORTIVOS — AllStreamTV

Principios de esta versión:
- Una única zona horaria de producto, configurable con APP_TIMEZONE.
- Agenda deportiva normalizada, con caché y control explícito de tasa.
- Correlación conservadora: no confunde deportes, sesiones, resúmenes ni eventos
  que solo comparten términos genéricos.
- Trazabilidad por evento y por señal descartada para depurar el catálogo.
- Salida compatible con la estructura previa de eventos_hoy.json; añade campos
  de confianza que pueden ser ignorados por clientes antiguos.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests


# ─── CONFIGURACIÓN ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("curador")

XTREAM_URL = (os.environ.get("XTREAM_URL") or "").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER") or ""
XTREAM_PASS = os.environ.get("XTREAM_PASS") or ""
PUENTE_URL = os.environ.get("PUENTE_URL") or "https://mi-dashboard-tv.onrender.com/api/puente_xtream"

THESPORTSDB_KEY = os.environ.get("THESPORTSDB_KEY") or "123"
THESPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}"
# El plan gratuito documenta 30 req/min. 2,1 s mantiene una cadencia segura.
THESPORTSDB_MIN_INTERVAL = max(float(os.environ.get("THESPORTSDB_MIN_INTERVAL", "2.1")), 0.0)
THESPORTSDB_MAX_RETRIES = max(int(os.environ.get("THESPORTSDB_MAX_RETRIES", "3")), 0)

APP_TIMEZONE = os.environ.get("APP_TIMEZONE") or "America/Bogota"
HORAS_CACHE = max(int(os.environ.get("HORAS_CACHE", "3")), 0)
ARCHIVO_CACHE = Path(os.environ.get("ARCHIVO_CACHE", "agenda_api_v9.json"))
ARCHIVO_SALIDA = Path(os.environ.get("ARCHIVO_SALIDA", "eventos_hoy.json"))
ARCHIVO_CUARENTENA = Path(os.environ.get("ARCHIVO_CUARENTENA", "eventos_descartados.json"))
ARCHIVO_META = Path(os.environ.get("ARCHIVO_META", "meta_curador.json"))
MAX_CUARENTENA = max(int(os.environ.get("MAX_CUARENTENA", "1000")), 0)

# Se consulta primero el calendario general y después deportes donde la cobertura
# del endpoint general suele ser irregular. La lista puede ampliarse por entorno.
DEPORTES_TSDB = [
    "Soccer", "Basketball", "Tennis", "Motorsport", "Baseball", "Ice Hockey",
    "Volleyball", "Rugby", "American Football", "Fighting", "Cycling", "Golf",
    "Snooker", "Athletics", "Gymnastics", "Swimming", "Darts",
]

CATEGORIAS = {
    "SOCCER": "Fútbol",
    "FOOTBALL": "Fútbol",
    "BASKETBALL": "Baloncesto",
    "TENNIS": "Tenis",
    "MOTORSPORT": "Motor",
    "FORMULA 1": "Motor",
    "BASEBALL": "Béisbol",
    "ICE HOCKEY": "Hockey",
    "HOCKEY": "Hockey",
    "VOLLEYBALL": "Voleibol",
    "RUGBY": "Rugby",
    "AMERICAN FOOTBALL": "Fútbol Americano",
    "FIGHTING": "Combate",
    "MMA": "Combate",
    "BOXING": "Combate",
    "WRESTLING": "Combate",
    "CYCLING": "Ciclismo",
    "GOLF": "Golf",
    "SNOOKER": "Snooker",
    "ATHLETICS": "Atletismo",
    "GYMNASTICS": "Gimnasia",
    "SWIMMING": "Natación",
    "DARTS": "Dardos",
}

DURACION_POR_CATEGORIA = {
    "Fútbol": 130, "Baloncesto": 160, "Tenis": 210, "Motor": 210,
    "Béisbol": 210, "Hockey": 160, "Voleibol": 160, "Rugby": 160,
    "Fútbol Americano": 220, "Combate": 210, "Ciclismo": 330, "Golf": 300,
    "Snooker": 240, "Atletismo": 180, "Gimnasia": 180, "Natación": 150,
    "Dardos": 180, "Deportes": 150,
}

# Términos que no identifican inequívocamente un evento. Se eliminan antes de
# comparar títulos, pero se conservan en la interfaz original.
STOPWORDS = {
    "A", "AL", "AND", "AT", "BY", "CON", "COPA", "DE", "DEL", "EL", "EN", "FOR",
    "GRAND", "GRAN", "HD", "LA", "LAS", "LEAGUE", "LIVE", "LOS", "OF", "ON", "OPEN",
    "PARA", "PARTIDO", "PPV", "RACE", "SERIES", "SPORT", "THE", "TO", "TOUR", "TV",
    "UN", "UNA", "VS", "VIVO", "WORLD", "CAMPEONATO", "CHAMPIONSHIP", "EVENT", "EVENTS",
    "FIGHT", "NIGHT", "MATCH", "DIRECTO", "HOY", "TODAY", "DAILY", "DIARIO", "DIA",
    "PRACTICE", "ENTRENAMIENTO", "ROUND", "FINAL", "FASE", "JORNADA",
}

# Contenido que se debe excluir del curador de eventos en vivo. No se descarta
# una práctica/clasificación: se rechaza solo si no tiene un evento oficial de la
# misma sesión, lo que evita vender un compacto como una carrera.
VETO_EMISION = {
    "REPETICION", "REPETICIÓN", "REPLAY", "RESUMEN", "HIGHLIGHTS", "COMPACTO",
    "NOTICIAS", "NEWS", "MAGAZINE", "PREVIA", "POSTPARTIDO", "POST PARTIDO",
    "DOCUMENTAL", "CLASICOS", "CLÁSICOS", "MEMORIAS", "VINTAGE", "MEJORES MOMENTOS",
}

# Las sesiones tienen que coincidir cuando están explícitas en ambos lados.
SESIONES = {
    "PRACTICE": "practica", "PRACTICA": "practica", "ENTRENAMIENTO": "practica",
    "QUALIFYING": "clasificacion", "CLASIFICACION": "clasificacion",
    "CLASIFICACIÓN": "clasificacion", "POLE": "clasificacion",
    "SPRINT": "sprint", "RACE": "carrera", "CARRERA": "carrera",
    "PARTIDO": "partido", "MATCH": "partido", "PELEA": "pelea", "FIGHT": "pelea",
}

DEPORTE_PISTAS = {
    "Fútbol": {"SOCCER", "FUTBOL", "LALIGA", "PREMIER", "BUNDESLIGA", "SERIE A", "CHAMPIONS"},
    "Baloncesto": {"NBA", "BASKET", "BALONCESTO", "EUROLEAGUE"},
    "Tenis": {"TENNIS", "TENIS", "ATP", "WTA", "ROLAND GARROS", "WIMBLEDON"},
    "Motor": {"FORMULA", "F1", "MOTOGP", "MOTO GP", "NASCAR", "RALLY", "INDYCAR"},
    "Béisbol": {"BASEBALL", "BEISBOL", "MLB"},
    "Hockey": {"HOCKEY"},
    "Voleibol": {"VOLLEY", "VOLEIBOL"},
    "Rugby": {"RUGBY"},
    "Fútbol Americano": {"NFL", "AMERICAN FOOTBALL", "FUTBOL AMERICANO"},
    "Combate": {"UFC", "MMA", "BKFC", "BOXING", "BOXEO", "WWE", "WRESTLING", "KICKBOXING"},
    "Ciclismo": {"CYCLING", "CICLISMO", "VUELTA", "GIRO", "TOUR DE FRANCE"},
    "Golf": {"GOLF", "PGA", "DP WORLD", "LIV GOLF"},
    "Snooker": {"SNOOKER"},
    "Atletismo": {"ATHLETICS", "ATLETISMO"},
    "Gimnasia": {"GYMNASTICS", "GIMNASIA"},
    "Natación": {"SWIMMING", "NATACION"},
    "Dardos": {"DARTS", "DARDOS"},
}


# ─── UTILIDADES DE TEXTO Y TIEMPO ───────────────────────────────────────────
def normalizar_texto(texto: Any) -> str:
    """Devuelve mayúsculas sin tildes ni puntuación irrelevante."""
    if texto is None:
        return ""
    valor = unicodedata.normalize("NFD", str(texto).upper())
    valor = "".join(c for c in valor if unicodedata.category(c) != "Mn")
    valor = re.sub(r"[^A-Z0-9\s]", " ", valor)
    return " ".join(valor.split())


def tokenizar(texto: Any, *, conservar_genericos: bool = False) -> set[str]:
    palabras = {p for p in normalizar_texto(texto).split() if len(p) >= 3 and not p.isdigit()}
    return palabras if conservar_genericos else palabras - STOPWORDS


def categoria_deporte(deporte: Any) -> str:
    normalizado = normalizar_texto(deporte)
    if normalizado in CATEGORIAS:
        return CATEGORIAS[normalizado]
    for clave, categoria in CATEGORIAS.items():
        if clave in normalizado or normalizado in clave:
            return categoria
    return "Deportes"


def inferir_deporte(texto: Any) -> Optional[str]:
    """Infiera categoría solo cuando la pista es inequívoca."""
    valor = normalizar_texto(texto)
    hallazgos: list[str] = []
    for categoria, pistas in DEPORTE_PISTAS.items():
        for pista in pistas:
            if normalizar_texto(pista) in valor:
                hallazgos.append(categoria)
                break
    # Si hay más de una disciplina explícita, el nombre no sirve para validar.
    unicos = list(dict.fromkeys(hallazgos))
    return unicos[0] if len(unicos) == 1 else None


def extraer_sesion(texto: Any) -> Optional[str]:
    valor = normalizar_texto(texto)
    encontrados = {sesion for pista, sesion in SESIONES.items() if pista in valor}
    if not encontrados:
        return None
    # Una clasificación sprint y una carrera sprint son sesiones distintas. El
    # valor "sprint" sin calificativo queda deliberadamente genérico para poder
    # asociarse con una carrera sprint oficial, pero nunca con clasificación.
    if "sprint" in encontrados and "clasificacion" in encontrados:
        return "clasificacion_sprint"
    if "sprint" in encontrados and "carrera" in encontrados:
        return "carrera_sprint"
    if "sprint" in encontrados:
        return "sprint"
    return sorted(encontrados)[0]


def contiene_veto(texto: Any) -> bool:
    valor = normalizar_texto(texto)
    return any(palabra in valor for palabra in VETO_EMISION)


def obtener_zona_aplicacion() -> ZoneInfo:
    try:
        return ZoneInfo(APP_TIMEZONE)
    except Exception:
        log.warning("APP_TIMEZONE inválida (%s); se usará UTC.", APP_TIMEZONE)
        return ZoneInfo("UTC")


def ahora_local() -> datetime:
    return datetime.now(obtener_zona_aplicacion())


def parsear_fecha_hora_tsdb(evento: dict[str, Any]) -> Optional[datetime]:
    """Convierte timestamp TheSportsDB a UTC, privilegiando strTimestamp."""
    timestamp = (evento.get("strTimestamp") or "").strip()
    if timestamp:
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass

    fecha = (evento.get("dateEvent") or "").strip()
    hora = (evento.get("strTime") or "00:00:00").strip()
    if not fecha:
        return None
    try:
        hora = (hora + ":00:00")[:8]
        return datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def extraer_hora_canal(texto: str, fecha_base: datetime) -> Optional[datetime]:
    """Extrae hora; la fecha embebida se usa solo como pista, no como verdad de zona."""
    valor = texto or ""
    # HH:MM, 8:30pm y variantes sin espacio.
    for match in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})\s*([APap][Mm])?(?!\d)", valor):
        hora, minuto = int(match.group(1)), int(match.group(2))
        ampm = (match.group(3) or "").upper()
        if ampm == "PM" and hora < 12:
            hora += 12
        elif ampm == "AM" and hora == 12:
            hora = 0
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return fecha_base.replace(hour=hora, minute=minuto, second=0, microsecond=0)

    for match in re.finditer(r"(?<!\d)(\d{1,2})\s*([APap][Mm])(?!\w)", valor):
        hora = int(match.group(1))
        ampm = match.group(2).upper()
        if ampm == "PM" and hora < 12:
            hora += 12
        elif ampm == "AM" and hora == 12:
            hora = 0
        if 0 <= hora <= 23:
            return fecha_base.replace(hour=hora, minute=0, second=0, microsecond=0)
    return None


def variaciones_fecha(fecha: datetime) -> set[str]:
    meses_es = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
    meses_en = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    d, m = fecha.day, fecha.month
    return {
        f"{d:02d}/{m:02d}", f"{d:02d}-{m:02d}", f"{m:02d}/{d:02d}", f"{m:02d}-{d:02d}",
        f"{d} {meses_es[m - 1]}", f"{meses_es[m - 1]} {d}",
        f"{d} {meses_en[m - 1]}", f"{meses_en[m - 1]} {d}",
    }


def iso_utc(fecha: datetime) -> str:
    return fecha.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── HTTP Y AGENDA ──────────────────────────────────────────────────────────
class ClienteTheSportsDB:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.ultimo_request = 0.0
        self.estadisticas = Counter()

    def get_json(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
        url = f"{THESPORTSDB_BASE}/{endpoint.lstrip('/')}"
        for intento in range(THESPORTSDB_MAX_RETRIES + 1):
            espera = THESPORTSDB_MIN_INTERVAL - (time.monotonic() - self.ultimo_request)
            if espera > 0:
                time.sleep(espera)
            try:
                respuesta = self.session.get(url, params=params, timeout=(10, 30))
                self.ultimo_request = time.monotonic()
                self.estadisticas["solicitudes"] += 1
            except requests.RequestException as exc:
                self.estadisticas["errores_red"] += 1
                log.warning("TheSportsDB sin respuesta (%s): %s", endpoint, exc)
                if intento < THESPORTSDB_MAX_RETRIES:
                    time.sleep(2 ** intento)
                    continue
                return None

            if respuesta.status_code == 200:
                try:
                    return respuesta.json()
                except ValueError:
                    self.estadisticas["json_invalido"] += 1
                    log.warning("TheSportsDB devolvió JSON inválido en %s.", endpoint)
                    return None

            self.estadisticas[f"http_{respuesta.status_code}"] += 1
            if respuesta.status_code == 429 and intento < THESPORTSDB_MAX_RETRIES:
                espera_429 = min(60, 8 * (intento + 1))
                log.warning("TheSportsDB limitó la solicitud; reintento en %ss.", espera_429)
                time.sleep(espera_429)
                continue
            log.warning("TheSportsDB respondió HTTP %s en %s.", respuesta.status_code, endpoint)
            return None
        return None


def normalizar_evento_tsdb(evento: dict[str, Any]) -> Optional[dict[str, Any]]:
    inicio = parsear_fecha_hora_tsdb(evento)
    if inicio is None:
        return None

    local = (evento.get("strHomeTeam") or "").strip()
    visitante = (evento.get("strAwayTeam") or "").strip()
    torneo = (evento.get("strLeague") or "").strip()
    titulo_original = (evento.get("strEvent") or evento.get("strFilename") or torneo).strip()
    if not titulo_original and not (local and visitante):
        return None

    if local and visitante:
        titulo = f"{local} vs {visitante}"
        tipo = "duelo"
    else:
        titulo = titulo_original
        tipo = "sencillo"

    categoria = categoria_deporte(evento.get("strSport") or "")
    return {
        "id": str(evento.get("idEvent") or ""),
        "titulo": titulo,
        "torneo": torneo,
        "categoria": categoria,
        "tipo_evento": tipo,
        "equipo_local": local,
        "equipo_visitante": visitante,
        "subtitulo": titulo_original if tipo == "sencillo" else "",
        "hora_utc": iso_utc(inicio),
        "duracion_min": DURACION_POR_CATEGORIA.get(categoria, 150),
        "logo_torneo": evento.get("strLeagueBadge") or evento.get("strBadge") or "",
        "logo_local": evento.get("strHomeTeamBadge") or evento.get("strThumb") or "",
        "logo_visitante": evento.get("strAwayTeamBadge") or "",
        "tier": 1,
        # Campos de auditoría que no rompen consumidores del esquema anterior.
        "origen": "thesportsdb",
        "estado": "confirmado",
        "confianza": "alta",
        "puntuacion_confianza": 100,
        "fuentes": [],
    }


def _leer_cache(fecha_consulta: str) -> Optional[list[dict[str, Any]]]:
    if HORAS_CACHE <= 0 or not ARCHIVO_CACHE.exists():
        return None
    antiguedad = time.time() - ARCHIVO_CACHE.stat().st_mtime
    if antiguedad > HORAS_CACHE * 3600:
        return None
    try:
        datos = json.loads(ARCHIVO_CACHE.read_text(encoding="utf-8"))
        if datos.get("fecha") == fecha_consulta and isinstance(datos.get("eventos"), list):
            return datos["eventos"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def obtener_agenda_maestra(fecha_consulta: str, cliente: Optional[ClienteTheSportsDB] = None) -> list[dict[str, Any]]:
    """Obtiene y unifica agenda general y por deporte sin duplicar idEvent."""
    cache = _leer_cache(fecha_consulta)
    if cache is not None:
        log.info("Agenda cargada de caché: %d eventos.", len(cache))
        return cache

    cliente = cliente or ClienteTheSportsDB()
    ids: set[str] = set()
    agenda: list[dict[str, Any]] = []

    consultas: list[tuple[str, dict[str, str]]] = [("eventsday.php", {"d": fecha_consulta})]
    consultas.extend(("eventsday.php", {"d": fecha_consulta, "s": deporte}) for deporte in DEPORTES_TSDB)

    for endpoint, params in consultas:
        datos = cliente.get_json(endpoint, params)
        for evento in (datos or {}).get("events") or []:
            normalizado = normalizar_evento_tsdb(evento)
            if not normalizado or not normalizado["id"] or normalizado["id"] in ids:
                continue
            ids.add(normalizado["id"])
            agenda.append(normalizado)

    agenda.sort(key=lambda e: e["hora_utc"])
    try:
        ARCHIVO_CACHE.write_text(
            json.dumps({"fecha": fecha_consulta, "eventos": agenda}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("No se pudo guardar la caché de agenda: %s", exc)

    log.info(
        "Agenda TheSportsDB lista: %d eventos | %d solicitudes | HTTP 429: %d",
        len(agenda), cliente.estadisticas["solicitudes"], cliente.estadisticas["http_429"],
    )
    return agenda


# ─── XTREAM Y SELECCIÓN DE CANDIDATOS ────────────────────────────────────────
def llamada_xtream(url: str, timeout: int = 45) -> Any:
    respuesta = requests.get(PUENTE_URL, params={"url": url}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.json()


def detectar_categorias_de_hoy(fecha_local: datetime) -> set[str]:
    if not XTREAM_URL:
        return set()
    api = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_categories"
    marcas_fecha = variaciones_fecha(fecha_local)
    ids: set[str] = set()
    try:
        for categoria in llamada_xtream(api, timeout=45) or []:
            nombre = normalizar_texto(categoria.get("category_name") or "")
            if any(normalizar_texto(marca) in nombre for marca in marcas_fecha):
                ids.add(str(categoria.get("category_id") or ""))
    except (requests.RequestException, ValueError, TypeError) as exc:
        log.warning("No se pudieron leer las categorías Xtream: %s", exc)
    ids.discard("")
    return ids


def es_candidato(canal: dict[str, Any], categorias_hoy: set[str], fecha_local: datetime) -> bool:
    nombre = str(canal.get("name") or "").strip()
    if not nombre or contiene_veto(nombre):
        return False

    normalizado = normalizar_texto(nombre)
    categoria_id = str(canal.get("category_id") or "")
    tiene_hora = extraer_hora_canal(nombre, fecha_local) is not None
    tiene_fecha = any(normalizar_texto(marca) in normalizado for marca in variaciones_fecha(fecha_local))
    deporte = inferir_deporte(nombre)
    tiene_duelo = bool(re.search(r"\b(VS|V|AT)\b|\s[-@]\s", normalizado))
    tiene_marca_evento = any(marca in normalizado for marca in ("PPV", "LIVE EVENT", "EVENTOS", "EVENTS"))

    # Una categoría fechada es una señal de contexto. Se exige además hora o una
    # identidad deportiva; no se absorben todos los canales de la categoría.
    en_categoria_hoy = bool(categorias_hoy and categoria_id in categorias_hoy)
    return bool(
        (en_categoria_hoy and (tiene_hora or deporte or tiene_duelo or tiene_marca_evento))
        or (tiene_fecha and (tiene_hora or deporte or tiene_duelo))
        or (tiene_hora and (deporte is not None or tiene_duelo))
    )


def obtener_canales_candidatos(fecha_local: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    metricas = Counter()
    if not XTREAM_URL or not XTREAM_USER or not XTREAM_PASS:
        log.error("Faltan XTREAM_URL, XTREAM_USER o XTREAM_PASS.")
        return [], metricas

    categorias_hoy = detectar_categorias_de_hoy(fecha_local)
    metricas["categorias_hoy"] = len(categorias_hoy)
    api = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        streams = llamada_xtream(api, timeout=75) or []
    except (requests.RequestException, ValueError, TypeError) as exc:
        log.error("No se pudieron obtener streams Xtream: %s", exc)
        return [], metricas

    metricas["streams_totales"] = len(streams)
    candidatos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for stream in streams:
        stream_id = str(stream.get("stream_id") or "")
        if not stream_id or stream_id in vistos:
            continue
        vistos.add(stream_id)
        if not es_candidato(stream, categorias_hoy, fecha_local):
            continue
        nombre = str(stream.get("name") or "").strip()
        candidatos.append({
            "id_xtream": stream_id,
            "nombre_ui": nombre,
            "texto_normalizado": normalizar_texto(nombre),
            "tokens": tokenizar(nombre),
            "hora_local": extraer_hora_canal(nombre, fecha_local),
            "categoria_inferida": inferir_deporte(nombre),
            "sesion": extraer_sesion(nombre),
            "categoria_xtream": str(stream.get("category_id") or ""),
        })
    metricas["candidatos"] = len(candidatos)
    log.info(
        "Xtream: %d streams | %d categorías del día | %d candidatos útiles.",
        metricas["streams_totales"], metricas["categorias_hoy"], metricas["candidatos"],
    )
    return candidatos, metricas


def detectar_base_media_m3u() -> str:
    """Obtiene la base real de medios sin almacenar credenciales en el JSON."""
    if not XTREAM_URL or not XTREAM_USER or not XTREAM_PASS:
        return XTREAM_URL
    m3u = f"{XTREAM_URL}/get.php?username={XTREAM_USER}&password={XTREAM_PASS}&type=m3u&output=ts"
    candidatos: list[str] = []
    try:
        with requests.get(m3u, headers={"Range": "bytes=0-65535"}, stream=True, timeout=(15, 35)) as respuesta:
            if respuesta.status_code not in (200, 206):
                return XTREAM_URL
            for linea in respuesta.iter_lines(chunk_size=8192):
                url = linea.decode("utf-8", errors="ignore").strip()
                if not url.startswith(("http://", "https://")):
                    continue
                parsed = urlparse(url)
                partes = [p for p in parsed.path.split("/") if p]
                if len(partes) >= 3 and partes[-3] == XTREAM_USER and partes[-2] == XTREAM_PASS:
                    candidatos.append(f"{parsed.scheme}://{parsed.netloc}")
                if len(candidatos) >= 3:
                    break
    except requests.RequestException:
        return XTREAM_URL
    return candidatos[0] if len(candidatos) >= 3 and len(set(candidatos)) == 1 else XTREAM_URL


# ─── CORRELACIÓN ────────────────────────────────────────────────────────────
def _inicio_evento(evento: dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.strptime(evento["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, KeyError, TypeError):
        return None


def _deportes_compatibles(canal: dict[str, Any], evento: dict[str, Any]) -> bool:
    inferido = canal.get("categoria_inferida")
    return not inferido or inferido == evento.get("categoria")


def _sesiones_compatibles(canal: dict[str, Any], evento: dict[str, Any]) -> bool:
    sesion_canal = canal.get("sesion")
    sesion_evento = extraer_sesion(f"{evento.get('titulo', '')} {evento.get('subtitulo', '')}")
    if not sesion_canal or not sesion_evento or sesion_canal == sesion_evento:
        return True
    # La agenda puede abreviar "Sprint" para una carrera sprint. Se permite esa
    # relación direccional, pero nunca se confunde con clasificación sprint.
    return sesion_evento == "sprint" and sesion_canal == "carrera_sprint"


def _proximidad_horaria(canal: dict[str, Any], evento: dict[str, Any]) -> tuple[bool, Optional[float]]:
    """Solo penaliza desfases grandes; la fecha de rótulos IPTV no es una zona fiable."""
    hora_canal = canal.get("hora_local")
    inicio_evento = _inicio_evento(evento)
    if hora_canal is None or inicio_evento is None:
        return True, None
    diferencia = abs((hora_canal.astimezone(timezone.utc) - inicio_evento).total_seconds()) / 60
    # Se permite hasta 6 h: muchos proveedores rotulan en otra zona o anuncian
    # con antelación. Fuera de ese intervalo el texto debe ser excepcionalmente fuerte.
    return diferencia <= 360, diferencia


def calcular_similitud_simple(titulo: str, torneo: str, canal: str) -> tuple[int, list[str]]:
    """Calcula una puntuación explicable para eventos individuales."""
    tokens_evento = tokenizar(f"{titulo} {torneo}")
    tokens_canal = tokenizar(canal)
    comunes = sorted(tokens_evento.intersection(tokens_canal))
    if not comunes:
        return 0, []

    # La cobertura del nombre oficial es más valiosa que el número bruto de tokens.
    cobertura = len(comunes) / max(len(tokens_evento), 1)
    precision = len(comunes) / max(len(tokens_canal), 1)
    puntuacion = round(100 * (0.76 * cobertura + 0.24 * precision))

    evento_norm = normalizar_texto(titulo)
    canal_norm = normalizar_texto(canal)
    if evento_norm and evento_norm in canal_norm:
        puntuacion = max(puntuacion, 92)
    # Una palabra única, no genérica y de longitud alta puede identificar eventos
    # como "Vuelta", pero no basta para términos cortos o ambiguos.
    if len(comunes) == 1 and len(comunes[0]) >= 7:
        puntuacion = max(puntuacion, 58)
    return min(puntuacion, 100), comunes


def emparejar_duelo(canal: dict[str, Any], eventos: Iterable[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], int, list[str]]:
    mejor: Optional[dict[str, Any]] = None
    mejor_puntuacion = 0
    mejor_razones: list[str] = []
    for evento in eventos:
        if evento.get("tipo_evento") != "duelo" or not _deportes_compatibles(canal, evento):
            continue
        local = tokenizar(evento.get("equipo_local", ""))
        visitante = tokenizar(evento.get("equipo_visitante", ""))
        if not local or not visitante:
            continue
        acierto_local = sorted(local.intersection(canal["tokens"]))
        acierto_visitante = sorted(visitante.intersection(canal["tokens"]))
        if not acierto_local or not acierto_visitante:
            continue
        cercano, diferencia = _proximidad_horaria(canal, evento)
        puntuacion = 82 + min(8, 4 * (len(acierto_local) + len(acierto_visitante)))
        razones = [f"equipo_local:{','.join(acierto_local)}", f"equipo_visitante:{','.join(acierto_visitante)}"]
        if diferencia is not None:
            razones.append(f"diferencia_min:{round(diferencia)}")
            if cercano:
                puntuacion += 5
            elif puntuacion < 96:
                continue
        if puntuacion > mejor_puntuacion:
            mejor, mejor_puntuacion, mejor_razones = evento, min(puntuacion, 100), razones
    return mejor, mejor_puntuacion, mejor_razones


def emparejar_sencillo(canal: dict[str, Any], eventos: Iterable[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], int, list[str]]:
    mejor: Optional[dict[str, Any]] = None
    mejor_puntuacion = 0
    mejor_razones: list[str] = []
    for evento in eventos:
        if evento.get("tipo_evento") != "sencillo" or not _deportes_compatibles(canal, evento):
            continue
        if not _sesiones_compatibles(canal, evento):
            continue
        puntuacion, comunes = calcular_similitud_simple(evento.get("titulo", ""), evento.get("torneo", ""), canal["nombre_ui"])
        if not comunes:
            continue
        cercano, diferencia = _proximidad_horaria(canal, evento)
        # Se piden dos palabras distintivas, salvo una frase oficial íntegra o un
        # identificador largo con una disciplina explícita compatible.
        texto_evento = normalizar_texto(evento.get("titulo", ""))
        texto_canal = canal["texto_normalizado"]
        frase_completa = bool(texto_evento and texto_evento in texto_canal)
        unica_fuerte = len(comunes) == 1 and len(comunes[0]) >= 7 and canal.get("categoria_inferida") == evento.get("categoria")
        if len(comunes) < 2 and not frase_completa and not unica_fuerte:
            continue
        if not cercano and puntuacion < 92:
            continue
        if cercano:
            puntuacion = min(100, puntuacion + 5)
        razones = [f"tokens:{','.join(comunes)}"]
        if diferencia is not None:
            razones.append(f"diferencia_min:{round(diferencia)}")
        if puntuacion > mejor_puntuacion:
            mejor, mejor_puntuacion, mejor_razones = evento, puntuacion, razones
    return mejor, mejor_puntuacion, mejor_razones


def emparejar_evento(canal: dict[str, Any], agenda: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], int, str, list[str]]:
    duelo, puntuacion_duelo, razones_duelo = emparejar_duelo(canal, agenda)
    sencillo, puntuacion_sencillo, razones_sencillo = emparejar_sencillo(canal, agenda)
    if duelo and puntuacion_duelo >= puntuacion_sencillo:
        return duelo, puntuacion_duelo, "duelo_verificado", razones_duelo
    if sencillo:
        return sencillo, puntuacion_sencillo, "evento_simple_verificado", razones_sencillo
    return None, 0, "sin_coincidencia_verificable", []


def fusionar_fuente(evento: dict[str, Any], fuente: dict[str, Any]) -> None:
    fuentes = evento.setdefault("fuentes", [])
    if not any(str(x.get("id_xtream")) == str(fuente.get("id_xtream")) for x in fuentes):
        fuentes.append(fuente)


def curar_eventos(agenda: list[dict[str, Any]], candidatos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter]:
    resultados: dict[str, dict[str, Any]] = {}
    cuarentena: list[dict[str, Any]] = []
    metricas = Counter()

    for canal in candidatos:
        evento, puntuacion, metodo, razones = emparejar_evento(canal, agenda)
        fuente = {"nombre": canal["nombre_ui"], "id_xtream": canal["id_xtream"]}
        if evento is None:
            metricas["cuarentena"] += 1
            if len(cuarentena) < MAX_CUARENTENA:
                cuarentena.append({
                    **fuente,
                    "motivo": metodo,
                    "categoria_inferida": canal.get("categoria_inferida"),
                    "sesion_inferida": canal.get("sesion"),
                    "texto_analizado": canal["texto_normalizado"],
                })
            continue

        clave = evento["id"]
        if clave not in resultados:
            clon = {k: v for k, v in evento.items() if k != "fuentes"}
            clon["fuentes"] = []
            clon["metodo_correlacion"] = metodo
            clon["razones_correlacion"] = razones
            clon["puntuacion_confianza"] = puntuacion
            clon["confianza"] = "alta" if puntuacion >= 85 else "media"
            clon["estado"] = "confirmado" if puntuacion >= 80 else "probable"
            resultados[clave] = clon
        else:
            # Conserva siempre la mejor evidencia de una misma tarjeta.
            if puntuacion > int(resultados[clave].get("puntuacion_confianza", 0)):
                resultados[clave]["puntuacion_confianza"] = puntuacion
                resultados[clave]["confianza"] = "alta" if puntuacion >= 85 else "media"
                resultados[clave]["estado"] = "confirmado" if puntuacion >= 80 else "probable"
                resultados[clave]["metodo_correlacion"] = metodo
                resultados[clave]["razones_correlacion"] = razones
        fusionar_fuente(resultados[clave], fuente)
        metricas["fuentes_emparejadas"] += 1

    eventos = list(resultados.values())
    eventos.sort(key=lambda evento: evento["hora_utc"])
    metricas["eventos_unicos"] = len(eventos)
    return eventos, cuarentena, metricas


# ─── SALIDA Y EJECUCIÓN ──────────────────────────────────────────────────────
def guardar_json(path: Path, contenido: Any) -> None:
    path.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    log.info("=== Iniciando Curador de Eventos AllStreamTV v9 ===")
    if not XTREAM_URL or not XTREAM_USER or not XTREAM_PASS:
        log.error("Faltan XTREAM_URL, XTREAM_USER o XTREAM_PASS.")
        raise SystemExit(2)

    tz = obtener_zona_aplicacion()
    fecha_local = datetime.now(tz)
    fecha_consulta = fecha_local.date().isoformat()
    cliente = ClienteTheSportsDB()
    agenda = obtener_agenda_maestra(fecha_consulta, cliente)
    candidatos, metricas_xtream = obtener_canales_candidatos(fecha_local)
    eventos, cuarentena, metricas_curacion = curar_eventos(agenda, candidatos)

    base_media = detectar_base_media_m3u()
    salida = {
        "version": 9,
        "generado_utc": iso_utc(datetime.now(timezone.utc)),
        "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha_consulta,
        "base_media": base_media,
        "eventos": eventos,
    }
    meta = {
        "version": 9,
        "generado_utc": salida["generado_utc"],
        "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha_consulta,
        "agenda_tsdb": len(agenda),
        "solicitudes_tsdb": dict(cliente.estadisticas),
        "xtream": dict(metricas_xtream),
        "curacion": dict(metricas_curacion),
        "eventos_finales_base": len(eventos),
        "cuarentena_guardada": len(cuarentena),
    }
    guardar_json(ARCHIVO_SALIDA, salida)
    guardar_json(ARCHIVO_CUARENTENA, cuarentena)
    guardar_json(ARCHIVO_META, meta)

    log.info(
        "Curador finalizado: agenda=%d | candidatos=%d | eventos=%d | cuarentena=%d.",
        len(agenda), len(candidatos), len(eventos), len(cuarentena),
    )


if __name__ == "__main__":
    main()
