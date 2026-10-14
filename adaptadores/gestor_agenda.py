# -*- coding: utf-8 -*-
"""
Gestor de la Agenda Maestra de Directos de Hoy:
Orquesta adaptadores fijos y verificados para televisión:
- Canales Suramérica (Win Sports CO, Caracol/RCN CO, y familia ESPN/DSports/TyC/TNT de Argentina)
- Canales de España verificados (Eurosport 1, Eurosport 2, Teledeporte)
- Canales de Motor autorizados (DAZN F1, Sky Sports F1)
Permite sumar opciones de distintas regiones (ej. Teledeporte + RCN + ESPN AR en Ciclismo),
pero impide mezclar dos canales distintos de la misma cadena en un mismo evento.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from .canales_suramerica import obtener_directos_suramerica
from .canales_ciegos_agy import obtener_directos_canales_ciegos_agy
from .espana import (
    obtener_dazn_f1_directos,
    obtener_eurosport_directos,
    obtener_sky_sports_uk_directos,
    obtener_teledeporte_directos,
)
from .modelos import EventoAgenda, normalizar_texto

log = logging.getLogger("gestor_agenda")

FAMILIAS_CANALES_EXCLUYENTES = [
    {"WIN SPORTS+", "WIN SPORTS"},
    {"ESPN PREMIUM ARGENTINA", "ESPN AR", "ESPN 2 AR", "ESPN 3 AR", "ESPN 4", "ESPN 5", "ESPN 6", "ESPN 7", "ESPN", "ESPN 2", "ESPN 3"},
    {"DSPORTS AR", "DSPORTS CO", "DSPORTS", "DSPORTS 2", "DSPORTS +"},
    {"EUROSPORT 1", "EUROSPORT 2"},
]

def _colisiona_misma_cadena(canal_nuevo: str, canales_actuales: List[str]) -> bool:
    c_up = canal_nuevo.upper().strip()
    actuales_up = {c.upper().strip() for c in canales_actuales}
    for fam in FAMILIAS_CANALES_EXCLUYENTES:
        if c_up in fam and (actuales_up & fam):
            return True
    return False

def construir_agenda_maestra_hoy(fecha_hoy_iso: str) -> List[EventoAgenda]:
    """
    Ejecuta en paralelo los adaptadores autorizados de canales lineales para hoy.
    """
    tareas = {
        "canales_ciegos_agy": lambda: obtener_directos_canales_ciegos_agy(fecha_hoy_iso),
        "suramerica": lambda: obtener_directos_suramerica(fecha_hoy_iso),
        "dazn_f1": lambda: obtener_dazn_f1_directos(fecha_hoy_iso),
        "sky_sports_f1": lambda: obtener_sky_sports_uk_directos(fecha_hoy_iso),
        "eurosport": lambda: obtener_eurosport_directos(fecha_hoy_iso),
        "teledeporte": lambda: obtener_teledeporte_directos(fecha_hoy_iso),
    }

    # Orden de precedencia para canales de la misma familia: primero canales_ciegos_agy y suramerica AR
    orden_adaptadores = ["canales_ciegos_agy", "suramerica", "teledeporte", "eurosport", "dazn_f1", "sky_sports_f1"]
    resultados_por_adaptador = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {executor.submit(fn): nombre for nombre, fn in tareas.items()}
        for fut in as_completed(futuros):
            nombre = futuros[fut]
            try:
                res = fut.result()
                log.info("Adaptador '%s': %d eventos obtenidos", nombre, len(res))
                resultados_por_adaptador[nombre] = res
            except Exception as e:
                log.error("Fallo critico en adaptador '%s': %s", nombre, e)
                resultados_por_adaptador[nombre] = []

    eventos_crudos: List[EventoAgenda] = []
    for nombre in orden_adaptadores:
        eventos_crudos.extend(resultados_por_adaptador.get(nombre, []))

    vistos = set()
    eventos_dedup: List[EventoAgenda] = []

    for ev in eventos_crudos:
        clave = (normalizar_texto(ev.titulo), ev.hora_utc[:16])
        if clave not in vistos:
            vistos.add(clave)
            eventos_dedup.append(ev)
        else:
            for existente in eventos_dedup:
                if (normalizar_texto(existente.titulo), existente.hora_utc[:16]) == clave:
                    for c in ev.canales:
                        if c not in existente.canales and not _colisiona_misma_cadena(c, existente.canales):
                            existente.canales.append(c)
                    break

    log.info("Agenda Maestra consolidada para hoy (%s): %d eventos unicos", fecha_hoy_iso, len(eventos_dedup))
    return eventos_dedup