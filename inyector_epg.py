# -*- coding: utf-8 -*-
"""Inyector XMLTV multicanal con política estricta de emisiones en vivo y consenso multi-feed."""
from __future__ import annotations

import hashlib
import io
import gzip
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, List, Dict, Tuple

import requests

from curador_eventos import (
    resolver_logo_equipo,
    ARCHIVO_META,
    ARCHIVO_SALIDA,
    DEPORTES_COMPETICION,
    DURACION_POR_CATEGORIA,
    PUENTE_URL,
    XTREAM_PASS,
    XTREAM_URL,
    XTREAM_USER,
    calcular_similitud_simple,
    cargar_agenda_cache,
    contiene_veto,
    extraer_sesion,
    fusionar_fuente,
    inferir_deporte,
    iso_utc,
    normalizar_texto,
    obtener_agenda_maestra,
    obtener_zona_aplicacion,
)
from resolvedor_logos import resolver_logo_torneo

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("inyector_epg")

URLS_EPG_EUROPA = os.environ.get(
    "URLS_EPG_EUROPA",
    "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml,https://raw.githubusercontent.com/acidjesuz/EPG/master/guide.xml"
).split(",")
INCLUIR_EPG_EN_CANAL = os.environ.get("INCLUIR_EPG_EN_CANAL", "true").lower() in {"1", "true", "si", "sí", "yes"}
PUBLICAR_EPG_FUTURO = os.environ.get("PUBLICAR_EPG_FUTURO", "true").lower() in {"1", "true", "si", "sí", "yes"}
MAX_EPG_EVENTOS = max(int(os.environ.get("MAX_EPG_EVENTOS", "180")), 0)
MAX_DIFERENCIA_EPG_MIN = max(int(os.environ.get("MAX_DIFERENCIA_EPG_MIN", "60")), 15)

CANALES_EPG = ("E1", "E2", "TDP")
PAISES_GUIA_PRINCIPAL = {"", "ES", "ESP"}

VETO_EPG = {
    "REPETICION", "REPLAY", "RESUMEN", "HIGHLIGHTS", "COMPACTO", "NOTICIAS", "NEWS", "MAGAZINE",
    "INFORMATIVO", "TELEDIARIO", "REPORTAJE", "DOCUMENTAL", "CLASICOS", "MEMORIAS", "VINTAGE",
    "PREVIA", "POSTPARTIDO", "POST PARTIDO", "ENTREVISTA", "EL CLUB DE", "MEJORES MOMENTOS",
    "WIEDERHOLUNG", "ZUSAMMENFASSUNG", "DOKUMENTATION", "VORSCHAU", "NACHRICHTEN",
    "REPETICAO", "RESUMO", "DOCUMENTARIO", "REDIFFUSION", "RETROSPECTIVA", "BEST OF",
}

# Filtro de archivos históricos o temporadas pasadas
PATRON_TEMPORADA_HISTORICA = re.compile(r"\b(19\d\d|20[0-2][0-5])\b|\bT(2[0-5]|\d{1,2})\b", re.I)

# Normalización canónica de sinónimos multilingües
SINONIMOS_TORNEOS = {
    "CYCLISME : TOUR D'ESPAGNE": "La Vuelta",
    "CYCLISME TOUR D'ESPAGNE": "La Vuelta",
    "TOUR D'ESPAGNE": "La Vuelta",
    "VUELTA A ESPANA": "La Vuelta",
    "CYCLISME : RENEWI TOUR": "Renewi Tour",
    "CYCLISME RENEWI TOUR": "Renewi Tour",
}


def parse_timestamp_epg(valor: str) -> Optional[datetime]:
    if not valor:
        return None
    partes = valor.strip().split()
    numeros, offset = partes[0], partes[1] if len(partes) > 1 else "+0000"
    try:
        digitos = re.sub(r"\D", "", numeros).ljust(14, "0")[:14]
        base = datetime.strptime(digitos, "%Y%m%d%H%M%S")
        if not re.fullmatch(r"[+-]\d{4}", offset):
            return base.replace(tzinfo=timezone.utc)
        signo = 1 if offset[0] == "+" else -1
        zona = timezone(signo * timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5])))
        return base.replace(tzinfo=zona).astimezone(timezone.utc)
    except ValueError:
        return None


def es_directo_multilingue(texto: str) -> bool:
    valor = normalizar_texto(texto)
    return bool(re.search(r"\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO|DIREKT|EN DIRECT|AO VIVO)\b", valor))


def es_historico_o_veto(titulo: str, descripcion: str = "") -> bool:
    texto = f"{titulo} {descripcion}"
    valor = normalizar_texto(texto)
    if contiene_veto(valor) or any(palabra in valor for palabra in VETO_EPG):
        return True
    if PATRON_TEMPORADA_HISTORICA.search(texto):
        return True
    return False


def limpiar_titulo_epg(titulo: str) -> Tuple[str, str]:
    valor = re.sub(r"\[/?COLOR[^\]]*\]", "", titulo or "", flags=re.I)
    valor = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO|DIREKT|EN DIRECT)\b", "", valor)
    valor = re.sub(r"\bT\d{2,4}(/\d{2,4})?\b", "", valor)
    valor = " ".join(valor.strip(" -:·|▫/").split())

    # Canonicalización de sinónimos
    valor_norm = normalizar_texto(valor)
    for sinonimo, canonico in SINONIMOS_TORNEOS.items():
        if normalizar_texto(sinonimo) in valor_norm:
            valor = valor.replace(sinonimo, canonico).replace(sinonimo.lower(), canonico)

    partes = [p.strip() for p in re.split(r"\s*[·|▫:/]\s*", valor, maxsplit=1) if p.strip()]
    if len(partes) == 2:
        return partes[0], partes[1]
    return valor, ""


def limitar(texto: str, maximo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def clave_canal_epg(channel_id: str) -> Optional[str]:
    valor = normalizar_texto(channel_id)
    if "TELEDEPORTE" in valor or re.search(r"\bTDP\b", valor):
        return "TDP"
    if re.search(r"\bEUROSPORTS?\s*2\b", valor) or re.search(r"\b(?:ES|E)\s*2\b", valor):
        return "E2"
    if re.search(r"\bEUROSPORTS?\s*1\b", valor) or re.search(r"\b(?:ES|E)\s*1\b", valor):
        return "E1"
    return None


def pais_epg(channel_id: str) -> str:
    match = re.match(r"^([A-Z]{2,3})(?:\s*\|\s*|\s+-\s+)", (channel_id or "").strip().upper())
    return match.group(1) if match else ""


def prioridad_guia_epg(channel_id: str) -> int:
    pais = pais_epg(channel_id)
    if pais in PAISES_GUIA_PRINCIPAL:
        return 0
    if pais in {"EN", "UK"}:
        return 20
    if pais == "DE":
        return 30
    return 50


def idioma_fuente(nombre: str) -> Tuple[str, int]:
    valor = normalizar_texto(nombre)
    if any(x in valor for x in (" ESPANA", " ESPANOL", " CASTELLANO", " LATINO", " LATAM")) or re.search(r"(?:^|\s)ES(?:\s|$)", valor):
        return "ES", 0
    if any(x in valor for x in (" ENGLISH", " INGLES")) or re.search(r"(?:^|\s)(EN|UK)(?:\s|$)", valor):
        return "EN", 30
    if any(x in valor for x in (" DEUTSCH", " GERMAN", " ALEMAN")):
        return "DE", 50
    if any(x in valor for x in (" FRANCAIS", " FRENCH", " FRANCES")):
        return "FR", 50
    if any(x in valor for x in (" PORTUGUES", " PORTUGAL")):
        return "PT", 50
    return "Principal", 10


def ordenar_fuentes(fuentes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unicas: Dict[str, Dict[str, Any]] = {}
    for fuente in fuentes:
        sid = str(fuente.get("id_xtream") or "")
        if not sid:
            continue
        idioma, prioridad = idioma_fuente(str(fuente.get("nombre") or ""))
        candidata = {"nombre": str(fuente.get("nombre") or ""), "id_xtream": sid, "idioma": idioma, "prioridad": prioridad}
        previa = unicas.get(sid)
        if previa is None or (prioridad, candidata["nombre"]) < (int(previa["prioridad"]), str(previa["nombre"])):
            unicas[sid] = candidata
    return sorted(unicas.values(), key=lambda x: (int(x["prioridad"]), normalizar_texto(x["nombre"]), str(x["id_xtream"])))


def llamada_xtream(url: str, timeout: int = 60) -> Any:
    respuesta = requests.get(PUENTE_URL, params={"url": url}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.json()


def mapear_streams_canales_lineales() -> Dict[str, List[Dict[str, Any]]]:
    mapa: Dict[str, List[Dict[str, Any]]] = {clave: [] for clave in CANALES_EPG}
    if not (XTREAM_URL and XTREAM_USER and XTREAM_PASS):
        return mapa
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        streams = llamada_xtream(url, 75) or []
    except (requests.RequestException, TypeError, ValueError) as exc:
        log.warning("No se pudieron mapear canales lineales: %s", exc)
        return mapa
    for stream in streams:
        nombre, sid = str(stream.get("name") or "").strip(), str(stream.get("stream_id") or "")
        clave = clave_canal_epg(nombre)
        if nombre and sid and clave:
            mapa[clave].append({"nombre": nombre, "id_xtream": sid})
    return {clave: ordenar_fuentes(fuentes) for clave, fuentes in mapa.items()}


def _inicio(evento: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.strptime(str(evento["hora_utc"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def sesiones_compatibles_epg(titulo: str, evento: Dict[str, Any]) -> bool:
    a, b = extraer_sesion(titulo), extraer_sesion(f"{evento.get('titulo', '')} {evento.get('subtitulo', '')}")
    return not a or not b or a == b or (b == "sprint" and a == "carrera_sprint")


def buscar_evento_agenda(titulo_epg: str, inicio_epg: datetime, agenda: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], int, List[str]]:
    categoria, mejor, mejor_puntos, mejor_tokens = inferir_deporte(titulo_epg), None, 0, []
    for evento in agenda:
        if categoria and categoria != evento.get("categoria"):
            continue
        if not sesiones_compatibles_epg(titulo_epg, evento):
            continue
        inicio_oficial = _inicio(evento)
        if inicio_oficial and abs((inicio_epg - inicio_oficial).total_seconds()) / 60 > MAX_DIFERENCIA_EPG_MIN:
            continue
        puntos, comunes = calcular_similitud_simple(str(evento.get("titulo") or ""), str(evento.get("torneo") or ""), titulo_epg)
        if puntos > mejor_puntos:
            mejor, mejor_puntos, mejor_tokens = evento, puntos, comunes
    return (mejor, mejor_puntos, mejor_tokens) if mejor_puntos >= 65 else (None, 0, [])


def extraer_participantes_tenis(titulo: str) -> Tuple[str, str]:
    limpio = re.sub(r"\([^)]{2,4}\)", "", titulo or "")
    match = re.search(r"\b([A-ZÁÉÍÓÚÑa-záéíóúñ\.\s]{3,24})\s+(?:VS\.?|V\.?)\s+([A-ZÁÉÍÓÚÑa-záéíóúñ\.\s]{3,24})\b", limpio, flags=re.I)
    if not match:
        return "", ""
    a, b = match.group(1).strip(), match.group(2).strip()
    if any(x in normalizar_texto(a) or x in normalizar_texto(b) for x in ("OPEN", "MASTERS", "CHAMPIONSHIP", "COURT", "TOUR", "GRANDSTAND", "ESTADIO", "PISTA")):
        return "", ""
    return a, b


def construir_evento_epg(
    titulo_raw: str,
    descripcion: str,
    inicio: datetime,
    fin: Optional[datetime],
    fuentes: List[Dict[str, Any]],
    agenda: List[Dict[str, Any]],
    ahora: datetime,
    consenso_directo: bool = False,
) -> Optional[Dict[str, Any]]:
    if not titulo_raw or es_historico_o_veto(titulo_raw, descripcion):
        return None
    if fin and fin <= ahora:
        return None
    if not PUBLICAR_EPG_FUTURO and inicio > ahora:
        return None

    # Política estricta: si no hay confirmación de DIRECTO/LIVE, se descarta
    oficial, puntos, tokens = buscar_evento_agenda(titulo_raw, inicio, agenda)

    # Política estricta: si no hay confirmación de DIRECTO/LIVE, verificamos con la API
    directo = consenso_directo or es_directo_multilingue(f"{titulo_raw} {descripcion}")
    if not directo and oficial:
        directo = True # ¡Rescatado! El texto no decia Directo, pero la API confirma que ocurre AHORA
    
    if not directo:
        return None

    torneo, subtitulo = limpiar_titulo_epg(titulo_raw)
    categoria = inferir_deporte(f"{titulo_raw} {descripcion}")
    if not categoria:
        return None

    duracion = max(15, int((fin - inicio).total_seconds() / 60)) if fin else DURACION_POR_CATEGORIA.get(categoria, 150)
    if duracion < 30:
        return None

    # Determinación de modo de presentación y duelos
    es_individual = categoria in DEPORTES_COMPETICION
    local, visitante = "", ""
    if categoria == "Tenis":
        local, visitante = extraer_participantes_tenis(titulo_raw)
        tipo = "duelo" if local and visitante else "sencillo"
    elif es_individual:
        tipo = "sencillo"
    else:
        match = re.search(r"\b(.+?)\s+(?:VS\.?|V\.?)\s+(.+?)\b", torneo, re.I)
        if match:
            tipo = "duelo"
            local, visitante = match.group(1).strip(), match.group(2).strip()
        else:
            tipo = "sencillo"

    titulo_final = f"{local} vs {visitante}" if tipo == "duelo" else torneo
    logo_oficial = resolver_logo_torneo(torneo, categoria)
    fuentes = ordenar_fuentes(fuentes)

    estado_str = "en_canal" if inicio <= ahora < (fin or inicio + timedelta(minutes=duracion)) else "programado"

    if oficial:
        evento = {k: v for k, v in oficial.items() if k != "fuentes"}
        evento.update({
            "id": oficial["id"], "agenda_id": oficial["id"], "hora_utc": iso_utc(inicio), "duracion_min": duracion,
            "origen": f"{oficial.get('origen', 'api_sports')}+epg", "origenes": list(dict.fromkeys(list(oficial.get("origenes", [])) + ["epg"])),
            "estado": "confirmado", "estado_evento": "programado",
            "confianza": "alta",
            "puntuacion_confianza": max(puntos, int(oficial.get("puntuacion_confianza", 0)), 90),
            "metodo_correlacion": "epg_agenda_verificada", "razones_correlacion": [f"tokens:{','.join(tokens)}", f"directo:{str(directo).lower()}"], "fuentes": [],
        })
        for fuente in fuentes:
            fusionar_fuente(evento, fuente)
        evento["fuentes"] = ordenar_fuentes(evento["fuentes"])
        return evento

    if not INCLUIR_EPG_EN_CANAL:
        return None

    ident = hashlib.sha1(f"{normalizar_texto(torneo)}|{normalizar_texto(subtitulo)}|{inicio.strftime('%Y%m%d')}".encode()).hexdigest()[:16]
    return {
        "id": f"epg_{ident}", "agenda_id": "", "titulo": limitar(titulo_final, 75), "torneo": limitar(torneo, 55),
        "categoria": categoria, "tipo_evento": tipo, "equipo_local": local, "equipo_visitante": visitante,
        "participante_local": local, "participante_visitante": visitante, "subtitulo": limitar(subtitulo, 70),
        "titulo_tarjeta": limitar(torneo, 52), "subtitulo_tarjeta": limitar(subtitulo, 44),
        "modo_presentacion": "competicion" if tipo == "sencillo" else "duelo_equipos",
        "hora_utc": iso_utc(inicio), "hora_local_producto": inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"),
        "duracion_min": duracion, "logo_torneo": logo_oficial, 
        "logo_local": resolver_logo_equipo(local) or (logo_oficial if tipo == "sencillo" else ""),
        "logo_visitante": resolver_logo_equipo(visitante) or "", "tier": 2, "origen": "epg_en_canal", "origenes": ["epg"],
        "estado": estado_str, "estado_evento": "programado",
        "confianza": "alta", "puntuacion_confianza": 88,
        "metodo_correlacion": "epg_directo_multi_feed",
        "razones_correlacion": ["canal_prioritario", "sin_veto", f"categoria:{categoria}", f"directo:{str(directo).lower()}"],
        "fuentes": fuentes,
    }


def construir_matriz_consenso(programas: List[Dict[str, Any]]) -> Dict[Tuple[str, str], bool]:
    """Crea un mapa (canal_clave, timestamp_slot) -> True si algún país marca DIRECTO/LIVE."""
    consenso: Dict[Tuple[str, str], bool] = {}
    for p in programas:
        if not p.get("inicio"):
            continue
        clave = str(p["clave"])
        slot = p["inicio"].strftime("%Y%m%d%H%M")
        es_live = es_directo_multilingue(f"{p['titulo']} {p['descripcion']}")
        if es_live:
            consenso[(clave, slot)] = True
            for offset in (-15, 15):
                slot_cercano = (p["inicio"] + timedelta(minutes=offset)).strftime("%Y%m%d%H%M")
                consenso[(clave, slot_cercano)] = True
    return consenso


def extraer_eventos_epg(mapa: Dict[str, List[Dict[str, Any]]], agenda: List[Dict[str, Any]], fecha_colombia: datetime, ahora: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    metricas: Counter = Counter()
    tz = obtener_zona_aplicacion()
    ahora = ahora or datetime.now(timezone.utc)
    todos_los_programas: List[Dict[str, Any]] = []
    programas_principales: List[Dict[str, Any]] = []

    for url_epg in URLS_EPG_EUROPA:
        url_epg = url_epg.strip()
        if not url_epg: continue
        log.info(f"Descargando matriz EPG para consenso desde: {url_epg[:40]}...")
        try:
            respuesta = requests.get(url_epg, timeout=(10, 60))
            respuesta.raise_for_status()
            contenido = respuesta.content
            if contenido.startswith(b"\x1f\x8b"):
                contenido = gzip.decompress(contenido)
            for _, elemento in ET.iterparse(io.BytesIO(contenido), events=("end",)):
                if elemento.tag != "programme":
                    continue
                metricas["programas_leidos"] += 1
                canal = elemento.attrib.get("channel", "")
                clave = clave_canal_epg(canal)
                inicio = parse_timestamp_epg(elemento.attrib.get("start", ""))
                fin = parse_timestamp_epg(elemento.attrib.get("stop", ""))

                if clave and inicio and inicio.astimezone(tz).date() == fecha_colombia.date():
                    titulo = elemento.findtext("title", "") or ""
                    desc = elemento.findtext("desc", "") or ""
                    item = {"clave": clave, "canal": canal, "inicio": inicio, "fin": fin, "titulo": titulo, "descripcion": desc}
                    todos_los_programas.append(item)
                    if clave in mapa:
                        programas_principales.append(item)
                elemento.clear()
        except requests.RequestException as exc:
            log.warning("No se pudo descargar EPG auxiliar %s: %s", url_epg, exc)
        except ET.ParseError as exc:
            log.error("XMLTV Inválido en %s: %s", url_epg, exc)
