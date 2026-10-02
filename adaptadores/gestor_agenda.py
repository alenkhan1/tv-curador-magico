# -*- coding: utf-8 -*-
"""
Gestor de la Agenda Maestra de Directos de Hoy:
Orquesta adaptadores fijos y verificados:
- Canales Suramérica (Win Sports, ESPN, DSports, TyC Sports, TNT Sports)
- Canales España (Movistar Plus, Eurosport 1 y 2, Teledeporte, DAZN España)
- Canales UK (Sky Sports Main Event, Football, F1, Golf vía wheresthematch)
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from .canales_suramerica import obtener_directos_suramerica
from .espana import (
    obtener_movistar_directos,
    obtener_eurosport_directos,
    obtener_teledeporte_directos,
    obtener_sky_sports_uk_directos,
    obtener_dazn_espana_directos,
)
from .modelos import EventoAgenda, normalizar_texto

log = logging.getLogger("gestor_agenda")

def construir_agenda_maestra_hoy(fecha_hoy_iso: str) -> List[EventoAgenda]:
    """
    Ejecuta en paralelo los adaptadores autorizados de canales lineales para hoy.
    """
    tareas = {
        "suramerica": lambda: obtener_directos_suramerica(fecha_hoy_iso),
        "movistar": lambda: obtener_movistar_directos(fecha_hoy_iso),
        "eurosport": lambda: obtener_eurosport_directos(fecha_hoy_iso),
        "teledeporte": lambda: obtener_teledeporte_directos(fecha_hoy_iso),
        "sky_sports": lambda: obtener_sky_sports_uk_directos(fecha_hoy_iso),
        "dazn_es": lambda: obtener_dazn_espana_directos(fecha_hoy_iso),
    }

    eventos_crudos: List[EventoAgenda] = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {executor.submit(fn): nombre for nombre, fn in tareas.items()}
        for fut in as_completed(futuros):
            nombre = futuros[fut]
            try:
                res = fut.result()
                log.info("Adaptador '%s': %d eventos obtenidos", nombre, len(res))
                eventos_crudos.extend(res)
            except Exception as e:
                log.error("Fallo critico en adaptador '%s': %s", nombre, e)

    # Deduplicación por (titulo normalizado, hora_utc)
    vistos = set()
    eventos_dedup: List[EventoAgenda] = []

    for ev in eventos_crudos:
        clave = (normalizar_texto(ev.titulo), ev.hora_utc[:16])
        if clave not in vistos:
            vistos.add(clave)
            eventos_dedup.append(ev)
        else:
            # Si ya existía, enriquecer los canales del evento existente
            for existente in eventos_dedup:
                if (normalizar_texto(existente.titulo), existente.hora_utc[:16]) == clave:
                    for c in ev.canales:
                        if c not in existente.canales:
                            existente.canales.append(c)
                    break

    log.info("Agenda Maestra consolidada para hoy (%s): %d eventos unicos", fecha_hoy_iso, len(eventos_dedup))
    return eventos_dedup
