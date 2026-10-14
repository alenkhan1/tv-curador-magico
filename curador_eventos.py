# -*- coding: utf-8 -*-
"""
Curador Deportivo Principal Multifuente:
1. Ingesta de Canales Lineales (Win Sports, ESPN Suramérica, DSports, TyC, TNT, DAZN F1, Eurosport 1/2, Teledeporte)
   emparejados estrictamente 1 a 1 con la Agenda Maestra de Hoy.
2. Detección Universal de Lista Xtream Actualizada:
   - No depende de carpetas ('Eventos') ni exclusivamente de fechas escritas (DD/MM).
   - Muestreo cruzado de duelos al azar (Equipo A vs Equipo B) contrastados contra la cartelera oficial de HOY vs AYER.
   - Salida rápida en 2s si la lista aún es de ayer.
3. Parada Inteligente ("One & Done"):
   - Una vez que la lista de Xtream es confirmada y procesada hoy, se guarda la bandera en meta_curador.json.
   - El cron no vuelve a tocar Xtream por el resto del día hasta las 00:15 de Madrid del día siguiente.
4. Curación Semántica, Deduplicación y Enriquecimiento Gráfico HD para Android TV (EventoCard.kt).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
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
    es_logo_basura,
    es_logo_compatible_con_categoria,
)
from sanitizador_nombres import sanitizar_evento_crudo, es_texto_no_vivo_o_aplazado
from curador_semantico import post_procesar_y_curar_eventos
from auditor_agy import auditar_catalogo_con_agy, revisar_y_enriquecer_eventos_incompletos_con_agy
from conector_api_sports import (
    descargar_fixtures_dia,
    guardar_equipos_en_catalogo,
    construir_indice_fixtures,
    emparejar_con_api_sports,
    envolver_proxy_wsrv,
    normalizar_clave,
)

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("curador_eventos")

REPO_DIR = Path(__file__).resolve().parent
# Cargar variables de entorno desde .env si existe
_env_path = REPO_DIR / ".env"
if _env_path.exists():
    try:
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
    except Exception:
        pass

XTREAM_URL = (os.environ.get("XTREAM_URL") or "").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER")
XTREAM_PASS = os.environ.get("XTREAM_PASS")
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Bogota")
API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY")
CACHE_CANALES = REPO_DIR / "canales_xtream_cache.json"
META_FILE = REPO_DIR / "meta_curador.json"

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

RE_COMODINES = re.compile(
    r"^(?:EVENTS\s*\d+\s*:|0\d+\s*\||CANAL\s*PPV\s*\d+|OPC(?:ION)?\s*\d+\s*:)\s*"
    r"(?:Movistar|DAZN|DISNEY|LALIGA|PREMIERE|TVS|EUROSPORT|ESPN|FOX|TUDN|GOL|DIRECTV|CANAL\s*\d+)",
    re.I
)
RE_HORA = re.compile(r"\b([0-1]?[0-9]|2[0-3]):([0-5][0-9])\s*(AM|PM)?\b", re.I)

def _duelo_coincide_con_fixtures(
    local_str: str,
    visitante_str: str,
    fixtures_list: List[Dict[str, Any]]
) -> bool:
    """Verifica si un duelo local vs visitante coincide con algún fixture de la lista."""
    loc_n = normalizar_clave(local_str)
    vis_n = normalizar_clave(visitante_str)
    if not loc_n or not vis_n or len(loc_n) < 3 or len(vis_n) < 3:
        return False

    for f in fixtures_list:
        f_loc = normalizar_clave(f.get("local") or "")
        f_vis = normalizar_clave(f.get("visitante") or "")
        if not f_loc or not f_vis:
            continue
        if (loc_n in f_loc or f_loc in loc_n) and (vis_n in f_vis or f_vis in vis_n):
            return True
        if (loc_n in f_vis or f_vis in loc_n) and (vis_n in f_loc or f_loc in vis_n):
            return True
    return False

def detectar_lista_actualizada_hoy(
    canales_xtream: List[Dict[str, Any]], 
    fecha_hoy_iso: str,
    fecha_ayer_iso: str,
    agenda_hoy: Optional[List[EventoAgenda]] = None,
    fixtures_dia: Optional[List[Dict[str, Any]]] = None,
    fixtures_ayer: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """
    Verifica con rigor y contraste directo contra la API oficial si la lista Xtream contiene streams actualizados para hoy.
    """
    duelos_candidatos = []
    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        cat_nombre = c.get("category_name") or ""

        if not RE_HORA.search(nombre) or RE_COMODINES.search(nombre):
            continue

        stype = (c.get("stream_type") or "").lower()
        if stype in ["movie", "series"] or any(k in nombre for k in ["Temporada", "Season", "T01E", "S01E", "Capitulo"]):
            continue

        parsed = sanitizar_evento_crudo(nombre, cat_nombre)
        if parsed and parsed.get("local") and parsed.get("visitante"):
            duelos_candidatos.append({
                "local": parsed["local"],
                "visitante": parsed["visitante"],
                "titulo": parsed.get("titulo", nombre),
                "deporte": parsed.get("deporte", "Futbol"),
                "raw": nombre,
            })

    if not duelos_candidatos:
        log.warning("No se encontraron duelos candidatos con horario en la lista Xtream.")
        return False

    fix_hoy = fixtures_dia if fixtures_dia is not None else descargar_fixtures_dia(fecha_hoy_iso, API_SPORTS_KEY)
    fix_ayer = fixtures_ayer if fixtures_ayer is not None else descargar_fixtures_dia(fecha_ayer_iso, API_SPORTS_KEY)

    votos_hoy = 0
    votos_ayer = 0
    duelos_vistos = set()

    for d in duelos_candidatos:
        loc, vis = d["local"], d["visitante"]
        clave_duelo = f"{normalizar_clave(loc)}_vs_{normalizar_clave(vis)}"
        if clave_duelo in duelos_vistos:
            continue
        duelos_vistos.add(clave_duelo)

        es_de_hoy = _duelo_coincide_con_fixtures(loc, vis, fix_hoy)
        if not es_de_hoy and agenda_hoy:
            l_n = normalizar_clave(loc)
            v_n = normalizar_clave(vis)
            for ev in agenda_hoy:
                if ev.local and ev.visitante:
                    ev_l = normalizar_clave(ev.local)
                    ev_v = normalizar_clave(ev.visitante)
                    if (l_n in ev_l or ev_l in l_n) and (v_n in ev_v or ev_v in v_n):
                        es_de_hoy = True
                        break

        es_de_ayer = _duelo_coincide_con_fixtures(loc, vis, fix_ayer)

        if es_de_hoy and not es_de_ayer:
            votos_hoy += 1
            log.info("  [Contraste API] '%s vs %s' -> Confirmado HOY (+1 voto HOY)", loc, vis)
        elif es_de_ayer and not es_de_hoy:
            votos_ayer += 1
            log.warning("  [Contraste API] '%s vs %s' -> Corresponde a AYER (+1 voto AYER)", loc, vis)
        elif es_de_hoy and es_de_ayer:
            votos_hoy += 1

    log.info(
        "Resultado del contraste API universal: %d duelos confirmados HOY | %d duelos confirmados AYER (de %d unicos evaluados)",
        votos_hoy, votos_ayer, len(duelos_vistos)
    )

    if votos_ayer >= 2 and votos_ayer >= votos_hoy:
        log.warning(
            "Lista Xtream RECHAZADA: contiene %d duelos de AYER vs %d de HOY. Estado: PENDIENTE (esperando renovacion de mediodia).",
            votos_ayer, votos_hoy
        )
        return False

    if votos_hoy >= 3 and votos_hoy > (votos_ayer * 2):
        log.info(
            "Lista Xtream CERTIFICADA como ACTUALIZADA hoy via contraste API (%d duelos HOY vs %d AYER).",
            votos_hoy, votos_ayer
        )
        return True

    if votos_ayer > 0 and votos_hoy == 0:
        log.warning("Lista Xtream contiene duelos de AYER (%d votos AYER, 0 HOY). Estado: PENDIENTE.", votos_ayer)
        return False

    log.warning("Lista Xtream sin confirmacion suficiente en API (%d HOY, %d AYER). Estado: PENDIENTE.", votos_hoy, votos_ayer)
    return False

def procesar_streams_eventos_xtream(
    canales_xtream: List[Dict[str, Any]],
    fecha_hoy_dd_mm: str,
    fecha_hoy_iso: str,
    agenda_hoy: List[EventoAgenda],
    tz_prod: Any,
    lista_actualizada: bool,
    fixtures_dia: Optional[List[Dict[str, Any]]] = None,
    fixtures_ayer: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Embudo Universal Directo para streams efímeros de Xtream.
    """
    eventos_finales = []
    descartados = []
    mapa_duelos_existentes = {}
    mapa_circuitos_existentes = {}

    p_hoy1, p_hoy2 = fecha_hoy_dd_mm.split('/')
    d_hoy, m_hoy = int(p_hoy1), int(p_hoy2)

    if not lista_actualizada:
        log.warning("La lista Xtream aún no tiene la actualización de hoy (%s). Omitiendo streams efímeros para evitar eventos viejos.", fecha_hoy_dd_mm)
        return [], []

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        stype = (c.get("stream_type") or "").lower()
        if not nombre or not sid:
            continue

        m_h = RE_HORA.search(nombre)
        if not m_h:
            continue

        if RE_COMODINES.search(nombre):
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "canal_transmision_no_evento"})
            continue

        if stype in ["movie", "series"] or any(k in nombre for k in ["Temporada", "Season", "T01E", "S01E", "Capitulo"]):
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "vod"})
            continue

        m_y = re.search(r'\b(20[0-1][0-9]|202[0-5])\b', nombre)
        if m_y:
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": f"anio_antiguo ({m_y.group(1)})"})
            continue

        m_f = re.search(r'\b([0-3]?[0-9])[./-]([0-3]?[0-9])\b', nombre)
        if m_f:
            n1, n2 = int(m_f.group(1)), int(m_f.group(2))
            es_fecha_valida = (1 <= n1 <= 12 and 1 <= n2 <= 31) or (1 <= n2 <= 12 and 1 <= n1 <= 31)
            if es_fecha_valida:
                es_hoy = (n1 == d_hoy and n2 == m_hoy) or (n1 == m_hoy and n2 == d_hoy)
                if not es_hoy:
                    descartados.append({"nombre": nombre, "id_xtream": sid, "razon": f"fecha_ajena ({m_f.group(0)})"})
                    continue

        cat_nombre = c.get("category_name") or ""
        parsed = sanitizar_evento_crudo(nombre, cat_nombre)
        if not parsed:
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "programa_no_vivo_o_aplazado"})
            continue

        titulo = parsed.get("titulo", nombre)
        torneo = parsed.get("torneo", "Deportes en Vivo")
        local = parsed.get("local", "")
        visitante = parsed.get("visitante", "")
        tipo = parsed.get("tipo", "duelo" if local and visitante else "circuito")
        deporte = parsed.get("deporte", "Otros Deportes")

        if local and visitante and fixtures_ayer and _duelo_coincide_con_fixtures(local, visitante, fixtures_ayer):
            es_de_hoy_tambien = bool(fixtures_dia and _duelo_coincide_con_fixtures(local, visitante, fixtures_dia))
            if not es_de_hoy_tambien:
                descartados.append({
                    "nombre": nombre,
                    "id_xtream": sid,
                    "razon": f"duelo_disputado_ayer ({local} vs {visitante})"
                })
                continue

        h = int(m_h.group(1))
        mi = int(m_h.group(2))
        ampm = (m_h.group(3) or "").upper()
        if ampm == "PM" and h < 12:
            h += 12
        elif ampm == "AM" and h == 12:
            h = 0
        hora_str = f"{h:02d}:{mi:02d}"

        coincide_agenda = None
        for ev in agenda_hoy:
            if local and visitante and ev.local and ev.visitante:
                l_u = normalizar_texto(local)
                v_u = normalizar_texto(visitante)
                ev_l = normalizar_texto(ev.local)
                ev_v = normalizar_texto(ev.visitante)
                if (l_u in ev_l or ev_l in l_u) and (v_u in ev_v or ev_v in v_n if 'v_n' in locals() else (v_u in ev_v or ev_v in v_u)):
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

        logo_tor = resolver_logo_torneo(tor, cat, local=local, visitante=visitante)
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
            "subtitulo": parsed.get("subtitulo") or f"{tor}",
            "referencia": parsed.get("referencia") or tor,
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
            "texto_original_crudo": parsed.get("texto_original_crudo", nombre),
            "titulo_original_crudo": parsed.get("titulo_original_crudo", nombre),
        }
        if clave_duelo:
            mapa_duelos_existentes[clave_duelo] = len(eventos_finales)
        if clave_circuito:
            mapa_circuitos_existentes[clave_circuito] = len(eventos_finales)

        eventos_finales.append(nuevo_ev)

    return eventos_finales, descartados

def _recalcular_hora_local(hora_utc_str: str, tz_prod: Any, fallback: str = "") -> str:
    if not hora_utc_str:
        return fallback
    try:
        dt_utc = datetime.fromisoformat(str(hora_utc_str).replace("Z", "+00:00"))
        return dt_utc.astimezone(tz_prod).strftime("%H:%M")
    except Exception:
        return fallback

def ejecutar_curacion(modo_check_xtream: bool = False, forzar: bool = False):
    tz_prod = obtener_tz(APP_TIMEZONE)
    ahora_prod = datetime.now(tz_prod)
    fecha_hoy_iso = ahora_prod.strftime("%Y-%m-%d")
    fecha_ayer_iso = (ahora_prod - timedelta(days=1)).strftime("%Y-%m-%d")
    fecha_hoy_dd_mm = ahora_prod.strftime("%d/%m")

    log.info("=== INICIANDO CURACION DEPORTIVA MULTIFUENTE (%s) ===", fecha_hoy_iso)

    # PARADA INTELIGENTE ("ONE & DONE"):
    if not forzar and META_FILE.exists():
        try:
            meta_prev = json.loads(META_FILE.read_text(encoding="utf-8"))
            if meta_prev.get("fecha_local_producto") == fecha_hoy_iso and meta_prev.get("xtream_procesada_hoy") is True:
                if modo_check_xtream:
                    log.info("[ONE & DONE] La lista Xtream para hoy (%s) ya fue verificada y procesada previamente. Saliendo de inmediato.", fecha_hoy_iso)
                    return
        except Exception as e:
            log.debug("Error leyendo meta_curador previo: %s", e)

    # Fase 1: Descarga de Agenda Maestra de canales lineales
    agenda_hoy = construir_agenda_maestra_hoy(fecha_hoy_iso)

    # Fase 1.5: Descarga o lectura de fixtures oficiales del dia (HOY y AYER para contraste riguroso)
    fixtures_dia = descargar_fixtures_dia(fecha_hoy_iso, API_SPORTS_KEY)
    fixtures_ayer = descargar_fixtures_dia(fecha_ayer_iso, API_SPORTS_KEY)
    guardar_equipos_en_catalogo(fixtures_dia)
    indice_fixtures = construir_indice_fixtures(fixtures_dia)

    # Descarga universal de canales Xtream
    canales_xtream = obtener_canales_xtream(fecha_hoy_iso)

    # Indexacion estricta 1 a 1 de canales lineales autorizados
    indice_canales = construir_indice_canales_lineales(canales_xtream)
    eventos_lineales_raw = inyectar_eventos_lineales(agenda_hoy, indice_canales, APP_TIMEZONE)

    # Cerrojo de Fixture Oficial y Aplazados en Canales Lineales:
    eventos_lineales = []
    for ev in eventos_lineales_raw:
        txt_raw = ev.get("texto_original_crudo") or ev.get("titulo") or ""
        if es_texto_no_vivo_o_aplazado(txt_raw) or es_texto_no_vivo_o_aplazado(ev.get("titulo", "")):
            log.info("Descartado evento lineal aplazado/no-vivo: '%s'", txt_raw)
            continue
        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")
        if loc and vis and fixtures_ayer and _duelo_coincide_con_fixtures(loc, vis, fixtures_ayer):
            es_hoy = bool(fixtures_dia and _duelo_coincide_con_fixtures(loc, vis, fixtures_dia))
            if not es_hoy:
                log.info("Descartado evento lineal jugado ayer segun fixture oficial API: '%s vs %s'", loc, vis)
                continue
        eventos_lineales.append(ev)

    # Deteccion Universal de Lista Xtream con contraste exhaustivo contra API oficial
    lista_actualizada = detectar_lista_actualizada_hoy(
        canales_xtream, fecha_hoy_iso, fecha_ayer_iso, agenda_hoy, fixtures_dia, fixtures_ayer
    )
    log.info("Estado de actualizacion de lista Xtream para hoy (%s): %s", fecha_hoy_dd_mm, "ACTUALIZADA" if lista_actualizada else "PENDIENTE")

    if modo_check_xtream and not lista_actualizada:
        log.info("[CHECK XTREAM] La lista Xtream aun contiene duelos del dia anterior. Termina en 2s y espera al siguiente cron.")
        return

    # Ingesta de streams efimeros de Xtream
    eventos_xtream, descartados = procesar_streams_eventos_xtream(
        canales_xtream, fecha_hoy_dd_mm, fecha_hoy_iso, agenda_hoy, tz_prod, lista_actualizada, fixtures_dia, fixtures_ayer
    )

    todos_eventos = list(eventos_lineales) + list(eventos_xtream)

    # Contraste y corroboración con API-Sports (12 Deportes con validación de división y ventana horaria)
    log.info("Fase 1.5: Contraste y corroboracion oficial con API-Sports (12 Deportes)...")
    emparejados_api = 0
    for ev in todos_eventos:
        match = emparejar_con_api_sports(ev, indice_fixtures, fixtures_dia)
        if match:
            emparejados_api += 1
            if match.get("hora_utc"):
                ev["hora_utc"] = match["hora_utc"]
                ev["hora_local_producto"] = _recalcular_hora_local(match["hora_utc"], tz_prod, ev.get("hora_local_producto", ""))
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
            if match.get("logo_torneo") and not es_logo_basura(match["logo_torneo"]) and es_logo_compatible_con_categoria(match["logo_torneo"], ev.get("categoria", "")):
                logo_existente = ev.get("logo_torneo", "")
                if not logo_existente or ("assets/logos/torneos/" not in logo_existente and "flagcdn.com" not in logo_existente):
                    ev["logo_torneo"] = envolver_proxy_wsrv(match["logo_torneo"])
            if match.get("local") and match.get("visitante"):
                ev["equipo_local"] = match["local"]
                ev["equipo_visitante"] = match["visitante"]
                ev["titulo"] = f"{match['local']} vs. {match['visitante']}"
                ev["tipo_evento"] = "duelo"
            if match.get("torneo") and (not ev.get("subtitulo") or ev["subtitulo"].lower() in ["deportes", "futbol", "fútbol", "en vivo"]):
                ev["subtitulo"] = match["torneo"]
                ev["referencia"] = match["torneo"]

    log.info("Eventos contrastados y corroborados con API-Sports: %d/%d", emparejados_api, len(todos_eventos))

    # FASE 2: CURACIÓN SEMÁNTICA Y DEDUPLICACIÓN POR ENTIDAD DEPORTIVA (1 TARJETA = N FUENTES)
    log.info("Fase 2: Fusión temporal, normalización y deduplicación por entidad...")
    eventos_fase2 = post_procesar_y_curar_eventos(todos_eventos)

    # FASE 3: AUDITORÍA Y PERFECCIONAMIENTO DE EVENTOS HUÉRFANOS CON AGY
    log.info("Fase 3: Auditoría y perfeccionamiento de eventos huérfanos con AGY en la VM...")
    eventos_pulidos, descartados_agy = auditar_catalogo_con_agy(eventos_fase2, fecha_hoy_iso)
    descartados.extend(descartados_agy)

    # Re-emparejamiento con API-Sports para duelos resueltos por AGY (ej. MLB, NFL)
    for ev in eventos_pulidos:
        if ev.get("tipo_evento") == "duelo" and not ev.get("contrastado_api"):
            match = emparejar_con_api_sports(ev, indice_fixtures, fixtures_dia)
            if match:
                ev["contrastado_api"] = True
                if match.get("hora_utc"):
                    ev["hora_utc"] = match["hora_utc"]
                    ev["hora_local_producto"] = _recalcular_hora_local(match["hora_utc"], tz_prod, ev.get("hora_local_producto", ""))
                if match.get("logo_local"):
                    ev["logo_local"] = envolver_proxy_wsrv(match["logo_local"])
                if match.get("logo_visitante"):
                    ev["logo_visitante"] = envolver_proxy_wsrv(match["logo_visitante"])
                if match.get("logo_torneo") and not es_logo_basura(match["logo_torneo"]) and es_logo_compatible_con_categoria(match["logo_torneo"], ev.get("categoria", "")):
                    logo_existente = ev.get("logo_torneo", "")
                    if not logo_existente or ("assets/logos/torneos/" not in logo_existente and "flagcdn.com" not in logo_existente):
                        ev["logo_torneo"] = envolver_proxy_wsrv(match["logo_torneo"])

    # FASE 3.6: DEDUPLICACION Y CONSOLIDACION FINAL TRAS AUDITORIA AGY Y API-SPORTS
    log.info("Fase 3.6: Deduplicacion y consolidacion final tras auditoria AGY y API-Sports...")
    eventos_consolidados = post_procesar_y_curar_eventos(eventos_pulidos)

    # FASE 3.8: REVISION FINAL DE AGY (ENRIQUECIMIENTO DE EVENTOS DE CIRCUITO SIN SUBTITULO O GENERICOS)
    candidatos_pendientes = [
        ev for ev in eventos_consolidados
        if (ev.get("tipo_evento") == "circuito" and not (ev.get("subtitulo") or "").strip())
        or any(k == (ev.get("titulo") or "").upper() for k in ["WWE RAW", "WWE SMACKDOWN", "UFC", "BOXEO"])
    ]
    if candidatos_pendientes:
        log.info("Fase 3.8: Revision final de AGY sobre %d tarjetas de circuito/combate sin subtitulo...", len(candidatos_pendientes))
        eventos_consolidados = revisar_y_enriquecer_eventos_incompletos_con_agy(eventos_consolidados, fecha_hoy_iso)
    else:
        log.info("Fase 3.8: Cartelera enriquecida y auditada en Fase 3. Se omite llamada redundante.")

    # FASE 4: GARANTIA GRAFICA PARA ANDROID TV (EventoCard.kt)
    for ev in eventos_consolidados:
        cat = ev.get("categoria", "")
        tor = ev.get("torneo") or ev["titulo"]
        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")
        logo_existente = ev.get("logo_torneo", "")

        if logo_existente and not es_logo_basura(logo_existente) and es_logo_compatible_con_categoria(logo_existente, cat):
            logo_cat = logo_existente
        else:
            logo_cat = resolver_logo_torneo(tor, cat, local=loc, visitante=vis)

        if logo_cat:
            ev["logo_torneo"] = logo_cat
        elif not ev.get("logo_torneo") or es_logo_basura(ev["logo_torneo"]) or not es_logo_compatible_con_categoria(ev.get("logo_torneo", ""), cat):
            ev["logo_torneo"] = ""

        if loc:
            ev["logo_local"] = ev.get("logo_local") or resolver_logo_equipo(loc, cat, tor) or ""
        if vis:
            ev["logo_visitante"] = ev.get("logo_visitante") or resolver_logo_equipo(vis, cat, tor) or ""
        ev["banner"] = ev.get("banner") or ev["logo_torneo"]
        if not es_logo_compatible_con_categoria(ev.get("banner", ""), cat):
            ev["banner"] = ev["logo_torneo", ""] if isinstance(ev.get("logo_torneo"), dict) else ev.get("logo_torneo", "")

    # Validar unicidad estricta de IDs
    ids_vistos = set()
    eventos_verificados = []
    for ev in eventos_consolidados:
        ev_id = ev.get("id")
        if ev_id in ids_vistos:
            nuevo_id = f"{ev_id}_{hashlib.sha1((ev['titulo'] + ev['hora_utc']).encode()).hexdigest()[:6]}"
            ev["id"] = nuevo_id
        ids_vistos.add(ev["id"])
        eventos_verificados.append(ev)

    eventos_verificados.sort(key=lambda x: x.get("hora_utc", ""))

    salida_final = {
        "version": "3.2-curador-tv-universal",
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zona_horaria_producto": APP_TIMEZONE,
        "fecha_local_producto": fecha_hoy_iso,
        "base_media": XTREAM_URL,
        "eventos": eventos_verificados,
        "metricas": {
            "fecha_local_producto": fecha_hoy_iso,
            "total_agenda_maestra": len(agenda_hoy),
            "total_canales_xtream": len(canales_xtream),
            "xtream_procesada_hoy": lista_actualizada,
            "lista_xtream_actualizada_hoy": lista_actualizada,
            "fixtures_api_sports_hoy": len(fixtures_dia),
            "eventos_lineales_inyectados": len(eventos_lineales),
            "eventos_xtream_aprobados": len(eventos_xtream),
            "eventos_contrastados_api": emparejados_api,
            "eventos_descartados_stale": len(descartados),
            "total_eventos_publicados": len(eventos_verificados),
            "hora_actualizacion_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    }

    (REPO_DIR / "eventos_hoy.json").write_text(json.dumps(salida_final, ensure_ascii=False, indent=2), encoding="utf-8")
    (REPO_DIR / "eventos_descartados.json").write_text(json.dumps(descartados, ensure_ascii=False, indent=2), encoding="utf-8")
    META_FILE.write_text(json.dumps(salida_final["metricas"], ensure_ascii=False, indent=2), encoding="utf-8")
    guardar_cache_logos()

    log.info("=== CURACION COMPLETADA CON EXITO ===")
    log.info("Eventos publicados: %d | Descartados: %d | Xtream procesada hoy: %s",
             len(eventos_verificados), len(descartados), lista_actualizada)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Curador Deportivo Universal Multifuente")
    parser.add_argument("--check-xtream", action="store_true", help="Modo chequeo rápido de lista Xtream (salida en 2s si no está actualizada)")
    parser.add_argument("--forzar", action="store_true", help="Fuerza la ejecución ignorando la bandera One & Done")
    args, _ = parser.parse_known_args()

    ejecutar_curacion(modo_check_xtream=args.check_xtream, forzar=args.forzar)