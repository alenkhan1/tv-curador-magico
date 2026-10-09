# -*- coding: utf-8 -*-
"""
Curador Deportivo Principal Multifuente:
1. Ingesta de Canales Lineales (Win Sports, ESPN Suramérica, DSports, TyC, TNT, DAZN F1) emparejados con la Agenda Maestra de Hoy.
2. Ingesta Universal de Streams Efímeros Xtream:
   - Filtro maestro por hora de inicio: descarta de inmediato canales 24/7 y VOD sin horario.
   - Soporte universal para listas categorizadas o listas de carpeta única.
   - Descarte de canales de transmisión/comodines (ej: 'EVENTS 11 : DAZN 1', '01 | DISNEY + (ESP)').
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
from adaptadores.modelos import EventoAgenda, calcular_duracion_evento, normalizar_texto, obtener_tz
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
from conector_api_sports import (
    descargar_fixtures_dia,
    guardar_equipos_en_catalogo,
    construir_indice_fixtures,
    emparejar_con_api_sports,
    envolver_proxy_wsrv,
)

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
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY", "8b4421b0de525a42888c4dbe24f8d825")
CACHE_CANALES = REPO_DIR / "canales_xtream_cache.json"

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def obtener_canales_xtream(fecha_hoy_iso: str) -> List[Dict[str, Any]]:
    """Descarga los canales de Xtream con soporte universal (carpetas organizadas o carpeta única plana)."""
    ahora_ts = time.time()

    if CACHE_CANALES.exists():
        try:
            raw = json.loads(CACHE_CANALES.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "canales" in raw:
                canales = raw.get("canales", [])
                ts = raw.get("timestamp", 0)
                f_cap = raw.get("fecha_captura", "")
                tiene_caracol = any("CARACOL" in (c.get("name") or "").upper() for c in canales)
                if f_cap == fecha_hoy_iso and (ahora_ts - ts) < 2700 and tiene_caracol:
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

            deporte_kws = [
                "EVENT", "DEPORT", "SPORT", "FUTBOL", "LALIGA", "PREMIER", "CHAMPIONS", "CONMEBOL",
                "NBA", "MLB", "UFC", "WWE", "WIN", "DSPORTS", "BEIN", "CLARO", "FOX", "SKY", "DAZN",
                "SPECIALS", "PPV", "COLOMBIA", "ARGENTINA", "ESPAÑA", "ESPANA", "LATAM", "DIRECTO", "VIVO"
            ]
            cats_deporte = [c for c in cats if any(k in (c.get("category_name") or "").upper() for k in deporte_kws)]

            canales = []
            if len(cats) <= 5 or not cats_deporte:
                # Proveedor de carpeta única o sin categorías deportivas: descarga integral
                log.info("Proveedor Xtream con carpeta única o lista plana (%d categorías). Descargando streams completos...", len(cats))
                s_url = f"{api_url}&action=get_live_streams"
                req_s = urllib.request.Request(s_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req_s, timeout=18, context=_crear_contexto_ssl()) as resp_s:
                    canales = json.loads(resp_s.read().decode("utf-8", errors="ignore"))
            else:
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
            if CACHE_CANALES.exists():
                try:
                    raw = json.loads(CACHE_CANALES.read_text(encoding="utf-8"))
                    canales = raw.get("canales", []) if isinstance(raw, dict) else raw
                    if canales:
                        log.info("Recuperados %d canales desde cache previo tras fallo de API", len(canales))
                        return canales
                except Exception:
                    pass

    return []

def detectar_lista_actualizada_hoy(
    canales_xtream: List[Dict[str, Any]], 
    d_hoy: int, 
    m_hoy: int,
    agenda_hoy: Optional[List[EventoAgenda]] = None
) -> bool:
    """
    Verifica con rigor y de forma universal si la lista contiene streams actualizados para hoy (Hora Colombia America/Bogota).
    - Criterio 1: Streams o categorias con fecha de hoy (DD/MM o D/M) >= 2.
    - Criterio 2: Ausencia de residuos de ayer: si contiene streams con fecha de ayer y 0 de hoy ni en agenda -> PENDIENTE.
    - Criterio 3 (Contraste Ancla para carpeta única): Si no hay fechas explícitas, valida si al menos
      2 eventos de la lista coinciden con la Agenda Maestra confirmada de hoy.
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

    # Criterio 1: Fechas explícitas de hoy encontradas
    if coincidencias_hoy >= 2:
        return True

    # Criterio 2: Fechas explícitas de ayer encontradas y 0 de hoy
    if coincidencias_hoy == 0 and coincidencias_ayer >= 2:
        if agenda_hoy:
            for ev in agenda_hoy:
                if ev.local and ev.visitante:
                    l_u = normalizar_texto(ev.local)
                    v_u = normalizar_texto(ev.visitante)
                    for c in canales_xtream:
                        n_u = normalizar_texto(c.get("name") or c.get("stream_name") or "")
                        if l_u in n_u and v_u in n_u:
                            return True
        log.warning(
            "Lista Xtream contiene %d eventos de ayer (%s) y 0 de hoy (%s). Estado: PENDIENTE (esperando actualización de mediodía).",
            coincidencias_ayer, pat_ayer1, pat_hoy1
        )
        return False

    # Criterio 3: Contraste Ancla con Agenda Maestra para listas de carpeta única o sin fechas en títulos
    if agenda_hoy:
        coincidencias_agenda = 0
        for ev in agenda_hoy:
            if ev.local and ev.visitante:
                l_u = normalizar_texto(ev.local)
                v_u = normalizar_texto(ev.visitante)
                for c in canales_xtream:
                    n_u = normalizar_texto(c.get("name") or c.get("stream_name") or "")
                    if (l_u and len(l_u) >= 4 and l_u in n_u and v_u and len(v_u) >= 4 and v_u in n_u) or (f"{l_u} VS {v_u}" in n_u):
                        coincidencias_agenda += 1
                        if coincidencias_agenda >= 2:
                            log.info(
                                "Lista sin fechas certificada como ACTUALIZADA hoy via Agenda Maestra (%d coincidencias ancla).",
                                coincidencias_agenda
                            )
                            return True
            elif not ev.local and not ev.visitante and ev.torneo:
                t_u = normalizar_texto(ev.torneo)
                if len(t_u) >= 5:
                    for c in canales_xtream:
                        n_u = normalizar_texto(c.get("name") or c.get("stream_name") or "")
                        if t_u in n_u:
                            coincidencias_agenda += 1
                            if coincidencias_agenda >= 2:
                                log.info(
                                    "Lista sin fechas certificada como ACTUALIZADA hoy via circuitos de Agenda Maestra (%d coincidencias).",
                                    coincidencias_agenda
                                )
                                return True

    return False

RE_COMODINES = re.compile(
    r"^(?:EVENTS\s*\d+\s*:|0\d+\s*\||CANAL\s*PPV\s*\d+|OPC(?:ION)?\s*\d+\s*:)\s*"
    r"(?:Movistar|DAZN|DISNEY|LALIGA|PREMIERE|TVS|EUROSPORT|ESPN|FOX|TUDN|GOL|DIRECTV|CANAL\s*\d+)",
    re.I
)
RE_HORA = re.compile(r"\b([0-1]?[0-9]|2[0-3]):([0-5][0-9])\s*(AM|PM)?\b", re.I)

def procesar_streams_eventos_xtream(
    canales_xtream: List[Dict[str, Any]],
    fecha_hoy_dd_mm: str,
    fecha_hoy_iso: str,
    agenda_hoy: List[EventoAgenda],
    tz_prod: Any,
    lista_actualizada: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Embudo Universal Directo:
    1. Filtro maestro de entrada: si el stream no tiene hora (HH:MM o AM/PM), se ignora de inmediato.
    2. Descarte rápido de comodines y VOD con hora casual.
    3. Descarte si tiene fecha explícita de ayer.
    4. Extracción limpia de Duelos y Circuitos multideporte (F1, MotoGP, Tenis, Pádel, Golf, Tejo, etc.).
    5. Fusión con la Agenda Maestra de canales lineales (1 tarjeta = N fuentes).
    """
    eventos_finales = []
    descartados = []
    mapa_duelos_existentes = {}
    mapa_circuitos_existentes = {}

    p_hoy1, p_hoy2 = fecha_hoy_dd_mm.split('/')
    d_hoy, m_hoy = int(p_hoy1), int(p_hoy2)
    f_hoy_norm = f"{d_hoy:02d}/{m_hoy:02d}"

    if not lista_actualizada:
        log.warning("La lista Xtream aún no tiene la actualización de hoy (%s). Omitiendo streams efímeros para evitar eventos viejos.", fecha_hoy_dd_mm)
        return [], []

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        stype = (c.get("stream_type") or "").lower()
        if not nombre or not sid:
            continue

        # 1. FILTRO MAESTRO: ¿Tiene hora asignada de evento?
        m_h = RE_HORA.search(nombre)
        if not m_h:
            # Canales 24/7 y VOD sin hora se ignoran de la cartelera efímera
            continue

        # 2. Descarte de canales comodín / enlaces vacíos de transmisión
        if RE_COMODINES.search(nombre):
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "canal_transmision_no_evento"})
            continue

        # 3. Descarte de VOD o series con hora casual
        if stype in ["movie", "series"] or any(k in nombre for k in ["Temporada", "Season", "T01E", "S01E", "Capitulo"]):
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "vod"})
            continue

        # 4. Descarte de fecha explícita antigua (ayer u otro día)
        m_f = re.search(r'([0-3]?[0-9]/[0-1]?[0-9])', nombre)
        if m_f:
            p1, p2 = m_f.group(1).split('/')
            f_norm = f"{int(p1):02d}/{int(p2):02d}"
            if f_norm != f_hoy_norm:
                descartados.append({"nombre": nombre, "id_xtream": sid, "razon": f"fecha_antigua ({f_norm} != {f_hoy_norm})"})
                continue

        # 5. Extracción y sanitización de datos del evento
        cat_nombre = c.get("category_name") or ""
        parsed = sanitizar_evento_crudo(nombre, cat_nombre)
        if not parsed:
            continue

        titulo = parsed.get("titulo", nombre)
        torneo = parsed.get("torneo", "Deportes en Vivo")
        local = parsed.get("local", "")
        visitante = parsed.get("visitante", "")
        tipo = parsed.get("tipo", "duelo" if local and visitante else "circuito")
        deporte = parsed.get("deporte", "Otros Deportes")

        # Conversión de hora a formato 24h
        h = int(m_h.group(1))
        mi = int(m_h.group(2))
        ampm = (m_h.group(3) or "").upper()
        if ampm == "PM" and h < 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        hora_str = f"{h:02d}:{mi:02d}"

        # Reconciliación con Agenda Maestra oficial si coincide
        coincide_agenda = None
        for ev in agenda_hoy:
            if local and visitante and ev.local and ev.visitante:
                l_u = normalizar_texto(local)
                v_u = normalizar_texto(visitante)
                ev_l = normalizar_texto(ev.local)
                ev_v = normalizar_texto(ev.visitante)
                if (l_u in ev_l or ev_l in l_u) and (v_u in ev_v or ev_v in v_u):
                    coincide_agenda = ev
                    break
            elif tipo == "circuito":
                t_u = normalizar_texto(torneo)
                ev_t = normalizar_texto(ev.torneo)
                if t_u and ev_t and (t_u in ev_t or ev_t in t_u):
                    coincide_agenda = ev
                    break

        if coincide_agenda and getattr(coincide_agenda, 'hora_utc', None):
            hora_utc = coincide_agenda.hora_utc
            cat = coincide_agenda.deporte
            tor = coincide_agenda.torneo
        else:
            try:
                dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_prod)
                hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                hora_utc = f"{fecha_hoy_iso}T00:00:00Z"
            cat = deporte
            tor = torneo

        # Agrupación y fusión de fuentes
        clave_duelo = f"{normalizar_texto(local)}_vs_{normalizar_texto(visitante)}" if local and visitante else ""
        clave_circuito = f"{normalizar_texto(titulo)}_{hora_utc[:13]}" if not clave_duelo else ""

        if clave_duelo and clave_duelo in mapa_duelos_existentes:
            idx = mapa_duelos_existentes[clave_duelo]
            fuentes_existentes = eventos_finales[idx]["fuentes"]
            if sid not in [f["id_xtream"] for f in fuentes_existentes] and len(fuentes_existentes) < 4:
                fuentes_existentes.append({"nombre": nombre, "id_xtream": sid})
            continue

        if clave_circuito and clave_circuito in mapa_circuitos_existentes:
            idx = mapa_circuitos_existentes[clave_circuito]
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
            "tipo_evento": tipo,
            "equipo_local": local,
            "equipo_visitante": visitante,
            "subtitulo": f"{tor}",
            "referencia": tor,
            "hora_utc": hora_utc,
            "hora_local_producto": hora_str,
            "duracion_min": calcular_duracion_evento(cat, titulo, 120),
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
        if clave_circuito:
            mapa_circuitos_existentes[clave_circuito] = len(eventos_finales)

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

    # FASE 1.5: CONTRASTE Y ENRIQUECIMIENTO CON API-SPORTS (12 DEPORTES)
    log.info("Fase 1.5: Contraste y corroboracion oficial con API-Sports (12 Deportes)...")
    fixtures_dia = descargar_fixtures_dia(fecha_hoy_iso, API_SPORTS_KEY)
    guardar_equipos_en_catalogo(fixtures_dia)
    indice_fixtures = construir_indice_fixtures(fixtures_dia)

    emparejados_api = 0
    for ev in todos_eventos:
        match = emparejar_con_api_sports(ev, indice_fixtures, fixtures_dia)
        if match:
            emparejados_api += 1
            ev["hora_utc"] = match.get("hora_utc") or ev.get("hora_utc")
            ev["fecha_confirmada"] = True
            ev["contrastado_api"] = True
            if match.get("deporte"):
                ev["categoria"] = match["deporte"]
            if match.get("torneo") and (not ev.get("torneo") or ev["torneo"].lower() in ["deportes", "fútbol", "futbol", "deportes en vivo"]):
                ev["torneo"] = match["torneo"]
            if match.get("logo_local"):
                ev["logo_local"] = envolver_proxy_wsrv(match["logo_local"])
            if match.get("logo_visitante"):
                ev["logo_visitante"] = envolver_proxy_wsrv(match["logo_visitante"])
            if match.get("logo_torneo"):
                ev["logo_torneo"] = envolver_proxy_wsrv(match["logo_torneo"])
            if match.get("local") and match.get("visitante"):
                if not ev.get("equipo_local") or not ev.get("equipo_visitante"):
                    ev["equipo_local"] = match["local"]
                    ev["equipo_visitante"] = match["visitante"]
                    ev["titulo"] = f"{match['local']} vs {match['visitante']}"

    log.info("Eventos contrastados y corroborados con API-Sports: %d/%d", emparejados_api, len(todos_eventos))

    # FASE 2: CURACIÓN SEMÁNTICA Y DEDUPLICACIÓN POR ENTIDAD DEPORTIVA (1 TARJETA = N FUENTES)
    log.info("Fase 2: Fusión temporal, normalización y deduplicación por entidad...")
    eventos_fase2 = post_procesar_y_curar_eventos(todos_eventos)

    # FASE 3: AUDITORÍA Y PERFECCIONAMIENTO DE EVENTOS HUÉRFANOS CON AGY
    log.info("Fase 3: Auditoría y perfeccionamiento de eventos huérfanos con AGY en la VM...")
    eventos_pulidos, descartados_agy = auditar_catalogo_con_agy(eventos_fase2, fecha_hoy_iso)
    descartados.extend(descartados_agy)

    # FASE 4: GARANTÍA GRÁFICA PARA ANDROID TV (EventoCard.kt)
    for ev in eventos_pulidos:
        cat = ev.get("categoria", "")
        tor = ev.get("torneo") or ev["titulo"]
        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")

        ev["logo_torneo"] = ev.get("logo_torneo") or resolver_logo_torneo(tor, cat) or ""
        if loc:
            ev["logo_local"] = ev.get("logo_local") or resolver_logo_equipo(loc, cat, tor) or ""
        if vis:
            ev["logo_visitante"] = ev.get("logo_visitante") or resolver_logo_equipo(vis, cat, tor) or ""
        ev["banner"] = ev.get("banner") or ev["logo_torneo"]

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
            "fixtures_api_sports_hoy": len(fixtures_dia),
            "eventos_lineales_inyectados": len(eventos_lineales),
            "eventos_xtream_aprobados": len(eventos_xtream),
            "eventos_contrastados_api": emparejados_api,
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
