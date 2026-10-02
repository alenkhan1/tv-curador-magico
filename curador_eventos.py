# -*- coding: utf-8 -*-
"""
Curador Deportivo Principal Multifuente:
1. Ingesta de Canales Lineales (Win Sports, ESPN Suramérica, DSports, TyC, TNT, DAZN F1) emparejados con la Agenda Maestra de Hoy.
2. Ingesta Universal de Streams Efímeros Xtream:
   - Detección inteligente de actualización de la lista: si la lista no contiene los eventos de hoy, se descartan los streams efímeros y solo se usan los canales lineales.
   - Soporte para cualquier lista Xtream (con carpetas o sin categorizar).
   - Descarte estricto de canales de transmisión (ej: 'EVENTS 14 : DAZN 1').
3. Curación Semántica y Deduplicación por Entidad Deportiva (1 Tarjeta = N Fuentes).
4. Auditoría y Perfeccionamiento con AGY (Gemini Pro en la VM).
5. Garantía Gráfica para Android TV (EventoCard.kt): Logos de Torneo, Escudos de Equipos y Banderas Oficiales.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import sys
import time
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

REPO_DIR = Path(__file__).resolve().parent
XTREAM_URL = os.environ.get("XTREAM_URL", "http://espartanos.live:8080").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER", "12user1506")
XTREAM_PASS = os.environ.get("XTREAM_PASS", "123456")
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Bogota")
CACHE_CANALES = REPO_DIR / "canales_xtream_cache.json"

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def obtener_canales_xtream(fecha_hoy_iso: str) -> List[Dict[str, Any]]:
    """Descarga los canales de Xtream con caché que expira automáticamente para capturar la actualización del mediodía."""
    ahora_ts = time.time()

    if CACHE_CANALES.exists():
        try:
            raw = json.loads(CACHE_CANALES.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "canales" in raw:
                canales = raw.get("canales", [])
                ts = raw.get("timestamp", 0)
                f_cap = raw.get("fecha_captura", "")
                if f_cap == fecha_hoy_iso and (ahora_ts - ts) < 2700:
                    log.info("Cargados %d canales desde cache vigente (%s, hace %d min)", len(canales), f_cap, int((ahora_ts - ts) / 60))
                    return canales
        except Exception as e:
            log.warning("No se pudo leer cache: %s", e)

    if XTREAM_URL and XTREAM_USER and XTREAM_PASS:
        api_url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
        try:
            req_cats = urllib.request.Request(f"{api_url}&action=get_live_categories", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_cats, timeout=14, context=_crear_contexto_ssl()) as resp:
                cats = json.loads(resp.read().decode("utf-8", errors="ignore"))

            deporte_kws = ["EVENT", "DEPORT", "SPORT", "FUTBOL", "LALIGA", "PREMIER", "CHAMPIONS", "CONMEBOL", "NBA", "MLB", "UFC", "WWE", "WIN", "DSPORTS", "BEIN", "CLARO", "FOX", "SKY", "DAZN", "SPECIALS", "PPV"]
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
                    cache_data = {
                        "fecha_captura": fecha_hoy_iso,
                        "timestamp": ahora_ts,
                        "canales": canales
                    }
                    CACHE_CANALES.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
                    log.info("Descargados y cacheados %d canales de Xtream para %s", len(canales), fecha_hoy_iso)
                except Exception:
                    pass
                return canales
        except Exception as e:
            log.warning("Fallo al consultar Xtream player_api: %s", e)

    return []

def detectar_lista_actualizada_hoy(
    canales_xtream: List[Dict[str, Any]], 
    d_hoy: int, 
    m_hoy: int,
    agenda_hoy: Optional[List[EventoAgenda]] = None
) -> bool:
    """
    Verifica con rigor si la lista contiene streams actualizados para hoy (Hora Colombia America/Bogota).
    - Criterio 1: Streams o categorias con fecha de hoy (DD/MM o D/M) >= 3.
    - Criterio 2: Listas no categorizadas o sin fechas explicitas: coincidencias con Agenda Maestra de hoy.
    - Criterio 3: Si contiene streams con la fecha de ayer y 0 de hoy, se declara PENDIENTE (ciclo 23:59 - mediodia).
    """
    tz_col = obtener_tz("America/Bogota")
    ahora_col = datetime.now(tz_col)
    ayer_col = ahora_col - timedelta(days=1)
    d_ayer, m_ayer = ayer_col.day, ayer_col.month

    pat_hoy1 = f"{d_hoy:02d}/{m_hoy:02d}"
    pat_hoy2 = f"{d_hoy}/{m_hoy}"
    pat_ayer1 = f"{d_ayer:02d}/{m_ayer:02d}"
    pat_ayer2 = f"{d_ayer}/{m_ayer}"

    coincidencias_hoy = 0
    coincidencias_ayer = 0

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        cat_nombre = c.get("category_name") or ""
        meta = f"{nombre} {cat_nombre}"
        if pat_hoy1 in meta or pat_hoy2 in meta:
            coincidencias_hoy += 1
        if pat_ayer1 in meta or pat_ayer2 in meta:
            coincidencias_ayer += 1

    if coincidencias_hoy >= 3:
        return True

    # Si hay streams de ayer y ninguno de hoy, la lista es obsoleta/ayer
    if coincidencias_hoy == 0 and coincidencias_ayer >= 3:
        log.warning("Lista Xtream contiene %d eventos de ayer (%s) y 0 de hoy (%s). Estado: PENDIENTE (ciclo 23:59 - mediodia).",
                    coincidencias_ayer, pat_ayer1, pat_hoy1)
        return False

    # Para listas no categorizadas o sin fechas en titulo: contrastar con agenda hoy
    if agenda_hoy:
        coincidencias_agenda = 0
        for ev in agenda_hoy:
            if ev.local and ev.visitante:
                l_u = ev.local.upper()
                v_u = ev.visitante.upper()
                for c in canales_xtream:
                    n_u = (c.get("name") or c.get("stream_name") or "").upper()
                    if (l_u in n_u and v_u in n_u) or (f"{l_u} VS {v_u}" in n_u):
                        coincidencias_agenda += 1
                        if coincidencias_agenda >= 2:
                            log.info("Lista no categorizada verificada como ACTUALIZADA via Agenda Maestra (%d coincidencias).", coincidencias_agenda)
                            return True

    return False

def procesar_streams_eventos_xtream(
    canales_xtream: List[Dict[str, Any]],
    fecha_hoy_dd_mm: str,
    fecha_hoy_iso: str,
    agenda_hoy: List[EventoAgenda],
    tz_prod: Any,
    lista_actualizada: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    eventos_finales = []
    descartados = []
    mapa_duelos_existentes = {}

    p_hoy1, p_hoy2 = fecha_hoy_dd_mm.split('/')
    d_hoy, m_hoy = int(p_hoy1), int(p_hoy2)
    f_hoy_norm = f"{d_hoy:02d}/{m_hoy:02d}"
    patron1 = f"{d_hoy:02d}/{m_hoy:02d}"
    patron2 = f"{d_hoy}/{m_hoy}"

    if not lista_actualizada:
        log.warning("La lista Xtream aún no tiene la actualización de hoy (%s). Omitiendo streams efímeros para evitar eventos viejos.", fecha_hoy_dd_mm)
        return [], []

    streams_candidatos = []
    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        cat_nombre = c.get("category_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        if not nombre or not sid:
            continue

        meta_todo = f"{nombre} {cat_nombre}".upper()
        es_candidato = (
            any(k in meta_todo for k in ["EVENT", "PPV", "LALIGA SUR", "DIRECTV SUR", "PARTIDO", " VS ", " V ", " - "]) or
            any(k in meta_todo for k in ["UFC", "MMA", "F1", "FORMULA 1", "MOTO GP", "ATP", "WTA", "NBA", "MLB", "NFL", "GRAND SLAM", "BOXEO", "BOXING", "CICLISMO", "TOUR DE FRANCE", "GIRO", "VUELTA"]) or
            patron1 in nombre or patron1 in cat_nombre or patron2 in nombre or patron2 in cat_nombre
        )
        if es_candidato:
            streams_candidatos.append(c)

    for c in streams_candidatos:
        nombre = c.get("name") or c.get("stream_name") or ""
        cat_nombre = c.get("category_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")

        # Descartar canales de transmisión etiquetados falsamente como eventos
        if re.search(r"^(?:EVENTS\s*\d+\s*:|0\d+\s*\|)\s*(?:Movistar|DAZN|DISNEY|LALIGA|PREMIERE|TVS|EUROSPORT|ESPN|FOX|TUDN|GOL|DIRECTV)", nombre, re.I):
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "canal_transmision_no_evento"})
            continue

        # Si la categoría tiene fecha explícita y no es hoy, DESCARTE TOTAL
        m_cat_f = re.search(r'([0-3]?[0-9]/[0-1]?[0-9])', cat_nombre)
        if m_cat_f:
            p1, p2 = m_cat_f.group(1).split('/')
            f_cat_norm = f"{int(p1):02d}/{int(p2):02d}"
            if f_cat_norm != f_hoy_norm:
                descartados.append({"nombre": nombre, "id_xtream": sid, "razon": f"categoria_antigua ({f_cat_norm} != {f_hoy_norm})"})
                continue

        # Si el nombre del stream tiene fecha explícita y no es hoy, DESCARTE TOTAL
        m_f = re.search(r'([0-3]?[0-9]/[0-1]?[0-9])', nombre)
        if m_f:
            p1, p2 = m_f.group(1).split('/')
            f_norm = f"{int(p1):02d}/{int(p2):02d}"
            if f_norm != f_hoy_norm:
                descartados.append({"nombre": nombre, "id_xtream": sid, "razon": f"fecha_antigua ({f_norm} != {f_hoy_norm})"})
                continue

        # Extraer datos sanitizados del stream
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
                if (normalizar_texto(local) in normalizar_texto(ev.local) or normalizar_texto(local) in normalizar_texto(ev.titulo)) and                    (normalizar_texto(visitante) in normalizar_texto(ev.visitante) or normalizar_texto(visitante) in normalizar_texto(ev.titulo)):
                    coincide_agenda = ev
                    break

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
    d_hoy, m_hoy = int(fecha_hoy_dd_mm.split('/')[0]), int(fecha_hoy_dd_mm.split('/')[1])

    log.info("=== INICIANDO CURACION DEPORTIVA MULTIFUENTE (%s) ===", fecha_hoy_iso)

    agenda_hoy = construir_agenda_maestra_hoy(fecha_hoy_iso)
    canales_xtream = obtener_canales_xtream(fecha_hoy_iso)

    indice_canales = construir_indice_canales_lineales(canales_xtream)
    eventos_lineales = inyectar_eventos_lineales(agenda_hoy, indice_canales, APP_TIMEZONE)

    lista_actualizada = detectar_lista_actualizada_hoy(canales_xtream, d_hoy, m_hoy, agenda_hoy)
    log.info("Estado de actualizacion de lista Xtream para hoy (%s): %s", fecha_hoy_dd_mm, "ACTUALIZADA" if lista_actualizada else "PENDIENTE")

    eventos_xtream, descartados = procesar_streams_eventos_xtream(
        canales_xtream, fecha_hoy_dd_mm, fecha_hoy_iso, agenda_hoy, tz_prod, lista_actualizada
    )

    todos_eventos = list(eventos_lineales) + list(eventos_xtream)

    # FASE 2: CURACIÓN SEMÁNTICA Y DEDUPLICACIÓN POR ENTIDAD DEPORTIVA
    log.info("Fase 2: Fusión temporal, normalización y deduplicación por entidad...")
    eventos_fase2 = post_procesar_y_curar_eventos(todos_eventos)

    # FASE 3: AUDITORÍA Y PERFECCIONAMIENTO FINAL CON AGY
    log.info("Fase 3: Auditoría y perfeccionamiento semántico integral con AGY...")
    eventos_pulidos = auditar_catalogo_con_agy(eventos_fase2, fecha_hoy_iso)

    # FASE 4: GARANTÍA GRÁFICA PARA ANDROID TV (EventoCard.kt)
    for ev in eventos_pulidos:
        cat = ev.get("categoria", "")
        tor = ev.get("torneo") or ev["titulo"]
        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")

        ev["logo_torneo"] = resolver_logo_torneo(tor, cat) or ev.get("logo_torneo", "")
        if loc:
            ev["logo_local"] = resolver_logo_equipo(loc, cat, tor) or ev.get("logo_local", "")
        if vis:
            ev["logo_visitante"] = resolver_logo_equipo(vis, cat, tor) or ev.get("logo_visitante", "")
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
        "version": "3.1-curador-tv-universal",
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zona_horaria_producto": APP_TIMEZONE,
        "fecha_local_producto": fecha_hoy_iso,
        "base_media": XTREAM_URL,
        "eventos": eventos_verificados,
        "metricas": {
            "total_agenda_maestra": len(agenda_hoy),
            "total_canales_xtream": len(canales_xtream),
            "lista_xtream_actualizada_hoy": lista_actualizada,
            "eventos_lineales_inyectados": len(eventos_lineales),
            "eventos_xtream_aprobados": len(eventos_xtream),
            "eventos_descartados_stale": len(descartados),
            "total_eventos_publicados": len(eventos_verificados),
        }
    }

    (REPO_DIR / "eventos_hoy.json").write_text(json.dumps(salida_final, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPO_DIR / "eventos_descartados.json").write_text(json.dumps(descartados, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPO_DIR / "meta_curador.json").write_text(json.dumps(salida_final["metricas"], ensure_ascii=False, indent=2), encoding="utf-8")
    guardar_cache_logos()

    log.info("=== CURACION COMPLETADA CON EXITO ===")
    log.info("Eventos publicados: %d | Descartados: %d | Xtream actualizada: %s",
             len(eventos_verificados), len(descartados), lista_actualizada)

if __name__ == "__main__":
    ejecutar_curacion()
