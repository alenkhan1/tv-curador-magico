# -*- coding: utf-8 -*-
"""
INYECTOR EPG DE EVENTOS DEPORTIVOS — AllStreamTV

Este módulo complementa la salida del curador base con la programación de
Eurosport y Teledeporte. Nunca reemplaza una categoría, un logo o un torneo por
una coincidencia débil: las emisiones no verificadas se conservan separadas y
se etiquetan como probables.
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
    DURACION_POR_CATEGORIA,
    PUENTE_URL,
    XTREAM_PASS,
    XTREAM_URL,
    XTREAM_USER,
    calcular_similitud_simple,
    categoria_deporte,
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

ARCHIVO_EVENTOS = Path(os.environ.get("ARCHIVO_SALIDA", "eventos_hoy.json"))
ARCHIVO_META = Path(os.environ.get("ARCHIVO_META", "meta_curador.json"))
URL_EPG_EUROPA = os.environ.get(
    "URL_EPG_EUROPA",
    "https://raw.githubusercontent.com/davidmuma/EPG_dobleM/master/guiatv.xml",
)
# En producción puede activarse para priorizar cobertura. Estas tarjetas nunca
# heredan categoría/logo de un evento oficial no confirmado.
INCLUIR_EPG_NO_VERIFICADO = (os.environ.get("INCLUIR_EPG_NO_VERIFICADO", "true").strip().lower() in {"1", "true", "si", "sí", "yes"})
MAX_EPG_EVENTOS = max(int(os.environ.get("MAX_EPG_EVENTOS", "150")), 0)

CANALES_EPG = {
    "EUROSPORT 1 HD": "E1",
    "EUROSPORT 1": "E1",
    "EUROSPORT 2 HD": "E2",
    "EUROSPORT 2": "E2",
    "TELEDEPORTE": "TDP",
    "TELEDEPORTE HD": "TDP",
    "TDP": "TDP",
}

# Estos no son eventos deportivos que deban poblar una tarjeta, incluso si el
# EPG los rotula como directo.
VETO_EPG = {
    "REPETICION", "REPETICIÓN", "REPLAY", "RESUMEN", "HIGHLIGHTS", "COMPACTO",
    "NOTICIAS", "NEWS", "MAGAZINE", "INFORMATIVO", "TELEDIARIO", "REPORTAJE",
    "DOCUMENTAL", "CLASICOS", "CLÁSICOS", "MEMORIAS", "VINTAGE", "PREVIA",
    "POSTPARTIDO", "POST PARTIDO", "ENTREVISTA", "EL CLUB DE",
}


# ─── UTILIDADES EPG ─────────────────────────────────────────────────────────
def parse_timestamp_epg(valor: str) -> Optional[datetime]:
    """Convierte XMLTV estándar y la variante exponencial a UTC."""
    if not valor:
        return None
    partes = valor.strip().split()
    numero = partes[0]
    offset = partes[1] if len(partes) > 1 else "+0000"
    try:
        if "E+" in numero.upper():
            digitos = f"{int(round(float(numero))):014d}"
        else:
            digitos = re.sub(r"\D", "", numero).ljust(14, "0")[:14]
        base = datetime.strptime(digitos, "%Y%m%d%H%M%S")
        if not re.fullmatch(r"[+-]\d{4}", offset):
            return base.replace(tzinfo=timezone.utc)
        signo = 1 if offset[0] == "+" else -1
        horas, minutos = int(offset[1:3]), int(offset[3:5])
        zona = timezone(signo * timedelta(hours=horas, minutes=minutos))
        return base.replace(tzinfo=zona).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def es_directo(titulo: str, descripcion: str = "") -> bool:
    valor = normalizar_texto(f"{titulo} {descripcion}")
    return bool(re.search(r"\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", valor))


def es_veto_epg(titulo: str, descripcion: str = "") -> bool:
    valor = normalizar_texto(f"{titulo} {descripcion}")
    if contiene_veto(valor):
        return True
    return any(palabra in valor for palabra in VETO_EPG)


def limpiar_titulo_epg(titulo: str) -> tuple[str, str]:
    """Retira marcas de emisión, conserva título y subtítulo de forma legible."""
    valor = re.sub(r"\[/?COLOR[^\]]*\]", "", titulo or "", flags=re.I)
    valor = re.sub(r"(?i)\b(DIRECTO|VIVO|LIVE|EN DIRECTO|EN VIVO)\b", "", valor)
    valor = " ".join(valor.strip(" -:·| ").split())
    partes = [p.strip() for p in re.split(r"\s*[·|]\s*", valor, maxsplit=1) if p.strip()]
    if len(partes) >= 2:
        return partes[0], partes[1]
    # Un guion se trata como separador solo si no parece un resultado/equipo.
    if " - " in valor:
        izquierda, derecha = valor.split(" - ", 1)
        if len(izquierda) >= 4 and len(derecha) >= 3:
            return izquierda.strip(), derecha.strip()
    return valor, ""


def limitar(texto: str, maximo: int) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= maximo else texto[: maximo - 1].rstrip() + "…"


def llamada_xtream(url: str, timeout: int = 45) -> Any:
    respuesta = requests.get(PUENTE_URL, params={"url": url}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.json()


def clave_canal_epg(channel_id: str) -> Optional[str]:
    normalizado = normalizar_texto(channel_id)
    for nombre, clave in CANALES_EPG.items():
        if normalizado == normalizar_texto(nombre):
            return clave
    return None


# ─── SEÑALES LINEALES ───────────────────────────────────────────────────────
def mapear_streams_canales_lineales() -> dict[str, list[dict[str, str]]]:
    mapa: dict[str, list[dict[str, str]]] = {"E1": [], "E2": [], "TDP": []}
    if not XTREAM_URL or not XTREAM_USER or not XTREAM_PASS:
        return mapa
    api = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}&action=get_live_streams"
    try:
        streams = llamada_xtream(api, timeout=75) or []
    except (requests.RequestException, ValueError, TypeError) as exc:
        log.warning("No se pudieron mapear canales lineales: %s", exc)
        return mapa

    vistos: set[tuple[str, str]] = set()
    for stream in streams:
        nombre = str(stream.get("name") or "").strip()
        stream_id = str(stream.get("stream_id") or "")
        if not nombre or not stream_id:
            continue
        mayus = normalizar_texto(nombre)
        clave: Optional[str] = None
        if any(pista in mayus for pista in ("TELEDEPORTE", " TDP ", "TDP HD", "TDP FHD", "TDP SD")):
            clave = "TDP"
        elif any(pista in mayus for pista in ("EUROSPORT 2", "EUROSPORT2", "EUROSPORTS 2", " ES2")):
            clave = "E2"
        elif any(pista in mayus for pista in ("EUROSPORT 1", "EUROSPORT1", "EUROSPORTS 1", " ES1")):
            clave = "E1"
        if clave and (clave, stream_id) not in vistos:
            mapa[clave].append({"nombre": nombre, "id_xtream": stream_id})
            vistos.add((clave, stream_id))

    log.info("Señales lineales: E1=%d | E2=%d | TDP=%d", len(mapa["E1"]), len(mapa["E2"]), len(mapa["TDP"]))
    return mapa


# ─── CORRELACIÓN EPG → AGENDA ───────────────────────────────────────────────
def sesiones_compatibles_epg(titulo_epg: str, evento: dict[str, Any]) -> bool:
    sesion_epg = extraer_sesion(titulo_epg)
    sesion_evento = extraer_sesion(f"{evento.get('titulo', '')} {evento.get('subtitulo', '')}")
    if not sesion_epg or not sesion_evento or sesion_epg == sesion_evento:
        return True
    return sesion_evento == "sprint" and sesion_epg == "carrera_sprint"


def buscar_evento_oficial(titulo_epg: str, agenda: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], int, list[str]]:
    """Devuelve la mejor coincidencia, no la primera de un token común."""
    categoria_epg = inferir_deporte(titulo_epg)
    tokens_epg = tokenizar(titulo_epg)
    mejor: Optional[dict[str, Any]] = None
    mejor_puntuacion = 0
    mejor_tokens: list[str] = []

    for evento in agenda:
        if categoria_epg and evento.get("categoria") != categoria_epg:
            continue
        if not sesiones_compatibles_epg(titulo_epg, evento):
            continue
        puntuacion, comunes = calcular_similitud_simple(
            str(evento.get("titulo") or ""),
            str(evento.get("torneo") or ""),
            titulo_epg,
        )
        if not comunes:
            continue
        evento_norm = normalizar_texto(evento.get("titulo") or "")
        epg_norm = normalizar_texto(titulo_epg)
        frase = bool(evento_norm and (evento_norm in epg_norm or epg_norm in evento_norm))
        # El simple nombre de la disciplina no identifica una competición.
        if len(comunes) == 1 and not frase:
            token = comunes[0]
            if len(token) < 7 or token in {"HOCKEY", "CYCLING", "TENNIS", "GOLF", "SOCCER", "FUTBOL"}:
                continue
        if puntuacion > mejor_puntuacion:
            mejor, mejor_puntuacion, mejor_tokens = evento, puntuacion, comunes

    # 65 requiere una evidencia material del nombre oficial. La frase exacta se
    # eleva a 92 por calcular_similitud_simple.
    return (mejor, mejor_puntuacion, mejor_tokens) if mejor_puntuacion >= 65 else (None, 0, [])


def construir_evento_epg(
    titulo_raw: str,
    descripcion: str,
    inicio: datetime,
    fin: Optional[datetime],
    fuentes: list[dict[str, str]],
    agenda: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not titulo_raw or len(titulo_raw.strip()) < 4 or es_veto_epg(titulo_raw, descripcion):
        return None

    oficial, puntuacion, tokens = buscar_evento_oficial(titulo_raw, agenda)
    directo = es_directo(titulo_raw, descripcion)
    torneo_epg, subtitulo_epg = limpiar_titulo_epg(titulo_raw)
    categoria_inferida = inferir_deporte(f"{titulo_raw} {descripcion}") or "Deportes"

    # Una tarjeta EPG sin correlación solo se admite si es explícitamente en vivo
    # y tiene una disciplina identificable. Se mantiene como probable, sin logo
    # ni categoría prestada por otra competencia.
    if oficial is None:
        if not (INCLUIR_EPG_NO_VERIFICADO and directo and categoria_inferida != "Deportes"):
            return None
        torneo = limitar(torneo_epg, 45)
        subtitulo = limitar(subtitulo_epg, 60)
        titulo = f"{torneo} · {subtitulo}" if subtitulo else torneo
        id_base = f"{normalizar_texto(titulo)}|{inicio.strftime('%Y%m%d%H%M')}"
        duracion = int((fin - inicio).total_seconds() / 60) if fin else DURACION_POR_CATEGORIA.get(categoria_inferida, 150)
        return {
            "id": f"epg_{hashlib.sha1(id_base.encode('utf-8')).hexdigest()[:14]}",
            "agenda_id": "",
            "titulo": titulo,
            "torneo": torneo,
            "categoria": categoria_inferida,
            "tipo_evento": "sencillo",
            "equipo_local": "",
            "equipo_visitante": "",
            "subtitulo": subtitulo,
            "hora_utc": iso_utc(inicio),
            "duracion_min": max(duracion, 15),
            "logo_torneo": "",
            "logo_local": "",
            "logo_visitante": "",
            "tier": 2,
            "origen": "epg_no_verificado",
            "estado": "probable",
            "confianza": "media",
            "puntuacion_confianza": 55,
            "metodo_correlacion": "epg_directo_sin_agenda_oficial",
            "razones_correlacion": ["etiqueta_directo", f"categoria_inferida:{categoria_inferida}"],
            "fuentes": list(fuentes),
        }

    # Con agenda oficial, se utiliza su identidad y sus metadatos. El título del
    # EPG se conserva como contexto de emisión, no como sustituto del evento.
    duracion = int((fin - inicio).total_seconds() / 60) if fin else int(oficial.get("duracion_min") or 150)
    id_base = f"{oficial['id']}|{inicio.strftime('%Y%m%d%H%M')}"
    return {
        "id": f"epg_{hashlib.sha1(id_base.encode('utf-8')).hexdigest()[:14]}",
        "agenda_id": oficial["id"],
        "titulo": oficial["titulo"],
        "torneo": oficial["torneo"],
        "categoria": oficial["categoria"],
        "tipo_evento": oficial["tipo_evento"],
        "equipo_local": oficial.get("equipo_local", ""),
        "equipo_visitante": oficial.get("equipo_visitante", ""),
        "subtitulo": limitar(subtitulo_epg or oficial.get("subtitulo", ""), 60),
        "hora_utc": iso_utc(inicio),
        "duracion_min": max(duracion, 15),
        "logo_torneo": oficial.get("logo_torneo", ""),
        "logo_local": oficial.get("logo_local", ""),
        "logo_visitante": oficial.get("logo_visitante", ""),
        "tier": 1,
        "origen": "epg_verificado",
        "estado": "confirmado",
        "confianza": "alta" if puntuacion >= 85 else "media",
        "puntuacion_confianza": puntuacion,
        "metodo_correlacion": "epg_agenda_oficial",
        "razones_correlacion": [f"tokens:{','.join(tokens)}", f"directo:{str(directo).lower()}"],
        "fuentes": list(fuentes),
    }


# ─── LECTURA DEL XMLTV Y CONSOLIDACIÓN ───────────────────────────────────────
def extraer_eventos_epg(
    mapa: dict[str, list[dict[str, str]]],
    agenda: list[dict[str, Any]],
    fecha_local: datetime,
) -> tuple[list[dict[str, Any]], Counter]:
    metricas = Counter()
    tz = obtener_zona_aplicacion()
    eventos: list[dict[str, Any]] = []
    try:
        respuesta = requests.get(URL_EPG_EUROPA, timeout=(10, 45))
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        log.warning("No se pudo descargar EPG: %s", exc)
        return eventos, metricas

    try:
        parser = ET.iterparse(io.BytesIO(respuesta.content), events=("end",))
        for _, elemento in parser:
            if elemento.tag != "programme":
                continue
            metricas["programas_leidos"] += 1
            clave = clave_canal_epg(elemento.attrib.get("channel", ""))
            if not clave or not mapa.get(clave):
                elemento.clear()
                continue
            inicio = parse_timestamp_epg(elemento.attrib.get("start", ""))
            fin = parse_timestamp_epg(elemento.attrib.get("stop", ""))
            if inicio is None or inicio.astimezone(tz).date() != fecha_local.date():
                elemento.clear()
                continue
            titulo = elemento.findtext("title", "") or ""
            descripcion = elemento.findtext("desc", "") or ""
            evento = construir_evento_epg(titulo, descripcion, inicio, fin, mapa[clave], agenda)
            if evento:
                eventos.append(evento)
                metricas["admitidos"] += 1
            else:
                metricas["rechazados"] += 1
            elemento.clear()
    except ET.ParseError as exc:
        log.warning("El XMLTV no es válido: %s", exc)
        return [], metricas

    # Primero se eliminan copias exactas (mismo evento/canal/hora). Después se
    # consolidan transmisiones solapadas del mismo evento, acumulando fuentes.
    unicos: dict[tuple[str, str], dict[str, Any]] = {}
    for evento in eventos:
        identidad = evento.get("agenda_id") or normalizar_texto(evento["titulo"])
        clave = (identidad, evento["hora_utc"])
        existente = unicos.get(clave)
        if existente is None:
            unicos[clave] = evento
        else:
            for fuente in evento["fuentes"]:
                fusionar_fuente(existente, fuente)

    consolidados: list[dict[str, Any]] = []
    for evento in sorted(unicos.values(), key=lambda e: e["hora_utc"]):
        identidad = evento.get("agenda_id") or normalizar_texto(evento["titulo"])
        inicio = datetime.strptime(evento["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        fusionado = False
        for previo in reversed(consolidados):
            previa_identidad = previo.get("agenda_id") or normalizar_texto(previo["titulo"])
            if previa_identidad != identidad:
                continue
            inicio_previo = datetime.strptime(previo["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            fin_previo = inicio_previo + timedelta(minutes=int(previo.get("duracion_min") or 0))
            # Una segunda emisión dentro de la ventana del mismo evento se trata
            # como fuente alternativa, no como tarjeta nueva.
            if inicio <= fin_previo + timedelta(minutes=20):
                for fuente in evento["fuentes"]:
                    fusionar_fuente(previo, fuente)
                fin_evento = inicio + timedelta(minutes=int(evento.get("duracion_min") or 0))
                previo["duracion_min"] = max(int(previo["duracion_min"]), int((fin_evento - inicio_previo).total_seconds() / 60))
                fusionado = True
            break
        if not fusionado:
            consolidados.append(evento)

    metricas["finales"] = len(consolidados)
    return consolidados[:MAX_EPG_EVENTOS], metricas


def eventos_se_solapan(a: dict[str, Any], b: dict[str, Any]) -> bool:
    try:
        inicio_a = datetime.strptime(a["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        inicio_b = datetime.strptime(b["hora_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, KeyError):
        return False
    fin_a = inicio_a + timedelta(minutes=int(a.get("duracion_min") or 0))
    fin_b = inicio_b + timedelta(minutes=int(b.get("duracion_min") or 0))
    return inicio_a <= fin_b + timedelta(minutes=20) and inicio_b <= fin_a + timedelta(minutes=20)


def fusionar_con_base(base: list[dict[str, Any]], epg: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter]:
    metricas = Counter()
    por_agenda: dict[str, dict[str, Any]] = {}
    for evento in base:
        event_id = str(evento.get("id") or "")
        if event_id and not event_id.startswith("epg_"):
            por_agenda[event_id] = evento

    resultado = list(base)
    for evento_epg in epg:
        agenda_id = str(evento_epg.get("agenda_id") or "")
        base_equivalente = por_agenda.get(agenda_id)
        if base_equivalente and eventos_se_solapan(base_equivalente, evento_epg):
            for fuente in evento_epg["fuentes"]:
                fusionar_fuente(base_equivalente, fuente)
            # Conserva la mayor confianza y evidencia de ambas fuentes.
            base_equivalente["origen"] = "thesportsdb+epg"
            base_equivalente.setdefault("enriquecimiento_epg", []).append({
                "hora_utc": evento_epg["hora_utc"],
                "metodo": evento_epg["metodo_correlacion"],
                "puntuacion": evento_epg["puntuacion_confianza"],
            })
            metricas["fusionados_con_base"] += 1
            continue

        # Evita insertar la misma tarjeta EPG por una ejecución anterior.
        if any(str(x.get("id")) == str(evento_epg.get("id")) for x in resultado):
            metricas["duplicados_descartados"] += 1
            continue
        resultado.append(evento_epg)
        metricas["epg_agregados"] += 1

    resultado.sort(key=lambda evento: evento.get("hora_utc", ""))
    return resultado, metricas


def cargar_salida_base() -> dict[str, Any]:
    if not ARCHIVO_EVENTOS.exists():
        return {"version": 9, "eventos": []}
    try:
        datos = json.loads(ARCHIVO_EVENTOS.read_text(encoding="utf-8"))
        if isinstance(datos, dict):
            datos.setdefault("eventos", [])
            return datos
    except (OSError, ValueError):
        pass
    return {"version": 9, "eventos": []}


def actualizar_meta(metricas: dict[str, Any], total: int) -> None:
    try:
        meta = json.loads(ARCHIVO_META.read_text(encoding="utf-8")) if ARCHIVO_META.exists() else {}
    except (OSError, ValueError):
        meta = {}
    meta["epg"] = metricas
    meta["eventos_finales_total"] = total
    meta["actualizado_utc"] = iso_utc(datetime.now(timezone.utc))
    ARCHIVO_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    log.info("=== Iniciando Inyector EPG AllStreamTV v9 ===")
    salida = cargar_salida_base()
    tz = obtener_zona_aplicacion()
    fecha_local = datetime.now(tz)
    # El curador ya dejó agenda_api_v9.json. En una ejecución aislada se crea
    # bajo la misma política de caché y de control de tasa.
    agenda = obtener_agenda_maestra(fecha_local.date().isoformat())
    mapa = mapear_streams_canales_lineales()
    eventos_epg, metricas_epg = extraer_eventos_epg(mapa, agenda, fecha_local)
    eventos, metricas_fusion = fusionar_con_base(list(salida.get("eventos") or []), eventos_epg)

    salida["version"] = 9
    salida["generado_utc"] = iso_utc(datetime.now(timezone.utc))
    salida["zona_horaria_producto"] = str(tz)
    salida["fecha_local_producto"] = fecha_local.date().isoformat()
    salida["eventos"] = eventos
    ARCHIVO_EVENTOS.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")

    metricas = {"lectura": dict(metricas_epg), "fusion": dict(metricas_fusion)}
    actualizar_meta(metricas, len(eventos))
    log.info("EPG finalizado: admitidos=%d | eventos EPG=%d | cartelera total=%d.", metricas_epg["admitidos"], len(eventos_epg), len(eventos))


if __name__ == "__main__":
    main()
