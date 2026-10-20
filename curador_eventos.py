# -*- coding: utf-8 -*-
"""
Curador Deportivo Principal Multifuente con Bóveda Diaria de Inmutabilidad:
1. Bóveda Diaria ('Curar una sola vez y blindar'):
   - Los eventos de hoy que ya fueron curados y certificados íntegramente (título limpio, categoría, subtítulo y ambos escudos compatibles) se conservan inmutables durante todo el día.
   - Los eventos ya descartados hoy no vuelven a evaluarse.
2. Ingesta de Canales Lineales con aislamiento territorial 1 a 1.
3. Detección Universal de Lista Xtream Actualizada y Parada Inteligente ('One & Done').
4. Curación Semántica, Deduplicación, Enriquecimiento Gráfico HD y Auto-Push a GitHub.
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
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from adaptadores.canales_suramerica import PLATAFORMAS_OTT
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
    es_entidad_seleccion_nacional,
    _es_url_logo_equipo_valida,
    _normalizar,
)
from sanitizador_nombres import sanitizar_evento_crudo, es_texto_no_vivo_o_aplazado, es_estado_aplazado_o_repeticion, formatear_titulo_inteligente
from curador_semantico import (
    post_procesar_y_curar_eventos,
    son_mismo_evento,
    DEPORTES_ESTRICTO_DUELO,
    DEPORTES_CIRCUITO,
    TORNEOS_GENERICOS_CIRCUITO,
    compactar_subtitulo_boton_circuito,
)
from auditor_agy import auditar_catalogo_con_agy, revisar_y_enriquecer_eventos_incompletos_con_agy
from conector_api_sports import (
    descargar_fixtures_dia,
    guardar_equipos_en_catalogo,
    construir_indice_fixtures,
    emparejar_con_api_sports,
    envolver_proxy_wsrv,
    extraer_marcadores_division,
    normalizar_clave,
)

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("curador_eventos")

REPO_DIR = Path(__file__).resolve().parent
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
EVENTOS_HOY_FILE = REPO_DIR / "eventos_hoy.json"
DESCARTADOS_FILE = REPO_DIR / "eventos_descartados.json"

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def es_evento_integramente_curado(ev: Dict[str, Any]) -> bool:
    """
    Certificacion Estricta de Calidad para la Boveda Diaria:
    Un evento SOLO se sella como inmutable ('curado_integro': True) si cumple el 100% de los requisitos:
    - Categoria valida y con tilde canonica (nunca 'Otros Deportes' ni vacia).
    - Titulo limpio (sin numeracion '01'/'02' y sin palabras largas en mayusculas sostenidas).
    - Fuentes de reproduccion presentes y SIN transmisiones exclusivas de apps de streaming (Disney+, DGO, etc.).
    - Si es DUELO:
      * Tiene equipo_local y equipo_visitante limpios y distintos.
      * Torneo y paises estrictamente compatibles (sin choques entre liga de un pais y equipos de otro).
      * Tiene AMBOS escudos (logo_local y logo_visitante) distintos, resueltos y compatibles con su deporte.
      * Torneo no generico (no 'Futbol' ni 'Deportes').
    - Si es CIRCUITO:
      * Pertenece a un deporte de DEPORTES_CIRCUITO (jamas Futbol, Baloncesto, Beisbol, NFL o NHL).
      * Su torneo/titulo no es un generico puro ('WTA 1000', 'ATP 1000') si hay nombre especifico.
      * Tiene 'subtitulo' (cancha/sede/sesion) no vacio y distinto del titulo.
    """
    if not ev or not ev.get("fuentes"):
        return False

    # Validar que las fuentes no sean transmisiones exclusivas de plataformas OTT
    fuentes_validas = []
    for f in ev.get("fuentes", []):
        nom = (f.get("nombre") or "").upper()
        c_lin = (f.get("canal_lineal") or "").upper()
        if any(ott in nom or ott in c_lin for ott in PLATAFORMAS_OTT):
            continue
        fuentes_validas.append(f)
    if not fuentes_validas:
        return False

    cat = (ev.get("categoria") or "").strip()
    if not cat or cat in ["Otros Deportes", "Deportes", "Deportes en Vivo", "Futbol", "Beisbol", "Padel", "Natacion"]:
        return False

    tit = (ev.get("titulo") or "").strip()
    tor = (ev.get("torneo") or "").strip()
    txt_raw = (ev.get("texto_original_crudo") or "").strip()
    if not tit or es_texto_no_vivo_o_aplazado(tit) or es_estado_aplazado_o_repeticion(txt_raw):
        return False

    # Rechazar si aun tiene numeracion de dial ('DIRTVISION 01') o palabras largas en mayusculas
    if re.search(r'[A-Za-z]{3,}\s+0?\d{1,2}', tit):
        return False

    tipo = ev.get("tipo_evento", "")
    if tipo == "duelo":
        loc = (ev.get("equipo_local") or "").strip()
        vis = (ev.get("equipo_visitante") or "").strip()
        if not loc or not vis or loc.lower() == vis.lower():
            return False
        if not tor or tor.lower() in ["fútbol", "futbol", "deportes", "deportes en vivo", "baloncesto", "béisbol"]:
            return False

        # Blindaje de compatibilidad Torneo vs Pais
        from conector_api_sports import _son_torneos_y_paises_compatibles
        contexto_ev = f"{tit} {ev.get('subtitulo', '')} {loc} {vis}".strip()
        if not _son_torneos_y_paises_compatibles({"torneo": tor}, {"torneo": contexto_ev}):
            return False

        logo_l = (ev.get("logo_local") or "").strip()
        logo_v = (ev.get("logo_visitante") or "").strip()
        if not logo_l or not logo_v or logo_l == logo_v:
            return False
        if not _es_url_logo_equipo_valida(logo_l, _normalizar(loc), cat):
            return False
        if not _es_url_logo_equipo_valida(logo_v, _normalizar(vis), cat):
            return False
        return True

    if tipo == "circuito":
        if cat in DEPORTES_ESTRICTO_DUELO:
            return False
        if _normalizar(tor) in TORNEOS_GENERICOS_CIRCUITO and _normalizar(tit) in TORNEOS_GENERICOS_CIRCUITO:
            return False
        sub = (ev.get("subtitulo") or "").strip()
        if not sub or sub.lower() == tit.lower() or sub.lower() == tor.lower():
            return False
        logo_t = (ev.get("logo_torneo") or "").strip()
        if logo_t and not es_logo_compatible_con_categoria(logo_t, cat):
            return False
        return True

    return False

def _cargar_boveda_diaria(fecha_hoy_iso: str) -> Tuple[List[Dict[str, Any]], Set[str], List[Dict[str, Any]]]:
    """
    Carga desde disco los eventos ya certificados hoy en 'eventos_hoy.json' y los descartados en 'eventos_descartados.json'.
    Retorna: (eventos_boveda, claves_descartadas_hoy, lista_descartados_hoy)
    """
    boveda: List[Dict[str, Any]] = []
    claves_descartadas: Set[str] = set()
    lista_descartados: List[Dict[str, Any]] = []

    if EVENTOS_HOY_FILE.exists():
        try:
            data = json.loads(EVENTOS_HOY_FILE.read_text(encoding="utf-8"))
            if data.get("fecha_local_producto") == fecha_hoy_iso:
                for ev in data.get("eventos", []):
                    if es_evento_integramente_curado(ev):
                        ev["curado_integro"] = True
                        boveda.append(ev)
                if boveda:
                    log.info("[Bóveda Diaria] Recuperados %d eventos íntegramente curados y blindados de hoy (%s).", len(boveda), fecha_hoy_iso)
        except Exception as e:
            log.warning("No se pudo leer Bóveda Diaria: %s", e)

    if DESCARTADOS_FILE.exists() and META_FILE.exists():
        try:
            meta = json.loads(META_FILE.read_text(encoding="utf-8"))
            if meta.get("fecha_local_producto") == fecha_hoy_iso:
                raw_desc = json.loads(DESCARTADOS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw_desc, list):
                    lista_descartados = raw_desc
                    for d in raw_desc:
                        if isinstance(d, dict):
                            sid = str(d.get("id_xtream") or "")
                            if sid:
                                claves_descartadas.add(f"sid_{sid}")
                            txt_d = d.get("texto_original_crudo") or d.get("nombre") or d.get("titulo") or ""
                            if txt_d:
                                claves_descartadas.add(f"txt_{_normalizar(txt_d)}")
        except Exception:
            pass

    return boveda, claves_descartadas, lista_descartados

def obtener_canales_xtream(fecha_hoy_iso: str) -> List[Dict[str, Any]]:
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
        elif es_de_ayer and not es_de_hoy:
            votos_ayer += 1
        elif es_de_hoy and es_de_ayer:
            votos_hoy += 1

    log.info(
        "Resultado del contraste API universal: %d duelos confirmados HOY | %d duelos confirmados AYER (de %d unicos evaluados)",
        votos_hoy, votos_ayer, len(duelos_vistos)
    )

    if votos_ayer >= 2 and votos_ayer >= votos_hoy:
        return False

    if votos_hoy >= 3 and votos_hoy > (votos_ayer * 2):
        return True

    if votos_ayer > 0 and votos_hoy == 0:
        return False

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
    claves_descartadas_hoy: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    eventos_finales = []
    descartados = []
    mapa_duelos_existentes = {}
    mapa_circuitos_existentes = {}
    descartados_set = claves_descartadas_hoy or set()

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

        if f"sid_{sid}" in descartados_set or f"txt_{_normalizar(nombre)}" in descartados_set:
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
            descartados.append({"nombre": nombre, "id_xtream": sid, "razon": "programa_no_vivo_o_falso_circuito"})
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

        # Conversión de zona horaria si el stream especifica 'ET' (Eastern Time, UTC-4 en octubre -> Bogota es ET - 1h)
        if re.search(r'\bET\b', nombre, re.I):
            h = (h - 1) % 24

        hora_str = f"{h:02d}:{mi:02d}"

        div_stream = extraer_marcadores_division(f"{nombre} {torneo}")
        coincide_agenda = None
        for ev in agenda_hoy:
            if local and visitante and ev.local and ev.visitante:
                div_ev = extraer_marcadores_division(f"{ev.titulo} {ev.torneo} {ev.local} {ev.visitante}")
                if div_stream != div_ev:
                    continue
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

        div_tag = "_".join(sorted(div_stream))
        clave_duelo = f"{normalizar_texto(local)}_vs_{normalizar_texto(visitante)}_{div_tag}_{h // 4}" if local and visitante else ""
        sub_c = parsed.get("subtitulo") or ""
        clave_circuito = f"{normalizar_texto(titulo)}_{normalizar_texto(sub_c)}_{hora_utc[:13]}" if not clave_duelo else ""

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
            "duracion_min": calcular_duracion_evento(cat, titulo),
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

def _reconciliar_con_boveda_diaria(
    eventos_entrantes: List[Dict[str, Any]],
    boveda_hoy: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Cruza los eventos entrantes con la Bóveda Diaria:
    - Si un evento entrante coincide con uno que ya está en la Bóveda Diaria ('curado_integro': True),
      se conserva la versión blindada de la Bóveda y únicamente se le agregan fuentes de reproducción nuevas.
    """
    if not boveda_hoy:
        return eventos_entrantes

    resultado: List[Dict[str, Any]] = [dict(b) for b in boveda_hoy]
    for ev in eventos_entrantes:
        encontrado_en_boveda = False
        for b_ev in resultado:
            mismo_id = ev.get("id") and ev.get("id") == b_ev.get("id")
            mismo_canal_hora = (
                ev.get("hora_utc")
                and ev.get("hora_utc") == b_ev.get("hora_utc")
                and any(
                    f.get("canal_lineal") and f.get("canal_lineal") in {fb.get("canal_lineal") for fb in b_ev.get("fuentes", [])}
                    for f in ev.get("fuentes", [])
                )
            )
            if mismo_id or mismo_canal_hora or son_mismo_evento(ev, b_ev):
                ids_exist = {f.get("id_xtream") for f in b_ev.get("fuentes", [])}
                canales_lin_exist = {f.get("canal_lineal") for f in b_ev.get("fuentes", []) if f.get("canal_lineal")}
                for f in ev.get("fuentes", []):
                    c_lin = f.get("canal_lineal")
                    nom_f = (f.get("nombre") or "").upper()
                    # Blindaje: jamas inyectar fuentes de streaming de apps exclusivas en un evento blindado
                    if any(ott in nom_f or (c_lin and ott in c_lin.upper()) for ott in PLATAFORMAS_OTT):
                        continue
                    if c_lin and canales_lin_exist and c_lin not in canales_lin_exist:
                        continue
                    if f.get("id_xtream") not in ids_exist and len(b_ev["fuentes"]) < 10:
                        b_ev["fuentes"].append(f)
                        ids_exist.add(f.get("id_xtream"))
                encontrado_en_boveda = True
                break
        if not encontrado_en_boveda:
            resultado.append(ev)

    return resultado

def ejecutar_curacion(modo_check_xtream: bool = False, forzar: bool = False, fecha_override: Optional[str] = None):
    tz_prod = obtener_tz(APP_TIMEZONE)
    if fecha_override:
        fecha_hoy_iso = fecha_override
        ahora_prod = datetime.strptime(fecha_override, "%Y-%m-%d").replace(tzinfo=tz_prod)
    else:
        ahora_prod = datetime.now(tz_prod)
        fecha_hoy_iso = ahora_prod.strftime("%Y-%m-%d")
    fecha_ayer_iso = (ahora_prod - timedelta(days=1)).strftime("%Y-%m-%d")
    fecha_hoy_dd_mm = ahora_prod.strftime("%d/%m")

    log.info("=== INICIANDO CURACION DEPORTIVA MULTIFUENTE (%s) ===", fecha_hoy_iso)

    if not forzar and META_FILE.exists():
        try:
            meta_prev = json.loads(META_FILE.read_text(encoding="utf-8"))
            if meta_prev.get("fecha_local_producto") == fecha_hoy_iso and meta_prev.get("xtream_procesada_hoy") is True:
                if modo_check_xtream:
                    log.info("[ONE & DONE] La lista Xtream para hoy (%s) ya fue verificada y procesada previamente. Saliendo de inmediato.", fecha_hoy_iso)
                    return
        except Exception as e:
            log.debug("Error leyendo meta_curador previo: %s", e)

    # Cargar Bóveda Diaria en ejecuciones normales; si se usa --forzar, recalcular todo desde cero con las reglas nuevas
    if not forzar:
        boveda_hoy, claves_descartadas_hoy, descartados_previos = _cargar_boveda_diaria(fecha_hoy_iso)
    else:
        log.info("[MODO FORZAR] Ignorando Bóveda previa para recalcular el 100% de la cartelera con las reglas actuales.")
        boveda_hoy, claves_descartadas_hoy, descartados_previos = [], set(), []

    # Fase 1: Descarga de Agenda Maestra de canales lineales
    agenda_hoy = construir_agenda_maestra_hoy(fecha_hoy_iso)

    # Fase 1.5: Descarga o lectura de fixtures oficiales del dia
    fixtures_dia = descargar_fixtures_dia(fecha_hoy_iso, API_SPORTS_KEY)
    fixtures_ayer = descargar_fixtures_dia(fecha_ayer_iso, API_SPORTS_KEY)
    guardar_equipos_en_catalogo(fixtures_dia)
    indice_fixtures = construir_indice_fixtures(fixtures_dia)

    # Descarga universal de canales Xtream
    canales_xtream = obtener_canales_xtream(fecha_hoy_iso)

    # Indexacion estricta 1 a 1 de canales lineales autorizados
    indice_canales = construir_indice_canales_lineales(canales_xtream)
    eventos_lineales_raw = inyectar_eventos_lineales(agenda_hoy, indice_canales, APP_TIMEZONE)

    eventos_lineales = []
    for ev in eventos_lineales_raw:
        txt_raw = ev.get("texto_original_crudo") or ev.get("titulo") or ""
        if f"txt_{_normalizar(txt_raw)}" in claves_descartadas_hoy:
            continue
        if es_texto_no_vivo_o_aplazado(ev.get("titulo", "")) or es_estado_aplazado_o_repeticion(txt_raw):
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

    lista_actualizada = detectar_lista_actualizada_hoy(
        canales_xtream, fecha_hoy_iso, fecha_ayer_iso, agenda_hoy, fixtures_dia, fixtures_ayer
    )
    log.info("Estado de actualizacion de lista Xtream para hoy (%s): %s", fecha_hoy_dd_mm, "ACTUALIZADA" if lista_actualizada else "PENDIENTE")

    if modo_check_xtream and not lista_actualizada:
        log.info("[CHECK XTREAM] La lista Xtream aun contiene duelos del dia anterior. Termina en 2s y espera al siguiente cron.")
        return

    eventos_xtream, descartados = procesar_streams_eventos_xtream(
        canales_xtream, fecha_hoy_dd_mm, fecha_hoy_iso, agenda_hoy, tz_prod,
        lista_actualizada, fixtures_dia, fixtures_ayer, claves_descartadas_hoy
    )
    descartados_totales = list(descartados_previos) + list(descartados)

    todos_eventos_crudos = list(eventos_lineales) + list(eventos_xtream)

    # Reconciliar con la Bóveda Diaria antes de tocar API-Sports o AGY
    todos_eventos = _reconciliar_con_boveda_diaria(todos_eventos_crudos, boveda_hoy)

    # Contraste y corroboración con API-Sports (solo para eventos que no estén ya sellados en la Bóveda)
    log.info("Fase 1.5: Contraste y corroboracion oficial con API-Sports (12 Deportes)...")
    emparejados_api = 0
    for ev in todos_eventos:
        if ev.get("curado_integro") is True:
            if ev.get("contrastado_api"):
                emparejados_api += 1
            continue
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
            cat_ev = ev.get("categoria", "")
            if match.get("torneo") and (not ev.get("torneo") or ev["torneo"].lower() in ["deportes", "fútbol", "futbol", "deportes en vivo"]):
                ev["torneo"] = formatear_titulo_inteligente(match["torneo"])
            loc_nom = ev.get("equipo_local") or match.get("local") or ""
            loc_es_nat, loc_flag = es_entidad_seleccion_nacional(_normalizar(loc_nom))
            if loc_es_nat and loc_flag:
                ev["logo_local"] = loc_flag
            elif match.get("logo_local"):
                url_l = envolver_proxy_wsrv(match["logo_local"])
                if es_logo_compatible_con_categoria(url_l, cat_ev):
                    ev["logo_local"] = url_l

            vis_nom = ev.get("equipo_visitante") or match.get("visitante") or ""
            vis_es_nat, vis_flag = es_entidad_seleccion_nacional(_normalizar(vis_nom))
            if vis_es_nat and vis_flag:
                ev["logo_visitante"] = vis_flag
            elif match.get("logo_visitante"):
                url_v = envolver_proxy_wsrv(match["logo_visitante"])
                if es_logo_compatible_con_categoria(url_v, cat_ev):
                    ev["logo_visitante"] = url_v
            if match.get("logo_torneo") and not es_logo_basura(match["logo_torneo"]) and es_logo_compatible_con_categoria(match["logo_torneo"], cat_ev):
                logo_existente = ev.get("logo_torneo", "")
                if not logo_existente or ("assets/logos/torneos/" not in logo_existente and "flagcdn.com" not in logo_existente):
                    ev["logo_torneo"] = envolver_proxy_wsrv(match["logo_torneo"])
            if match.get("local") and match.get("visitante"):
                ev["equipo_local"] = formatear_titulo_inteligente(match["local"])
                ev["equipo_visitante"] = formatear_titulo_inteligente(match["visitante"])
                ev["titulo"] = f"{ev['equipo_local']} vs. {ev['equipo_visitante']}"
                ev["tipo_evento"] = "duelo"
            if match.get("torneo") and (not ev.get("subtitulo") or ev["subtitulo"].lower() in ["deportes", "futbol", "fútbol", "en vivo"]):
                ev["subtitulo"] = formatear_titulo_inteligente(match["torneo"])
                ev["referencia"] = ev["subtitulo"]

    log.info("Eventos contrastados y corroborados con API-Sports: %d/%d", emparejados_api, len(todos_eventos))

    # FASE 2: CURACIÓN SEMÁNTICA Y DEDUPLICACIÓN POR ENTIDAD DEPORTIVA
    log.info("Fase 2: Fusión temporal, normalización y deduplicación por entidad...")
    eventos_fase2 = post_procesar_y_curar_eventos(todos_eventos)

    # FASE 3: MONTAJE Y GARANTIA GRAFICA DE LA CARTELERA CANDIDATA (PREPARACION DEL JSON)
    log.info("Fase 3: Montaje grafico, escudos, torneos y formateo inteligente...")
    eventos_candidatos = []
    for ev in eventos_fase2:
        cat = ev.get("categoria", "")
        tipo = ev.get("tipo_evento", "")
        if tipo == "circuito" and cat in DEPORTES_ESTRICTO_DUELO:
            continue

        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")
        if loc:
            es_sel_l, flag_l = es_entidad_seleccion_nacional(_normalizar(loc))
            if es_sel_l and flag_l:
                ev["logo_local"] = flag_l
        if vis:
            es_sel_v, flag_v = es_entidad_seleccion_nacional(_normalizar(vis))
            if es_sel_v and flag_v:
                ev["logo_visitante"] = flag_v

        if ev.get("curado_integro") is True:
            eventos_candidatos.append(ev)
            continue

        tor = formatear_titulo_inteligente(ev.get("torneo") or ev["titulo"])
        tit = formatear_titulo_inteligente(ev["titulo"])
        ev["titulo"] = tit
        ev["torneo"] = tor

        if tipo == "circuito":
            sub_compacto = compactar_subtitulo_boton_circuito(tit, ev.get("subtitulo") or ev.get("ronda") or "")
            if sub_compacto:
                ev["subtitulo"] = sub_compacto
                ev["ronda"] = sub_compacto

        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")
        logo_existente = ev.get("logo_torneo", "")

        if logo_existente and not es_logo_basura(logo_existente) and es_logo_compatible_con_categoria(logo_existente, cat):
            logo_cat = logo_existente
        else:
            logo_cat = resolver_logo_torneo(f"{tor} {tit}".strip(), cat, local=loc, visitante=vis)

        ev["logo_torneo"] = logo_cat or ""

        if loc:
            es_sel_l, flag_l = es_entidad_seleccion_nacional(_normalizar(loc))
            if es_sel_l and flag_l:
                ev["logo_local"] = flag_l
            else:
                loc_actual = ev.get("logo_local") or ""
                if not _es_url_logo_equipo_valida(loc_actual, _normalizar(loc), cat):
                    ev["logo_local"] = resolver_logo_equipo(loc, cat, tor) or ""
        if vis:
            es_sel_v, flag_v = es_entidad_seleccion_nacional(_normalizar(vis))
            if es_sel_v and flag_v:
                ev["logo_visitante"] = flag_v
            else:
                vis_actual = ev.get("logo_visitante") or ""
                if not _es_url_logo_equipo_valida(vis_actual, _normalizar(vis), cat):
                    ev["logo_visitante"] = resolver_logo_equipo(vis, cat, tor) or ""

        ev["banner"] = ev.get("banner") or ev["logo_torneo"]
        if not es_logo_compatible_con_categoria(ev.get("banner", ""), cat):
            ev["banner"] = ev.get("logo_torneo", "")

        eventos_candidatos.append(ev)

    # FASE 4: AUDITORIA Y CONTROL DE CALIDAD FINAL CON AGY (EN ULTIMA INSTANCIA)
    # AGY actua como revisor/auditor sobre el JSON candidato ya armado:
    # Si detecta fallos, revisa, corrige los que tengan problemas y alli si entrega la cartelera definitiva.
    log.info("Fase 4: Auditoria y revision final con AGY sobre el JSON candidato (%d eventos)...", len(eventos_candidatos))
    eventos_auditados, descartados_agy = auditar_catalogo_con_agy(eventos_candidatos, fecha_hoy_iso)
    descartados_totales.extend(descartados_agy)

    # FASE 5: REPASO DE ESCUDOS PARA NOMBRES CORREGIDOS POR AGY Y SELLADO EN BOVEDA
    sellados_boveda = 0
    eventos_finales_limpios = []
    for ev in eventos_auditados:
        cat = ev.get("categoria", "")
        tipo = ev.get("tipo_evento", "")
        if tipo == "circuito" and cat in DEPORTES_ESTRICTO_DUELO:
            continue

        loc = ev.get("equipo_local", "")
        vis = ev.get("equipo_visitante", "")
        tor = ev.get("torneo", "")

        # Re-resolver logo_torneo si AGY pulió el torneo o corrigió la categoría (o el evento no tenía logo)
        tor_actual = ev.get("torneo") or ev.get("titulo", "")
        cat_actual = ev.get("categoria", "")
        logo_tor = ev.get("logo_torneo", "")
        if not logo_tor or not es_logo_compatible_con_categoria(logo_tor, cat_actual):
            nuevo_logo_tor = resolver_logo_torneo(tor_actual, cat_actual, local=loc, visitante=vis)
            if nuevo_logo_tor:
                ev["logo_torneo"] = nuevo_logo_tor
                ev["banner"] = nuevo_logo_tor

        # Si AGY limpio el nombre de algun club que no tenia escudo previamente, resolverlo ahora
        if loc and (not ev.get("logo_local") or not es_logo_compatible_con_categoria(ev.get("logo_local"), cat_actual)):
            es_sel_l, flag_l = es_entidad_seleccion_nacional(_normalizar(loc))
            if es_sel_l and flag_l:
                ev["logo_local"] = flag_l
            else:
                ev["logo_local"] = resolver_logo_equipo(loc, cat_actual, tor) or ""
        if vis and (not ev.get("logo_visitante") or not es_logo_compatible_con_categoria(ev.get("logo_visitante"), cat_actual)):
            es_sel_v, flag_v = es_entidad_seleccion_nacional(_normalizar(vis))
            if es_sel_v and flag_v:
                ev["logo_visitante"] = flag_v
            else:
                ev["logo_visitante"] = resolver_logo_equipo(vis, cat_actual, tor) or ""

        # Evaluar si el evento alcanza el estandar 100% integro para quedar sellado en la Boveda Diaria
        if es_evento_integramente_curado(ev):
            ev["curado_integro"] = True
            sellados_boveda += 1

        eventos_finales_limpios.append(ev)

    # Validar unicidad estricta de IDs
    ids_vistos = set()
    eventos_verificados = []
    for ev in eventos_finales_limpios:
        ev_id = ev.get("id")
        if ev_id in ids_vistos:
            nuevo_id = f"{ev_id}_{hashlib.sha1((ev['titulo'] + ev['hora_utc']).encode()).hexdigest()[:6]}"
            ev["id"] = nuevo_id
        ids_vistos.add(ev["id"])
        eventos_verificados.append(ev)

    eventos_verificados.sort(key=lambda x: x.get("hora_utc", ""))

    salida_final = {
        "version": "3.3-curador-tv-boveda-inmutable",
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
            "eventos_sellados_boveda_inmutable": sellados_boveda,
            "eventos_descartados_stale": len(descartados_totales),
            "total_eventos_publicados": len(eventos_verificados),
            "hora_actualizacion_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    }

    EVENTOS_HOY_FILE.write_text(json.dumps(salida_final, ensure_ascii=False, indent=2), encoding="utf-8")
    DESCARTADOS_FILE.write_text(json.dumps(descartados_totales, ensure_ascii=False, indent=2), encoding="utf-8")
    META_FILE.write_text(json.dumps(salida_final["metricas"], ensure_ascii=False, indent=2), encoding="utf-8")
    guardar_cache_logos()

    if os.environ.get("NO_GIT_PUSH") != "1":
        try:
            archivos_git = [
                "eventos_hoy.json",
                "eventos_descartados.json",
                "meta_curador.json",
                "catalogo_maestro_equipos.json",
                "catalogo_maestro_torneos.json",
                "logos_cache.json",
            ]
            archivos_existentes = [a for a in archivos_git if (REPO_DIR / a).exists()]
            subprocess.run(["git", "add"] + archivos_existentes, cwd=REPO_DIR, check=False, timeout=15)
            commit_res = subprocess.run(
                ["git", "commit", "-m", f"chore: actualizar eventos_hoy ({fecha_hoy_iso}) [skip ci]"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=15
            )
            if commit_res.returncode == 0:
                subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=REPO_DIR, check=False, timeout=25)
                push_res = subprocess.run(["git", "push", "origin", "main"], cwd=REPO_DIR, capture_output=True, text=True, timeout=30)
                if push_res.returncode == 0:
                    log.info("Cambios de eventos_hoy.json sincronizados y subidos a GitHub exitosamente.")
                else:
                    log.warning("No se pudo hacer git push: %s", push_res.stderr)
        except Exception as e:
            log.warning("Error sincronizando con GitHub: %s", e)

    log.info("=== CURACION COMPLETADA CON EXITO ===")
    log.info("Eventos publicados: %d (%d sellados en Bóveda Inmutable) | Descartados: %d | Xtream procesada hoy: %s",
             len(eventos_verificados), sellados_boveda, len(descartados_totales), lista_actualizada)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Curador Deportivo Universal Multifuente")
    parser.add_argument("--check-xtream", action="store_true", help="Modo chequeo rápido de lista Xtream (salida en 2s si no está actualizada)")
    parser.add_argument("--forzar", action="store_true", help="Fuerza la ejecución ignorando la bandera One & Done")
    parser.add_argument("--fecha", type=str, default=None, help="Fuerza la fecha de curación (formato YYYY-MM-DD)")
    args, _ = parser.parse_known_args()

    ejecutar_curacion(modo_check_xtream=args.check_xtream, forzar=args.forzar, fecha_override=args.fecha)