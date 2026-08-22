# -*- coding: utf-8 -*-
"""Curador deportivo multideporte para AllStreamTV.

La agenda se construye con API-Sports por disciplina. Xtream aporta las señales
reproducibles y nunca se descarta una señal vigente solo porque TheSportsDB no
la conozca. Colombia (America/Bogota) es la única zona que determina la jornada.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("curador")

# ─── Configuración ───────────────────────────────────────────────────────────
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Bogota")
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY", "").strip()
API_SPORTS_MIN_INTERVAL = max(float(os.environ.get("API_SPORTS_MIN_INTERVAL", "7")), 0.0)
API_SPORTS_MAX_RETRIES = max(int(os.environ.get("API_SPORTS_MAX_RETRIES", "1")), 0)
API_SPORTS_DEPORTES = {
    x.strip().lower()
    for x in os.environ.get(
        "API_SPORTS_DEPORTES",
        "football,basketball,baseball,formula-1,hockey,mma,nba,nfl,rugby,volleyball,handball,afl",
    ).split(",")
    if x.strip()
}

XTREAM_URL = (os.environ.get("XTREAM_URL") or "").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER") or ""
XTREAM_PASS = os.environ.get("XTREAM_PASS") or ""
PUENTE_URL = os.environ.get("PUENTE_URL") or "https://mi-dashboard-tv.onrender.com/api/puente_xtream"

THESPORTSDB_KEY = (os.environ.get("THESPORTSDB_KEY") or "123").strip()
USAR_THESPORTSDB_RESPALDO = os.environ.get("USAR_THESPORTSDB_RESPALDO", "true").lower() in {"1", "true", "si", "sí", "yes"}
THESPORTSDB_MAX_EVENTOS = max(int(os.environ.get("THESPORTSDB_MAX_EVENTOS", "3")), 0)
THESPORTSDB_MAX_SOLICITUDES_DIA = max(int(os.environ.get("THESPORTSDB_MAX_SOLICITUDES_DIA", "3")), 0)

# Nombre estable: evita crear una caché distinta por cada versión del curador.
ARCHIVO_CACHE = Path(os.environ.get("ARCHIVO_CACHE", "agenda_api_actual.json"))
ARCHIVO_SALIDA = Path(os.environ.get("ARCHIVO_SALIDA", "eventos_hoy.json"))
ARCHIVO_CUARENTENA = Path(os.environ.get("ARCHIVO_CUARENTENA", "eventos_descartados.json"))
ARCHIVO_META = Path(os.environ.get("ARCHIVO_META", "meta_curador.json"))
ARCHIVO_PRESUPUESTO = Path(os.environ.get("ARCHIVO_PRESUPUESTO", "presupuesto_api.json"))
MINUTOS_CACHE = max(int(os.environ.get("MINUTOS_CACHE", "45")), 0)
MAX_CUARENTENA = max(int(os.environ.get("MAX_CUARENTENA", "1000")), 0)
MAX_DIFERENCIA_MIN = max(int(os.environ.get("MAX_DIFERENCIA_MIN", "180")), 15)
PUBLICAR_XTREAM_PROBABLE = os.environ.get("PUBLICAR_XTREAM_PROBABLE", "true").lower() in {"1", "true", "si", "sí", "yes"}
PUBLICAR_AGENDA_SIN_FUENTE = os.environ.get("PUBLICAR_AGENDA_SIN_FUENTE", "false").lower() in {"1", "true", "si", "sí", "yes"}

DURACION_POR_CATEGORIA = {
    "Fútbol": 130, "Baloncesto": 160, "Béisbol": 210, "Motor": 210,
    "Hockey": 160, "Combate": 210, "Tenis": 210, "Rugby": 160,
    "Voleibol": 160, "Fútbol Americano": 220, "Handball": 150,
    "Ciclismo": 240, "Snooker": 180, "Golf": 300, "Gimnasia": 150,
    "Deportes": 150,
}

# host, endpoint, categoría, forma de respuesta. El endpoint se consulta una
# vez por jornada; los adaptadores toleran campos ausentes de ligas pequeñas.
API_CONFIGS: dict[str, dict[str, str]] = {
    "football": {"host": "https://v3.football.api-sports.io", "endpoint": "fixtures", "categoria": "Fútbol", "tipo": "games"},
    "basketball": {"host": "https://v1.basketball.api-sports.io", "endpoint": "games", "categoria": "Baloncesto", "tipo": "games"},
    "baseball": {"host": "https://v1.baseball.api-sports.io", "endpoint": "games", "categoria": "Béisbol", "tipo": "games"},
    "hockey": {"host": "https://v1.hockey.api-sports.io", "endpoint": "games", "categoria": "Hockey", "tipo": "games"},
    "handball": {"host": "https://v1.handball.api-sports.io", "endpoint": "games", "categoria": "Handball", "tipo": "games"},
    "rugby": {"host": "https://v1.rugby.api-sports.io", "endpoint": "games", "categoria": "Rugby", "tipo": "games"},
    "volleyball": {"host": "https://v1.volleyball.api-sports.io", "endpoint": "games", "categoria": "Voleibol", "tipo": "games"},
    "afl": {"host": "https://v1.afl.api-sports.io", "endpoint": "games", "categoria": "Fútbol Australiano", "tipo": "games"},
    "nfl": {"host": "https://v1.american-football.api-sports.io", "endpoint": "games", "categoria": "Fútbol Americano", "tipo": "games"},
    "nba": {"host": "https://v2.nba.api-sports.io", "endpoint": "games", "categoria": "Baloncesto", "tipo": "games"},
    "formula-1": {"host": "https://v1.formula-1.api-sports.io", "endpoint": "races", "categoria": "Motor", "tipo": "races"},
    "mma": {"host": "https://v1.mma.api-sports.io", "endpoint": "fights", "categoria": "Combate", "tipo": "fights"},
}

STOPWORDS = {
    "A", "AL", "AND", "AT", "BY", "CON", "COPA", "DE", "DEL", "EL", "EN", "FOR", "GRAND", "GRAN",
    "HD", "LA", "LAS", "LEAGUE", "LIVE", "LOS", "OF", "ON", "OPEN", "PARA", "PARTIDO", "PPV", "RACE",
    "SERIES", "SPORT", "THE", "TO", "TOUR", "TV", "UN", "UNA", "VS", "VIVO", "WORLD", "CAMPEONATO",
    "CHAMPIONSHIP", "EVENT", "EVENTS", "FIGHT", "NIGHT", "MATCH", "DIRECTO", "HOY", "TODAY", "DAILY",
    "DIARIO", "DIA", "PRACTICE", "ENTRENAMIENTO", "ROUND", "FINAL", "FASE", "JORNADA",
}
VETO_EMISION = {
    "REPETICION", "REPLAY", "RESUMEN", "HIGHLIGHTS", "COMPACTO", "NOTICIAS", "NEWS", "MAGAZINE", "PREVIA",
    "POSTPARTIDO", "POST PARTIDO", "DOCUMENTAL", "CLASICOS", "MEMORIAS", "VINTAGE", "MEJORES MOMENTOS",
    "WIEDERHOLUNG", "ZUSAMMENFASSUNG", "DOKUMENTATION", "VORSCHAU", "NACHRICHTEN",
    "REPETICAO", "RESUMO", "DOCUMENTARIO", "REDIFFUSION", "RETROSPECTIVA",
}
SESIONES = {
    "PRACTICE": "practica", "PRACTICA": "practica", "ENTRENAMIENTO": "practica",
    "QUALIFYING": "clasificacion", "CLASIFICACION": "clasificacion", "POLE": "clasificacion",
    "SPRINT": "sprint", "RACE": "carrera", "CARRERA": "carrera", "PARTIDO": "partido", "MATCH": "partido",
    "PELEA": "pelea", "FIGHT": "pelea",
}
# Las pistas se evalúan por palabra/frase completa y con prioridad editorial. Evita
# que "Championship" active erróneamente la antigua pista genérica "CHAMPIONS".
DEPORTE_PISTAS = {
    "Golf": {"GOLF", "PGA", "DP WORLD", "BMW CHAMPIONSHIP"},
    "Snooker": {"SNOOKER"},
    "Ciclismo": {"VUELTA", "CYCLING", "CICLISMO", "RADSPORT", "CYCLISME", "TOUR DE FRANCE"},
    "Gimnasia": {"GIMNASIA", "GYMNASTICS", "GYMNASTIQUE", "TURNEN", "ARTISTICA"},
    "Tenis": {"TENNIS", "TENIS", "ATP", "WTA"},
    "Fútbol": {"SOCCER", "FUTBOL", "LALIGA", "PREMIER LEAGUE", "BUNDESLIGA", "SERIE A", "CHAMPIONS LEAGUE", "LIBERTADORES", "SUDAMERICANA"},
    "Baloncesto": {"NBA", "BASKET", "BALONCESTO", "EUROLEAGUE", "FIBA"},
    "Béisbol": {"BASEBALL", "BEISBOL", "MLB"},
    "Motor": {"FORMULA", "F1", "MOTOGP", "MOTO GP", "NASCAR", "RALLY", "INDYCAR"},
    "Hockey": {"HOCKEY"}, "Combate": {"UFC", "MMA", "BKFC", "BOXING", "BOXEO", "WWE", "WRESTLING", "KICKBOXING"},
    "Rugby": {"RUGBY"}, "Voleibol": {"VOLLEY", "VOLEIBOL"},
    "Fútbol Americano": {"NFL", "AMERICAN FOOTBALL", "FUTBOL AMERICANO"}, "Handball": {"HANDBALL", "BALONMANO"},
}


# ─── Utilidades ──────────────────────────────────────────────────────────────
def normalizar_texto(texto: Any) -> str:
    if texto is None:
        return ""
    valor = unicodedata.normalize("NFD", str(texto).upper())
    valor = "".join(c for c in valor if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^A-Z0-9\s]", " ", valor).split())


def tokenizar(texto: Any, *, conservar_genericos: bool = False) -> set[str]:
    palabras = {p for p in normalizar_texto(texto).split() if len(p) >= 3 and not p.isdigit()}
    return palabras if conservar_genericos else palabras - STOPWORDS


def contiene_veto(texto: Any) -> bool:
    valor = normalizar_texto(texto)
    return any(palabra in valor for palabra in VETO_EMISION)


def _pista_completa(valor: str, pista: str) -> bool:
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(pista)}(?![A-Z0-9])", valor))


def inferir_deporte(texto: Any) -> Optional[str]:
    """Clasifica por prioridad y coincidencias de palabras completas."""
    valor = normalizar_texto(texto)
    for categoria, pistas in DEPORTE_PISTAS.items():
        if any(_pista_completa(valor, pista) for pista in pistas):
            return categoria
    return None


def extraer_sesion(texto: Any) -> Optional[str]:
    valor = normalizar_texto(texto)
    encontrados = {sesion for pista, sesion in SESIONES.items() if pista in valor}
    if "sprint" in encontrados and "clasificacion" in encontrados:
        return "clasificacion_sprint"
    if "sprint" in encontrados and "carrera" in encontrados:
        return "carrera_sprint"
    return "sprint" if "sprint" in encontrados else (sorted(encontrados)[0] if encontrados else None)


def obtener_zona_aplicacion() -> ZoneInfo:
    try:
        return ZoneInfo(APP_TIMEZONE)
    except Exception:
        log.warning("APP_TIMEZONE inválida (%s); se usará America/Bogota.", APP_TIMEZONE)
        return ZoneInfo("America/Bogota")


def iso_utc(valor: datetime) -> str:
    return valor.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parsear_iso_o_timestamp(valor: Any) -> Optional[datetime]:
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)) or (isinstance(valor, str) and valor.strip().isdigit()):
        try:
            return datetime.fromtimestamp(int(valor), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(valor, str):
        try:
            fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
            return (fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except ValueError:
            pass
        for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(valor, formato).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def extraer_hora_canal(texto: str, fecha_base: datetime) -> Optional[datetime]:
    for match in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})\s*([APap][Mm])?(?!\d)", texto or ""):
        hora, minuto = int(match.group(1)), int(match.group(2))
        ampm = (match.group(3) or "").upper()
        if ampm == "PM" and hora < 12:
            hora += 12
        elif ampm == "AM" and hora == 12:
            hora = 0
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return fecha_base.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return None


def variaciones_fecha(fecha: datetime) -> set[str]:
    meses_es = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
    meses_en = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    d, m = fecha.day, fecha.month
    return {f"{d:02d}/{m:02d}", f"{d:02d}-{m:02d}", f"{d} {meses_es[m-1]}", f"{d} {meses_en[m-1]}", f"{meses_es[m-1]} {d}", f"{meses_en[m-1]} {d}"}


def fecha_xtream_explicita(texto: str, fecha_producto: date) -> Optional[bool]:
    """None significa que el rótulo no expone una fecha; True/False es definitivo."""
    hallada = False
    for dia, mes, _ in re.findall(r"(?<!\d)(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?!\d)", texto or ""):
        hallada = True
        try:
            if date(fecha_producto.year, int(mes), int(dia)) == fecha_producto:
                return True
        except ValueError:
            continue
    return False if hallada else None


def _get(objeto: Any, *rutas: str) -> Any:
    for ruta in rutas:
        actual = objeto
        ok = True
        for parte in ruta.split("."):
            if not isinstance(actual, dict) or parte not in actual:
                ok = False
                break
            actual = actual[parte]
        if ok and actual not in (None, ""):
            return actual
    return None


def guardar_json(path: Path, contenido: Any) -> None:
    path.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── Cliente API-Sports ──────────────────────────────────────────────────────
class ClienteApiSports:
    def __init__(self, clave: str) -> None:
        self.clave = clave
        self.sesion = requests.Session()
        self.ultimo_request = 0.0
        self.metricas: dict[str, dict[str, Any]] = {}

    def get(self, deporte: str, host: str, endpoint: str, params: dict[str, str]) -> Optional[dict[str, Any]]:
        datos = self.metricas.setdefault(deporte, Counter())
        if not self.clave:
            datos["omitido_sin_clave"] += 1
            return None
        url = f"{host.rstrip('/')}/{endpoint.lstrip('/')}"
        for intento in range(API_SPORTS_MAX_RETRIES + 1):
            espera = API_SPORTS_MIN_INTERVAL - (time.monotonic() - self.ultimo_request)
            if espera > 0:
                time.sleep(espera)
            try:
                respuesta = self.sesion.get(url, params=params, headers={"x-apisports-key": self.clave}, timeout=(10, 35))
                self.ultimo_request = time.monotonic()
                datos["solicitudes"] += 1
            except requests.RequestException as exc:
                datos["errores_red"] += 1
                log.warning("API-Sports %s sin respuesta: %s", deporte, exc)
                if intento < API_SPORTS_MAX_RETRIES:
                    time.sleep(3 * (intento + 1))
                    continue
                return None

            datos["http"] = respuesta.status_code
            for cabecera, destino in (
                ("x-ratelimit-requests-limit", "limite_diario"),
                ("x-ratelimit-requests-remaining", "restantes_diarios"),
                ("X-RateLimit-Limit", "limite_minuto"),
                ("X-RateLimit-Remaining", "restantes_minuto"),
            ):
                if respuesta.headers.get(cabecera):
                    datos[destino] = respuesta.headers[cabecera]
            if respuesta.status_code == 200:
                try:
                    cuerpo = respuesta.json()
                except ValueError:
                    datos["json_invalido"] += 1
                    return None
                errores = cuerpo.get("errors") if isinstance(cuerpo, dict) else None
                if errores and errores not in ({}, [], ""):
                    datos["errores_api"] += 1
                    datos["ultimo_error"] = str(errores)[:240]
                return cuerpo if isinstance(cuerpo, dict) else None
            datos[f"http_{respuesta.status_code}"] += 1
            if respuesta.status_code in (429, 500, 502, 503, 504) and intento < API_SPORTS_MAX_RETRIES:
                time.sleep(6 * (intento + 1))
                continue
            log.warning("API-Sports %s respondió HTTP %s.", deporte, respuesta.status_code)
            return None
        return None


# ─── Agenda normalizada ──────────────────────────────────────────────────────
def _fecha_evento_api(item: dict[str, Any]) -> Optional[datetime]:
    candidatos = (
        _get(item, "fixture.timestamp", "game.date.timestamp", "race.date.timestamp", "date.timestamp"),
        _get(item, "fixture.date", "game.date.date", "race.date", "date.date", "date"),
    )
    for valor in candidatos:
        resultado = parsear_iso_o_timestamp(valor)
        if resultado:
            return resultado
    return None


def _texto_evento_api(item: dict[str, Any], config: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    local = str(_get(item, "teams.home.name", "home.name", "fighters.home.name") or "").strip()
    visitante = str(_get(item, "teams.away.name", "away.name", "fighters.away.name") or "").strip()
    torneo = str(_get(item, "league.name", "competition.name", "race.competition.name", "event.name") or "").strip()
    subtitulo = str(_get(item, "game.stage", "game.week", "fixture.status.long", "race.type", "race.name", "name") or "").strip()
    if local and visitante:
        return f"{local} vs {visitante}", torneo, local, visitante, "duelo", subtitulo
    titulo = str(_get(item, "race.competition.name", "race.name", "fight.name", "event.name", "name", "league.name") or "").strip()
    if not titulo:
        titulo = torneo
    return titulo, torneo, "", "", "sencillo", subtitulo


def normalizar_evento_api(deporte: str, item: dict[str, Any], config: dict[str, str], tz: ZoneInfo) -> Optional[dict[str, Any]]:
    inicio = _fecha_evento_api(item)
    if inicio is None or inicio.astimezone(tz).date() != datetime.now(tz).date():
        return None
    titulo, torneo, local, visitante, tipo, subtitulo = _texto_evento_api(item, config)
    if not titulo:
        return None
    id_origen = str(_get(item, "fixture.id", "game.id", "race.id", "fight.id", "id") or "")
    if not id_origen:
        id_origen = hashlib.sha1(f"{deporte}|{titulo}|{iso_utc(inicio)}".encode()).hexdigest()[:16]
    logo_torneo = str(_get(item, "league.logo", "competition.logo", "race.competition.logo") or "")
    logo_local = str(_get(item, "teams.home.logo", "home.logo") or "")
    logo_visitante = str(_get(item, "teams.away.logo", "away.logo") or "")
    estado_raw = str(_get(item, "fixture.status.short", "game.status.short", "race.status.short", "status.short", "status.long") or "NS").upper()
    estado = "en_vivo" if estado_raw in {"1H", "2H", "HT", "Q1", "Q2", "Q3", "Q4", "LIVE", "IN", "P", "RUNNING"} else "finalizado" if estado_raw in {"FT", "AET", "PEN", "FINISHED", "ENDED"} else "programado"
    return {
        "id": f"apisports_{deporte}_{id_origen}", "agenda_id": f"apisports_{deporte}_{id_origen}",
        "titulo": titulo, "torneo": torneo, "categoria": config["categoria"], "tipo_evento": tipo,
        "equipo_local": local, "equipo_visitante": visitante, "subtitulo": subtitulo,
        "hora_utc": iso_utc(inicio), "hora_local_producto": inicio.astimezone(tz).strftime("%H:%M"),
        "duracion_min": DURACION_POR_CATEGORIA.get(config["categoria"], 150),
        "logo_torneo": logo_torneo, "logo_local": logo_local, "logo_visitante": logo_visitante,
        "tier": 1, "origen": f"api_sports:{deporte}", "origenes": [f"api_sports:{deporte}"],
        "estado": "confirmado", "estado_evento": estado, "confianza": "alta", "puntuacion_confianza": 100,
        "fuentes": [], "metodo_correlacion": "agenda_api_sports", "razones_correlacion": [f"deporte:{deporte}"],
    }


def _leer_cache(fecha_consulta: str, permitir_vencida: bool = False) -> Optional[dict[str, Any]]:
    if not ARCHIVO_CACHE.exists():
        return None
    try:
        datos = json.loads(ARCHIVO_CACHE.read_text(encoding="utf-8"))
        if not isinstance(datos, dict) or datos.get("fecha_local_producto") != fecha_consulta:
            return None
        edad = time.time() - ARCHIVO_CACHE.stat().st_mtime
        if permitir_vencida or (MINUTOS_CACHE > 0 and edad <= MINUTOS_CACHE * 60):
            return datos
    except (OSError, ValueError):
        return None
    return None


def cargar_agenda_cache(fecha_consulta: str) -> list[dict[str, Any]]:
    datos = _leer_cache(fecha_consulta, permitir_vencida=True)
    return list(datos.get("eventos", [])) if datos else []


def reservar_presupuesto(nombre: str, fecha_consulta: str, limite: int) -> bool:
    """Reserva una llamada diaria persistente; el workflow serializa ejecuciones."""
    if limite <= 0:
        return False
    try:
        datos = json.loads(ARCHIVO_PRESUPUESTO.read_text(encoding="utf-8")) if ARCHIVO_PRESUPUESTO.exists() else {}
    except (OSError, ValueError):
        datos = {}
    if datos.get("fecha_local_producto") != fecha_consulta:
        datos = {"fecha_local_producto": fecha_consulta, "usos": {}}
    usos = datos.setdefault("usos", {})
    usados = int(usos.get(nombre, 0))
    if usados >= limite:
        return False
    usos[nombre] = usados + 1
    guardar_json(ARCHIVO_PRESUPUESTO, datos)
    return True


def obtener_respaldo_thesportsdb(fecha_consulta: str, tz: ZoneInfo, metricas: Counter) -> list[dict[str, Any]]:
    if not USAR_THESPORTSDB_RESPALDO or not THESPORTSDB_MAX_EVENTOS:
        return []
    if not reservar_presupuesto("thesportsdb", fecha_consulta, THESPORTSDB_MAX_SOLICITUDES_DIA):
        metricas["omitido_presupuesto_diario"] += 1
        return []
    url = f"https://www.thesportsdb.com/api/v1/json/{THESPORTSDB_KEY}/eventsday.php"
    try:
        respuesta = requests.get(url, params={"d": fecha_consulta}, timeout=(10, 30))
        metricas["solicitudes"] += 1
        if respuesta.status_code != 200:
            metricas[f"http_{respuesta.status_code}"] += 1
            return []
        datos = respuesta.json()
    except (requests.RequestException, ValueError) as exc:
        metricas["errores"] += 1
        log.warning("TheSportsDB de respaldo no disponible: %s", exc)
        return []
    eventos: list[dict[str, Any]] = []
    for bruto in (datos.get("events") or [])[:THESPORTSDB_MAX_EVENTOS]:
        inicio = parsear_iso_o_timestamp(bruto.get("strTimestamp"))
        if inicio is None:
            inicio = parsear_iso_o_timestamp(f"{bruto.get('dateEvent', '')} {bruto.get('strTime', '00:00:00')}")
        if inicio is None or inicio.astimezone(tz).date() != datetime.now(tz).date():
            continue
        local, visitante = (bruto.get("strHomeTeam") or "").strip(), (bruto.get("strAwayTeam") or "").strip()
        titulo = f"{local} vs {visitante}" if local and visitante else (bruto.get("strEvent") or bruto.get("strLeague") or "").strip()
        if not titulo:
            continue
        categoria = inferir_deporte(f"{bruto.get('strSport', '')} {titulo}") or "Deportes"
        ident = str(bruto.get("idEvent") or hashlib.sha1(f"{titulo}|{iso_utc(inicio)}".encode()).hexdigest()[:16])
        eventos.append({
            "id": f"tsdb_{ident}", "agenda_id": f"tsdb_{ident}", "titulo": titulo,
            "torneo": (bruto.get("strLeague") or "").strip(), "categoria": categoria,
            "tipo_evento": "duelo" if local and visitante else "sencillo", "equipo_local": local, "equipo_visitante": visitante,
            "subtitulo": "", "hora_utc": iso_utc(inicio), "hora_local_producto": inicio.astimezone(tz).strftime("%H:%M"),
            "duracion_min": DURACION_POR_CATEGORIA.get(categoria, 150), "logo_torneo": bruto.get("strLeagueBadge") or "",
            "logo_local": bruto.get("strHomeTeamBadge") or "", "logo_visitante": bruto.get("strAwayTeamBadge") or "",
            "tier": 2, "origen": "thesportsdb_respaldo", "origenes": ["thesportsdb_respaldo"],
            "estado": "confirmado", "estado_evento": "programado", "confianza": "media", "puntuacion_confianza": 78,
            "fuentes": [], "metodo_correlacion": "agenda_thesportsdb_respaldo", "razones_correlacion": [],
        })
    metricas["eventos"] = len(eventos)
    return eventos


def obtener_agenda_maestra(fecha_consulta: str, metricas_salida: Optional[dict[str, Any]] = None, forzar: bool = False) -> list[dict[str, Any]]:
    tz = obtener_zona_aplicacion()
    cache = None if forzar else _leer_cache(fecha_consulta)
    if cache:
        if metricas_salida is not None:
            metricas_salida.update(cache.get("metricas", {}))
            metricas_salida["cache"] = "vigente"
        return list(cache.get("eventos", []))

    cliente = ClienteApiSports(API_SPORTS_KEY)
    agenda: list[dict[str, Any]] = []
    for deporte, config in API_CONFIGS.items():
        if deporte not in API_SPORTS_DEPORTES:
            continue
        params = {"date": fecha_consulta, "timezone": APP_TIMEZONE}
        datos = cliente.get(deporte, config["host"], config["endpoint"], params)
        for bruto in (datos or {}).get("response") or []:
            if isinstance(bruto, dict):
                evento = normalizar_evento_api(deporte, bruto, config, tz)
                if evento:
                    agenda.append(evento)

    metricas_tsdb: Counter = Counter()
    agenda.extend(obtener_respaldo_thesportsdb(fecha_consulta, tz, metricas_tsdb))
    unicos = {evento["id"]: evento for evento in agenda}
    agenda = sorted(unicos.values(), key=lambda e: e["hora_utc"])
    metricas = {"api_sports": {k: dict(v) for k, v in cliente.metricas.items()}, "thesportsdb_respaldo": dict(metricas_tsdb), "eventos": len(agenda)}
    if not agenda:
        anterior = _leer_cache(fecha_consulta, permitir_vencida=True)
        if anterior:
            agenda = list(anterior.get("eventos", []))
            metricas["cache"] = "venciente_usada_por_fallo"
    guardar_json(ARCHIVO_CACHE, {"version": 11, "fecha_local_producto": fecha_consulta, "generado_utc": iso_utc(datetime.now(timezone.utc)), "eventos": agenda, "metricas": metricas})
    if metricas_salida is not None:
        metricas_salida.update(metricas)
    return agenda


# ─── Xtream ──────────────────────────────────────────────────────────────────
def llamada_xtream(url: str, timeout: int = 60) -> Any:
    respuesta = requests.get(PUENTE_URL, params={"url": url}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.json()


def detectar_categorias_fechadas(fecha_local: datetime) -> tuple[set[str], set[str]]:
    """Separa categorías de hoy de categorías que expresan otra fecha concreta."""
    if not XTREAM_URL:
        return set(), set()
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_categories"
    hoy, ajenas = set(), set()
    try:
        for categoria in llamada_xtream(url, 45) or []:
            sid = str(categoria.get("category_id") or "")
            nombre_original = str(categoria.get("category_name") or "")
            if not sid:
                continue
            fecha_texto = fecha_xtream_explicita(nombre_original, fecha_local.date())
            if fecha_texto is True or any(normalizar_texto(marca) in normalizar_texto(nombre_original) for marca in variaciones_fecha(fecha_local)):
                hoy.add(sid)
            elif fecha_texto is False:
                ajenas.add(sid)
    except (requests.RequestException, TypeError, ValueError) as exc:
        log.warning("No se pudieron leer categorías Xtream: %s", exc)
    return hoy, ajenas


def es_candidato(stream: dict[str, Any], categorias_hoy: set[str], fecha_local: datetime, categorias_ajenas: Optional[set[str]] = None) -> tuple[bool, str]:
    nombre = str(stream.get("name") or "").strip()
    if not nombre:
        return False, "sin_nombre"
    if contiene_veto(nombre):
        return False, "contenido_no_evento"
    fecha_en_texto = fecha_xtream_explicita(nombre, fecha_local.date())
    if fecha_en_texto is False:
        return False, "fecha_xtream_fuera_de_jornada"
    if str(stream.get("category_id") or "") in (categorias_ajenas or set()):
        return False, "categoria_xtream_fuera_de_jornada"
    hora = extraer_hora_canal(nombre, fecha_local)
    deporte, duelo = inferir_deporte(nombre), bool(re.search(r"\b(VS|V|AT)\b|\s[-@]\s", normalizar_texto(nombre)))
    categoria_hoy = str(stream.get("category_id") or "") in categorias_hoy
    if fecha_en_texto is True and (hora or deporte or duelo):
        return True, "fecha_xtream_hoy"
    if categoria_hoy and (hora or deporte or duelo):
        return True, "categoria_xtream_hoy"
    if hora and (deporte or duelo):
        return True, "hora_y_identidad_sin_fecha"
    return False, "sin_vigencia_o_identidad"


def obtener_canales_candidatos(fecha_local: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    metricas: Counter = Counter()
    if not (XTREAM_URL and XTREAM_USER and XTREAM_PASS):
        metricas["error"] = "faltan_credenciales_xtream"
        return [], dict(metricas)
    categorias_hoy, categorias_ajenas = detectar_categorias_fechadas(fecha_local)
    metricas["categorias_hoy"] = len(categorias_hoy)
    metricas["categorias_fuera_de_jornada"] = len(categorias_ajenas)
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        streams = llamada_xtream(url, 75) or []
    except (requests.RequestException, TypeError, ValueError) as exc:
        metricas["error"] = f"xtream:{exc}"
        return [], dict(metricas)
    candidatos: list[dict[str, Any]] = []
    vistos: set[str] = set()
    for stream in streams:
        sid = str(stream.get("stream_id") or "")
        if not sid or sid in vistos:
            continue
        vistos.add(sid)
        metricas["streams_totales"] += 1
        aceptado, motivo = es_candidato(stream, categorias_hoy, fecha_local, categorias_ajenas)
        if not aceptado:
            metricas[f"descartado_{motivo}"] += 1
            continue
        nombre = str(stream.get("name") or "").strip()
        candidatos.append({
            "id_xtream": sid, "nombre_ui": nombre, "texto_normalizado": normalizar_texto(nombre), "tokens": tokenizar(nombre),
            "hora_local": extraer_hora_canal(nombre, fecha_local), "categoria_inferida": inferir_deporte(nombre),
            "sesion": extraer_sesion(nombre), "categoria_xtream": str(stream.get("category_id") or ""), "motivo_vigencia": motivo,
        })
    metricas["candidatos"] = len(candidatos)
    return candidatos, dict(metricas)


def detectar_base_media_m3u() -> str:
    """No incluye usuario ni contraseña; la aplicación conserva su mecanismo actual de reproducción."""
    return XTREAM_URL


# ─── Correlación ─────────────────────────────────────────────────────────────
def _inicio_evento(evento: dict[str, Any]) -> Optional[datetime]:
    return parsear_iso_o_timestamp(evento.get("hora_utc"))


def _deportes_compatibles(canal: dict[str, Any], evento: dict[str, Any]) -> bool:
    return not canal.get("categoria_inferida") or canal["categoria_inferida"] == evento.get("categoria")


def _sesiones_compatibles(canal: dict[str, Any], evento: dict[str, Any]) -> bool:
    sesion_canal = canal.get("sesion")
    sesion_evento = extraer_sesion(f"{evento.get('titulo', '')} {evento.get('subtitulo', '')}")
    if not sesion_canal or not sesion_evento or sesion_canal == sesion_evento:
        return True
    return sesion_evento == "sprint" and sesion_canal == "carrera_sprint"


def _proximidad_horaria(canal: dict[str, Any], evento: dict[str, Any]) -> tuple[bool, Optional[int]]:
    hora_canal, hora_evento = canal.get("hora_local"), _inicio_evento(evento)
    if hora_canal is None or hora_evento is None:
        return True, None
    diferencia = round(abs((hora_canal.astimezone(timezone.utc) - hora_evento).total_seconds()) / 60)
    # Nunca se permite la excepción antigua de 23 h: un horario explícito es una
    # restricción dura, no solo una penalización de puntuación.
    return diferencia <= MAX_DIFERENCIA_MIN, diferencia


def calcular_similitud_simple(titulo: str, torneo: str, canal: str) -> tuple[int, list[str]]:
    evento_tokens, canal_tokens = tokenizar(f"{titulo} {torneo}"), tokenizar(canal)
    comunes = sorted(evento_tokens & canal_tokens)
    if not comunes:
        return 0, []
    cobertura = len(comunes) / max(len(evento_tokens), 1)
    precision = len(comunes) / max(len(canal_tokens), 1)
    puntuacion = round(100 * (0.76 * cobertura + 0.24 * precision))
    evento_norm, canal_norm = normalizar_texto(titulo), normalizar_texto(canal)
    if evento_norm and evento_norm in canal_norm:
        puntuacion = max(puntuacion, 92)
    if len(comunes) == 1 and len(comunes[0]) >= 8:
        puntuacion = max(puntuacion, 58)
    return min(puntuacion, 100), comunes


def emparejar_evento(canal: dict[str, Any], agenda: Iterable[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], int, str, list[str]]:
    mejor, mejor_puntos, mejor_metodo, mejor_razones = None, 0, "sin_coincidencia_verificable", []
    for evento in agenda:
        if not _deportes_compatibles(canal, evento) or not _sesiones_compatibles(canal, evento):
            continue
        cercano, diferencia = _proximidad_horaria(canal, evento)
        if not cercano:
            continue
        local, visita = tokenizar(evento.get("equipo_local", "")), tokenizar(evento.get("equipo_visitante", ""))
        acierto_local, acierto_visita = sorted(local & canal["tokens"]), sorted(visita & canal["tokens"])
        if local and visita and acierto_local and acierto_visita:
            puntos = min(100, 84 + 4 * (len(acierto_local) + len(acierto_visita)) + (5 if diferencia is not None else 0))
            metodo, razones = "duelo_verificado", [f"local:{','.join(acierto_local)}", f"visitante:{','.join(acierto_visita)}"]
        else:
            puntos, comunes = calcular_similitud_simple(evento.get("titulo", ""), evento.get("torneo", ""), canal["nombre_ui"])
            frase = normalizar_texto(evento.get("titulo", "")) in canal["texto_normalizado"] if evento.get("titulo") else False
            fuerte = len(comunes) == 1 and len(comunes[0]) >= 8 and canal.get("categoria_inferida") == evento.get("categoria")
            if len(comunes) < 2 and not frase and not fuerte:
                continue
            puntos = min(100, puntos + (5 if diferencia is not None else 0))
            metodo, razones = "evento_simple_verificado", [f"tokens:{','.join(comunes)}"]
        if diferencia is not None:
            razones.append(f"diferencia_min:{diferencia}")
        if puntos > mejor_puntos:
            mejor, mejor_puntos, mejor_metodo, mejor_razones = evento, puntos, metodo, razones
    return mejor, mejor_puntos, mejor_metodo, mejor_razones


def fusionar_fuente(evento: dict[str, Any], fuente: dict[str, Any]) -> None:
    fuentes = evento.setdefault("fuentes", [])
    if not any(str(x.get("id_xtream")) == str(fuente.get("id_xtream")) for x in fuentes):
        fuentes.append(fuente)


def _titulo_probable(canal: dict[str, Any]) -> str:
    titulo = re.sub(r"(?<!\d)\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?(?!\d)", "", canal["nombre_ui"])
    titulo = re.sub(r"(?<!\d)\d{1,2}:\d{2}\s*(?:[APap][Mm])?(?!\d)", "", titulo)
    return " ".join(titulo.strip(" -|·:").split()) or canal["nombre_ui"]


def crear_probable_xtream(canal: dict[str, Any], tz: ZoneInfo) -> Optional[dict[str, Any]]:
    hora = canal.get("hora_local")
    if hora is None:
        return None
    titulo = _titulo_probable(canal)
    categoria = canal.get("categoria_inferida") or "Deportes"
    clave = f"{normalizar_texto(titulo)}|{hora.strftime('%Y%m%d%H%M')}"
    ident = hashlib.sha1(clave.encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"xtream_{ident}", "agenda_id": "", "titulo": titulo, "torneo": "", "categoria": categoria,
        "tipo_evento": "duelo" if re.search(r"\b(VS|V|AT)\b", canal["texto_normalizado"]) else "sencillo",
        "equipo_local": "", "equipo_visitante": "", "subtitulo": "", "hora_utc": iso_utc(hora),
        "hora_local_producto": hora.astimezone(tz).strftime("%H:%M"), "duracion_min": DURACION_POR_CATEGORIA.get(categoria, 150),
        "logo_torneo": "", "logo_local": "", "logo_visitante": "", "tier": 3, "origen": "xtream_probable",
        "origenes": ["xtream"], "estado": "probable", "estado_evento": "programado", "confianza": "media",
        "puntuacion_confianza": 58, "fuentes": [], "metodo_correlacion": "xtream_vigente_sin_agenda",
        "razones_correlacion": [canal["motivo_vigencia"], f"categoria:{categoria}"],
    }


def curar_eventos(agenda: list[dict[str, Any]], candidatos: list[dict[str, Any]], fecha_producto: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    resultados: dict[str, dict[str, Any]] = {}
    cuarentena: list[dict[str, Any]] = []
    metricas: Counter = Counter()
    tz = obtener_zona_aplicacion()
    for canal in candidatos:
        evento, puntos, metodo, razones = emparejar_evento(canal, agenda)
        fuente = {"nombre": canal["nombre_ui"], "id_xtream": canal["id_xtream"]}
        if evento is None and PUBLICAR_XTREAM_PROBABLE:
            evento = crear_probable_xtream(canal, tz)
            if evento:
                puntos = int(evento["puntuacion_confianza"])
                metodo = str(evento["metodo_correlacion"])
                razones = list(evento["razones_correlacion"])
        if evento is None:
            metricas["cuarentena"] += 1
            if len(cuarentena) < MAX_CUARENTENA:
                cuarentena.append({**fuente, "motivo": metodo, "categoria_inferida": canal.get("categoria_inferida"), "texto_analizado": canal["texto_normalizado"]})
            continue
        clave = evento["id"]
        if clave not in resultados:
            clon = {k: v for k, v in evento.items() if k != "fuentes"}
            clon["fuentes"] = []
            clon["puntuacion_confianza"] = puntos
            clon["metodo_correlacion"] = metodo
            clon["razones_correlacion"] = razones
            resultados[clave] = clon
        fusionar_fuente(resultados[clave], fuente)
        metricas["fuentes_emparejadas" if evento.get("agenda_id") else "fuentes_probables"] += 1
    if PUBLICAR_AGENDA_SIN_FUENTE:
        for evento in agenda:
            resultados.setdefault(evento["id"], {k: v for k, v in evento.items()})
    eventos = sorted(resultados.values(), key=lambda e: e["hora_utc"])
    metricas["eventos_unicos"] = len(eventos)
    return eventos, cuarentena, dict(metricas)


def main() -> None:
    tz = obtener_zona_aplicacion()
    ahora = datetime.now(tz)
    fecha = ahora.date().isoformat()
    log.info("=== Curador multideporte v11 EN CANAL | Colombia %s ===", fecha)
    metricas_agenda: dict[str, Any] = {}
    agenda = obtener_agenda_maestra(fecha, metricas_agenda)
    candidatos, metricas_xtream = obtener_canales_candidatos(ahora)
    eventos, cuarentena, metricas_curacion = curar_eventos(agenda, candidatos, ahora.date())
    salida = {
        "version": 11, "generado_utc": iso_utc(datetime.now(timezone.utc)), "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha, "base_media": detectar_base_media_m3u(), "eventos": eventos,
    }
    meta = {
        "version": 11, "generado_utc": salida["generado_utc"], "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha, "agenda": metricas_agenda, "xtream": metricas_xtream,
        "curacion": metricas_curacion, "eventos_finales_base": len(eventos), "cuarentena_guardada": len(cuarentena),
    }
    guardar_json(ARCHIVO_SALIDA, salida)
    guardar_json(ARCHIVO_CUARENTENA, cuarentena)
    guardar_json(ARCHIVO_META, meta)
    log.info("Finalizado: agenda=%d candidatos=%d eventos=%d cuarentena=%d", len(agenda), len(candidatos), len(eventos), len(cuarentena))


if __name__ == "__main__":
    main()
