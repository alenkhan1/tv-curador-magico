# -*- coding: utf-8 -*-
"""
Curador Deportivo Principal Multifuente con 3 Fases:
1. Ingesta de Canales Lineales 24/7 emparejados con la Agenda Maestra de Directos de Hoy.
2. Ingesta Estricta de Eventos Efímeros Xtream (SOLO si tienen fecha confirmada de HOY tanto en categoría como en nombre, descartando canales de transmisión como 'EVENTS 14 : DAZN 1').
3. FASE 3 DE AUDITORÍA Y PERFECCIONAMIENTO CON AGY (Inteligencia Artificial Pro):
   - Elimina la categoría 'Otros Deportes' asignando la disciplina exacta (Natación, Snooker, Tiro, etc.).
   - Reescribe títulos sobrecargados a nombres concisos y profesionales.
   - Elimina redundancias (Título == Subtítulo).
   - Descarta magazines residuales y eventos obsoletos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from adaptadores.gestor_agenda import construir_agenda_maestra_hoy
from adaptadores.modelos import EventoAgenda, normalizar_texto, obtener_tz
from inyector_canales_lineales import (
    construir_indice_canales_lineales,
    inyectar_eventos_lineales,
)
from resolvedor_logos import (
    envolver_cdn_proxy,
    guardar_cache_logos,
    resolver_logo_equipo,
    resolver_logo_torneo,
)
from sanitizador_nombres import sanitizar_evento_crudo
from curador_semantico import post_procesar_y_curar_eventos
from auditor_agy import auditar_catalogo_con_agy

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("curador_eventos")

XTREAM_URL = os.environ.get("XTREAM_URL", "http://espartanos.live:8080").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER", "12user1506")
XTREAM_PASS = os.environ.get("XTREAM_PASS", "123456")
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Bogota")
CACHE_CANALES = Path("canales_xtream_cache.json")
M3U_LOCAL = Path(os.environ.get("M3U_PATH", "C:/Users/Alejandro/Downloads/tv_channels_12user1506_plus.m3u"))

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def obtener_canales_xtream() -> List[Dict[str, Any]]:
    if CACHE_CANALES.exists():
        try:
            canales = json.loads(CACHE_CANALES.read_text(encoding="utf-8"))
            log.info("Cargados %d canales desde cache local: %s", len(canales), CACHE_CANALES)
            return canales
        except Exception as e:
            log.warning("No se pudo leer cache: %s", e)

    if XTREAM_URL and XTREAM_USER and XTREAM_PASS:
        api_url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
        try:
            req_cats = urllib.request.Request(f"{api_url}&action=get_live_categories", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_cats, timeout=12, context=_crear_contexto_ssl()) as resp:
                cats = json.loads(resp.read().decode("utf-8", errors="ignore"))

            deporte_kws = ["EVENT", "DEPORT", "SPORT", "FUTBOL", "LALIGA", "PREMIER", "CHAMPIONS", "CONMEBOL", "NBA", "MLB", "UFC", "WWE", "WIN", "DSPORTS", "BEIN", "CLARO", "FOX", "SKY", "DAZN"]
            cats_deporte = [c for c in cats if any(k in (c.get("category_name") or "").upper() for k in deporte_kws)]

            canales = []
            for c in cats_deporte:
                cid = c.get("category_id")
                cname = c.get("category_name")
                s_url = f"{api_url}&action=get_live_streams&category_id={cid}"
                try:
                    req_s = urllib.request.Request(s_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req_s, timeout=12, context=_crear_contexto_ssl()) as resp_s:
                        streams = json.loads(resp_s.read().decode("utf-8", errors="ignore"))
                        for s in streams:
                            s["category_name"] = cname
                            canales.append(s)
                except Exception as e:
                    log.debug("Error leyendo categoria %s: %s", cid, e)

            if canales:
                try:
                    CACHE_CANALES.write_text(json.dumps(canales, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                return canales
        except Exception as e:
            log.warning("Fallo al consultar Xtream player_api: %s", e)

    return []

def procesar_streams_eventos_xtream(
    canales_xtream: List[Dict[str, Any]],
    fecha_hoy_dd_mm: str,
    fecha_hoy_iso: str,
    agenda_hoy: List[EventoAgenda],
    tz_prod: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    eventos_finales = []
    descartados = []
    mapa_duelos_existentes = {}

    p_hoy1, p_hoy2 = fecha_hoy_dd_mm.split('/')
    f_hoy_norm = f"{int(p_hoy1):02d}/{int(p_hoy2):02d}"

    # REGLA ARQUITECTÓNICA UNIVERSAL:
    # Las categorías o nombres tipo 'EVENTS 14 : DAZN 1' son CANALES DE TRANSMISIÓN, no eventos.
    streams_candidatos = [
        c for c in canales_xtream
        if any(k in (c.get("category_name") or "").upper() for k in ["EVENT", "PPV", "LALIGA SUR", "M+ DAZN", "DIRECTV SUR"])
        or any(k in (c.get("name") or "").upper() for k in ["EVENTS", "EVENTO", "PARTIDO", "VS"])
    ]

    for c in streams_candidatos:
        nombre = c.get("name") or c.get("stream_name") or ""
        cat_nombre = c.get("category_name") or ""
        sid = str(c.get("stream_id") or "")
        if not nombre or not sid:
            continue

        # Descartar canales de transmisión etiquetados como eventos
        # Ej: "EVENTS 14 : Movistar DAZN 1 FHD", "01 | DAZN 1", "02 | DISNEY + (ESP)"
        if re.search(r"^(?:EVENTS\s*\d+\s*:|0\d+\s*\|)\s*(?:Movistar|DAZN|DISNEY|LALIGA|PREMIERE|TVS|EUROSPORT|ESPN|FOX)", nombre, re.I):
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "canal_transmision_no_evento"})
            continue

        # Si la categoría tiene fecha (ej: "01/10 | EVENTOS DIARIOS 1") y no es hoy, DESCARTE TOTAL
        m_cat_f = re.search(r'([0-3]?[0-9]/[0-1]?[0-9])', cat_nombre)
        if m_cat_f:
            p1, p2 = m_cat_f.group(1).split('/')
            f_cat_norm = f"{int(p1):02d}/{int(p2):02d}"
            if f_cat_norm != f_hoy_norm:
                descartados.append({"nombre": nombre, "id_xtream": sid, "razon": f"categoria_antigua ({f_cat_norm} != {f_hoy_norm})"})
                continue

        # Si el nombre del stream tiene fecha y no es hoy, DESCARTE TOTAL
        m_f = re.search(r'([0-3]?[0-9]/[0-1]?[0-9])', nombre)
        if m_f:
            p1, p2 = m_f.group(1).split('/')
            f_norm = f"{int(p1):02d}/{int(p2):02d}"
            if f_norm != f_hoy_norm:
                descartados.append({"nombre": nombre, "id_xtream": sid, "razon": f"fecha_antigua ({f_norm} != {f_hoy_norm})"})
                continue

        # Si no tiene fecha explícita ni duelo, verificar coincidencia obligatoria con la Agenda Maestra de hoy
        parsed = sanitizar_evento_crudo(nombre)
        titulo = parsed.get("titulo", nombre)
        torneo = parsed.get("torneo", "Deportes en Vivo")
        local = parsed.get("local", "")
        visitante = parsed.get("visitante", "")
        m_h = re.search(r"([0-2]?[0-9]:[0-5][0-9])", nombre)
        hora_str = m_h.group(1) if m_h else parsed.get("hora", "00:00")

        coincide_agenda = None
        for ev in agenda_hoy:
            if local and visitante:
                if (normalizar_texto(local) in normalizar_texto(ev.local) or normalizar_texto(local) in normalizar_texto(ev.titulo)) and \
                   (normalizar_texto(visitante) in normalizar_texto(ev.visitante) or normalizar_texto(visitante) in normalizar_texto(ev.titulo)):
                    coincide_agenda = ev
                    break

        # Regla: Si el stream efímero no tiene fecha confirmada de hoy y no coincide con la agenda, se descarta
        if not m_cat_f and not m_f and not coincide_agenda:
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "stream_efimero_sin_confirmacion_hoy"})
            continue

        try:
            h, mi = [int(x) for x in hora_str.split(":")]
            dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_prod)
            hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            hora_utc = f"{fecha_hoy_iso}T00:00:00Z"

        cat = coincide_agenda.deporte if coincide_agenda else "Otros Deportes"
        tor = coincide_agenda.torneo if coincide_agenda else torneo

        clave_duelo = f"{normalizar_texto(local)}_vs_{normalizar_texto(visitante)}" if local and visitante else ""
        if clave_duelo and clave_duelo in mapa_duelos_existentes:
            idx = mapa_duelos_existentes[clave_duelo]
            fuentes_existentes = eventos_finales[idx]["fuentes"]
            if sid not in [f["id_xtream"] for f in fuentes_existentes] and len(fuentes_existentes) < 4:
                fuentes_existentes.append({"nombre": nombre, "id_xtream": sid})
            continue

        logo_tor = resolver_logo_torneo(tor, cat)
        logo_l = resolver_logo_equipo(local, cat, tor) if local else ""
        logo_v = resolver_logo_equipo(visitante, cat, tor) if visitante else ""

        hash_id = hashlib.sha1(f"{titulo}_{hora_utc}_{sid}".encode()).hexdigest()[:8]
        nuevo_ev = {
            "id": f"xtream_{hash_id}",
            "agenda_id": "",
            "titulo": titulo,
            "torneo": tor,
            "categoria": cat,
            "tipo_evento": "duelo" if local and visitante else "circuito",
            "equipo_local": local,
            "equipo_visitante": visitante,
            "subtitulo": f"{tor}",
            "referencia": tor,
            "hora_utc": hora_utc,
            "hora_local_producto": hora_str,
            "duracion_min": 120,
            "logo_torneo": logo_tor,
            "logo_local": logo_l,
            "logo_visitante": logo_v,
            "banner": logo_tor,
            "tier": 1 if any(k in tor.upper() for k in ["LALIGA", "PREMIER", "CHAMPIONS", "BETPLAY", "CONMEBOL", "NBA", "MLB"]) else 2,
            "origen": "xtream_evento",
            "origenes": ["xtream_evento"],
            "estado": "confirmado",
            "estado_evento": "confirmado",
            "confianza": "alta" if coincide_agenda else "media",
            "puntuacion_confianza": 0.9 if coincide_agenda else 0.75,
            "fuentes": [{"nombre": nombre, "id_xtream": sid}],
        }
        if clave_duelo:
            mapa_duelos_existentes[clave_duelo] = len(eventos_finales)
        eventos_finales.append(nuevo_ev)

    return eventos_finales, descartados

def ejecutar_curacion():
    tz_prod = obtener_tz(APP_TIMEZONE)
    ahora_prod = datetime.now(tz_prod)
    fecha_hoy_iso = ahora_prod.strftime("%Y-%m-%d")
    fecha_hoy_dd_mm = ahora_prod.strftime("%d/%m")
    log.info("=== INICIANDO CURACION DEPORTIVA MULTIFUENTE (%s) ===", fecha_hoy_iso)

    agenda_hoy = construir_agenda_maestra_hoy(fecha_hoy_iso)
    canales_xtream = obtener_canales_xtream()

    indice_canales = construir_indice_canales_lineales(canales_xtream)
    eventos_lineales = inyectar_eventos_lineales(agenda_hoy, indice_canales, APP_TIMEZONE)

    eventos_xtream, descartados = procesar_streams_eventos_xtream(
        canales_xtream, fecha_hoy_dd_mm, fecha_hoy_iso, agenda_hoy, tz_prod
    )

    todos_eventos = list(eventos_lineales)
    for ex in eventos_xtream:
        tit_ex = normalizar_texto(ex["titulo"])
        fusionado = False
        for el in todos_eventos:
            if tit_ex == normalizar_texto(el["titulo"]) and el["hora_local_producto"] == ex["hora_local_producto"]:
                for f in ex["fuentes"]:
                    if f["id_xtream"] not in [x["id_xtream"] for x in el["fuentes"]]:
                        el["fuentes"].append(f)
                fusionado = True
                break
        if not fusionado:
            todos_eventos.append(ex)

    # FASE 2: CURACIÓN Y PERFECCIONAMIENTO SEMÁNTICO BASE
    log.info("Fase 2: Fusión temporal, normalización y descarte de duplicados...")
    eventos_fase2 = post_procesar_y_curar_eventos(todos_eventos)

    # FASE 3: AUDITORÍA Y PERFECCIONAMIENTO FINAL CON AGY (Inteligencia Artificial)
    log.info("Fase 3: Auditoría y perfeccionamiento semántico integral con AGY...")
    eventos_pulidos = auditar_catalogo_con_agy(eventos_fase2, fecha_hoy_iso)

    # Re-resolver logos si AGY afinó la categoría o torneo
    for ev in eventos_pulidos:
        ev["logo_torneo"] = resolver_logo_torneo(ev.get("torneo") or ev["titulo"], ev["categoria"])
        ev["banner"] = ev["logo_torneo"]

    # Validar unicidad estricta de IDs
    ids_vistos = set()
    eventos_verificados = []
    for ev in eventos_pulidos:
        ev_id = ev.get("id")
        if ev_id in ids_vistos:
            nuevo_id = f"{ev_id}_{hashlib.sha1((ev['titulo'] + ev['hora_utc']).encode()).hexdigest()[:6]}"
            ev["id"] = nuevo_id
        ids_vistos.add(ev["id"])
        eventos_verificados.append(ev)

    eventos_verificados.sort(key=lambda x: x.get("hora_utc", ""))

    salida_final = {
        "version": "3.0-curador-universal-agy",
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zona_horaria_producto": APP_TIMEZONE,
        "fecha_local_producto": fecha_hoy_iso,
        "base_media": XTREAM_URL,
        "eventos": eventos_verificados,
        "metricas": {
            "total_agenda_maestra": len(agenda_hoy),
            "total_canales_xtream": len(canales_xtream),
            "eventos_lineales_inyectados": len(eventos_lineales),
            "eventos_xtream_aprobados": len(eventos_xtream),
            "eventos_descartados_stale": len(descartados),
            "total_eventos_publicados": len(eventos_verificados),
        }
    }

    Path("eventos_hoy.json").write_text(json.dumps(salida_final, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("eventos_descartados.json").write_text(json.dumps(descartados, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("meta_curador.json").write_text(json.dumps(salida_final["metricas"], ensure_ascii=False, indent=2), encoding="utf-8")
    guardar_cache_logos()

    log.info("=== CURACION COMPLETADA CON EXITO (UNIVERSAL AGY) ===")
    log.info("Eventos publicados: %d | Descartados: %d", len(eventos_verificados), len(descartados))

if __name__ == "__main__":
    ejecutar_curacion()
