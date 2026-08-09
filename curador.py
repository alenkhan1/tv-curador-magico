#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Curador VOD -> catalogo_curado.json

Principio central (acordado 01-ago-2026):
El catalogo completo NUNCA descarta un titulo solo porque TMDB no lo identifico.
Solo se descarta por: contenido adulto, exclusion manual explicita, o duplicado
inferior (que igual se conserva como respaldo, no se destruye).

Dos niveles de confianza en el mismo archivo:
  - "items"          -> identificados con TMDB: poster, genero, sinopsis, nota.
                        Estos alimentan las filas de vitrina.
  - "sin_identificar"-> sobrevive el filtro adulto pero TMDB no lo resolvio.
                        Solo nombre limpio e id del proveedor. Van al buscador,
                        nunca a una fila de vitrina (no hay genero/anio real).

Arquitectura de dos fases:
FASE 1 clasificar TODO el catalogo (sin decidir nada de presentacion)
FASE 2 armar la vitrina (agrupar duplicados, fusionar filas flacas, ordenar, recortar)

La calidad tecnica (CAM, BRrip, etc.) NUNCA excluye un titulo por si sola.
Solo se usa para desempatar entre copias del MISMO titulo (mismo id de TMDB).
Si una pelicula solo existe en una copia de baja calidad, esa copia se acepta
igual: es la unica que existe en el mundo real para ese titulo.

Archivos que maneja:
catalogo_curado.json generado. NUNCA se edita a mano.
cache_tmdb.json memoria persistente. Permite reanudar y hace las corridas diarias rapidas.
correcciones.json OPCIONAL, la editas tu a mano: equivalencias, exclusiones, forzados.
                  El curador funciona perfecto sin tocarlo nunca.
informe_no_resueltos.json generado. Diagnostico de lo no identificado, agrupado por patron.
"""

import os
import re
import sys
import json
import time
import random
import difflib
import threading
import unicodedata
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    from curl_cffi import requests as cffi_requests
except Exception:
    cffi_requests = None

# ============================================================================
# CONFIGURACION
# ============================================================================

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
XTREAM_URL = (os.environ.get("XTREAM_URL") or "").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
PUENTE_URL = os.environ.get(
    "PUENTE_URL", "https://mi-dashboard-tv.onrender.com/api/puente_xtream"
)

ARCHIVO_CATALOGO = "catalogo_curado.json"
ARCHIVO_CACHE = "cache_tmdb.json"
ARCHIVO_CORRECCIONES = "correcciones.json"
ARCHIVO_INFORME = "informe_no_resueltos.json"

SCHEMA = 3          # sube de 2 a 3: catalogo completo + nivel "sin_identificar"
CACHE_VERSION = 1

# --- Topes de VITRINA (cuantos posters por fila, NO del catalogo) ----------
# El catalogo completo (items + sin_identificar) nunca tiene tope.
# Estos topes solo deciden cuantas referencias entran en cada fila.
TOPE_GENERO = int(os.environ.get("TOPE_GENERO", "50"))
TOPE_ESTRENOS = int(os.environ.get("TOPE_ESTRENOS", "30"))
TOPE_EPOCA = int(os.environ.get("TOPE_EPOCA", "50"))
PISO_FILA = int(os.environ.get("PISO_FILA", "10"))

# --- Reglas temporales (ventana rodante) ------------------------------------
ANIOS_ANTIGUO = 25
CLASICO_NOTA_MIN = 7.8
CLASICO_VOTOS_MIN = 500
DIAS_ESTRENO_PELICULA = 180
DIAS_ESTRENO_SERIE = 90
DIAS_PROVEEDOR_ESTRENO = 150
MARGEN_FUTURO_DIAS = 15

# --- Rendimiento ------------------------------------------------------------
HILOS_TMDB = int(os.environ.get("HILOS_TMDB", "12"))
TMDB_RPS = float(os.environ.get("TMDB_RPS", "35"))
PRESUPUESTO_MIN = int(os.environ.get("PRESUPUESTO_MIN", "240"))
GUARDAR_CADA = int(os.environ.get("GUARDAR_CADA", "400"))
PAUSA_PROVEEDOR = float(os.environ.get("PAUSA_PROVEEDOR", "0.3"))
UMBRAL_NO_FILTRA = 50000

# --- Refresco de memoria (dias) ---------------------------------------------
REFRESCO_RECIENTE = 30
REFRESCO_ANTIGUO = 180
REFRESCO_FALLIDO = 14
REFRESCO_SERIE_ACTIVA = 7
REFRESCO_SERIE_TERMINADA = 180

# --- Validacion antes de publicar -------------------------------------------
TOLERANCIA_CAIDA = 0.60
MINIMO_FILAS = 3

# --- Umbrales de coincidencia TMDB ------------------------------------------
UMBRAL_NORMAL = 0.80
UMBRAL_RIESGO = 0.88

# --- Quinto intento: titulos alternativos (acordado 09-ago-2026) -----------
# Cuantos candidatos (de los ya traidos por busquedas anteriores) se
# revisan en detalle contra sus titulos alternativos/traducciones antes de
# rendirse. No dispara busquedas nuevas de texto, solo peticiones de
# detalle por candidato ya visto; se limita para no disparar el tiempo de
# corrida en catalogos grandes.
TOPE_CANDIDATOS_ALTERNATIVOS = 5

# --- Alerta de calidad del informe (acordado 09-ago-2026) -------------------
# El usuario no revisa el informe a mano; el propio workflow debe avisar
# cuando el porcentaje de titulos sin identificar se sale de lo normal.
# Esto NO bloquea la publicacion del catalogo (eso lo sigue decidiendo
# 'validar()'), solo deja una senal visible en el resumen del job.
UMBRAL_ALERTA_NO_IDENTIFICADO = float(
    os.environ.get("UMBRAL_ALERTA_NO_IDENTIFICADO", "15")
)  # porcentaje

HOY = datetime.now(timezone.utc)
ANIO_ACTUAL = HOY.year
ANIO_ANTIGUO = ANIO_ACTUAL - ANIOS_ANTIGUO
LIMITE_ESTRENO_PELI = HOY - timedelta(days=DIAS_ESTRENO_PELICULA)
LIMITE_ESTRENO_SERIE = HOY - timedelta(days=DIAS_ESTRENO_SERIE)
LIMITE_FUTURO = HOY + timedelta(days=MARGEN_FUTURO_DIAS)
INICIO = time.monotonic()

POSTER_BASE = "https://image.tmdb.org/t/p/"
POSTER_SIZE = "w500"

# ============================================================================
# GENEROS
# ============================================================================

GEN_PELICULAS = {
    28: "Accion", 12: "Aventura", 16: "Animacion", 35: "Comedia",
    80: "Crimen", 99: "Documental", 18: "Drama", 10751: "Familia",
    14: "Fantasia", 36: "Historia", 27: "Terror", 10402: "Musica",
    9648: "Misterio", 10749: "Romance", 878: "Ciencia Ficcion",
    10770: "Pelicula de TV", 53: "Suspense", 10752: "Belica", 37: "Western",
}

GEN_SERIES = {
    10759: "Accion y Aventura", 16: "Animacion", 35: "Comedia",
    80: "Crimen", 99: "Documental", 18: "Drama", 10751: "Familia",
    10762: "Infantil", 9648: "Misterio", 10763: "Documental",
    10764: "Reality", 10765: "Sci-Fi & Fantasy", 10766: "Telenovela",
    10767: "Talk Show", 10768: "Guerra y Politica", 37: "Western",
}

FILA_ANIME = "Anime"
FILA_SIN_GENERO = "Descubrir"

PRIORIDAD_PELICULAS = [
    99, 16, 27, 37, 878, 10752, 10402, 36, 14, 80, 9648, 53, 12, 28,
    10749, 10751, 35, 18, 10770,
]
PRIORIDAD_SERIES = [
    99, 16, 37, 10765, 10768, 80, 9648, 10762, 10759, 10764, 10767,
    10763, 10766, 10751, 35, 18,
]

FUERTES_PELICULAS = {99, 16, 27, 37, 878, 10752, 10402, 36, 14, 80, 9648, 53}
FUERTES_SERIES = {99, 16, 37, 10765, 10768, 80, 9648, 10762}

ORDEN_CATEGORIAS = [
    "Estrenos", "Clasicos", "Retro",
    "Accion", "Accion y Aventura", "Ciencia Ficcion", "Sci-Fi & Fantasy",
    "Terror", "Suspense", "Misterio", "Crimen",
    "Comedia", "Romance", "Drama", "Telenovela",
    "Animacion", "Anime", "Familia", "Infantil",
    "Fantasia", "Aventura",
    "Documental", "Historia", "Belica", "Guerra y Politica",
    "Musica", "Pelicula de TV", "Reality", "Talk Show", "Western",
    "Descubrir",
]

FUSION = {
    "Belica": "Accion",
    "Historia": "Documental",
    "Western": "Accion",
    "Pelicula de TV": "Drama",
    "Musica": "Documental",
    "Fantasia": "Ciencia Ficcion",
    "Misterio": "Suspense",
    "Aventura": "Accion",
    "Romance": "Drama",
    "Familia": "Animacion",
    "Guerra y Politica": "Drama",
    "Talk Show": "Reality",
    "Reality": "Documental",
    "Telenovela": "Drama",
    "Infantil": "Animacion",
    "Anime": "Animacion",
    "Suspense": "Crimen",
    "Sci-Fi & Fantasy": "Accion y Aventura",
}

TOPES = {"Estrenos": TOPE_ESTRENOS, "Clasicos": TOPE_EPOCA, "Retro": TOPE_EPOCA}
TIPOS_FILA = {"Estrenos": "estrenos", "Clasicos": "epoca", "Retro": "epoca"}

# ============================================================================
# NORMALIZACION
# ============================================================================

def sin_acentos(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    )

def normalizar(texto):
    t = sin_acentos(texto).upper()
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    return " ".join(t.split())

def clave(titulo, anio):
    return "%s|%s" % (normalizar(titulo), int(anio or 0))

def contiene_secuencia(norm, secuencia):
    return (" %s " % secuencia) in (" %s " % norm)

# ============================================================================
# FILTRO DE CONTENIDO ADULTO (unico motivo de exclusion "de origen")
# ============================================================================

CAT_SUBCADENAS = [
    "XXX", "ADULT", "HENTAI", "ONLYFANS", "BRAZZER", "PORN", "GANGBANG",
    "EROTIC", "PLAYBOY", "SWINGER", "FETISH", "BUKKAKE", "XVIDEO", "YOUPORN",
    "REDTUBE", "XNXX", "PRIVATE COM", "PUTA", "SEXTAPE",
]
CAT_TOKENS = [
    "XX", "SEX", "SEXO", "SEXY", "CAM", "CAMS", "24 7", "PAREJAS LIBERALES",
    "SOLO ADULTOS", "R18", "NSFW",
]
TITULO_SUBCADENAS = ["XXX", "HENTAI", "PORNO", "ONLYFANS", "BRAZZER"]
TITULO_TOKENS = [
    "HDCAM", "CAMRIP", "TELESYNC", "SCREENER", "DVDSCR", "TS SCREENER", "TSRIP",
]

RE_MAS18 = re.compile(r"(\+\s?18|18\s?\+|\(\s?18\s?\)|\b18\s?PLUS\b)", re.I)

def categoria_prohibida(nombre_categoria):
    if not nombre_categoria:
        return False
    if RE_MAS18.search(nombre_categoria):
        return True
    norm = normalizar(nombre_categoria)
    if not norm:
        return False
    for sub in CAT_SUBCADENAS:
        if sub in norm:
            return True
    for tok in CAT_TOKENS:
        if contiene_secuencia(norm, tok):
            return True
    return False

def titulo_prohibido(nombre_crudo):
    if not nombre_crudo:
        return False
    if RE_MAS18.search(nombre_crudo):
        return True
    norm = normalizar(nombre_crudo)
    for sub in TITULO_SUBCADENAS:
        if sub in norm:
            return True
    for tok in TITULO_TOKENS:
        if contiene_secuencia(norm, tok):
            return True
    return False

# ============================================================================
# LIMPIEZA DE TITULOS (por capas, extrayendo señal antes de descartar)
# Cobertura ampliada de etiquetas tecnicas para evitar residuos tipo
# "BRrip x264" al final del nombre mostrado (defecto reportado en la vitrina).
# ============================================================================

PREFIJOS = {
    "ES", "ESP", "SPA", "LAT", "LATINO", "MX", "MEX", "AR", "CO", "CL", "PE",
    "VE", "US", "UK", "EN", "BR", "PT", "FR", "IT", "JP", "KR",
    "VIP", "4K", "UHD", "HD", "FHD", "SD", "HQ", "NEW", "NUEVO", "NUEVOS",
    "ESTRENO", "ESTRENOS", "PREESTRENO", "CINE", "CINEX", "MULTI", "DUAL",
    "SUB", "SUBS", "VOSE", "VOS", "TOP", "PACK", "SAGA", "COL", "COLECCION",
    "PELICULA", "PELICULAS", "SERIE", "SERIES", "ANIME", "DOC", "INFANTIL",
}

CALIDADES = {
    "4K": (4, "4K"), "2160P": (4, "4K"), "UHD": (4, "4K"),
    "1080P": (3, "1080p"), "1080I": (3, "1080p"), "FULLHD": (3, "1080p"),
    "FHD": (3, "1080p"),
    "720P": (2, "720p"), "HD": (2, "720p"),
    "480P": (1, "SD"), "360P": (1, "SD"), "SD": (1, "SD"), "DVD": (1, "SD"),
}

FUENTES = {
    "REMUX": 3, "BLURAY": 3, "BDRIP": 3, "BRRIP": 3, "WEBDL": 3, "BDREMUX": 3,
    "WEBRIP": 2, "HDRIP": 2, "DVDRIP": 2, "HDTV": 2, "WEB": 2, "AMZN": 2,
    "NF": 2, "DSNP": 2, "HMAX": 2, "ATVP": 2, "HULU": 2,
    "HDCAM": 0, "CAMRIP": 0, "CAM": 0, "TS": 0, "TELESYNC": 0,
    "SCREENER": 0, "DVDSCR": 0, "TSRIP": 0,
}

# Palabras que SI son etiquetas de plataforma/origen pero tambien son
# palabras reales frecuentes en titulos (Star Wars, Max, etc). Solo se
# reconocen como etiqueta si vienen claramente delimitadas: dentro de un
# corchete/parentesis, o como la ULTIMA palabra suelta del texto. Nunca
# se descartan si aparecen como primera palabra o en medio del titulo,
# para no comerse titulos reales (bug detectado: "Star Wars" -> "Wars").
FUENTES_AMBIGUAS = {"MAX": 2, "STAR": 2}

IDIOMAS = {
    "LATINO": (3, "Latino"), "LAT": (3, "Latino"), "DUAL": (3, "Dual"),
    "MULTI": (3, "Dual"), "CASTELLANO": (2, "Castellano"),
    "ESPANOL": (2, "Castellano"), "ESP": (2, "Castellano"),
    "SUBTITULADO": (1, "Subtitulado"), "SUBS": (1, "Subtitulado"),
    "SUB": (1, "Subtitulado"), "VOSE": (1, "Subtitulado"),
    "VOS": (1, "Subtitulado"), "SUBTITULADA": (1, "Subtitulado"),
}

BASURA = {
    "X264", "X265", "H264", "H265", "HEVC", "AVC", "AAC", "AC3", "EAC3",
    "DTS", "DTSHD", "ATMOS", "TRUEHD", "DDP", "DD", "HDR", "HDR10", "DV",
    "DOLBY", "VISION", "REPACK", "PROPER", "RIP", "AUDIO", "VOD", "MKV",
    "MP4", "AVI", "RARBG", "YTS", "YIFY", "EVO", "GALAXYRG", "FGT", "ETRG",
    "PSA", "TGX", "MEGUSTA", "ION10", "SPARKS", "IMAX", "EXTENDED", "UNRATED",
    "REMASTERED", "OPEN", "MATTE", "LINE", "DUBBED", "10BIT", "8BIT",
    "WEBCAP", "PREAIR", "LIMITED", "INTERNAL", "COMPLETE", "MULTISUB",
}

RE_EXT = re.compile(r"\.(mp4|mkv|avi|mov|ts|m4v|flv|wmv)\s*$", re.I)
RE_CORCHETES = re.compile(r"\[([^\]]*)\]")
RE_PARENTESIS = re.compile(r"\(([^)]*)\)")
RE_ANIO = re.compile(r"\b(19\d{2}|20\d{2})\b")
RE_PREFIJO = re.compile(r"^\s*([^\-|:]{1,20})\s*[\-|:]\s+")
RE_SEPARADORES = re.compile(r"[|_·•‹›»«]+")
RE_INICIO_SUCIO = re.compile(r"^[^\w¡¿(\"']+", re.UNICODE)
# Numero suelto de colección al inicio ("01 - Titulo", "03 - Titulo"):
# lo usan proveedores para ordenar sagas en su explorador de archivos,
# no es parte real del titulo. Se descarta antes de tocar el resto.
RE_NUMERO_COLECCION = re.compile(r"^\s*\d{1,3}\s*[\-\.]\s+")

RE_BIGRAMAS = [
    (re.compile(r"\bWEB[\s\.\-]?DL\b", re.I), " WEBDL "),
    (re.compile(r"\bWEB[\s\.\-]?RIP\b", re.I), " WEBRIP "),
    (re.compile(r"\bBLU[\s\.\-]?RAY\b", re.I), " BLURAY "),
    (re.compile(r"\bBD[\s\.\-]?RE?MUX\b", re.I), " BDREMUX "),
    (re.compile(r"\bBD[\s\.\-]?RIP\b", re.I), " BDRIP "),
    (re.compile(r"\bBR[\s\.\-]?RIP\b", re.I), " BRRIP "),
    (re.compile(r"\bHD[\s\.\-]?RIP\b", re.I), " HDRIP "),
    (re.compile(r"\bDVD[\s\.\-]?RIP\b", re.I), " DVDRIP "),
    (re.compile(r"\bDTS[\s\.\-]?HD\b", re.I), " DTSHD "),
    (re.compile(r"\bFULL\s?HD\b", re.I), " FULLHD "),
    (re.compile(r"\b[HXhx][\s\.]?26([45])\b"), r" X26\1 "),
    (re.compile(r"\b[2578]\.[01]\b"), " "),
    (re.compile(r"\bDD\s?\+?\s?5\b", re.I), " "),
]

RE_SXXEYY = re.compile(r"\bS\s?\d{1,2}\s?E\s?\d{1,3}\b", re.I)
RE_NXNN = re.compile(r"\b\d{1,2}\s?[xX]\s?\d{2,3}\b")
RE_TEMPORADA = re.compile(
    r"\b(?:S|T|TEMP|TEMPORADA|SEASON)\s*\.?\s*(\d{1,2})\b", re.I
)
RE_EPISODIO = re.compile(
    r"\b(?:E|EP|CAP|CAPITULO|EPISODIO|EPISODE)\s*\.?\s*\d{1,3}\b", re.I
)

def absorber_etiquetas(fragmento, info):
    """Extrae calidad / origen / idioma de un trozo que vamos a descartar
    (prefijo del proveedor, contenido entre corchetes o parentesis).
    Asi no perdemos la senal que necesita el desempate de duplicados."""
    for token in re.split(r"[\s\-\.,;/\\|_]+", fragmento or ""):
        norm = normalizar(token)
        if not norm:
            continue
        if norm in CALIDADES:
            rango, txt = CALIDADES[norm]
            if rango > info["calidad"]:
                info["calidad"], info["calidad_txt"] = rango, txt
            info["etiquetas"].append(norm)
        elif norm in FUENTES or norm in FUENTES_AMBIGUAS:
            # Aqui es seguro reconocer las ambiguas (STAR, MAX): el
            # fragmento ya viene aislado (corchetes/parentesis/prefijo),
            # no es una palabra suelta en medio del titulo visible.
            rango = FUENTES.get(norm, FUENTES_AMBIGUAS.get(norm))
            if info["fuente"] is None or rango < info["fuente"]:
                info["fuente"] = rango
            info["etiquetas"].append(norm)
        elif norm in IDIOMAS:
            rango, txt = IDIOMAS[norm]
            if rango > info["idioma"]:
                info["idioma"], info["idioma_txt"] = rango, txt
            info["etiquetas"].append(norm)

def limpiar_titulo(nombre, es_serie=False):
    """Devuelve dict con: buscar, anio, calidad, calidad_txt, fuente,
    idioma, idioma_txt, temporada, etiquetas, prefijo."""
    info = {
        "buscar": "", "anio": 0, "calidad": 0, "calidad_txt": "",
        "fuente": None, "idioma": 0, "idioma_txt": "", "temporada": None,
        "etiquetas": [], "prefijo": "",
    }
    if not nombre:
        return info

    texto = RE_EXT.sub("", nombre.strip())

    # 1) contenido entre corchetes y parentesis -> metadatos
    for grupo in RE_PARENTESIS.findall(texto) + RE_CORCHETES.findall(texto):
        m = RE_ANIO.search(grupo)
        if m and not info["anio"]:
            candidato = int(m.group(1))
            if 1900 <= candidato <= ANIO_ACTUAL + 2:
                info["anio"] = candidato
        absorber_etiquetas(grupo, info)
    texto = RE_CORCHETES.sub(" ", texto)
    texto = RE_PARENTESIS.sub(" ", texto)

    # 2) numero suelto de coleccion al inicio ("01 - ", "03 - "), luego
    # prefijos del proveedor (hasta dos capas)
    texto = RE_NUMERO_COLECCION.sub("", texto)
    for _ in range(2):
        texto = RE_INICIO_SUCIO.sub("", texto)
        m = RE_PREFIJO.match(texto)
        if not m:
            break
        cabeza = normalizar(m.group(1))
        tokens = cabeza.split()
        if tokens and len(tokens) <= 3 and all(t in PREFIJOS for t in tokens):
            info["prefijo"] = (info["prefijo"] + " " + cabeza).strip()
            absorber_etiquetas(m.group(1), info)
            texto = texto[m.end():]
        else:
            break
    texto = RE_INICIO_SUCIO.sub("", texto)

    # 3) marcas de temporada / episodio (solo series)
    if es_serie:
        m = RE_SXXEYY.search(texto) or RE_TEMPORADA.search(texto)
        if m:
            digitos = re.search(r"\d{1,2}", m.group(0))
            if digitos:
                info["temporada"] = int(digitos.group(0))
        texto = RE_SXXEYY.sub(" ", texto)
        texto = RE_NXNN.sub(" ", texto)
        texto = RE_TEMPORADA.sub(" ", texto)
        texto = RE_EPISODIO.sub(" ", texto)

    # 4) bigramas tecnicos -> token unico (evita que sobrevivan pegados)
    for patron, reemplazo in RE_BIGRAMAS:
        texto = patron.sub(reemplazo, texto)

    texto = RE_SEPARADORES.sub(" ", texto)
    texto = texto.replace("–", "-").replace("—", "-")

    # 5) tokenizar y descartar etiquetas, extrayendo señal.
    # Las FUENTES_AMBIGUAS (STAR, MAX...) tambien son palabras reales de
    # titulos ("Star Wars", "Mad Max"), asi que aqui NO se descartan por
    # simple coincidencia: solo se marcan como candidatas y se resuelven
    # al final, cuando ya sabemos su posicion dentro del titulo limpio.
    crudos = re.split(r"[\s\-\.,;/\\]+", texto)
    limpios = []
    candidatos_ambiguos = []
    for token in crudos:
        if not token:
            continue
        norm = normalizar(token)
        if not norm:
            continue
        if norm in CALIDADES:
            rango, txt = CALIDADES[norm]
            if rango > info["calidad"]:
                info["calidad"], info["calidad_txt"] = rango, txt
            info["etiquetas"].append(norm)
            continue
        if norm in FUENTES:
            rango = FUENTES[norm]
            if info["fuente"] is None or rango < info["fuente"]:
                info["fuente"] = rango
            info["etiquetas"].append(norm)
            continue
        if norm in IDIOMAS:
            rango, txt = IDIOMAS[norm]
            if rango > info["idioma"]:
                info["idioma"], info["idioma_txt"] = rango, txt
            info["etiquetas"].append(norm)
            continue
        if norm in BASURA:
            info["etiquetas"].append(norm)
            continue
        if norm in FUENTES_AMBIGUAS:
            candidatos_ambiguos.append((len(limpios), norm))
        limpios.append(token)

    # Una ambigua solo se trata como etiqueta tecnica si quedo como la
    # ULTIMA palabra del titulo limpio (patron real de nombre de archivo:
    # "Titulo (2024) MAX"), nunca si esta al inicio o en medio (evita
    # romper "Star Wars", "Mad Max", etc).
    if candidatos_ambiguos and limpios:
        pos_ultimo, norm_ultimo = candidatos_ambiguos[-1]
        if pos_ultimo == len(limpios) - 1 and len(limpios) >= 2:
            rango = FUENTES_AMBIGUAS[norm_ultimo]
            if info["fuente"] is None or rango < info["fuente"]:
                info["fuente"] = rango
            info["etiquetas"].append(norm_ultimo)
            limpios.pop()

    # 6) año suelto al final (o en cualquier posicion residual)
    if limpios:
        ultimo = normalizar(limpios[-1])
        if re.fullmatch(r"(19|20)\d{2}", ultimo):
            valor = int(ultimo)
            if 1900 <= valor <= ANIO_ACTUAL + 2 and len(limpios) >= 2:
                if not info["anio"]:
                    info["anio"] = valor
                limpios.pop()

    if not info["anio"]:
        for token in list(limpios):
            norm = normalizar(token)
            if re.fullmatch(r"(19|20)\d{2}", norm):
                valor = int(norm)
                if 1900 <= valor <= ANIO_ACTUAL + 2 and len(limpios) >= 2:
                    info["anio"] = valor
                    limpios.remove(token)
                    break

    if info["fuente"] is None:
        info["fuente"] = 2
    if not info["calidad"]:
        info["calidad"] = 2
    if not info["idioma"]:
        info["idioma"] = 2

    info["buscar"] = " ".join(" ".join(limpios).split())
    return info

def titulo_recortado(titulo):
    """Version corta para el tercer intento: quita el subtitulo tras : o -"""
    for sep in (":", " - "):
        if sep in titulo:
            cabeza = titulo.split(sep)[0].strip()
            if len(cabeza.split()) >= 2:
                return cabeza
    tokens = titulo.split()
    if len(tokens) >= 4:
        return " ".join(tokens[:-1])
    return ""

# ============================================================================
# RED: proveedor (puente en Render + respaldo directo)
# ============================================================================

_local = threading.local()

def sesion():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "curador-vod/3.0",
        })
        _local.s = s
    return s

def url_xtream(accion, extra=""):
    return "%s/player_api.php?username=%s&password=%s&action=%s%s" % (
        XTREAM_URL, XTREAM_USER, XTREAM_PASS, accion, extra
    )

def puente_get(url_original, timeout=(15, 240), intentos=3):
    for i in range(intentos):
        try:
            r = sesion().get(
                PUENTE_URL, params={"url": url_original}, timeout=timeout
            )
            if r.status_code == 200:
                return r.json()
            print(" [aviso] el puente respondio HTTP %s (intento %s/%s)"
                  % (r.status_code, i + 1, intentos))
        except Exception as e:
            print(" [aviso] fallo el puente: %s (intento %s/%s)"
                  % (e, i + 1, intentos))
        if i < intentos - 1:
            time.sleep(3 * (i + 1) ** 2)
    return None

def directo_get(url_original, timeout=240):
    if cffi_requests is None:
        return None
    try:
        r = cffi_requests.get(url_original, impersonate="chrome", timeout=timeout)
        if r.status_code == 200:
            return r.json()
        print(" [aviso] conexion directa respondio HTTP %s" % r.status_code)
    except Exception as e:
        print(" [aviso] fallo la conexion directa: %s" % e)
    return None

def proveedor_get(url_original):
    datos = puente_get(url_original)
    if datos is None:
        print(" [aviso] el puente no respondio, intentando conexion directa...")
        datos = directo_get(url_original)
    if datos is None:
        return None
    if isinstance(datos, dict) and "user_info" in datos:
        return None
    if not isinstance(datos, list):
        print(" [aviso] la respuesta no es una lista (%s)" % type(datos).__name__)
        return None
    return datos

def descargar_catalogo(es_serie):
    """Descarga carpeta por carpeta. Devuelve (items, mapa_categorias, plano)."""
    accion_cat = "get_series_categories" if es_serie else "get_vod_categories"
    accion_lista = "get_series" if es_serie else "get_vod_streams"
    etiqueta = "series" if es_serie else "peliculas"

    print("-> Descargando carpetas de %s..." % etiqueta)
    categorias = proveedor_get(url_xtream(accion_cat))
    if categorias is None:
        print("[ERROR FATAL] no se pudo obtener el listado de carpetas de %s."
              % etiqueta)
        return None, None, False

    mapa = {}
    for cat in categorias:
        cid = str(cat.get("category_id") or "")
        if cid:
            mapa[cid] = cat.get("category_name") or ""
    print(" %s carpetas encontradas." % len(mapa))

    plano = len(mapa) <= 1
    if plano:
        print(" [AVISO] el proveedor tiene estructura plana (%s carpeta). "
              "El filtro por carpetas queda inactivo; se usaran las otras capas."
              % len(mapa))

    items = {}
    id_key = "series_id" if es_serie else "stream_id"
    modo_completo = plano
    fallidas = 0

    if not plano:
        visibles = [c for c in mapa if not categoria_prohibida(mapa[c])]
        bloqueadas = len(mapa) - len(visibles)
        if bloqueadas:
            print(" %s carpetas bloqueadas por el filtro de adultos."
                  % bloqueadas)
        print("-> Descargando %s carpeta por carpeta..." % etiqueta)
        for indice, cid in enumerate(visibles, 1):
            datos = proveedor_get(url_xtream(accion_lista, "&category_id=%s" % cid))
            if datos is None:
                fallidas += 1
                print(" [aviso] carpeta '%s' (%s) no se pudo descargar."
                      % (mapa[cid], cid))
                continue
            if len(datos) > UMBRAL_NO_FILTRA:
                print(" [aviso] el proveedor ignora el filtro por carpeta. "
                      "Cambiando a descarga completa.")
                modo_completo = True
                items = {}
                break
            for it in datos:
                sid = it.get(id_key)
                if sid is not None:
                    it.setdefault("category_id", cid)
                    items[str(sid)] = it
            if indice % 25 == 0:
                print(" %s/%s carpetas, %s items acumulados."
                      % (indice, len(visibles), len(items)))
            time.sleep(PAUSA_PROVEEDOR)

        if visibles and fallidas > max(2, len(visibles) * 0.2):
            print(" [aviso] fallaron %s de %s carpetas. "
                  "Cambiando a descarga completa." % (fallidas, len(visibles)))
            modo_completo = True
            items = {}

    if modo_completo or not items:
        print("-> Descargando %s en una sola peticion..." % etiqueta)
        datos = proveedor_get(url_xtream(accion_lista))
        if datos is None:
            print("[ERROR FATAL] no se pudo obtener el catalogo de %s." % etiqueta)
            return None, mapa, plano
        for it in datos:
            sid = it.get(id_key)
            if sid is not None:
                items[str(sid)] = it

    print(" %s items de %s listos." % (len(items), etiqueta))
    return list(items.values()), mapa, plano

# ============================================================================
# RED: TMDB (limitador, reintentos, escalera de intentos, puntuacion)
# ============================================================================

class Limitador:
    def __init__(self, rps):
        self.intervalo = 1.0 / max(rps, 1.0)
        self.lock = threading.Lock()
        self.siguiente = 0.0

    def esperar(self):
        with self.lock:
            ahora = time.monotonic()
            if self.siguiente <= ahora:
                self.siguiente = ahora + self.intervalo
                espera = 0.0
            else:
                espera = self.siguiente - ahora
                self.siguiente += self.intervalo
        if espera > 0:
            time.sleep(espera)

LIMITADOR = Limitador(TMDB_RPS)

def tmdb_get(ruta, params, intentos=4):
    """Devuelve dict, o None si hubo fallo de red (distinto de 'no encontrado')."""
    p = dict(params)
    p["api_key"] = TMDB_API_KEY
    for i in range(intentos):
        LIMITADOR.esperar()
        try:
            r = sesion().get(
                "https://api.themoviedb.org/3/%s" % ruta, params=p, timeout=(10, 25)
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                espera = float(r.headers.get("Retry-After", 2)) + 0.5
                time.sleep(min(espera, 15))
                continue
            if r.status_code == 404:
                return {}
            if 500 <= r.status_code < 600:
                time.sleep(1.5 * (i + 1))
                continue
            return {}
        except Exception:
            time.sleep(1.5 * (i + 1) + random.random())
    return None

def puntuar(candidato, consulta_norm, anio_consulta, es_serie):
    if candidato.get("adult"):
        return None
    if not candidato.get("poster_path"):
        return None

    if es_serie:
        nombres = [candidato.get("name"), candidato.get("original_name")]
        fecha = candidato.get("first_air_date") or ""
    else:
        nombres = [candidato.get("title"), candidato.get("original_title")]
        fecha = candidato.get("release_date") or ""

    mejor = 0.0
    for nombre in nombres:
        if not nombre:
            continue
        nn = normalizar(nombre)
        if not nn:
            continue
        if nn == consulta_norm:
            mejor = 1.0
            break
        seq = difflib.SequenceMatcher(None, consulta_norm, nn).ratio()
        tq = set(consulta_norm.split())
        tc = set(nn.split())
        cobertura = (len(tq & tc) / len(tc)) if tc else 0.0
        mejor = max(mejor, 0.55 * cobertura + 0.45 * seq)

    anio_cand = 0
    if len(fecha) >= 4 and fecha[:4].isdigit():
        anio_cand = int(fecha[:4])

    bono = 0.0
    if anio_consulta and anio_cand:
        delta = abs(anio_consulta - anio_cand)
        if delta == 0:
            bono = 0.10
        elif delta == 1:
            bono = 0.04
        elif delta == 2:
            bono = 0.0
        else:
            if mejor < 0.95:
                return None
            bono = -0.15

    pop = float(candidato.get("popularity") or 0.0)
    return {
        "score": mejor + bono + min(pop, 100.0) / 2000.0,
        "sim": mejor,
        "anio": anio_cand,
        "fecha": fecha,
        "candidato": candidato,
    }

def tmdb_buscar(titulo, es_serie, anio=None):
    ruta = "search/tv" if es_serie else "search/movie"
    params = {"query": titulo, "language": "es-ES", "include_adult": "false"}
    if anio:
        params["first_air_date_year" if es_serie else "year"] = anio
    datos = tmdb_get(ruta, params)
    if datos is None:
        return None
    return datos.get("results") or []

def mejor_de(resultados, consulta_norm, anio, es_serie, umbral):
    mejor = None
    for candidato in (resultados or [])[:12]:
        p = puntuar(candidato, consulta_norm, anio, es_serie)
        if p and (mejor is None or p["score"] > mejor["score"]):
            mejor = p
    if mejor and mejor["score"] >= umbral:
        return mejor
    return None

def empaquetar(elegido, es_serie):
    c = elegido["candidato"]
    generos = [g for g in (c.get("genre_ids") or []) if isinstance(g, int)]
    paises = c.get("origin_country") or []
    return {
        "ok": True,
        "tmdb": int(c.get("id") or 0),
        "titulo": (c.get("name") if es_serie else c.get("title")) or "",
        "poster": c.get("poster_path") or "",
        "generos": generos,
        "fecha": elegido["fecha"] or "",
        "nota": round(float(c.get("vote_average") or 0.0), 1),
        "votos": int(c.get("vote_count") or 0),
        "pop": round(float(c.get("popularity") or 0.0), 2),
        "lang": c.get("original_language") or "",
        "paises": paises if isinstance(paises, list) else [],
        "sinopsis": (c.get("overview") or "").strip(),
        "ts": int(time.time()),
    }

def tmdb_buscar_multilenguaje(titulo, es_serie, anio):
    """Cuarto intento: TMDB indexa cada ficha en un idioma principal, y a
    veces el titulo en espanol que tiene el proveedor no coincide con el
    'title' que TMDB devuelve en es-ES (varia por doblaje/region), pero SI
    coincide con el titulo original o el ingles. Se prueba en esos otros
    idiomas antes de rendirse, sin bajar el umbral de confianza normal."""
    ruta = "search/tv" if es_serie else "search/movie"
    resultados = []
    for idioma in ("en-US", "es-MX"):
        params = {"query": titulo, "language": idioma, "include_adult": "false"}
        if anio:
            params["first_air_date_year" if es_serie else "year"] = anio
        datos = tmdb_get(ruta, params)
        if datos is None:
            continue
        resultados.extend(datos.get("results") or [])
    return resultados or None


def tmdb_titulos_alternativos(tmdb_id, es_serie):
    """Trae la lista de titulos alternativos/traducciones que TMDB tiene
    guardados para una ficha puntual (endpoint 'alternative_titles' o
    'translations'). Sirve para el quinto intento: cuando el nombre que
    trae el proveedor no coincide con el titulo principal en ningun idioma
    de busqueda, puede coincidir con una traduccion regional (ej: LATAM)
    que TMDB SI tiene guardada pero que 'search/movie' no usa para indexar
    texto. Devuelve una lista plana de nombres (str), o [] si no hay nada
    o hubo fallo de red (nunca None: aqui un fallo de red no debe frenar
    la escalera completa, solo se pierde esta oportunidad puntual)."""
    tipo = "tv" if es_serie else "movie"
    nombres = []

    datos = tmdb_get("%s/%s/alternative_titles" % (tipo, tmdb_id), {})
    if datos:
        clave = "results" if es_serie else "titles"
        for t in datos.get(clave) or []:
            nombre = t.get("title")
            if nombre:
                nombres.append(nombre)

    datos = tmdb_get("%s/%s/translations" % (tipo, tmdb_id), {})
    if datos:
        for t in datos.get("translations") or []:
            data = t.get("data") or {}
            nombre = data.get("title") or data.get("name")
            if nombre:
                nombres.append(nombre)

    return nombres


def buscar_por_titulos_alternativos(titulo, candidatos, consulta_norm,
                                     anio, es_serie, umbral):
    """Quinto intento: revisa, de los candidatos ya traidos por los
    intentos anteriores (no dispara una busqueda nueva), si el nombre del
    proveedor calza con alguno de los titulos alternativos/traducciones
    regionales que TMDB tiene guardados para esa ficha. Solo se revisan
    los primeros candidatos mas relevantes, para no disparar peticiones
    de mas por cada titulo sin resolver."""
    for candidato in (candidatos or [])[:TOPE_CANDIDATOS_ALTERNATIVOS]:
        tmdb_id = candidato.get("id")
        if not tmdb_id:
            continue
        nombres_alt = tmdb_titulos_alternativos(tmdb_id, es_serie)
        if not nombres_alt:
            continue
        mejor_score = 0.0
        for nombre_alt in nombres_alt:
            nn = normalizar(nombre_alt)
            if not nn:
                continue
            if nn == consulta_norm:
                mejor_score = 1.0
                break
            seq = difflib.SequenceMatcher(None, consulta_norm, nn).ratio()
            tq, tc = set(consulta_norm.split()), set(nn.split())
            cobertura = (len(tq & tc) / len(tc)) if tc else 0.0
            mejor_score = max(mejor_score, 0.55 * cobertura + 0.45 * seq)
        if mejor_score >= umbral:
            p = puntuar(candidato, consulta_norm, anio, es_serie)
            if p is None:
                # el candidato no pasa el filtro basico (adulto/sin poster),
                # se respeta esa regla igual que en cualquier otro intento.
                continue
            p["sim"] = mejor_score
            return p
    return None


def resolver_titulo(titulo, anio, es_serie):
    """Escalera de intentos. Devuelve (datos|None, motivo)."""
    consulta_norm = normalizar(titulo)
    if not consulta_norm:
        return None, "titulo_vacio"

    hubo_fallo_red = False
    candidatos_vistos = []

    if anio:
        res = tmdb_buscar(titulo, es_serie, anio)
        if res is None:
            hubo_fallo_red = True
        else:
            candidatos_vistos.extend(res)
            elegido = mejor_de(res, consulta_norm, anio, es_serie, UMBRAL_NORMAL)
            if elegido:
                return empaquetar(elegido, es_serie), "ok"

    res = tmdb_buscar(titulo, es_serie, None)
    if res is None:
        hubo_fallo_red = True
    else:
        candidatos_vistos.extend(res)
        elegido = mejor_de(res, consulta_norm, anio, es_serie, UMBRAL_NORMAL)
        if elegido:
            return empaquetar(elegido, es_serie), "ok"

    corto = titulo_recortado(titulo)
    if corto:
        res = tmdb_buscar(corto, es_serie, None)
        if res is None:
            hubo_fallo_red = True
        else:
            candidatos_vistos.extend(res)
            elegido = mejor_de(
                res, normalizar(corto), anio, es_serie, UMBRAL_RIESGO
            )
            if elegido:
                return empaquetar(elegido, es_serie), "ok"

    # Cuarto intento: mismo titulo original, buscando en otros idiomas de
    # indexacion de TMDB. Recupera casos donde el titulo en espanol del
    # proveedor es correcto, pero TMDB solo lo tiene calzado bajo su
    # nombre original o su variante en-US/es-MX.
    res = tmdb_buscar_multilenguaje(titulo, es_serie, anio)
    if res is None:
        hubo_fallo_red = True
    elif res:
        candidatos_vistos.extend(res)
        elegido = mejor_de(res, consulta_norm, anio, es_serie, UMBRAL_RIESGO)
        if elegido:
            return empaquetar(elegido, es_serie), "ok"

    # Quinto intento (nuevo, acordado 09-ago-2026): titulos alternativos.
    # Cubre el caso de franquicias como "007" o "Depredador", donde el
    # titulo en espanol del proveedor no es una traduccion literal del
    # titulo principal de TMDB, pero SI existe como titulo alternativo o
    # traduccion regional guardada en la propia ficha de TMDB. No hace
    # busqueda de texto nueva: reutiliza los candidatos ya vistos en los
    # intentos anteriores, para no multiplicar peticiones de mas.
    ids_vistos = set()
    candidatos_unicos = []
    for c in candidatos_vistos:
        cid = c.get("id")
        if cid and cid not in ids_vistos:
            ids_vistos.add(cid)
            candidatos_unicos.append(c)

    if candidatos_unicos:
        elegido = buscar_por_titulos_alternativos(
            titulo, candidatos_unicos, consulta_norm, anio, es_serie,
            UMBRAL_RIESGO,
        )
        if elegido:
            return empaquetar(elegido, es_serie), "ok"

    if hubo_fallo_red:
        return None, "fallo_red"
    return None, "no_encontrado"

def tmdb_por_id(tmdb_id, es_serie):
    """Para las equivalencias manuales del archivo de correcciones."""
    ruta = "tv/%s" % tmdb_id if es_serie else "movie/%s" % tmdb_id
    datos = tmdb_get(ruta, {"language": "es-ES"})
    if datos is None:
        return None, "fallo_red"
    if not datos or not datos.get("id"):
        return None, "id_inexistente"
    if not datos.get("poster_path"):
        return None, "sin_poster"
    generos = [g.get("id") for g in (datos.get("genres") or []) if g.get("id")]
    fecha = (datos.get("first_air_date") if es_serie
             else datos.get("release_date")) or ""
    paises = datos.get("origin_country") or []
    return {
        "ok": True,
        "tmdb": int(datos["id"]),
        "titulo": (datos.get("name") if es_serie else datos.get("title")) or "",
        "poster": datos.get("poster_path") or "",
        "generos": generos,
        "fecha": fecha,
        "nota": round(float(datos.get("vote_average") or 0.0), 1),
        "votos": int(datos.get("vote_count") or 0),
        "pop": round(float(datos.get("popularity") or 0.0), 2),
        "lang": datos.get("original_language") or "",
        "paises": paises if isinstance(paises, list) else [],
        "sinopsis": (datos.get("overview") or "").strip(),
        "ts": int(time.time()),
    }, "ok"

def detalles_serie(tmdb_id):
    datos = tmdb_get("tv/%s" % tmdb_id, {"language": "es-ES"})
    if datos is None:
        return None
    if not datos:
        return {"ok": False, "ts": int(time.time())}
    return {
        "ok": True,
        "estado": datos.get("status") or "",
        "primera": datos.get("first_air_date") or "",
        "ultima": datos.get("last_air_date") or "",
        "temporadas": int(datos.get("number_of_seasons") or 0),
        "ts": int(time.time()),
    }

# ============================================================================
# MEMORIA PERSISTENTE
# ============================================================================

def leer_json(ruta, por_defecto):
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return por_defecto

def escribir_json(ruta, datos, compacto=False):
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compacto:
            json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta)

def cargar_cache():
    cache = leer_json(ARCHIVO_CACHE, None)
    if not isinstance(cache, dict) or cache.get("version") != CACHE_VERSION:
        cache = {"version": CACHE_VERSION, "movie": {}, "tv": {}, "detalles_tv": {}}
    for k in ("movie", "tv", "detalles_tv"):
        cache.setdefault(k, {})
    return cache

CACHE_LOCK = threading.Lock()

def guardar_cache(cache):
    with CACHE_LOCK:
        escribir_json(ARCHIVO_CACHE, cache, compacto=True)

def cache_vigente(entrada):
    if not isinstance(entrada, dict):
        return False
    edad = (time.time() - float(entrada.get("ts") or 0)) / 86400.0
    if not entrada.get("ok"):
        return edad < REFRESCO_FALLIDO
    anio = 0
    fecha = entrada.get("fecha") or ""
    if len(fecha) >= 4 and fecha[:4].isdigit():
        anio = int(fecha[:4])
    limite = REFRESCO_RECIENTE if anio >= ANIO_ACTUAL - 2 else REFRESCO_ANTIGUO
    return edad < limite

def detalles_vigentes(entrada):
    if not isinstance(entrada, dict):
        return False
    edad = (time.time() - float(entrada.get("ts") or 0)) / 86400.0
    estado = (entrada.get("estado") or "").lower()
    if estado in ("ended", "canceled", "cancelled"):
        return edad < REFRESCO_SERIE_TERMINADA
    return edad < REFRESCO_SERIE_ACTIVA

def presupuesto_agotado():
    return (time.monotonic() - INICIO) / 60.0 >= PRESUPUESTO_MIN

# ============================================================================
# CORRECCIONES MANUALES (100% opcional, valvula de escape, no obligatorio)
# ============================================================================

AYUDA_CORRECCIONES = [
    "OPCIONAL. El curador funciona perfecto sin que edites este archivo nunca.",
    "Sirve solo para forzar un caso puntual sin esperar a que TMDB lo resuelva.",
    "equivalencias: fuerza la identificacion. tipo es 'movie' o 'tv'.",
    "  {\"tipo\": \"movie\", \"nombre\": \"Gran Turismo De jugador a corredor\", \"tmdb_id\": 442249}",
    "exclusiones: nunca entra al catalogo. Por nombre o por id del proveedor.",
    "  {\"tipo\": \"movie\", \"nombre\": \"Evento UFC 300\"} | {\"tipo\": \"movie\", \"id\": 173558}",
    "forzados: manda el item a una fila concreta, ademas de la que le toque.",
    "  {\"tipo\": \"movie\", \"nombre\": \"El callejon de los milagros\", \"fila\": \"Retro\"}",
    "El campo 'nombre' se compara sin acentos, sin mayusculas y sin signos,",
    "asi que no tienes que copiarlo con precision milimetrica.",
]

def cargar_correcciones():
    datos = leer_json(ARCHIVO_CORRECCIONES, None)
    if not isinstance(datos, dict):
        datos = {"_ayuda": AYUDA_CORRECCIONES, "equivalencias": [],
                  "exclusiones": [], "forzados": []}
        escribir_json(ARCHIVO_CORRECCIONES, datos)
        print("-> Creado %s vacio (opcional, editalo a mano solo si quieres)."
              % ARCHIVO_CORRECCIONES)
    datos["_ayuda"] = AYUDA_CORRECCIONES
    for k in ("equivalencias", "exclusiones", "forzados"):
        if not isinstance(datos.get(k), list):
            datos[k] = []

    indice = {
        "equivalencias": {"movie": {}, "tv": {}},
        "exclusiones_nombre": {"movie": set(), "tv": set()},
        "exclusiones_id": {"movie": set(), "tv": set()},
        "forzados": {"movie": {}, "tv": {}},
    }
    for e in datos["equivalencias"]:
        tipo = e.get("tipo") or "movie"
        if tipo in indice["equivalencias"] and e.get("nombre") and e.get("tmdb_id"):
            indice["equivalencias"][tipo][normalizar(e["nombre"])] = int(e["tmdb_id"])
    for e in datos["exclusiones"]:
        tipo = e.get("tipo") or "movie"
        if tipo not in indice["exclusiones_nombre"]:
            continue
        if e.get("nombre"):
            indice["exclusiones_nombre"][tipo].add(normalizar(e["nombre"]))
        if e.get("id") is not None:
            indice["exclusiones_id"][tipo].add(str(e["id"]))
    for e in datos["forzados"]:
        tipo = e.get("tipo") or "movie"
        if tipo in indice["forzados"] and e.get("nombre") and e.get("fila"):
            indice["forzados"][tipo][normalizar(e["nombre"])] = e["fila"]

    total = (len(datos["equivalencias"]) + len(datos["exclusiones"])
             + len(datos["forzados"]))
    if total:
        print("-> %s correcciones manuales cargadas." % total)
    escribir_json(ARCHIVO_CORRECCIONES, datos)
    return indice

# ============================================================================
# FASE 1 - CLASIFICAR TODO EL CATALOGO
# Un titulo solo se descarta aqui por: adulto, o exclusion manual.
# Si no tiene nombre legible tras la limpieza, se conserva igual con el
# nombre crudo del proveedor (nunca desaparece en silencio).
# ============================================================================

def fase1(items, es_serie, mapa_cat, plano, cache, corr, informe):
    tipo = "tv" if es_serie else "movie"
    etiqueta = "series" if es_serie else "peliculas"
    id_key = "series_id" if es_serie else "stream_id"
    fecha_key = "last_modified" if es_serie else "added"

    print("\n=== FASE 1: clasificando %s %s ===" % (len(items), etiqueta))

    contadores = {
        "adulto": 0, "excluido": 0, "sin_nombre": 0, "no_encontrado": 0,
        "fallo_red": 0, "clasificados": 0, "sin_identificar": 0,
    }

    grupos = {}  # clave de busqueda -> {"anio", "titulo", "copias":[...]}
    forzados_por_clave = {}

    for item in items:
        sid = item.get(id_key)
        if sid is None:
            continue
        sid = str(sid)
        nombre = (item.get("name") or "").strip()
        if not nombre:
            contadores["sin_nombre"] += 1
            continue

        nombre_norm = normalizar(nombre)

        if sid in corr["exclusiones_id"][tipo] or \
           nombre_norm in corr["exclusiones_nombre"][tipo]:
            contadores["excluido"] += 1
            continue

        cat_nombre = mapa_cat.get(str(item.get("category_id") or ""), "")
        if (not plano and categoria_prohibida(cat_nombre)) or titulo_prohibido(nombre):
            contadores["adulto"] += 1
            continue

        info = limpiar_titulo(nombre, es_serie)
        # Nombre para buscar en TMDB puede quedar vacio en casos raros
        # (titulo compuesto solo de etiquetas tecnicas). El item NO se pierde:
        # usa el nombre crudo como clave y como nombre para mostrar.
        titulo_busqueda = info["buscar"] or nombre

        k = clave(titulo_busqueda, info["anio"])
        grupo = grupos.get(k)
        if grupo is None:
            grupo = {"anio": info["anio"], "titulo": titulo_busqueda, "copias": []}
            grupos[k] = grupo

        marca = 0
        try:
            marca = int(item.get(fecha_key) or 0)
        except (TypeError, ValueError):
            marca = 0

        grupo["copias"].append({
            "id": sid, "nombre": nombre, "nombre_crudo": nombre,
            "carpeta": cat_nombre,
            "calidad": info["calidad"], "calidad_txt": info["calidad_txt"],
            "fuente": info["fuente"], "idioma": info["idioma"],
            "idioma_txt": info["idioma_txt"], "temporada": info["temporada"],
            "marca": marca, "etiquetas": info["etiquetas"],
            "prefijo": info["prefijo"],
        })

        fila_forzada = (corr["forzados"][tipo].get(nombre_norm)
                        or corr["forzados"][tipo].get(normalizar(titulo_busqueda)))
        if fila_forzada:
            forzados_por_clave[k] = fila_forzada

    print(" %s titulos distintos tras agrupar (de %s items)."
          % (len(grupos), len(items)))
    print(" descartados de entrada -> adulto: %s | excluidos a mano: %s | "
          "sin nombre: %s"
          % (contadores["adulto"], contadores["excluido"], contadores["sin_nombre"]))

    # --- resolver contra TMDB ------------------------------------------------
    pendientes = []
    equivalencias = corr["equivalencias"][tipo]
    for k, grupo in grupos.items():
        forzado_id = equivalencias.get(normalizar(grupo["titulo"]))
        if not forzado_id:
            for copia in grupo["copias"]:
                forzado_id = equivalencias.get(normalizar(copia["nombre"]))
                if forzado_id:
                    break
        grupo["forzado_id"] = forzado_id
        entrada = cache[tipo].get(k)
        if forzado_id:
            if not (entrada and entrada.get("ok")
                    and entrada.get("tmdb") == forzado_id and cache_vigente(entrada)):
                pendientes.append(k)
        elif not cache_vigente(entrada):
            pendientes.append(k)

    print(" %s titulos ya estaban en memoria, %s por resolver."
          % (len(grupos) - len(pendientes), len(pendientes)))

    if pendientes:
        resueltos = 0
        omitidos_presupuesto = 0

        def trabajo(k):
            grupo = grupos[k]
            if grupo.get("forzado_id"):
                datos, motivo = tmdb_por_id(grupo["forzado_id"], es_serie)
            else:
                datos, motivo = resolver_titulo(
                    grupo["titulo"], grupo["anio"], es_serie
                )
            return k, datos, motivo

        with ThreadPoolExecutor(max_workers=HILOS_TMDB) as pool:
            futuros = {pool.submit(trabajo, k): k for k in pendientes}
            for futuro in as_completed(futuros):
                if presupuesto_agotado():
                    omitidos_presupuesto += 1
                    futuro.cancel()
                    continue
                try:
                    k, datos, motivo = futuro.result()
                except Exception as e:
                    print(" [aviso] error resolviendo: %s" % e)
                    continue
                if datos:
                    cache[tipo][k] = datos
                elif motivo != "fallo_red":
                    cache[tipo][k] = {"ok": False, "motivo": motivo,
                                       "ts": int(time.time())}
                resueltos += 1
                if resueltos % GUARDAR_CADA == 0:
                    guardar_cache(cache)
                    print(" ... %s/%s resueltos (%.1f min transcurridos)"
                          % (resueltos, len(pendientes),
                             (time.monotonic() - INICIO) / 60.0))

        guardar_cache(cache)
        if omitidos_presupuesto:
            print(" [AVISO] presupuesto de %s min agotado. %s titulos quedan "
                  "para la proxima corrida (el avance ya esta guardado)."
                  % (PRESUPUESTO_MIN, omitidos_presupuesto))

    # --- detalles de series (fecha de ultima actividad) ---------------------
    if es_serie:
        ids_series = set()
        for k, grupo in grupos.items():
            entrada = cache[tipo].get(k)
            if entrada and entrada.get("ok") and entrada.get("tmdb"):
                ids_series.add(int(entrada["tmdb"]))
        faltan = [i for i in ids_series
                  if not detalles_vigentes(cache["detalles_tv"].get(str(i)))]
        if faltan and not presupuesto_agotado():
            print(" Pidiendo ficha completa de %s series (ultima actividad)..."
                  % len(faltan))
            hechos = 0
            with ThreadPoolExecutor(max_workers=HILOS_TMDB) as pool:
                futuros = {pool.submit(detalles_serie, i): i for i in faltan}
                for futuro in as_completed(futuros):
                    if presupuesto_agotado():
                        futuro.cancel()
                        continue
                    tmdb_id = futuros[futuro]
                    try:
                        det = futuro.result()
                    except Exception:
                        det = None
                    if det:
                        cache["detalles_tv"][str(tmdb_id)] = det
                    hechos += 1
                    if hechos % GUARDAR_CADA == 0:
                        guardar_cache(cache)
            guardar_cache(cache)

    # --- construir clasificados y sin_identificar ----------------------------
    clasificados = []
    sin_identificar = []

    for k, grupo in grupos.items():
        entrada = cache[tipo].get(k)
        motivo = None
        if not entrada:
            contadores["fallo_red"] += 1
            motivo = "pendiente"
        elif not entrada.get("ok"):
            motivo = entrada.get("motivo") or "no_encontrado"
            contadores["no_encontrado" if motivo == "no_encontrado"
                        else "fallo_red"] += 1

        if motivo:
            # NO se pierde: entra al nivel "sin_identificar" del catalogo,
            # visible para el buscador con su nombre limpio. Va tambien
            # al informe para que el usuario sepa por que no tiene ficha.
            mejor_copia, _ = elegir_mejor_copia(grupo["copias"])
            sin_identificar.append({
                "id": int(mejor_copia["id"]) if str(mejor_copia["id"]).isdigit()
                      else mejor_copia["id"],
                "titulo": grupo["titulo"] or mejor_copia["nombre_crudo"],
                "anio": grupo["anio"] or None,
                "alt": [
                    int(c["id"]) for c in grupo["copias"]
                    if c is not mejor_copia and str(c["id"]).isdigit()
                ][:5],
            })
            contadores["sin_identificar"] += 1

            ejemplo = grupo["copias"][0]
            informe.append({
                "id": ejemplo["id"], "tipo": tipo,
                "nombre_original": ejemplo["nombre"],
                "titulo_buscado": grupo["titulo"], "anio": grupo["anio"],
                "motivo": motivo, "carpeta": ejemplo["carpeta"],
                "etiquetas": ejemplo["etiquetas"], "prefijo": ejemplo["prefijo"],
                "copias": len(grupo["copias"]),
            })
            continue

        clasificados.append({
            "clave": k,
            "tmdb": int(entrada["tmdb"]),
            "titulo": entrada["titulo"] or grupo["titulo"],
            "poster": entrada["poster"],
            "generos": entrada.get("generos") or [],
            "fecha": entrada.get("fecha") or "",
            "nota": entrada.get("nota") or 0.0,
            "votos": entrada.get("votos") or 0,
            "pop": entrada.get("pop") or 0.0,
            "lang": entrada.get("lang") or "",
            "paises": entrada.get("paises") or [],
            "sinopsis": entrada.get("sinopsis") or "",
            "copias": grupo["copias"],
            "fila_forzada": forzados_por_clave.get(k),
            "detalles": cache["detalles_tv"].get(str(entrada["tmdb"])) if es_serie else None,
        })
        contadores["clasificados"] += 1

    print(" clasificados con ficha TMDB: %s | sin identificar (van al buscador): %s"
          % (contadores["clasificados"], contadores["sin_identificar"]))
    return clasificados, sin_identificar, contadores

# ============================================================================
# FASE 2 - ARMAR LA VITRINA
# Aqui se decide en que filas aparece cada titulo IDENTIFICADO.
# Los "sin_identificar" nunca pasan por aqui: no tienen genero ni año reales.
# ============================================================================

def anio_de(fecha):
    if fecha and len(fecha) >= 4 and fecha[:4].isdigit():
        return int(fecha[:4])
    return 0

def fecha_obj(fecha):
    try:
        return datetime.strptime(fecha[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

def elegir_mejor_copia(copias):
    """Escalera de preferencia: resolucion > origen > idioma > mas reciente.
    Nunca descarta contenido: la peor copia se guarda como respaldo (alt),
    y si es la UNICA copia que existe, se acepta igual sin importar su calidad."""
    ordenadas = sorted(
        copias,
        key=lambda c: (c["calidad"], c["fuente"], c["idioma"], c["marca"]),
        reverse=True,
    )
    return ordenadas[0], ordenadas[1:]

def elegir_generos(clasificado, es_serie):
    """Devuelve (fila_principal, fila_secundaria|None)."""
    diccionario = GEN_SERIES if es_serie else GEN_PELICULAS
    prioridad = PRIORIDAD_SERIES if es_serie else PRIORIDAD_PELICULAS
    fuertes = FUERTES_SERIES if es_serie else FUERTES_PELICULAS
    generos = clasificado["generos"]

    es_anime = (
        16 in generos
        and (clasificado["lang"] == "ja" or "JP" in (clasificado["paises"] or []))
    )

    ordenados = [g for g in prioridad if g in generos]
    if not ordenados:
        return (FILA_ANIME if es_anime else None), None

    principal = FILA_ANIME if es_anime else diccionario.get(ordenados[0])
    secundaria = None
    for g in ordenados:
        fila = diccionario.get(g)
        if fila and fila != principal and g in fuertes:
            secundaria = fila
            break
    if es_anime and not secundaria:
        secundaria = diccionario.get(ordenados[0])
    return principal, secundaria

def fase2(clasificados, es_serie, total_origen):
    etiqueta = "series" if es_serie else "peliculas"
    print("\n=== FASE 2: armando la vitrina de %s ===" % etiqueta)

    # --- 1) agrupar duplicados por id de TMDB -------------------------------
    por_tmdb = {}
    for c in clasificados:
        actual = por_tmdb.get(c["tmdb"])
        if actual is None:
            por_tmdb[c["tmdb"]] = c
        else:
            actual["copias"].extend(c["copias"])
            if c["fila_forzada"] and not actual["fila_forzada"]:
                actual["fila_forzada"] = c["fila_forzada"]

    duplicados_fusionados = len(clasificados) - len(por_tmdb)
    print(" %s titulos unicos (%s agrupaciones por duplicado)."
          % (len(por_tmdb), duplicados_fusionados))

    # --- 2) construir registros y asignar filas -----------------------------
    registros = []
    sin_genero = []
    copias_descartadas = 0

    for c in por_tmdb.values():
        mejor, resto = elegir_mejor_copia(c["copias"])
        copias_descartadas += len(resto)

        anio_origen = anio_de(c["fecha"])
        anio_display = anio_origen
        fecha_actividad = c["fecha"]

        detalles = c.get("detalles") or {}
        if es_serie and detalles.get("ok"):
            if detalles.get("primera"):
                anio_origen = anio_de(detalles["primera"]) or anio_origen
            if detalles.get("ultima"):
                fecha_actividad = detalles["ultima"]
                anio_display = anio_de(detalles["ultima"]) or anio_display

        principal, secundaria = elegir_generos(c, es_serie)

        registro = {
            "tmdb": c["tmdb"],
            "titulo": c["titulo"],
            "poster": c["poster"],
            "anio": anio_display or anio_origen,
            "anio_origen": anio_origen,
            "nota": c["nota"],
            "votos": c["votos"],
            "pop": c["pop"],
            "sinopsis": c["sinopsis"],
            "fecha_orden": fecha_actividad or c["fecha"],
            "id": int(mejor["id"]) if str(mejor["id"]).isdigit() else mejor["id"],
            "calidad": mejor["calidad_txt"],
            "idioma": mejor["idioma_txt"],
            "alt": [int(x["id"]) for x in resto[:3] if str(x["id"]).isdigit()],
            "filas": set(),
            "es_clasico": False,
        }

        # Sin genero utilizable: TMDB no trae genero para esta ficha (pasa
        # con contenido internacional/especiales). Ya NO se deja fuera de
        # toda fila: se le asigna la fila "Descubrir" para que el titulo
        # (que ya tiene poster/sinopsis/nota reales) siga siendo visible
        # en vitrina en vez de vivir solo en el buscador.
        if not principal:
            registro["filas"].add(FILA_SIN_GENERO)
            sin_genero.append(registro)
            registros.append(registro)
            continue

        filas = registro["filas"]
        filas.add(principal)
        if secundaria:
            filas.add(secundaria)

        # Epoca: NO exclusiva, ventana rodante
        if anio_origen and anio_origen <= ANIO_ANTIGUO:
            if c["nota"] >= CLASICO_NOTA_MIN and c["votos"] >= CLASICO_VOTOS_MIN:
                filas.add("Clasicos")
                registro["es_clasico"] = True
            else:
                filas.add("Retro")

        # Estrenos
        fobj = fecha_obj(fecha_actividad)
        if fobj:
            if es_serie:
                if LIMITE_ESTRENO_SERIE <= fobj <= LIMITE_FUTURO:
                    marca = mejor["marca"]
                    fresco_proveedor = True
                    if marca:
                        edad = (time.time() - marca) / 86400.0
                        fresco_proveedor = edad <= DIAS_PROVEEDOR_ESTRENO
                    if fresco_proveedor:
                        filas.add("Estrenos")
            else:
                if LIMITE_ESTRENO_PELI <= fobj <= LIMITE_FUTURO:
                    filas.add("Estrenos")

        if c["fila_forzada"]:
            filas.add(c["fila_forzada"])

        registros.append(registro)

    if sin_genero:
        print(" %s titulos sin genero utilizable: quedan en el catalogo y en "
              "el buscador, pero no aparecen en ninguna fila de vitrina."
              % len(sin_genero))
    if copias_descartadas:
        print(" %s copias duplicadas de menor calidad conservadas como "
              "respaldo (nunca se destruyen)." % copias_descartadas)

    # --- 3) fusionar filas flacas (solo afecta la VITRINA, no el catalogo) --
    def contar():
        cuenta = {}
        for r in registros:
            for f in r["filas"]:
                cuenta[f] = cuenta.get(f, 0) + 1
        return cuenta

    fusiones_hechas = []
    for _ in range(6):
        cuenta = contar()
        flacas = [
            f for f, n in cuenta.items()
            if n < PISO_FILA and f in FUSION and FUSION[f] != f
        ]
        if not flacas:
            break
        for flaca in flacas:
            destino = FUSION[flaca]
            movidos = 0
            for r in registros:
                if flaca in r["filas"]:
                    r["filas"].discard(flaca)
                    r["filas"].add(destino)
                    movidos += 1
            if movidos:
                fusiones_hechas.append("%s -> %s (%s)" % (flaca, destino, movidos))

    for linea in fusiones_hechas:
        print(" fusion: %s" % linea)

    cuenta = contar()
    descartadas = [f for f, n in cuenta.items() if n < PISO_FILA]
    for f in descartadas:
        for r in registros:
            r["filas"].discard(f)
        print(" fila '%s' eliminada de la vitrina: solo %s items (piso %s). "
              "Los titulos siguen en el catalogo completo y el buscador."
              % (f, cuenta[f], PISO_FILA))

    # --- 4) ordenar y recortar SOLO las filas de vitrina ---------------------
    def orden_estrenos(r):
        return (r["fecha_orden"] or "", r["pop"])

    def orden_clasicos(r):
        return (r["nota"], r["votos"])

    def orden_retro(r):
        return (r["pop"], r["nota"])

    def orden_genero(r):
        return (r["pop"], r["nota"])

    ordenadores = {
        "Estrenos": orden_estrenos,
        "Clasicos": orden_clasicos,
        "Retro": orden_retro,
    }

    por_fila = {}
    for r in registros:
        for f in r["filas"]:
            por_fila.setdefault(f, []).append(r)

    # --- items_planos: TODO lo identificado entra aqui, sin tope. -----------
    # Las filas de vitrina solo guardan referencias (posiciones) a esta lista,
    # y su tope de presentacion NO recorta lo que hay en items_planos.
    items_planos = []
    usados = {}

    def indice_de(r):
        idx = usados.get(r["tmdb"])
        if idx is not None:
            return idx
        item = {
            "id": r["id"],
            "titulo": r["titulo"],
            "year": r["anio"],
            "poster": r["poster"],
            "tmdb": r["tmdb"],
            "nota": r["nota"],
            "sinopsis": r["sinopsis"],
        }
        if r["anio_origen"] and r["anio_origen"] != r["anio"]:
            item["year_origen"] = r["anio_origen"]
        if r["calidad"]:
            item["calidad"] = r["calidad"]
        if r["idioma"]:
            item["idioma"] = r["idioma"]
        if r["alt"]:
            item["alt"] = r["alt"]
        idx = len(items_planos)
        items_planos.append(item)
        usados[r["tmdb"]] = idx
        return idx

    # Aseguramos que TODO registro identificado (tenga o no fila de vitrina)
    # quede en items_planos, para que el buscador lo encuentre siempre.
    for r in registros:
        indice_de(r)
    for r in sin_genero:
        indice_de(r)

    nombres_ordenados = [f for f in ORDEN_CATEGORIAS if f in por_fila]
    nombres_ordenados += sorted(f for f in por_fila if f not in ORDEN_CATEGORIAS)

    # --- Variedad diaria de vitrina (acordado 09-ago-2026) ------------------
    # "Estrenos" siempre debe ir estrictamente del mas nuevo al mas viejo:
    # no rota. El resto de filas (generos, Clasicos, Retro) SI rotan: cuando
    # hay mas candidatos que el tope de la fila, se reserva una porcion fija
    # ("anclas", los mejores por relevancia) para que la fila nunca pierda
    # calidad de piso, y el resto de espacios se llena con una muestra
    # aleatoria del resto del pool. La semilla se deriva de la fecha del dia
    # (no de la hora), asi que la fila es estable durante todo el dia pero
    # cambia de una corrida diaria a la siguiente, mezclando titulos de
    # distintas epocas en vez de mostrar siempre el mismo top fijo.
    FILAS_SIN_ROTACION = {"Estrenos"}
    PORCENTAJE_ANCLAS = 0.4
    SEMILLA_DIA = HOY.strftime("%Y-%m-%d")

    def seleccionar_con_variedad(nombre, lista, tope):
        if nombre in FILAS_SIN_ROTACION or len(lista) <= tope:
            return lista[:tope]
        cantidad_anclas = max(1, int(round(tope * PORCENTAJE_ANCLAS)))
        cantidad_anclas = min(cantidad_anclas, tope)
        anclas = lista[:cantidad_anclas]
        resto_pool = lista[cantidad_anclas:]
        cupo_rotacion = tope - len(anclas)
        rng = random.Random("%s|%s" % (SEMILLA_DIA, nombre))
        elegidos_rotacion = rng.sample(
            resto_pool, min(cupo_rotacion, len(resto_pool))
        )
        return anclas + elegidos_rotacion

    filas_finales = []
    for nombre in nombres_ordenados:
        lista = por_fila[nombre]
        lista.sort(key=ordenadores.get(nombre, orden_genero), reverse=True)
        tope = TOPES.get(nombre, TOPE_GENERO)
        recortada = seleccionar_con_variedad(nombre, lista, tope)
        referencias = [indice_de(r) for r in recortada]
        filas_finales.append({
            "nombre": nombre,
            "tipo": TIPOS_FILA.get(nombre, "genero"),
            "items": referencias,
        })
        rota = " (con rotacion diaria)" if (
            nombre not in FILAS_SIN_ROTACION and len(lista) > tope
        ) else ""
        print(" %-26s %3s items en vitrina (de %s identificados con este genero)%s"
              % (nombre, len(referencias), len(lista), rota))

    salida = {
        "origen": total_origen,
        "titulos_identificados": len(items_planos),
        "items": items_planos,
        "filas": filas_finales,
    }
    print(" -> %s filas de vitrina | %s titulos identificados totales en el catalogo."
          % (len(filas_finales), len(items_planos)))
    return salida

# ============================================================================
# INFORME DE NO RESUELTOS (diagnostico, ordenado por frecuencia del patron)
# ============================================================================

def calcular_alerta(contadores_peli, contadores_serie):
    """Porcentaje de titulos sin ficha TMDB sobre el total de cada bloque,
    y si supera el umbral configurado. El workflow lee esto para decidir
    si deja una alerta visible, sin bloquear la publicacion del catalogo."""
    def porcentaje(contadores):
        total = contadores.get("clasificados", 0) + contadores.get("sin_identificar", 0)
        if not total:
            return 0.0
        return round(100.0 * contadores.get("sin_identificar", 0) / total, 1)

    pct_peli = porcentaje(contadores_peli)
    pct_serie = porcentaje(contadores_serie)
    return {
        "porcentaje_peliculas": pct_peli,
        "porcentaje_series": pct_serie,
        "umbral": UMBRAL_ALERTA_NO_IDENTIFICADO,
        "supera_umbral": (
            pct_peli > UMBRAL_ALERTA_NO_IDENTIFICADO
            or pct_serie > UMBRAL_ALERTA_NO_IDENTIFICADO
        ),
    }


def escribir_informe(informe, contadores_peli, contadores_serie):
    patrones = {}
    etiquetas = {}
    carpetas = {}
    motivos = {}

    for reg in informe:
        motivos[reg["motivo"]] = motivos.get(reg["motivo"], 0) + 1

        pref = (reg.get("prefijo") or "").strip()
        if not pref:
            nombre = reg.get("nombre_original") or ""
            m = re.match(r"^\s*([^\-|:]{1,20})\s*[\-|:]\s+", nombre)
            pref = normalizar(m.group(1)) if m else "(sin prefijo)"
        clave_patron = pref or "(sin prefijo)"
        entrada = patrones.setdefault(
            clave_patron, {"patron": clave_patron, "veces": 0, "ejemplos": []}
        )
        entrada["veces"] += 1
        if len(entrada["ejemplos"]) < 5:
            entrada["ejemplos"].append(reg.get("nombre_original"))

        for tag in reg.get("etiquetas") or []:
            etiquetas[tag] = etiquetas.get(tag, 0) + 1
        carpeta = reg.get("carpeta") or "(sin carpeta)"
        carpetas[carpeta] = carpetas.get(carpeta, 0) + 1

    lista_patrones = sorted(patrones.values(), key=lambda x: -x["veces"])
    top_etiquetas = sorted(etiquetas.items(), key=lambda x: -x[1])[:25]
    top_carpetas = sorted(carpetas.items(), key=lambda x: -x[1])[:25]

    datos = {
        "_ayuda": [
            "Diagnostico. Estos titulos NO se descartaron: viven en el catalogo",
            "dentro de 'sin_identificar' y son buscables, solo no tienen ficha",
            "de TMDB (sin poster/genero real) y por eso no aparecen en vitrina.",
            "Revisa 'patrones' de arriba a abajo: arreglar los primeros",
            "recupera muchos items de golpe hacia el catalogo identificado.",
            "Si quieres forzar un caso, agregalo a correcciones.json (opcional).",
            "motivo 'no_encontrado' = TMDB no lo tiene o el nombre sigue sucio.",
            "motivo 'fallo_red' o 'pendiente' = se reintenta solo en la proxima corrida.",
        ],
        "generado": HOY.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "resumen": {
            "total": len(informe),
            "por_motivo": motivos,
            "peliculas": contadores_peli,
            "series": contadores_serie,
            "alerta": calcular_alerta(contadores_peli, contadores_serie),
        },
        "patrones": lista_patrones[:80],
        "etiquetas_mas_frecuentes": [
            {"etiqueta": k, "veces": v} for k, v in top_etiquetas
        ],
        "carpetas_mas_frecuentes": [
            {"carpeta": k, "veces": v} for k, v in top_carpetas
        ],
        "items": informe[:800],
    }
    escribir_json(ARCHIVO_INFORME, datos)
    print("\n-> Informe escrito en %s (%s casos, %s patrones)."
          % (ARCHIVO_INFORME, len(informe), len(lista_patrones)))
    if lista_patrones[:5]:
        print(" patrones mas repetidos:")
        for p in lista_patrones[:5]:
            print("  %-22s %s veces" % (p["patron"][:22], p["veces"]))

def volcar_carpetas(mapa_peli, mapa_serie):
    def resumir(mapa):
        return sorted(
            [{"id": k, "nombre": v, "bloqueada": categoria_prohibida(v)}
             for k, v in (mapa or {}).items()],
            key=lambda x: x["nombre"],
        )
    return {"peliculas": resumir(mapa_peli), "series": resumir(mapa_serie)}

# ============================================================================
# VALIDACION ANTES DE PUBLICAR
# El freno mira el TOTAL de titulos en el catalogo (identificados +
# sin_identificar), no solo lo que entra a la vitrina, para detectar de
# verdad una caida real de contenido y no un simple reordenamiento de filas.
# ============================================================================

def total_catalogo(bloque):
    return len(bloque.get("items") or []) + len(bloque.get("sin_identificar") or [])

def validar(nuevo):
    problemas = []
    for tipo in ("movies", "series"):
        bloque = nuevo.get(tipo) or {}
        filas = bloque.get("filas") or []
        if tipo == "movies" and len(filas) < MINIMO_FILAS:
            problemas.append(
                "peliculas solo tiene %s filas de vitrina (minimo %s)"
                % (len(filas), MINIMO_FILAS)
            )
        if tipo == "movies" and total_catalogo(bloque) == 0:
            problemas.append("peliculas quedo con el catalogo completo vacio")

    anterior = leer_json(ARCHIVO_CATALOGO, None)
    if isinstance(anterior, dict) and anterior.get("schema") == SCHEMA:
        for tipo in ("movies", "series"):
            ant = total_catalogo(anterior.get(tipo) or {})
            nue = total_catalogo(nuevo.get(tipo) or {})
            if ant >= 20 and nue < ant * TOLERANCIA_CAIDA:
                problemas.append(
                    "%s cayo de %s a %s titulos en el catalogo (tolerancia %.0f%%)"
                    % (tipo, ant, nue, TOLERANCIA_CAIDA * 100)
                )
    return problemas

# ============================================================================
# MAIN
# ============================================================================

def main():
    faltantes = [n for n, v in (
        ("TMDB_API_KEY", TMDB_API_KEY), ("XTREAM_URL", XTREAM_URL),
        ("XTREAM_USER", XTREAM_USER), ("XTREAM_PASS", XTREAM_PASS),
    ) if not v]
    if faltantes:
        print("[ERROR FATAL] faltan secretos: %s" % ", ".join(faltantes))
        return 1

    print("Curador VOD - %s" % HOY.strftime("%Y-%m-%d %H:%M UTC"))
    print("Topes de VITRINA (no del catalogo): genero %s | estrenos %s | epoca %s | piso %s"
          % (TOPE_GENERO, TOPE_ESTRENOS, TOPE_EPOCA, PISO_FILA))
    print("Antiguo = %s o anterior | presupuesto %s min | %s hilos"
          % (ANIO_ANTIGUO, PRESUPUESTO_MIN, HILOS_TMDB))

    cache = cargar_cache()
    corr = cargar_correcciones()
    informe = []

    peliculas, mapa_peli, plano_peli = descargar_catalogo(False)
    if peliculas is None:
        print("[ERROR FATAL] sin catalogo de peliculas no se publica nada. "
              "Se conserva el catalogo anterior.")
        guardar_cache(cache)
        return 2

    series, mapa_serie, plano_serie = descargar_catalogo(True)
    if series is None:
        print("[AVISO] no se pudo descargar series. Se sigue solo con peliculas.")
        series, mapa_serie, plano_serie = [], {}, False

    clas_peli, sin_id_peli, cont_peli = fase1(
        peliculas, False, mapa_peli, plano_peli, cache, corr, informe
    )
    clas_serie, sin_id_serie, cont_serie = fase1(
        series, True, mapa_serie, plano_serie, cache, corr, informe
    )
    guardar_cache(cache)

    bloque_peli = fase2(clas_peli, False, len(peliculas))
    bloque_serie = fase2(clas_serie, True, len(series))

    bloque_peli["sin_identificar"] = sin_id_peli
    bloque_serie["sin_identificar"] = sin_id_serie

    nuevo = {
        "schema": SCHEMA,
        "generado": HOY.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "poster_base": POSTER_BASE,
        "poster_size": POSTER_SIZE,
        "topes": {"genero": TOPE_GENERO, "estrenos": TOPE_ESTRENOS,
                  "epoca": TOPE_EPOCA, "piso": PISO_FILA},
        "movies": bloque_peli,
        "series": bloque_serie,
        "carpetas_vistas": volcar_carpetas(mapa_peli, mapa_serie),
    }

    escribir_informe(informe, cont_peli, cont_serie)

    problemas = validar(nuevo)
    if problemas:
        print("\n[VALIDACION FALLIDA] no se publica el catalogo nuevo:")
        for p in problemas:
            print(" - %s" % p)
        print("Se conserva %s tal como estaba. La memoria y el informe SI se "
              "guardaron, asi que el avance no se pierde." % ARCHIVO_CATALOGO)
        return 2

    escribir_json(ARCHIVO_CATALOGO, nuevo)
    tam = os.path.getsize(ARCHIVO_CATALOGO) / 1024.0
    total_peli = total_catalogo(bloque_peli)
    total_serie = total_catalogo(bloque_serie)
    print("\n=== LISTO ===")
    print("Peliculas: %s filas de vitrina, %s en catalogo completo (%s con ficha + %s sin identificar)"
          % (len(bloque_peli["filas"]), total_peli,
             len(bloque_peli["items"]), len(sin_id_peli)))
    print("Series: %s filas de vitrina, %s en catalogo completo (%s con ficha + %s sin identificar)"
          % (len(bloque_serie["filas"]), total_serie,
             len(bloque_serie["items"]), len(sin_id_serie)))
    print("%s escrito (%.0f KB) en %.1f minutos."
          % (ARCHIVO_CATALOGO, tam, (time.monotonic() - INICIO) / 60.0))
    return 0

if __name__ == "__main__":
    sys.exit(main())
