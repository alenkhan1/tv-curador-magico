# -*- coding: utf-8 -*-
"""
Inyector de Canales Lineales Deportivos Clave.
Sustituye la descarga pesada de XMLTV EPG por mapeo determinista de canales
y consulta supervisada de directos reales con Gemini.

Canales Objetivo:
- España: Eurosport 1/2, Teledeporte, DAZN F1, DAZN 1, DAZN LaLiga (principal), #Vamos, Movistar Deportes (España).
- Colombia / LatAm: Win Sports, Win Sports+, DSports (1, 2, +), TyC Sports.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from agente_deportivo_ia import consultar_directos_canales_lineales
from resolvedor_logos import resolver_logo_torneo, resolver_logo_equipo

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("inyector_canales_lineales")

ARCHIVO_SALIDA = Path(os.environ.get("ARCHIVO_SALIDA", "eventos_hoy.json"))
ARCHIVO_CACHE_XTREAM = Path(os.environ.get("ARCHIVO_CACHE_XTREAM", "canales_xtream_cache.json"))

# Reglas de mapeo quirúrgico para agrupar todas las opciones (Opc.1, Opc.2, HD, FHD)
MAPAS_CANALES: Dict[str, Dict[str, Any]] = {
    "WIN SPORTS+": {
        "patrones": [r"WIN SPORTS\s*\+"],
        "vetos": [],
        "deporte_defecto": "Fútbol",
        "torneo_defecto": "Liga BetPlay Dimayor",
    },
    "WIN SPORTS": {
        "patrones": [r"WIN SPORTS(?![\s\+]*\+)"],
        "vetos": [r"WIN SPORTS\s*\+"],
        "deporte_defecto": "Fútbol",
        "torneo_defecto": "Torneo BetPlay Dimayor",
    },
    "EUROSPORT 1": {
        "patrones": [r"EUROSPORT\s*1\b", r"\bES\s*\|\s*EUROSPORT\s*1\b"],
        "vetos": [r"\b(FR|DE|GER|FRANCE|ALEMANIA|FRANCIA|POLONIA|PL|IT|PORTUGAL|PT)\b"],
        "deporte_defecto": "Ciclismo",
        "torneo_defecto": "UCI WorldTour",
    },
    "EUROSPORT 2": {
        "patrones": [r"EUROSPORT\s*2\b", r"\bES\s*\|\s*EUROSPORT\s*2\b"],
        "vetos": [r"\b(FR|DE|GER|FRANCE|ALEMANIA|FRANCIA|POLONIA|PL|IT|PORTUGAL|PT)\b"],
        "deporte_defecto": "Tenis",
        "torneo_defecto": "ATP Tour",
    },
    "TELEDEPORTE": {
        "patrones": [r"TELEDEPORTE\b", r"\bTDP\b"],
        "vetos": [],
        "deporte_defecto": "Otros Deportes",
        "torneo_defecto": "Teledeporte Directo",
    },
    "DAZN F1": {
        "patrones": [r"DAZN\s*F1\b", r"DAZN\s*FORMULA\s*1\b"],
        "vetos": [],
        "deporte_defecto": "Motor",
        "torneo_defecto": "Fórmula 1",
    },
    "DAZN 1": {
        "patrones": [r"DAZN\s*1\b"],
        "vetos": [r"DAZN\s*[2-9]\b", r"DAZN\s*LALIGA", r"DAZN\s*F1"],
        "deporte_defecto": "Premier League",
        "torneo_defecto": "Premier League",
    },
    "DAZN LALIGA": {
        "patrones": [r"DAZN\s+LA\s*LIGA\b", r"EVENTS\s*\d+\s*:\s*DAZN\s+LA\s*LIGA(?!\s*[2-9])\b"],
        "vetos": [r"DAZN\s+LA\s*LIGA\s*[2-9]\b", r"DAZN\s*LALIGA\s*[2-9]\b"],
        "deporte_defecto": "Fútbol",
        "torneo_defecto": "LaLiga EA Sports",
    },
    "MOVISTAR #VAMOS": {
        "patrones": [r"#VAMOS\b", r"M\+\s*VAMOS\b", r"MOVISTAR\s*VAMOS\b"],
        "vetos": [],
        "deporte_defecto": "Fútbol",
        "torneo_defecto": "LaLiga Hypermotion",
    },
    "MOVISTAR DEPORTES": {
        "patrones": [r"M\.\s*DEPORTES\b", r"M\+\s*DEPORTES\b", r"DEPORTES\s+POR\s+M\+\b"],
        "vetos": [r"\bPERU\b", r"\bPERÚ\b", r"\bPE\b", r"DEPORTES\s+PERU"],
        "deporte_defecto": "Baloncesto",
        "torneo_defecto": "Liga Endesa ACB",
    },
    "DSPORTS": {
        "patrones": [r"DSPORTS\s*1\b", r"DSPORTS\s*\+\b", r"DIRECTV\s*SPORTS\s*1\b"],
        "vetos": [],
        "deporte_defecto": "Fútbol",
        "torneo_defecto": "Copa Sudamericana",
    },
    "TYC SPORTS": {
        "patrones": [r"TYC\s*SPORTS\b"],
        "vetos": [],
        "deporte_defecto": "Fútbol",
        "torneo_defecto": "Copa Argentina",
    },
}

def agrupar_fuentes_canales(canales_xtream: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """Clasifica los streams disponibles de la lista Xtream bajo cada canal clave, agrupando opciones."""
    grupos: Dict[str, List[Dict[str, str]]] = {k: [] for k in MAPAS_CANALES}
    
    for canal in canales_xtream:
        nombre = str(canal.get("name") or "").strip()
        grupo_nombre = str(canal.get("group_name") or canal.get("category_name") or "").strip()
        texto_completo = f"{grupo_nombre} {nombre}".upper()
        sid = str(canal.get("stream_id") or canal.get("id_xtream") or "").strip()
        if not sid or not nombre:
            continue
            
        for clave_canal, config in MAPAS_CANALES.items():
            # Comprobar vetos
            if any(re.search(v, texto_completo, re.I) for v in config["vetos"]):
                continue
            # Comprobar inclusión
            if any(re.search(p, nombre, re.I) for p in config["patrones"]):
                grupos[clave_canal].append({
                    "nombre": nombre,
                    "id_xtream": sid,
                })
                break
                
    return grupos

def ejecutar_inyeccion_lineal() -> None:
    """Ejecuta el proceso de inyección de canales lineales."""
    tz = ZoneInfo("America/Bogota")
    ahora_local = datetime.now(tz)
    fecha_hoy = ahora_local.date().isoformat()
    
    log.info("=== Inyector Canales Lineales v1.0 | Fecha: %s ===", fecha_hoy)
    
    # 1. Cargar canales Xtream cacheados
    canales_xtream: List[Dict[str, Any]] = []
    if ARCHIVO_CACHE_XTREAM.exists():
        try:
            canales_xtream = json.loads(ARCHIVO_CACHE_XTREAM.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("No se pudo leer cache Xtream: %s", e)
            
    if not canales_xtream:
        log.warning("No hay canales Xtream para mapear.")
        return
        
    fuentes_por_canal = agrupar_fuentes_canales(canales_xtream)
    for canal, fuentes in fuentes_por_canal.items():
        log.info("Canal [%s] -> %d fuentes emparejadas", canal, len(fuentes))
        
    # 2. Consultar con Gemini los directos reales de hoy
    canales_activos = [c for c, f in fuentes_por_canal.items() if len(f) > 0]
    eventos_confirmados = consultar_directos_canales_lineales(fecha_hoy, canales_activos)
    
    if not eventos_confirmados:
        log.info("No hay emisiones en vivo adicionales confirmadas por IA para canales lineales.")
        return
        
    # 3. Cargar eventos_hoy.json existente
    datos_actuales: Dict[str, Any] = {"eventos": []}
    if ARCHIVO_SALIDA.exists():
        try:
            datos_actuales = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("No se pudo leer eventos_hoy.json: %s", e)
            
    eventos_existentes: List[Dict[str, Any]] = datos_actuales.get("eventos", [])
    mapa_existentes = {e.get("id"): e for e in eventos_existentes if e.get("id")}
    nuevos_creados = 0
    
    for ev in eventos_confirmados:
        canal_obj = ev.get("canal_objetivo", "").upper().strip()
        # Buscar mapeo de canal
        clave_mapeada = None
        for k in MAPAS_CANALES:
            if k in canal_obj or canal_obj in k:
                clave_mapeada = k
                break
                
        if not clave_mapeada or not fuentes_por_canal.get(clave_mapeada):
            continue
            
        fuentes_disponibles = fuentes_por_canal[clave_mapeada]
        tipo = "duelo" if ev.get("tipo_evento") == "duelo" else "circuito"
        local = str(ev.get("equipo_local") or "").strip()
        visitante = str(ev.get("equipo_visitante") or "").strip()
        torneo = str(ev.get("torneo") or MAPAS_CANALES[clave_mapeada]["torneo_defecto"]).strip()
        categoria = str(ev.get("categoria") or MAPAS_CANALES[clave_mapeada]["deporte_defecto"]).strip()
        referencia = str(ev.get("referencia") or "").strip()
        
        titulo = f"{local} vs {visitante}" if tipo == "duelo" and local and visitante else torneo
        hora_str = ev.get("hora_local_aprox") or ahora_local.strftime("%H:%M")
        
        try:
            h, m = [int(x) for x in hora_str.split(":")[:2]]
            dt_local = ahora_local.replace(hour=h, minute=m, second=0, microsecond=0)
        except Exception:
            dt_local = ahora_local
            
        hora_utc_iso = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        
        logo_torneo = resolver_logo_torneo(torneo, categoria)
        logo_loc = resolver_logo_equipo(local, categoria) if tipo == "duelo" else ""
        logo_vis = resolver_logo_equipo(visitante, categoria) if tipo == "duelo" else ""
        
        ident = f"lineal_{clave_mapeada.lower().replace(' ', '_')}_{dt_local.strftime('%H%M')}"
        
        if ident in mapa_existentes:
            # Fusionar fuentes
            for f in fuentes_disponibles:
                if f["id_xtream"] not in [x.get("id_xtream") for x in mapa_existentes[ident].get("fuentes", [])]:
                    mapa_existentes[ident].setdefault("fuentes", []).append(f)
        else:
            nuevo_evento = {
                "id": ident,
                "agenda_id": "",
                "titulo": titulo,
                "torneo": torneo,
                "categoria": categoria,
                "tipo_evento": tipo,
                "equipo_local": local if tipo == "duelo" else "",
                "equipo_visitante": visitante if tipo == "duelo" else "",
                "subtitulo": referencia,
                "referencia": referencia,
                "hora_utc": hora_utc_iso,
                "hora_local_producto": dt_local.strftime("%H:%M"),
                "duracion_min": ev.get("duracion_min", 120),
                "logo_torneo": logo_torneo,
                "logo_local": logo_loc,
                "logo_visitante": logo_vis,
                "banner": "",
                "tier": 2,
                "origen": f"lineal_{clave_mapeada.lower()}",
                "origenes": [f"lineal_{clave_mapeada.lower()}"],
                "estado": "confirmado",
                "estado_evento": "programado",
                "confianza": "alta",
                "puntuacion_confianza": 90,
                "fuentes": list(fuentes_disponibles),
            }
            mapa_existentes[ident] = nuevo_evento
            nuevos_creados += 1
            
    # Filtrar estrictamente cualquier evento que no tenga fuentes
    eventos_filtrados = [e for e in mapa_existentes.values() if e.get("fuentes") and len(e["fuentes"]) > 0]
    eventos_ordenados = sorted(eventos_filtrados, key=lambda e: e.get("hora_utc", ""))
    
    datos_actuales["eventos"] = eventos_ordenados
    datos_actuales["generado_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    ARCHIVO_SALIDA.write_text(json.dumps(datos_actuales, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Inyección finalizada: %d eventos totales (%d nuevos creados)", len(eventos_ordenados), nuevos_creados)

if __name__ == "__main__":
    ejecutar_inyeccion_lineal()
