# -*- coding: utf-8 -*-
"""
Curador Deportivo Principal Multifuente:
1. Conecta con el proveedor Xtream (player_api.php o M3U, con cache local).
2. Procesa la Agenda Maestra de directos de hoy mediante los adaptadores web verificados.
3. Inyecta los canales lineales deportivos con sus eventos confirmados de hoy.
4. Filtra y procesa los streams efimeros de eventos con semaforo anti-stale (descarta eventos de ayer).
5. Consolida, deduplica y genera:
   - eventos_hoy.json
   - eventos_descartados.json
   - meta_curador.json
"""
from __future__ import annotations

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

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("curador_eventos")

# Configuracion de entorno
XTREAM_URL = os.environ.get("XTREAM_URL", "http://espartanos.live:8080").rstrip("/")
XTREAM_USER = os.environ.get("XTREAM_USER", "12user1506")
XTREAM_PASS = os.environ.get("XTREAM_PASS", "123456")
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "America/Bogota")
CACHE_CANALES = Path("canales_xtream_cache.json")
M3U_LOCAL = Path(os.environ.get("M3U_PATH", r"C:\Users\Alejandro\Downloads\tv_channels_12user1506_plus.m3u"))

def _crear_contexto_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def obtener_canales_xtream() -> List[Dict[str, Any]]:
    """Descarga los canales en vivo via player_api de Xtream o carga de cache/M3U."""
    if CACHE_CANALES.exists():
        try:
            canales = json.loads(CACHE_CANALES.read_text(encoding="utf-8"))
            log.info("Cargados %d canales desde cache local: %s", len(canales), CACHE_CANALES)
            return canales
        except Exception as e:
            log.warning("No se pudo leer cache: %s", e)

    # 1. Probar player_api.php (ligero y ultra-rapido)
    if XTREAM_URL and XTREAM_USER and XTREAM_PASS:
        api_url = f"{XTREAM_URL}/player_api.php?username={XTREAM_USER}&password={XTREAM_PASS}"
        try:
            req_cats = urllib.request.Request(f"{api_url}&action=get_live_categories", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_cats, timeout=10, context=_crear_contexto_ssl()) as resp:
                cats = json.loads(resp.read().decode("utf-8", errors="ignore"))

            log.info("Xtream API: %d categorias en vivo encontradas", len(cats))
            # Filtrar solo categorias deportivas o de eventos
            deporte_kws = ["EVENT", "DEPORT", "SPORT", "FUTBOL", "LALIGA", "PREMIER", "CHAMPIONS", "CONMEBOL", "NBA", "MLB", "UFC", "WWE", "WIN", "DSPORTS", "BEIN", "CLARO", "FOX", "SKY", "DAZN", "01/10", "30/09"]
            cats_deporte = [c for c in cats if any(k in (c.get("category_name") or "").upper() for k in deporte_kws)]
            log.info("Categorias deportivas seleccionadas para procesar: %d", len(cats_deporte))

            canales = []
            for c in cats_deporte:
                cid = c.get("category_id")
                cname = c.get("category_name")
                s_url = f"{api_url}&action=get_live_streams&category_id={cid}"
                try:
                    req_s = urllib.request.Request(s_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req_s, timeout=10, context=_crear_contexto_ssl()) as resp_s:
                        streams = json.loads(resp_s.read().decode("utf-8", errors="ignore"))
                        for s in streams:
                            s["category_name"] = cname
                            canales.append(s)
                except Exception as e:
                    log.debug("Error leyendo categoria %s: %s", cid, e)

            if canales:
                log.info("Xtream API: %d canales deportivos obtenidos con exito", len(canales))
                try:
                    CACHE_CANALES.write_text(json.dumps(canales, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
                return canales
        except Exception as e:
            log.warning("Fallo al consultar Xtream player_api: %s. Probando fallback M3U.", e)

    # 2. Fallback a M3U local si existe
    if M3U_LOCAL.exists():
        log.info("Parseando M3U local: %s", M3U_LOCAL)
        canales = []
        with open(M3U_LOCAL, "r", encoding="utf-8", errors="ignore") as f:
            nombre = ""
            gid = ""
            for line in f:
                line = line.strip()
                if line.startswith("#EXTINF:"):
                    m_grp = re.search(r'group-title="([^"]*)"', line)
                    gid = m_grp.group(1) if m_grp else ""
                    nombre = line.split(",")[-1].strip()
                elif line and not line.startswith("#"):
                    sid = line.split("/")[-1].split(".")[0]
                    canales.append({
                        "name": nombre,
                        "stream_id": sid,
                        "category_name": gid,
                    })
        log.info("M3U local parseado: %d canales totales", len(canales))
        return canales

    return []

def extraer_fecha_y_hora_stream(nombre: str, grupo: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extrae (fecha_dd_mm, hora_hh_mm) de un titulo de stream o nombre de grupo.
    Ej: '05:00 30/09 | Nuno Borges vs. Djokovic' -> ('30/09', '05:00')
        '30/09 | EVENTOS DIARIOS 1' -> fecha '30/09'
        '11:00 - Amistoso - Lituania vs. Andorra' -> (None, '11:00')
    """
    texto = f"{grupo} {nombre}"
    
    # 1. Extraer hora HH:MM
    m_hora = re.search(r"\b([0-2]?[0-9][:.:][0-5][0-9])\b", nombre)
    hora = m_hora.group(1).replace(".", ":") if m_hora else None
    if hora and len(hora) == 4:
        hora = "0" + hora

    # 2. Extraer fecha numerica DD/MM o DD-MM
    m_fecha = re.search(r"\b([0-3]?[0-9][/-][0-1]?[0-9])\b", texto)
    fecha = m_fecha.group(1).replace("-", "/") if m_fecha else None

    # 3. Extraer fecha textual (ej. 30 DE SEPTIEMBRE, 1 DE OCTUBRE)
    if not fecha:
        meses = {
            "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04",
            "MAYO": "05", "JUNIO": "06", "JULIO": "07", "AGOSTO": "08",
            "SEPTIEMBRE": "09", "OCTUBRE": "10", "NOVIEMBRE": "11", "DICIEMBRE": "12"
        }
        for mes_nom, mes_num in meses.items():
            m_t = re.search(rf"\b([0-3]?[0-9])\s+DE\s+{mes_nom}\b", texto.upper())
            if m_t:
                dia = int(m_t.group(1))
                fecha = f"{dia:02d}/{mes_num}"
                break

    return fecha, hora

def procesar_streams_eventos_xtream(
    canales_xtream: List[Dict[str, Any]],
    fecha_hoy_dd_mm: str,
    fecha_hoy_iso: str,
    agenda_hoy: List[EventoAgenda],
    tz_producto: Any
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Procesa streams efímeros aplicando el Semáforo Anti-Stale:
    - Descarta eventos de fechas pasadas (ej. 30/09 hoy 01/10).
    - Descarta canales sin hora.
    - Empareja con la Agenda Maestra si carece de fecha.
    """
    aceptados = []
    descartados = []

    # Crear conjunto de titulos de hoy en agenda para busqueda rapida
    titulos_agenda = {normalizar_texto(ev.titulo): ev for ev in agenda_hoy}

    for c in canales_xtream:
        nombre = c.get("name") or ""
        grupo = c.get("category_name") or ""
        sid = str(c.get("stream_id") or "")
        u_nom = nombre.upper()
        u_grp = grupo.upper()

        # Filtrar si parece evento
        es_carpeta_evento = any(k in u_grp for k in ["EVENT", "DIRECTO", "PPV", "MLB DEL DIA"])
        tiene_duelo = bool(re.search(r"\b(VS|VERSUS| V )\b", u_nom))
        
        if not (es_carpeta_evento or tiene_duelo):
            continue

        fecha_stream, hora_stream = extraer_fecha_y_hora_stream(nombre, grupo)

        # Regla 1: Sin hora formal -> Descarte
        if not hora_stream:
            descartados.append({
                "stream": nombre,
                "motivo": "sin_hora_valida",
                "grupo": grupo
            })
            continue

        # Regla 2: Semáforo de frescura con fecha explícita
        if fecha_stream:
            dia_str, mes_str = [int(x) for x in fecha_stream.split("/")]
            hoy_dia, hoy_mes = [int(x) for x in fecha_hoy_dd_mm.split("/")]
            if (dia_str, mes_str) < (hoy_dia, hoy_mes):
                descartados.append({
                    "stream": nombre,
                    "motivo": f"evento_caducado_stale ({fecha_stream} anterior a hoy {fecha_hoy_dd_mm})",
                    "grupo": grupo
                })
                continue

        # Regla 3: Si no trae fecha (como en Stejar), contrastar con Agenda
        n_norm = normalizar_texto(nombre)
        coincide_agenda = None
        for tit_ag, ev_ag in titulos_agenda.items():
            if tit_ag in n_norm or (ev_ag.local and normalizar_texto(ev_ag.local) in n_norm and ev_ag.visitante and normalizar_texto(ev_ag.visitante) in n_norm):
                coincide_agenda = ev_ag
                break

        if not fecha_stream and not coincide_agenda:
            # Podria ser un residuo viejo sin fecha
            descartados.append({
                "stream": nombre,
                "motivo": "sin_fecha_y_no_figura_en_agenda_de_hoy",
                "grupo": grupo
            })
            continue

        # Extraer enfrentamiento
        partes = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", nombre, flags=re.I)
        loc = partes[0].strip() if len(partes) >= 2 else (coincide_agenda.local if coincide_agenda else "")
        vis = partes[1].strip() if len(partes) >= 2 else (coincide_agenda.visitante if coincide_agenda else "")
        dep = coincide_agenda.deporte if coincide_agenda else "Otros Deportes"
        torneo = coincide_agenda.torneo if coincide_agenda else (grupo or "Evento en Vivo")

        # Convertir hora_stream a hora_utc
        try:
            h, mi = [int(x) for x in hora_stream.split(":")]
            dt_local = datetime.fromisoformat(f"{fecha_hoy_iso}T{h:02d}:{mi:02d}:00").replace(tzinfo=tz_producto)
            hora_utc = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        logo_tor = resolver_logo_torneo(torneo, dep)
        logo_l = resolver_logo_equipo(loc, dep) if loc else ""
        logo_v = resolver_logo_equipo(vis, dep) if vis else ""

        id_ev = f"xtream_{sid}"
        aceptados.append({
            "id": id_ev,
            "agenda_id": "",
            "titulo": f"{loc} vs {vis}" if loc and vis else nombre,
            "torneo": torneo,
            "categoria": dep,
            "tipo_evento": "duelo" if loc and vis else "circuito",
            "equipo_local": loc,
            "equipo_visitante": vis,
            "subtitulo": f"{torneo} | Evento Directo",
            "referencia": torneo,
            "hora_utc": hora_utc,
            "hora_local_producto": hora_stream,
            "duracion_min": 120,
            "logo_torneo": logo_tor,
            "logo_local": logo_l,
            "logo_visitante": logo_v,
            "banner": logo_tor or logo_l,
            "tier": 2,
            "origen": "xtream_evento",
            "origenes": ["xtream_evento"],
            "estado": "confirmado",
            "estado_evento": "confirmado",
            "confianza": "alta" if coincide_agenda else "media",
            "puntuacion_confianza": 0.9 if coincide_agenda else 0.75,
            "fuentes": [{"nombre": nombre, "id_xtream": sid}],
        })

    log.info("Streams de eventos Xtream: %d aceptados, %d descartados", len(aceptados), len(descartados))
    return aceptados, descartados

def ejecutar_curacion():
    """Punto de entrada principal del curador."""
    tz_prod = obtener_tz(APP_TIMEZONE)
    ahora_prod = datetime.now(tz_prod)
    fecha_hoy_iso = ahora_prod.strftime("%Y-%m-%d")
    fecha_hoy_dd_mm = ahora_prod.strftime("%d/%m")
    log.info("=== INICIANDO CURACION DEPORTIVA MULTIFUENTE (%s) ===", fecha_hoy_iso)

    # 1. Construir Agenda Maestra Multifuente
    agenda_hoy = construir_agenda_maestra_hoy(fecha_hoy_iso)

    # 2. Descargar o cargar canales Xtream
    canales_xtream = obtener_canales_xtream()

    # 3. Inyectar eventos de canales lineales fijos
    indice_canales = construir_indice_canales_lineales(canales_xtream)
    eventos_lineales = inyectar_eventos_lineales(agenda_hoy, indice_canales, APP_TIMEZONE)

    # 4. Procesar streams efímeros de eventos Xtream (con semáforo de frescura)
    eventos_xtream, descartados = procesar_streams_eventos_xtream(
        canales_xtream, fecha_hoy_dd_mm, fecha_hoy_iso, agenda_hoy, tz_prod
    )

    # 5. Fusionar y deduplicar todos los eventos
    todos_eventos = list(eventos_lineales)
    for ex in eventos_xtream:
        # Verificar si ya existe en lineales
        tit_ex = normalizar_texto(ex["titulo"])
        fusionado = False
        for el in todos_eventos:
            if tit_ex == normalizar_texto(el["titulo"]) and el["hora_local_producto"] == ex["hora_local_producto"]:
                # Sumar las fuentes
                for f in ex["fuentes"]:
                    if f["id_xtream"] not in [x["id_xtream"] for x in el["fuentes"]]:
                        el["fuentes"].append(f)
                fusionado = True
                break
        if not fusionado:
            todos_eventos.append(ex)

    # Ordenar por hora UTC
    todos_eventos.sort(key=lambda x: x.get("hora_utc", ""))

    # 6. Escribir salidas canónicas
    salida_final = {
        "version": "2.0-universal",
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zona_horaria_producto": APP_TIMEZONE,
        "fecha_local_producto": fecha_hoy_iso,
        "base_media": XTREAM_URL,
        "eventos": todos_eventos,
        "metricas": {
            "total_agenda_maestra": len(agenda_hoy),
            "total_canales_xtream": len(canales_xtream),
            "eventos_lineales_inyectados": len(eventos_lineales),
            "eventos_xtream_aprobados": len(eventos_xtream),
            "eventos_descartados_stale": len(descartados),
            "total_eventos_publicados": len(todos_eventos),
        }
    }

    Path("eventos_hoy.json").write_text(json.dumps(salida_final, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("eventos_descartados.json").write_text(json.dumps(descartados, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("meta_curador.json").write_text(json.dumps(salida_final["metricas"], ensure_ascii=False, indent=2), encoding="utf-8")
    guardar_cache_logos()

    log.info("=== CURACION COMPLETADA CON EXITO ===")
    log.info("Eventos publicados: %d | Descartados por stale/incompletos: %d", len(todos_eventos), len(descartados))

if __name__ == "__main__":
    ejecutar_curacion()
