# -*- coding: utf-8 -*-
"""Inyector XMLTV para Eurosport y Teledeporte.

El XMLTV puede estar expresado en horario europeo, pero todo programa se compara
como instante absoluto y solo se publica si su inicio cae hoy en Colombia.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from curador_eventos import (
    APP_TIMEZONE,
    ARCHIVO_CACHE,
    ARCHIVO_META,
    ARCHIVO_SALIDA,
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
    tokenizar,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("inyector_epg")

URL_EPG_EUROPA = os.environ.get("URL_EPG_EUROPA", "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml")
INCLUIR_EPG_NO_VERIFICADO = os.environ.get("INCLUIR_EPG_NO_VERIFICADO", "true").lower() in {"1", "true", "si", "sí", "yes"}
MAX_EPG_EVENTOS = max(int(os.environ.get("MAX_EPG_EVENTOS", "150")), 0)
MAX_DIFERENCIA_EPG_MIN = max(int(os.environ.get("MAX_DIFERENCIA_EPG_MIN", "210")), 30)

CANALES_EPG = {
    "EUROSPORT 1 HD": "E1", "EUROSPORT 1": "E1", "EUROSPORT 2 HD": "E2", "EUROSPORT 2": "E2",
    "TELEDEPORTE": "TDP", "TELEDEPORTE HD": "TDP", "TDP": "TDP",
}
VETO_EPG = {
    "REPETICION", "REPLAY", "RESUMEN", "HIGHLIGHTS", "COMPACTO", "NOTICIAS", "NEWS", "MAGAZINE",
    "INFORMATIVO", "TELEDIARIO", "REPORTAJE", "DOCUMENTAL", "CLASICOS", "MEMORIAS", "VINTAGE",
    "PREVIA", "POSTPARTIDO", "POST PARTIDO", "ENTREVISTA", "EL CLUB DE",
}


def parse_timestamp_epg(valor: str) -> Optional[datetime]:
    """Admite XMLTV con offset, que es obligatorio para convertir Europa→Colombia."""
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


def es_directo(titulo: str, descripcion: str = "") -> bool:
    return bool(re.search(r"\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", normalizar_texto(f"{titulo} {descripcion}")))


def es_veto_epg(titulo: str, descripcion: str = "") -> bool:
    valor = normalizar_texto(f"{titulo} {descripcion}")
    return contiene_veto(valor) or any(palabra in valor for palabra in VETO_EPG)


def limpiar_titulo_epg(titulo: str) -> tuple[str, str]:
    valor = re.sub(r"\[/?COLOR[^\]]*\]", "", titulo or "", flags=re.I)
    valor = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", "", valor)
    valor = " ".join(valor.strip(" -:·| ").split())
    partes = [p.strip() for p in re.split(r"\s*[·|]\s*", valor, maxsplit=1) if p.strip()]
    if len(partes) == 2:
        return partes[0], partes[1]
    return valor, ""


def limitar(texto: str, maximo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def llamada_xtream(url: str, timeout: int = 60) -> Any:
    respuesta = requests.get(PUENTE_URL, params={"url": url}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.json()


def clave_canal_epg(channel_id: str) -> Optional[str]:
    valor = normalizar_texto(channel_id)
    for nombre, clave in CANALES_EPG.items():
        if valor == normalizar_texto(nombre):
            return clave
    return None


def mapear_streams_canales_lineales() -> dict[str, list[dict[str, str]]]:
    mapa: dict[str, list[dict[str, str]]] = {"E1": [], "E2": [], "TDP": []}
    if not (XTREAM_URL and XTREAM_USER and XTREAM_PASS):
        return mapa
    url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        streams = llamada_xtream(url, 75) or []
    except (requests.RequestException, TypeError, ValueError) as exc:
        log.warning("No se pudieron mapear canales lineales: %s", exc)
        return mapa
    vistos: set[tuple[str, str]] = set()
    for stream in streams:
        nombre, sid = str(stream.get("name") or "").strip(), str(stream.get("stream_id") or "")
        if not nombre or not sid:
            continue
        valor = normalizar_texto(nombre)
        clave = "TDP" if ("TELEDEPORTE" in valor or re.search(r"\bTDP\b", valor)) else None
        if not clave and any(x in valor for x in ("EUROSPORT 2", "EUROSPORTS 2", "EUROSPORT2", " ES2")):
            clave = "E2"
        if not clave and any(x in valor for x in ("EUROSPORT 1", "EUROSPORTS 1", "EUROSPORT1", " ES1")):
            clave = "E1"
        if clave and (clave, sid) not in vistos:
            mapa[clave].append({"nombre": nombre, "id_xtream": sid})
            vistos.add((clave, sid))
    return mapa


def _inicio(evento: dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.strptime(evento["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def sesiones_compatibles_epg(titulo: str, evento: dict[str, Any]) -> bool:
    a, b = extraer_sesion(titulo), extraer_sesion(f"{evento.get('titulo', '')} {evento.get('subtitulo', '')}")
    return not a or not b or a == b or (b == "sprint" and a == "carrera_sprint")


def buscar_evento_agenda(titulo_epg: str, inicio_epg: datetime, agenda: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], int, list[str]]:
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
        evento_norm, epg_norm = normalizar_texto(evento.get("titulo") or ""), normalizar_texto(titulo_epg)
        frase = bool(evento_norm and (evento_norm in epg_norm or epg_norm in evento_norm))
        if len(comunes) == 1 and not frase and (len(comunes[0]) < 8 or comunes[0] in {"HOCKEY", "TENNIS", "CYCLING", "GOLF", "FUTBOL"}):
            continue
        if puntos > mejor_puntos:
            mejor, mejor_puntos, mejor_tokens = evento, puntos, comunes
    return (mejor, mejor_puntos, mejor_tokens) if mejor_puntos >= 65 else (None, 0, [])


def construir_evento_epg(titulo_raw: str, descripcion: str, inicio: datetime, fin: Optional[datetime], fuentes: list[dict[str, str]], agenda: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not titulo_raw or es_veto_epg(titulo_raw, descripcion):
        return None
    oficial, puntos, tokens = buscar_evento_agenda(titulo_raw, inicio, agenda)
    torneo, subtitulo = limpiar_titulo_epg(titulo_raw)
    categoria = inferir_deporte(f"{titulo_raw} {descripcion}") or "Deportes"
    directo = es_directo(titulo_raw, descripcion)
    duracion = max(15, int((fin - inicio).total_seconds() / 60)) if fin else DURACION_POR_CATEGORIA.get(categoria, 150)
    if oficial:
        evento = {k: v for k, v in oficial.items() if k != "fuentes"}
        evento.update({
            "id": oficial["id"], "agenda_id": oficial["id"], "hora_utc": iso_utc(inicio), "duracion_min": duracion,
            "origen": f"{oficial.get('origen', 'api_sports')}+epg", "origenes": list(dict.fromkeys(list(oficial.get("origenes", [])) + ["epg"])),
            "estado": "confirmado", "confianza": "alta" if puntos >= 85 else "media", "puntuacion_confianza": max(puntos, int(oficial.get("puntuacion_confianza", 0))),
            "metodo_correlacion": "epg_agenda_verificada", "razones_correlacion": [f"tokens:{','.join(tokens)}", f"directo:{str(directo).lower()}"], "fuentes": [],
        })
        for fuente in fuentes:
            fusionar_fuente(evento, fuente)
        return evento
    if not (INCLUIR_EPG_NO_VERIFICADO and directo and categoria != "Deportes"):
        return None
    ident = hashlib.sha1(f"{normalizar_texto(titulo_raw)}|{inicio.strftime('%Y%m%d%H%M')}".encode()).hexdigest()[:16]
    evento = {
        "id": f"epg_{ident}", "agenda_id": "", "titulo": limitar(torneo, 75), "torneo": limitar(torneo, 55), "categoria": categoria,
        "tipo_evento": "sencillo", "equipo_local": "", "equipo_visitante": "", "subtitulo": limitar(subtitulo, 70),
        "hora_utc": iso_utc(inicio), "hora_local_producto": inicio.astimezone(obtener_zona_aplicacion()).strftime("%H:%M"), "duracion_min": duracion,
        "logo_torneo": "", "logo_local": "", "logo_visitante": "", "tier": 3, "origen": "epg_probable", "origenes": ["epg"],
        "estado": "probable", "estado_evento": "programado", "confianza": "media", "puntuacion_confianza": 60,
        "metodo_correlacion": "epg_directo_vigente_sin_agenda", "razones_correlacion": ["etiqueta_directo", f"categoria:{categoria}"], "fuentes": [],
    }
    for fuente in fuentes:
        fusionar_fuente(evento, fuente)
    return evento


def extraer_eventos_epg(mapa: dict[str, list[dict[str, str]]], agenda: list[dict[str, Any]], fecha_colombia: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    metricas: Counter = Counter()
    tz = obtener_zona_aplicacion()
    try:
        respuesta = requests.get(URL_EPG_EUROPA, timeout=(10, 60))
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        log.warning("No se pudo descargar EPG: %s", exc)
        return [], dict(metricas)
    eventos: list[dict[str, Any]] = []
    try:
        for _, elemento in ET.iterparse(io.BytesIO(respuesta.content), events=("end",)):
            if elemento.tag != "programme":
                continue
            metricas["programas_leidos"] += 1
            clave = clave_canal_epg(elemento.attrib.get("channel", ""))
            inicio = parse_timestamp_epg(elemento.attrib.get("start", ""))
            fin = parse_timestamp_epg(elemento.attrib.get("stop", ""))
            # Esta es la regla que une los dos días españoles necesarios: fecha
            # colombiana tras interpretar el offset original, no texto de fecha EPG.
            if not clave or not mapa.get(clave) or inicio is None or inicio.astimezone(tz).date() != fecha_colombia.date():
                elemento.clear()
                continue
            evento = construir_evento_epg(elemento.findtext("title", "") or "", elemento.findtext("desc", "") or "", inicio, fin, mapa[clave], agenda)
            if evento:
                eventos.append(evento)
                metricas["admitidos"] += 1
            else:
                metricas["rechazados"] += 1
            elemento.clear()
    except ET.ParseError as exc:
        log.warning("XMLTV inválido: %s", exc)
        return [], dict(metricas)
    return consolidar_eventos_epg(eventos), dict(metricas)


def consolidar_eventos_epg(eventos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unicos: dict[tuple[str, str], dict[str, Any]] = {}
    for evento in eventos:
        clave = (str(evento.get("agenda_id") or normalizar_texto(evento["titulo"])), evento["hora_utc"])
        previo = unicos.get(clave)
        if previo is None:
            unicos[clave] = evento
        else:
            for fuente in evento["fuentes"]:
                fusionar_fuente(previo, fuente)
    return sorted(unicos.values(), key=lambda e: e["hora_utc"])[:MAX_EPG_EVENTOS]


def eventos_se_solapan(a: dict[str, Any], b: dict[str, Any]) -> bool:
    inicio_a, inicio_b = _inicio(a), _inicio(b)
    if not inicio_a or not inicio_b:
        return False
    fin_a = inicio_a + timedelta(minutes=int(a.get("duracion_min") or 0))
    fin_b = inicio_b + timedelta(minutes=int(b.get("duracion_min") or 0))
    return inicio_a <= fin_b + timedelta(minutes=20) and inicio_b <= fin_a + timedelta(minutes=20)


def fusionar_con_base(base: list[dict[str, Any]], epg: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    metricas: Counter = Counter()
    resultado = list(base)
    por_agenda = {str(e.get("agenda_id") or e.get("id")): e for e in resultado if e.get("agenda_id") or str(e.get("id", "")).startswith("apisports_")}
    for evento in epg:
        existente = por_agenda.get(str(evento.get("agenda_id") or evento.get("id")))
        if existente and eventos_se_solapan(existente, evento):
            for fuente in evento["fuentes"]:
                fusionar_fuente(existente, fuente)
            existente["origenes"] = list(dict.fromkeys(list(existente.get("origenes", [])) + ["epg"]))
            existente["origen"] = f"{existente.get('origen', 'api_sports')}+epg"
            existente["puntuacion_confianza"] = max(int(existente.get("puntuacion_confianza", 0)), int(evento.get("puntuacion_confianza", 0)))
            metricas["fusionados"] += 1
        elif not any(str(e.get("id")) == str(evento.get("id")) for e in resultado):
            resultado.append(evento)
            metricas["agregados"] += 1
    return sorted(resultado, key=lambda e: e["hora_utc"]), dict(metricas)


def main() -> None:
    tz = obtener_zona_aplicacion()
    ahora, fecha = datetime.now(tz), datetime.now(tz).date().isoformat()
    try:
        salida = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8")) if ARCHIVO_SALIDA.exists() else {"version": 10, "eventos": []}
    except (OSError, ValueError):
        salida = {"version": 10, "eventos": []}
    agenda = cargar_agenda_cache(fecha) or obtener_agenda_maestra(fecha)
    mapa = mapear_streams_canales_lineales()
    eventos_epg, metricas_epg = extraer_eventos_epg(mapa, agenda, ahora)
    eventos, metricas_fusion = fusionar_con_base(list(salida.get("eventos") or []), eventos_epg)
    salida.update({"version": 10, "generado_utc": iso_utc(datetime.now(timezone.utc)), "zona_horaria_producto": str(tz), "fecha_local_producto": fecha, "eventos": eventos})
    ARCHIVO_SALIDA.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        meta = json.loads(ARCHIVO_META.read_text(encoding="utf-8")) if ARCHIVO_META.exists() else {}
    except (OSError, ValueError):
        meta = {}
    meta["epg"] = {"lectura": metricas_epg, "fusion": metricas_fusion, "canales_mapeados": {k: len(v) for k, v in mapa.items()}}
    meta["eventos_finales_total"] = len(eventos)
    meta["actualizado_utc"] = iso_utc(datetime.now(timezone.utc))
    ARCHIVO_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("EPG finalizado: leídos=%s agregados=%s cartelera=%d", metricas_epg.get("programas_leidos", 0), metricas_fusion.get("agregados", 0), len(eventos))


if __name__ == "__main__":
    main()
