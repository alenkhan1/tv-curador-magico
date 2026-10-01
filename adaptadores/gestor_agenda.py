# -*- coding: utf-8 -*-
"""
Gestor de Agenda Maestra Multifuente.
Ejecuta concurrentemente todos los adaptadores verificados y consolida los eventos de hoy
en UTC, deduplicando por participantes y franja horaria.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo

from .modelos import EventoAgenda
from .espana import obtener_movistar_directos, obtener_eurosport_directos, obtener_dazn_espana_directos
from .red_futbolenvivo import extraer_multideporte_argentina, extraer_partidos_canal
from .mitv import obtener_directos_win_sports
from .tvpassport import obtener_directos_usa
from .skysports import obtener_directos_sky_sports

log = logging.getLogger("gestor_agenda")

def construir_agenda_maestra_hoy(fecha_hoy_iso: str) -> List[EventoAgenda]:
    """Descarga en paralelo todas las fuentes verificadas y consolida la agenda deportiva de hoy."""
    log.info("Iniciando construccion de Agenda Maestra deportiva para fecha: %s", fecha_hoy_iso)
    
    tareas = [
        # España
        ("movistar", lambda: obtener_movistar_directos(fecha_hoy_iso)),
        ("eurosport", lambda: obtener_eurosport_directos(fecha_hoy_iso)),
        ("dazn_espana", lambda: obtener_dazn_espana_directos(fecha_hoy_iso)),
        # Colombia
        ("win_sports", lambda: obtener_directos_win_sports(fecha_hoy_iso)),
        # Multideporte Sudamerica
        ("tenis_ar", lambda: extraer_multideporte_argentina("tenis", "Tenis", fecha_hoy_iso)),
        ("baloncesto_ar", lambda: extraer_multideporte_argentina("baloncesto", "Baloncesto", fecha_hoy_iso)),
        ("beisbol_ar", lambda: extraer_multideporte_argentina("beisbol", "Béisbol", fecha_hoy_iso)),
        ("futsal_ar", lambda: extraer_multideporte_argentina("futbol-sala", "Fútbol Sala", fecha_hoy_iso)),
        # Canales ESPN Sudamerica
        ("espn2_ar", lambda: extraer_partidos_canal("https://www.futbolenvivoargentina.com/canal/espn2-argentina", "ESPN 2", "America/Argentina/Buenos_Aires", fecha_hoy_iso)),
        ("espn_col", lambda: extraer_partidos_canal("https://www.futbolenvivocolombia.com/canal/espn-colombia", "ESPN", "America/Bogota", fecha_hoy_iso)),
        # USA y UK
        ("tvpassport_usa", lambda: obtener_directos_usa(fecha_hoy_iso)),
        ("sky_sports_uk", lambda: obtener_directos_sky_sports(fecha_hoy_iso)),
    ]

    eventos_totales: List[EventoAgenda] = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futuros = {executor.submit(fn): nombre for nombre, fn in tareas}
        for fut in as_completed(futuros):
            nom = futuros[fut]
            try:
                res = fut.result()
                if res:
                    eventos_totales.extend(res)
            except Exception as e:
                log.warning("Fallo en adaptador %s: %s", nom, e)

    log.info("Total eventos crudos descargados de todas las fuentes: %d", len(eventos_totales))

    # Deduplicacion y fusion de canales
    dedup: Dict[str, EventoAgenda] = {}
    for ev in eventos_totales:
        k = ev.clave_deduplicacion()
        if k not in dedup:
            dedup[k] = ev
        else:
            # Fusionar canales donde se emite
            existente = dedup[k]
            for c in ev.canales:
                if c not in existente.canales:
                    existente.canales.append(c)

    agenda_consolidada = sorted(dedup.values(), key=lambda x: x.hora_utc)
    log.info("Agenda Maestra final deduplicada: %d eventos unicos confirmados", len(agenda_consolidada))
    return agenda_consolidada
