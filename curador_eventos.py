# -*- coding: utf-8 -*-
"""Curador deportivo multideporte para AllStreamTV con parser de duelos de cobertura total."""
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
from urllib.parse import urlparse
from typing import Any, Optional, Iterable, List, Dict, Tuple
from zoneinfo import ZoneInfo

import requests

from resolvedor_logos import resolver_logo_torneo

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("curador")

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
    "Escalada": 180, "Deportes Acuáticos": 150, "Deportes": 150,
    "Tejo": 180, "Atletismo": 180, "Otros Deportes": 150,
}

DEPORTES_COMPETICION = {"Ciclismo", "Snooker", "Golf", "Gimnasia", "Motor", "Escalada", "Deportes Acuáticos", "Atletismo"}
DEPORTES_HIBRIDOS = {"Combate", "Tenis", "Deportes", "Otros Deportes", "Tejo"}
ARCHIVO_LOGOS_EQUIPOS = Path(os.environ.get("ARCHIVO_LOGOS_EQUIPOS", "logos_equipos.json"))

MESES_MAP = {
    "ENERO": 1, "ENE": 1, "JANUARY": 1, "JAN": 1,
    "FEBRERO": 2, "FEB": 2, "FEBRUARY": 2,
    "MARZO": 3, "MAR": 3, "MARCH": 3,
    "ABRIL": 4, "ABR": 4, "APRIL": 4, "APR": 4,
    "MAYO": 5, "MAY": 5,
    "JUNIO": 6, "JUN": 6, "JUNE": 6,
    "JULIO": 7, "JUL": 7, "JULY": 7,
    "AGOSTO": 8, "AGO": 8, "AUGUST": 8, "AUG": 8,
    "SEPTIEMBRE": 9, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCTUBRE": 10, "OCT": 10, "OCTOBER": 10,
    "NOVIEMBRE": 11, "NOV": 11, "NOVEMBER": 11,
    "DICIEMBRE": 12, "DIC": 12, "DECEMBER": 12, "DEC": 12,
}

def _cargar_cache_equipos() -> Dict[str, str]:
    if not ARCHIVO_LOGOS_EQUIPOS.exists(): return {}
    try:
        return json.loads(ARCHIVO_LOGOS_EQUIPOS.read_text(encoding="utf-8"))
    except:
        return {}

def _guardar_cache_equipos(cache: Dict[str, str]) -> None:
    try:
        ARCHIVO_LOGOS_EQUIPOS.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except:
        pass

CACHE_EQUIPOS = _cargar_cache_equipos()

def resolver_logo_equipo(nombre: str) -> str:
    if not nombre: return ""
    return CACHE_EQUIPOS.get(normalizar_texto(nombre), "")

def registrar_logo_equipo(nombre: str, url: str) -> None:
    if not nombre or not url: return
    norm = normalizar_texto(nombre)
    if CACHE_EQUIPOS.get(norm) != url:
        CACHE_EQUIPOS[norm] = url
        _guardar_cache_equipos(CACHE_EQUIPOS)


API_CONFIGS: Dict[str, Dict[str, str]] = {
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

TOKENS_GENERICOS = {
    "UNITED", "CITY", "REAL", "CLUB", "DEPORTIVO", "RACING", "INTER", "ATLETICO",
    "SPORT", "SPORTING", "SAN", "SANTA", "SAO", "FC", "CF", "CD", "AC", "SC",
    "U19", "U20", "U21", "U23", "FEMENIL", "WOMEN", "W", "PRIMERA", "SEGUNDA",
    "DIVISION", "SUPER", "LEAGUE", "NATIONAL", "DEPORTE", "DEPORTES",
}

VETO_EMISION = {
    "REPETICION", "REPLAY", "RESUMEN", "HIGHLIGHTS", "COMPACTO", "NOTICIAS", "NEWS", "MAGAZINE", "PREVIA",
    "POSTPARTIDO", "POST PARTIDO", "DOCUMENTAL", "DOCUMENTALES", "CLASICOS", "MEMORIAS", "VINTAGE", "MEJORES MOMENTOS",
    "WIEDERHOLUNG", "ZUSAMMENFASSUNG", "DOKUMENTATION", "VORSCHAU", "NACHRICHTEN",
    "REPETICAO", "RESUMO", "DOCUMENTARIO", "REDIFFUSION", "RETROSPECTIVA",
    "PELICULA", "PELICULAS", "MOVIES", "SERIE", "SERIES", "ANIME", "ANIMACION", "ESTRENOS", "CONCIERTOS",
}

SESIONES = {
    "PRACTICE": "practica", "PRACTICA": "practica", "ENTRENAMIENTO": "practica",
    "QUALIFYING": "clasificacion", "CLASIFICACION": "clasificacion", "POLE": "clasificacion",
    "SPRINT": "sprint", "RACE": "carrera", "CARRERA": "carrera", "PARTIDO": "partido", "MATCH": "partido",
    "PELEA": "pelea", "FIGHT": "pelea",
}

DEPORTE_PISTAS = {
    "Golf": {"GOLF", "PGA", "DP WORLD", "BMW CHAMPIONSHIP"},
    "Snooker": {"SNOOKER"},
    "Ciclismo": {"VUELTA", "CYCLING", "CICLISMO", "RADSPORT", "CYCLISME", "TOUR DE FRANCE", "RENEWI TOUR", "MOUNTAIN BIKE", "MTB", "BTT", "DESCENSO"},
    "Escalada": {"ESCALADA", "CLIMBING", "BOULDER", "IFSC", "MURO"},
    "Deportes Acuáticos": {"PIRAGUISMO", "REMO", "CANOTAJE", "CANOE", "KAYAK", "SURFING", "SURF", "NATACION", "WATERPOLO"},
    "Gimnasia": {"GIMNASIA", "GYMNASTICS", "GYMNASTIQUE", "TURNEN", "ARTISTICA"},
    "Tenis": {"TENNIS", "TENIS", "ATP", "WTA", "US OPEN", "AUSTRALIAN OPEN", "ROLAND GARROS", "WIMBLEDON"},
    "Fútbol": {"SOCCER", "FUTBOL", "LALIGA", "PREMIER LEAGUE", "BUNDESLIGA", "SERIE A", "CHAMPIONS LEAGUE", "LIBERTADORES", "SUDAMERICANA"},
    "Baloncesto": {"NBA", "BASKET", "BALONCESTO", "EUROLEAGUE", "FIBA", "WNBA"},
    "Béisbol": {"BASEBALL", "BEISBOL", "MLB", "LMB", "LITTLE LEAGUE"},
    "Motor": {"FORMULA", "F1", "MOTOGP", "MOTO GP", "NASCAR", "RALLY", "INDYCAR", "SUPERBIKE", "RESISTENCIA DE LA FIA"},
    "Hockey": {"HOCKEY"},
    "Combate": {"UFC", "MMA", "BKFC", "BOXING", "BOXEO", "WWE", "WRESTLING", "KICKBOXING"},
    "Rugby": {"RUGBY"},
    "Voleibol": {"VOLLEY", "VOLEIBOL"},
    "Fútbol Americano": {"NFL", "AMERICAN FOOTBALL", "FUTBOL AMERICANO"},
    "Handball": {"HANDBALL", "BALONMANO"},
    "Tejo": {"TEJO", "TURMEQUE"},
}


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
    if any(palabra in valor for palabra in VETO_EMISION):
        return True
    # Veto de series (S01 E02, T01 E05, S1E1, etc.)
    if re.search(r"\b[ST]\d{1,2}\s*E\d{1,2}\b", valor):
        return True
    # Veto de películas con año explícito de estreno en paréntesis (ej: (2018), (2007))
    if re.search(r"\((?:19\d{2}|20[0-2]\d)\)", str(texto)):
        return True
    return False


def _pista_completa(valor: str, pista: str) -> bool:
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(pista)}(?![A-Z0-9])", valor))


def inferir_deporte(texto: Any) -> Optional[str]:
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
    """Extrae la hora programada soportando múltiples formatos de listas IPTV (HH:MM, H:MM, HH.MM, HHhMM, AM/PM, hs/hrs)."""
    if not texto:
        return None

    # 1. Formato con dos puntos o punto: "12:30", "12.30", "12:30 PM", "8:00 AM", "12:30hrs", "12:30 hs", "12:30h"
    m = re.search(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)\s*([APap][Mm]|[Hh][Rr]?[Ss]?)?(?!\d)", texto)
    if m:
        hora, minuto = int(m.group(1)), int(m.group(2))
        sufijo = (m.group(3) or "").upper()
        if "PM" in sufijo and hora < 12:
            hora += 12
        elif "AM" in sufijo and hora == 12:
            hora = 0
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return fecha_base.replace(hour=hora, minute=minuto, second=0, microsecond=0)

    # 2. Formato europeo con 'h'/'H': "12h30", "20H45", "12h00", "8h30"
    m = re.search(r"(?<!\d)([01]?\d|2[0-3])[hH]([0-5]\d)(?!\d)", texto)
    if m:
        hora, minuto = int(m.group(1)), int(m.group(2))
        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return fecha_base.replace(hour=hora, minute=minuto, second=0, microsecond=0)

    return None


def variaciones_fecha(fecha: datetime) -> set[str]:
    meses_es = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
    meses_en = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    d, m = fecha.day, fecha.month
    return {f"{d:02d}/{m:02d}", f"{d:02d}-{m:02d}", f"{d} {meses_es[m-1]}", f"{d} {meses_en[m-1]}", f"{meses_es[m-1]} {d}", f"{meses_en[m-1]} {d}"}


def fecha_xtream_explicita(texto: str, fecha_producto: date) -> Optional[bool]:
    """Detecta si un texto contiene una fecha explícita numérica (31/08) o textual (31 DE AGOSTO) y valida si es hoy."""
    if not texto:
        return None
    # Eliminar marcas de canales 24/7 para evitar falsos positivos con fechas
    texto_limpio = re.sub(r"\b24/7(?:/365)?\b", "", texto, flags=re.I)
    norm = normalizar_texto(texto_limpio)
    hallada = False

    # 1. Formato numérico DD/MM o DD-MM o DD/MM/YYYY (evitando 24/7)
    for dia, mes, _ in re.findall(r"(?<!\d)(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?!\d)", texto_limpio):
        if 1 <= int(dia) <= 31 and 1 <= int(mes) <= 12:
            hallada = True
            try:
                if date(fecha_producto.year, int(mes), int(dia)) == fecha_producto:
                    return True
            except ValueError:
                continue

    # 2. Formato textual ("31 DE AGOSTO", "31 AGO", "AGOSTO 31", "31 AUGUST", "AUG 31")
    meses_re = "|".join(sorted(MESES_MAP.keys(), key=len, reverse=True))
    for dia, mes_str in re.findall(rf"(?<!\d)(\d{{1,2}})\s*(?:DE\s*)?({meses_re})(?!\w)", norm):
        hallada = True
        mes_num = MESES_MAP.get(mes_str)
        try:
            if mes_num and date(fecha_producto.year, mes_num, int(dia)) == fecha_producto:
                return True
        except ValueError:
            continue

    for mes_str, dia in re.findall(rf"({meses_re})\s*(?:DE\s*)?(\d{{1,2}})(?!\d)", norm):
        hallada = True
        mes_num = MESES_MAP.get(mes_str)
        try:
            if mes_num and date(fecha_producto.year, mes_num, int(dia)) == fecha_producto:
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


class ClienteApiSports:
    def __init__(self, clave_o_claves: Union[str, List[str]]) -> None:
        if isinstance(clave_o_claves, list):
            self.claves = [k.strip() for k in clave_o_claves if k and k.strip()]
        else:
            self.claves = [k.strip() for k in str(clave_o_claves or "").split(",") if k.strip()]
        self.idx_clave = 0
        self.sesion = requests.Session()
        self.ultimo_request = 0.0
        self.metricas: Dict[str, Dict[str, Any]] = {}

    @property
    def clave(self) -> str:
        if 0 <= self.idx_clave < len(self.claves):
            return self.claves[self.idx_clave]
        return ""

    def rotar_clave(self, motivo: str) -> bool:
        if self.idx_clave + 1 < len(self.claves):
            anterior = self.idx_clave + 1
            self.idx_clave += 1
            log.warning(
                "API-Sports: Clave %d/%d agotada o bloqueada (%s). Rotando automáticamente a clave %d/%d...",
                anterior, len(self.claves), motivo, self.idx_clave + 1, len(self.claves)
            )
            return True
        log.error("API-Sports: Todas las claves configuradas (%d) han sido agotadas o suspendidas.", len(self.claves))
        return False

    def _es_bloqueo_o_limite(self, status_code: int, cuerpo: Any, restantes_diarios: Optional[str]) -> Tuple[bool, str]:
        if status_code in (401, 403):
            return True, f"HTTP_{status_code}"
        if status_code == 429:
            return True, "HTTP_429_demasiadas_peticiones"
        if restantes_diarios is not None and str(restantes_diarios).strip() == "0":
            return True, "cuota_diaria_0"
        if isinstance(cuerpo, dict):
            errores = cuerpo.get("errors")
            if errores and errores not in ({}, [], ""):
                texto = str(errores).lower()
                if any(k in texto for k in ("suspend", "limit", "reached", "quota", "exceeded", "not allowed", "token")):
                    return True, str(errores)[:160]
        return False, ""

    def get(self, deporte: str, host: str, endpoint: str, params: Dict[str, str]) -> Optional[Dict[str, Any]]:
        datos = self.metricas.setdefault(deporte, Counter())
        if not self.clave:
            datos["omitido_sin_clave"] += 1
            return None
        url = f"{host.rstrip('/')}/{endpoint.lstrip('/')}"

        while self.clave:
            datos["clave_indice"] = self.idx_clave + 1
            datos["total_claves"] = len(self.claves)
            rotar = False
            motivo_rotar = ""

            for intento in range(API_SPORTS_MAX_RETRIES + 1):
                espera = API_SPORTS_MIN_INTERVAL - (time.monotonic() - self.ultimo_request)
                if espera > 0:
                    time.sleep(espera)
                try:
                    respuesta = self.sesion.get(
                        url, params=params, headers={"x-apisports-key": self.clave}, timeout=(10, 35)
                    )
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
                restantes = respuesta.headers.get("x-ratelimit-requests-remaining")
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
                        es_bloqueo, motivo = self._es_bloqueo_o_limite(200, cuerpo, restantes)
                        if es_bloqueo:
                            rotar = True
                            motivo_rotar = motivo
                            break
                    if restantes is not None and str(restantes).strip() == "0":
                        self.rotar_clave("cuota_diaria_0_despues_de_exito")
                    return cuerpo if isinstance(cuerpo, dict) else None

                datos[f"http_{respuesta.status_code}"] += 1
                es_bloqueo, motivo = self._es_bloqueo_o_limite(respuesta.status_code, None, restantes)
                if es_bloqueo:
                    rotar = True
                    motivo_rotar = motivo
                    break

                if respuesta.status_code in (429, 500, 502, 503, 504) and intento < API_SPORTS_MAX_RETRIES:
                    time.sleep(6 * (intento + 1))
                    continue
                log.warning("API-Sports %s respondió HTTP %s.", deporte, respuesta.status_code)
                return None

            if rotar:
                if self.rotar_clave(motivo_rotar):
                    datos["rotaciones_clave"] += 1
                    continue
                return None

        return None


def _fecha_evento_api(item: Dict[str, Any]) -> Optional[datetime]:
    candidatos = (
        _get(item, "fixture.timestamp", "game.date.timestamp", "race.date.timestamp", "date.timestamp"),
        _get(item, "fixture.date", "game.date.date", "race.date", "date.date", "date"),
    )
    for valor in candidatos:
        resultado = parsear_iso_o_timestamp(valor)
        if resultado:
            return resultado
    return None


def _texto_evento_api(item: Dict[str, Any], config: Dict[str, str]) -> Tuple[str, str, str, str, str, str]:
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


def normalizar_evento_api(deporte: str, item: Dict[str, Any], config: Dict[str, str], tz: ZoneInfo, fecha_referencia: Optional[date] = None) -> Optional[Dict[str, Any]]:
    inicio = _fecha_evento_api(item)
    ref = fecha_referencia or datetime.now(tz).date()
    if inicio is None or inicio.astimezone(tz).date() != ref:
        return None
    titulo, torneo, local, visitante, tipo, subtitulo = _texto_evento_api(item, config)
    if not titulo:
        return None
    id_origen = str(_get(item, "fixture.id", "game.id", "race.id", "fight.id", "id") or "")
    if not id_origen:
        id_origen = hashlib.sha1(f"{deporte}|{titulo}|{iso_utc(inicio)}".encode()).hexdigest()[:16]

    logo_torneo = str(_get(item, "league.logo", "competition.logo", "race.competition.logo") or "")
    if not logo_torneo:
        logo_torneo = resolver_logo_torneo(torneo or titulo, config["categoria"])

    logo_local = str(_get(item, "teams.home.logo", "home.logo") or "")
    logo_visitante = str(_get(item, "teams.away.logo", "away.logo") or "")

    # GUARDADO EN MEMORIA PERPETUA:
    if local and logo_local: registrar_logo_equipo(local, logo_local)
    if visitante and logo_visitante: registrar_logo_equipo(visitante, logo_visitante)

    estado_raw = str(_get(item, "fixture.status.short", "game.status.short", "race.status.short", "status.short", "status.long") or "NS").upper()
    estado = "en_vivo" if estado_raw in {"1H", "2H", "HT", "Q1", "Q2", "Q3", "Q4", "LIVE", "IN", "P", "RUNNING"} else "finalizado" if estado_raw in {"FT", "AET", "PEN", "FINISHED", "ENDED"} else "programado"
    return {
        "id": f"apisports_{deporte}_{id_origen}", "agenda_id": f"apisports_{deporte}_{id_origen}",
        "titulo": titulo, "torneo": torneo, "categoria": config["categoria"], "tipo_evento": tipo,
        "equipo_local": local, "equipo_visitante": visitante, "subtitulo": subtitulo,
        "hora_utc": iso_utc(inicio), "hora_local_producto": inicio.astimezone(tz).strftime("%H:%M"),
        "duracion_min": DURACION_POR_CATEGORIA.get(config["categoria"], 150),
        "logo_torneo": logo_torneo, "logo_local": logo_local, "logo_visitante": logo_visitante,
        "banner": "",
        "tier": 1, "origen": f"api_sports:{deporte}", "origenes": [f"api_sports:{deporte}"],
        "estado": "confirmado", "estado_evento": estado, "confianza": "alta", "puntuacion_confianza": 100,
        "fuentes": [], "metodo_correlacion": "agenda_api_sports", "razones_correlacion": [f"deporte:{deporte}"],
    }


def _leer_cache(fecha_consulta: str, permitir_vencida: bool = False) -> Optional[Dict[str, Any]]:
    if not ARCHIVO_CACHE.exists():
        return None
    try:
        datos = json.loads(ARCHIVO_CACHE.read_text(encoding="utf-8"))
        if not isinstance(datos, dict) or datos.get("fecha_local_producto") != fecha_consulta:
            return None
        generado = parsear_iso_o_timestamp(datos.get("generado_utc"))
        if generado:
            edad = (datetime.now(timezone.utc) - generado).total_seconds()
        else:
            edad = time.time() - ARCHIVO_CACHE.stat().st_mtime
        if permitir_vencida or (MINUTOS_CACHE > 0 and edad <= MINUTOS_CACHE * 60):
            return datos
    except (OSError, ValueError):
        return None
    return None


def cargar_agenda_cache(fecha_consulta: str) -> List[Dict[str, Any]]:
    datos = _leer_cache(fecha_consulta, permitir_vencida=True)
    return list(datos.get("eventos", [])) if datos else []


def reservar_presupuesto(nombre: str, fecha_consulta: str, limite: int) -> bool:
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


def obtener_respaldo_thesportsdb(fecha_consulta: str, tz: ZoneInfo, metricas: Counter) -> List[Dict[str, Any]]:
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
    eventos: List[Dict[str, Any]] = []
    for bruto in (datos.get("events") or [])[:THESPORTSDB_MAX_EVENTOS]:
        inicio = parsear_iso_o_timestamp(bruto.get("strTimestamp"))
        if inicio is None:
            inicio = parsear_iso_o_timestamp(f"{bruto.get('dateEvent', '')} {bruto.get('strTime', '00:00:00')}")
        if inicio is None or inicio.astimezone(tz).date().isoformat() != fecha_consulta:
            continue
        local, visitante = (bruto.get("strHomeTeam") or "").strip(), (bruto.get("strAwayTeam") or "").strip()
        titulo = f"{local} vs {visitante}" if local and visitante else (bruto.get("strEvent") or bruto.get("strLeague") or "").strip()
        if not titulo:
            continue
        categoria = inferir_deporte(f"{bruto.get('strSport', '')} {titulo}") or "Deportes"
        ident = str(bruto.get("idEvent") or hashlib.sha1(f"{titulo}|{iso_utc(inicio)}".encode()).hexdigest()[:16])
        torneo = (bruto.get("strLeague") or "").strip()
        logo_torneo = bruto.get("strLeagueBadge") or resolver_logo_torneo(torneo, categoria)
        eventos.append({
            "id": f"tsdb_{ident}", "agenda_id": f"tsdb_{ident}", "titulo": titulo,
            "torneo": torneo, "categoria": categoria,
            "tipo_evento": "duelo" if local and visitante and categoria not in DEPORTES_COMPETICION else "sencillo",
            "equipo_local": local if categoria not in DEPORTES_COMPETICION else "",
            "equipo_visitante": visitante if categoria not in DEPORTES_COMPETICION else "",
            "subtitulo": "", "hora_utc": iso_utc(inicio), "hora_local_producto": inicio.astimezone(tz).strftime("%H:%M"),
            "duracion_min": DURACION_POR_CATEGORIA.get(categoria, 150), "logo_torneo": logo_torneo,
            "logo_local": bruto.get("strHomeTeamBadge") or "", "logo_visitante": bruto.get("strAwayTeamBadge") or "",
            "banner": "",
            "tier": 2, "origen": "thesportsdb_respaldo", "origenes": ["thesportsdb_respaldo"],
            "estado": "confirmado", "estado_evento": "programado", "confianza": "media", "puntuacion_confianza": 78,
            "fuentes": [], "metodo_correlacion": "agenda_thesportsdb_respaldo", "razones_correlacion": [],
        })
    metricas["eventos"] = len(eventos)
    return eventos


def obtener_agenda_maestra(fecha_consulta: str, metricas_salida: Optional[Dict[str, Any]] = None, forzar: bool = False, cliente: Optional[ClienteApiSports] = None) -> List[Dict[str, Any]]:
    tz = obtener_zona_aplicacion()
    cache = None if forzar else _leer_cache(fecha_consulta)
    if cache:
        if metricas_salida is not None:
            metricas_salida.update(cache.get("metricas", {}))
            metricas_salida["cache"] = "vigente"
        return list(cache.get("eventos", []))

    if cliente is None:
        cliente = ClienteApiSports(API_SPORTS_KEY)
    agenda: List[Dict[str, Any]] = []
    fecha_ref = date.fromisoformat(fecha_consulta)
    for deporte, config in API_CONFIGS.items():
        if deporte not in API_SPORTS_DEPORTES:
            continue
        params = {"date": fecha_consulta, "timezone": APP_TIMEZONE}
        datos = cliente.get(deporte, config["host"], config["endpoint"], params)
        for bruto in (datos or {}).get("response") or []:
            if isinstance(bruto, dict):
                evento = normalizar_evento_api(deporte, bruto, config, tz, fecha_referencia=fecha_ref)
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


def llamada_xtream(url: str, timeout: int = 60) -> Any:
    respuesta = requests.get(PUENTE_URL, params={"url": url}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.json()


def detectar_base_media_m3u() -> str:
    """Obtiene el host real de streaming para reproducir leyendo el M3U."""
    base_api = XTREAM_URL
    url_m3u = f"{base_api}/get.php?username={XTREAM_USER}&password={XTREAM_PASS}&type=m3u&output=ts"
    candidatos = []
    try:
        with requests.get(url_m3u, headers={"Range": "bytes=0-65535"}, stream=True, timeout=(15, 30)) as r:
            if r.status_code not in (200, 206):
                return base_api
            for linea_bytes in r.iter_lines(chunk_size=8192):
                linea = linea_bytes.decode("utf-8", errors="ignore").strip()
                if not linea.startswith(("http://", "https://")):
                    continue
                parsed = urlparse(linea)
                partes = [p for p in parsed.path.split("/") if p]
                if len(partes) >= 3 and partes[-3] == XTREAM_USER and partes[-2] == XTREAM_PASS:
                    candidatos.append(f"{parsed.scheme}://{parsed.netloc}")
                if len(candidatos) >= 3:
                    break

        if len(candidatos) >= 3 and len(set(candidatos)) == 1:
            base_media = candidatos[0]
            log.info(f"Base de media detectada desde M3U: {base_media}")
            return base_media
    except Exception:
        pass
    return base_api


def detectar_categorias_fechadas(fecha_local: datetime) -> Tuple[set[str], set[str]]:
    """Identifica categorías fechadas numéricamente o con meses escritos para descartar categorías de ayer."""
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


def es_candidato(stream: Dict[str, Any], categorias_hoy: set[str], fecha_local: datetime, categorias_ajenas: Optional[set[str]] = None) -> Tuple[bool, str]:
    """Evalúa estrictamente si un stream es un evento válido con hora programada."""
    nombre = str(stream.get("name") or "").strip()
    if not nombre:
        return False, "sin_nombre"
    if contiene_veto(nombre):
        return False, "contenido_no_evento"

    # 1. Comprobación de fecha explícita (numérica o textual, ej: "31 DE AGOSTO")
    fecha_en_texto = fecha_xtream_explicita(nombre, fecha_local.date())
    if fecha_en_texto is False:
        return False, "fecha_xtream_fuera_de_jornada"
    if str(stream.get("category_id") or "") in (categorias_ajenas or set()):
        return False, "categoria_xtream_fuera_de_jornada"

    # 2. REGLA INQUEBRANTABLE: Un evento real SIEMPRE tiene hora programada
    hora = extraer_hora_canal(nombre, fecha_local)
    if hora is None:
        return False, "sin_hora_programada"

    # 3. Categorización e identidad
    deporte = inferir_deporte(nombre)
    duelo = bool(re.search(r"\b(VS|V|AT)\b|\s[-@]\s", normalizar_texto(nombre)))
    categoria_hoy = str(stream.get("category_id") or "") in categorias_hoy

    if fecha_en_texto is True:
        return True, "fecha_xtream_hoy"
    if categoria_hoy:
        return True, "categoria_xtream_hoy"
    if deporte or duelo:
        return True, "hora_y_deporte_valido"

    return True, "hora_programada_en_canal"


def obtener_canales_candidatos(fecha_local: datetime) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
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
    candidatos: List[Dict[str, Any]] = []
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
            "logo_xtream": str(stream.get("stream_icon") or stream.get("tvg_logo") or ""),
            "added": stream.get("added"),
        })
    metricas["candidatos"] = len(candidatos)
    return candidatos, dict(metricas)


def _inicio_evento(evento: Dict[str, Any]) -> Optional[datetime]:
    return parsear_iso_o_timestamp(evento.get("hora_utc"))


def _deportes_compatibles(canal: Dict[str, Any], evento: Dict[str, Any]) -> bool:
    return not canal.get("categoria_inferida") or canal["categoria_inferida"] == evento.get("categoria")


def _sesiones_compatibles(canal: Dict[str, Any], evento: Dict[str, Any]) -> bool:
    sesion_canal = canal.get("sesion")
    sesion_evento = extraer_sesion(f"{evento.get('titulo', '')} {evento.get('subtitulo', '')}")
    if not sesion_canal or not sesion_evento or sesion_canal == sesion_evento:
        return True
    return sesion_evento == "sprint" and sesion_canal == "carrera_sprint"


def _proximidad_horaria(canal: Dict[str, Any], evento: Dict[str, Any]) -> Tuple[bool, Optional[int]]:
    hora_canal, hora_evento = canal.get("hora_local"), _inicio_evento(evento)
    if hora_canal is None or hora_evento is None:
        return True, None
    diferencia = round(abs((hora_canal.astimezone(timezone.utc) - hora_evento).total_seconds()) / 60)
    return diferencia <= MAX_DIFERENCIA_MIN, diferencia


def calcular_similitud_simple(titulo: str, torneo: str, canal: str) -> Tuple[int, List[str]]:
    evento_tokens = tokenizar(f"{titulo} {torneo}")
    canal_tokens = tokenizar(canal)
    comunes = sorted(evento_tokens & canal_tokens)
    if not comunes:
        return 0, []

    comunes_significativos = [t for t in comunes if t not in TOKENS_GENERICOS]
    if not comunes_significativos:
        return 0, []

    cobertura = len(comunes) / max(len(evento_tokens), 1)
    precision = len(comunes) / max(len(canal_tokens), 1)
    puntuacion = round(100 * (0.76 * cobertura + 0.24 * precision))
    evento_norm, canal_norm = normalizar_texto(titulo), normalizar_texto(canal)
    if evento_norm and evento_norm in canal_norm:
        puntuacion = max(puntuacion, 92)
    if len(comunes_significativos) == 1 and len(comunes_significativos[0]) >= 8:
        puntuacion = max(puntuacion, 58)
    return min(puntuacion, 100), comunes


def emparejar_evento(canal: Dict[str, Any], agenda: Iterable[Dict[str, Any]], *, ignorar_proximidad_fecha: bool = False) -> Tuple[Optional[Dict[str, Any]], int, str, List[str]]:
    mejor, mejor_puntos, mejor_metodo, mejor_razones = None, 0, "sin_coincidencia_verificable", []
    for evento in agenda:
        if not _deportes_compatibles(canal, evento) or not _sesiones_compatibles(canal, evento):
            continue
        diferencia = None
        if not ignorar_proximidad_fecha:
            cercano, diferencia = _proximidad_horaria(canal, evento)
            if not cercano:
                continue

        local = tokenizar(evento.get("equipo_local", ""))
        visita = tokenizar(evento.get("equipo_visitante", ""))
        acierto_local = sorted(local & canal["tokens"])
        acierto_visita = sorted(visita & canal["tokens"])

        local_sig = [t for t in acierto_local if t not in TOKENS_GENERICOS]
        visita_sig = [t for t in acierto_visita if t not in TOKENS_GENERICOS]

        if local and visita and acierto_local and acierto_visita and (local_sig or visita_sig):
            puntos = min(100, 84 + 4 * (len(acierto_local) + len(acierto_visita)) + (5 if diferencia is not None else 0))
            metodo, razones = "duelo_verificado", [f"local:{','.join(acierto_local)}", f"visitante:{','.join(acierto_visita)}"]
        else:
            puntos, comunes = calcular_similitud_simple(evento.get("titulo", ""), evento.get("torneo", ""), canal["nombre_ui"])
            frase = normalizar_texto(evento.get("titulo", "")) in canal["texto_normalizado"] if evento.get("titulo") else False
            fuerte = any(len(t) >= 8 and t not in TOKENS_GENERICOS for t in comunes) and canal.get("categoria_inferida") == evento.get("categoria")
            if len(comunes) < 2 and not frase and not fuerte:
                continue
            puntos = min(100, puntos + (5 if diferencia is not None else 0))
            metodo, razones = "evento_simple_verificado", [f"tokens:{','.join(comunes)}"]

        if diferencia is not None:
            razones.append(f"diferencia_min:{diferencia}")
        if puntos > mejor_puntos:
            mejor, mejor_puntos, mejor_metodo, mejor_razones = evento, puntos, metodo, razones
    return mejor, mejor_puntos, mejor_metodo, mejor_razones


def fusionar_fuente(evento: Dict[str, Any], fuente: Dict[str, Any]) -> None:
    fuentes = evento.setdefault("fuentes", [])
    if not any(str(x.get("id_xtream")) == str(fuente.get("id_xtream")) for x in fuentes):
        fuentes.append(fuente)


def analizar_titulo_xtream(nombre_ui: str, categoria: str) -> Tuple[str, str, str, str, str]:
    limpio = re.sub(r"(?<!\d)\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?(?!\d)", "", nombre_ui)
    limpio = re.sub(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)\s*([APap][Mm]|[Hh][Rr]?[Ss]?)?(?!\d)", "", limpio)
    limpio = re.sub(r"(?<!\d)([01]?\d|2[0-3])[hH]([0-5]\d)(?!\d)", "", limpio)
    limpio = re.sub(r"\b(FHD|HD|SD|OP1|OP2|4K|HEVC|MULTI|ES|SPAIN|LATAM|EVENTOS?)\b", "", limpio, flags=re.I)
    limpio = " ".join(limpio.strip(" -|·:▪/|").split())

    es_competicion = categoria in DEPORTES_COMPETICION

    # Pivote de duelo
    duelo_match = None
    if not es_competicion:
        duelo_match = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus|@)\s+(.+)", limpio, flags=re.I)
        if not duelo_match:
            duelo_match = re.search(r"(.+?)\s+(?:x|-)\s+(.+)", limpio, flags=re.I)

    if duelo_match:
        parte_izq, parte_der = duelo_match.group(1).strip(), duelo_match.group(2).strip()
        sub_izq = [p.strip() for p in re.split(r"\s*[|·▪/:-]\s*", parte_izq) if p.strip()]
        sub_der = [p.strip() for p in re.split(r"\s*[|·▪/:-]\s*", parte_der) if p.strip()]

        torneo, local = (sub_izq[0], sub_izq[-1]) if len(sub_izq) > 1 else (categoria, parte_izq)
        visitante = sub_der[0] if sub_der else parte_der
        subtitulo = " - ".join(sub_der[1:]) if len(sub_der) > 1 else ""
        return torneo, subtitulo, "duelo", local, visitante

    partes = [p.strip() for p in re.split(r"\s*[|·▪/]\s*", limpio) if p.strip()]
    if len(partes) >= 2:
        return partes[0], " - ".join(partes[1:]), "sencillo", "", ""
    return limpio, "", "sencillo", "", ""


def crear_evento_independiente_xtream(canal: Dict[str, Any], tz: ZoneInfo, existentes: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Crea un evento legítimo no presente en API Sports (ej: Tejo, deportes raros o regionales). Requiere hora obligatoria."""
    hora = canal.get("hora_local")
    if hora is None:
        return None

    categoria = canal.get("categoria_inferida") or "Otros Deportes"
    torneo, subtitulo, tipo, local, visitante = analizar_titulo_xtream(canal["nombre_ui"], categoria)

    if not torneo and not local:
        return None

    titulo = f"{local} vs {visitante}" if tipo == "duelo" else (torneo or canal["nombre_ui"])
    clave_dedup = normalizar_texto(titulo) + hora.strftime('%H%M')
    ident = existentes.get(clave_dedup) or hashlib.sha1(clave_dedup.encode("utf-8")).hexdigest()[:16]
    existentes[clave_dedup] = ident

    logo_torneo = canal.get("logo_xtream") or resolver_logo_torneo(torneo, categoria)
    logo_loc = resolver_logo_equipo(local) or (logo_torneo if tipo == "sencillo" else canal.get("logo_xtream", ""))
    logo_vis = resolver_logo_equipo(visitante)

    return {
        "id": f"xtream_{ident}", "agenda_id": "", "titulo": titulo, "torneo": torneo, "categoria": categoria,
        "tipo_evento": tipo, "equipo_local": local, "equipo_visitante": visitante, "subtitulo": subtitulo,
        "hora_utc": iso_utc(hora), "hora_local_producto": hora.astimezone(tz).strftime("%H:%M"),
        "duracion_min": DURACION_POR_CATEGORIA.get(categoria, 150),
        "logo_torneo": logo_torneo, "logo_local": logo_loc, "logo_visitante": logo_vis,
        "banner": "",
        "tier": 3, "origen": "xtream_evento", "origenes": ["xtream"], "estado": "confirmado",
        "estado_evento": "programado", "confianza": "media", "puntuacion_confianza": 75, "fuentes": [],
        "metodo_correlacion": "evento_independiente_verificado",
        "razones_correlacion": [canal.get("motivo_vigencia", ""), f"categoria:{categoria}"],
    }

# Alias para compatibilidad hacia atrás
crear_probable_xtream = crear_evento_independiente_xtream


def evaluar_frescura_lista(candidatos: List[Dict[str, Any]], agenda_ayer: List[Dict[str, Any]], agenda_hoy: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Evalúa con contraste negativo si la lista Xtream es un residuo de ayer o si está fresca."""
    coincidencias_ayer = 0
    coincidencias_hoy = 0
    total_evaluados = 0

    for canal in candidatos:
        if not canal.get("categoria_inferida") and "VS" not in canal["texto_normalizado"]:
            continue
        total_evaluados += 1
        ev_ayer, pts_ayer, _, _ = emparejar_evento(canal, agenda_ayer, ignorar_proximidad_fecha=True)
        ev_hoy, pts_hoy, _, _ = emparejar_evento(canal, agenda_hoy)

        if ev_ayer and pts_ayer >= 75:
            coincidencias_ayer += 1
        if ev_hoy and pts_hoy >= 75:
            coincidencias_hoy += 1

    es_stale = False
    if total_evaluados >= 3 and coincidencias_ayer >= 3 and coincidencias_hoy == 0:
        es_stale = True
        log.warning("Semáforo de Frescura: Detectada lista Xtream OBSOLETA (de ayer). Coincidencias ayer=%d, hoy=%d", coincidencias_ayer, coincidencias_hoy)
    else:
        log.info("Semáforo de Frescura: Lista Xtream VIGENTE. Coincidencias ayer=%d, hoy=%d, evaluados=%d", coincidencias_ayer, coincidencias_hoy, total_evaluados)

    return {
        "es_stale": es_stale,
        "coincidencias_ayer": coincidencias_ayer,
        "coincidencias_hoy": coincidencias_hoy,
        "evaluados": total_evaluados
    }


def curar_eventos(agenda_hoy: List[Dict[str, Any]], agenda_ayer: List[Dict[str, Any]], candidatos: List[Dict[str, Any]], fecha_producto: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    resultados: Dict[str, Dict[str, Any]] = {}
    cuarentena: List[Dict[str, Any]] = []
    metricas: Counter = Counter()
    deduplicador: Dict[str, str] = {}
    tz = obtener_zona_aplicacion()

    frescura = evaluar_frescura_lista(candidatos, agenda_ayer, agenda_hoy)
    metricas["lista_stale"] = 1 if frescura["es_stale"] else 0

    for canal in candidatos:
        # Si el canal no tiene hora programada, jamás se procesa
        if canal.get("hora_local") is None:
            metricas["descartado_sin_hora"] += 1
            continue

        fuente = {"nombre": canal["nombre_ui"], "id_xtream": canal["id_xtream"]}

        # 1. Contraste principal con agenda oficial de hoy
        evento, puntos, metodo, razones = emparejar_evento(canal, agenda_hoy)

        # 2. Contraste negativo: Si no coincide con hoy pero sí con ayer, es un residuo del día anterior
        if evento is None and agenda_ayer:
            ev_ayer, pts_ayer, _, _ = emparejar_evento(canal, agenda_ayer, ignorar_proximidad_fecha=True)
            if ev_ayer and pts_ayer >= 70:
                metricas["descartado_partido_de_ayer"] += 1
                if len(cuarentena) < MAX_CUARENTENA:
                    cuarentena.append({**fuente, "motivo": "residuo_partido_de_ayer", "evento_ayer": ev_ayer.get("titulo")})
                continue

        # 3. Si la lista global fue calificada como obsoleta y el canal no coincidió con hoy, descartar
        if evento is None and frescura["es_stale"]:
            metricas["descartado_lista_stale"] += 1
            if len(cuarentena) < MAX_CUARENTENA:
                cuarentena.append({**fuente, "motivo": "lista_xtream_no_rotada"})
            continue

        # 4. Para deportes legítimos no cubiertos por la API (ej: Tejo, deportes especiales de hoy)
        if evento is None and PUBLICAR_XTREAM_PROBABLE:
            evento = crear_evento_independiente_xtream(canal, tz, deduplicador)
            if evento:
                puntos = int(evento["puntuacion_confianza"])
                metodo = str(evento["metodo_correlacion"])
                razones = list(evento["razones_correlacion"])
                metricas["eventos_independientes_creados"] += 1

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
        metricas["fuentes_emparejadas" if evento.get("agenda_id") else "fuentes_independientes"] += 1

    if PUBLICAR_AGENDA_SIN_FUENTE:
        for evento in agenda_hoy:
            resultados.setdefault(evento["id"], {k: v for k, v in evento.items()})

    eventos = sorted(resultados.values(), key=lambda e: e["hora_utc"])
    metricas["eventos_unicos"] = len(eventos)
    return eventos, cuarentena, {**dict(metricas), "frescura": frescura}


def main() -> None:
    tz = obtener_zona_aplicacion()
    ahora = datetime.now(tz)
    fecha_hoy = ahora.date().isoformat()
    fecha_ayer = (ahora.date() - timedelta(days=1)).isoformat()
    log.info("=== Curador multideporte v12 Inteligente | Colombia %s ===", fecha_hoy)

    cliente_api = ClienteApiSports(API_SPORTS_KEY)

    metricas_agenda_hoy: Dict[str, Any] = {}
    agenda_hoy = obtener_agenda_maestra(fecha_hoy, metricas_agenda_hoy, cliente=cliente_api)

    # Agenda de ayer para contraste negativo (si no está en caché local, se consulta)
    agenda_ayer = cargar_agenda_cache(fecha_ayer)
    if not agenda_ayer:
        log.info("Cargando agenda de ayer (%s) para verificación de frescura y contraste...", fecha_ayer)
        agenda_ayer = obtener_agenda_maestra(fecha_ayer, cliente=cliente_api)

    candidatos, metricas_xtream = obtener_canales_candidatos(ahora)
    eventos, cuarentena, metricas_curacion = curar_eventos(agenda_hoy, agenda_ayer, candidatos, ahora.date())

    salida = {
        "version": 12, "generado_utc": iso_utc(datetime.now(timezone.utc)), "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha_hoy, "base_media": detectar_base_media_m3u(), "eventos": eventos,
    }
    meta = {
        "version": 12, "generado_utc": salida["generado_utc"], "zona_horaria_producto": str(tz),
        "fecha_local_producto": fecha_hoy, "agenda": metricas_agenda_hoy, "xtream": metricas_xtream,
        "curacion": metricas_curacion, "eventos_finales_base": len(eventos), "cuarentena_guardada": len(cuarentena),
    }
    guardar_json(ARCHIVO_SALIDA, salida)
    guardar_json(ARCHIVO_CUARENTENA, cuarentena)
    guardar_json(ARCHIVO_META, meta)
    log.info("Finalizado: agenda=%d candidatos=%d eventos=%d cuarentena=%d", len(agenda_hoy), len(candidatos), len(eventos), len(cuarentena))


if __name__ == "__main__":
    main()
