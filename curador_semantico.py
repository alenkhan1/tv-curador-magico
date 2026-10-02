# -*- coding: utf-8 -*-
"""
Post-Procesador y Normalizador Semántico Deportivo:
Ejecuta la Fase 2 de Curación Inteligente sobre todos los eventos del catálogo:
1. Deduce la categoría real de eventos huérfanos ('Asobal' -> 'Balonmano', 'Shenzhen' -> 'Snooker', 'ACB' -> 'Baloncesto').
2. Desmonta y limpia prefijos de jornadas ('Jornada 4: Ciudad Real - Ademar León' -> 'Ciudad Real vs Ademar León').
3. Genera subtítulos deportivos limpios y elegantes (Cero 'En vivo por MOVISTAR...').
4. Resuelve escudos, logos de competición específicos (Nations League != Conmebol) y BANDERAS OFICIALES para todas las selecciones.
5. Filtra duelos de selecciones incompletos o huérfanos.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any, Dict, List, Tuple

from resolvedor_logos import (
    resolver_logo_equipo,
    resolver_logo_torneo,
    BANDERAS_PAISES,
    _normalizar,
)

log = logging.getLogger("curador_semantico")

# Diccionario de desambiguación deportiva para categorías huérfanas
DEDUCCIONES_DEPORTE = [
    (re.compile(r"\b(ASOBAL|BALONMANO|HANDBALL|EHF)\b", re.I), "Balonmano"),
    (re.compile(r"\b(ACB|LIGA ENDESA|EUROLEAGUE|EUROLIGA|NBA|WNBA|BASKET|BALONCESTO)\b", re.I), "Baloncesto"),
    (re.compile(r"\b(SNOOKER|BILLAR|SHENZHEN OPEN)\b", re.I), "Snooker"),
    (re.compile(r"\b(CICLISMO|CYCLING|VUELTA|GIRO|TOUR DE FRANCE)\b", re.I), "Ciclismo"),
    (re.compile(r"\b(PADEL|PÁDEL|PREMIER PADEL|FIP)\b", re.I), "Pádel"),
    (re.compile(r"\b(ATP|WTA|TENIS|TENNIS|CHALLENGER|ROLAND GARROS|WIMBLEDON|US OPEN)\b", re.I), "Tenis"),
    (re.compile(r"\b(F1|FORMULA 1|FÓRMULA 1|MOTOGP|MOTO GP|WRC|RALLY|NASCAR|INDYCAR)\b", re.I), "Motor"),
    (re.compile(r"\b(UFC|BOXEO|BOXING|COMBATE|MMA|BELLATOR|BKFC)\b", re.I), "Combate"),
    (re.compile(r"\b(MLB|BEISBOL|BÉISBOL)\b", re.I), "Béisbol"),
    (re.compile(r"\b(NFL|FUTBOL AMERICANO|NCAA FOOTBALL)\b", re.I), "Fútbol Americano"),
    (re.compile(r"\b(GOLF|PGA|DP WORLD|LIV GOLF)\b", re.I), "Golf"),
    (re.compile(r"\b(LALIGA|PREMIER LEAGUE|SERIE A|BUNDESLIGA|LIGUE 1|NATIONS LEAGUE|LIBERTADORES|SUDAMERICANA|BETPLAY|FUTBOL|FÚTBOL)\b", re.I), "Fútbol"),
]

# Prefijos ruidosos que ensucian los títulos
PREFIJOS_RONDA = re.compile(
    r"^(?:Jornada\s+\d+|Fase\s+de\s+grupos|Cuartos\s+de\s+final.*?|Semifinal.*?|Final|Round\s+\d+|2ª\s+Ronda.*?|1ª\s+Ronda.*?):\s*",
    re.I
)

def limpiar_texto(s: str) -> str:
    """Limpia entidades HTML y dobles espacios."""
    if not s:
        return ""
    desescapado = html_lib.unescape(s)
    return " ".join(desescapado.split()).strip()

def deducir_categoria(titulo: str, torneo: str, cat_actual: str) -> str:
    """Evita que deportes conocidos caigan en 'Otros Deportes'."""
    todo = f"{titulo} {torneo}"
    for patron, dep in DEDUCCIONES_DEPORTE:
        if patron.search(todo):
            return dep
    return cat_actual or "Otros Deportes"

def desmontar_duelo(titulo: str, categoria: str) -> Tuple[str, str, str, str]:
    """
    Desmonta prefijos de jornada y extrae local y visitante limpios.
    Retorna: (titulo_limpio, local, visitante, ronda_extraida)
    """
    tit = limpiar_texto(titulo)
    ronda = ""
    m_ronda = PREFIJOS_RONDA.search(tit)
    if m_ronda:
        ronda = m_ronda.group(0).rstrip(": ").strip()
        tit = tit[m_ronda.end():].strip()

    # Separar duelistas
    partes = re.split(r"\s+(?:vs\.?|v\.?|-)\s+", tit, flags=re.I)
    if len(partes) == 2 and categoria in ["Fútbol", "Baloncesto", "Balonmano", "Pádel", "Tenis", "Béisbol", "Fútbol Americano"]:
        loc = limpiar_texto(partes[0])
        vis = limpiar_texto(partes[1])
        return f"{loc} vs {vis}", loc, vis, ronda
    
    return tit, "", "", ronda

def _minutos_utc(iso_utc: str) -> int:
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return int(dt.timestamp() // 60)
    except Exception:
        return 0

def post_procesar_y_curar_eventos(eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Fase 2: Curación y enriquecimiento de todo el catálogo consolidado con fusión inteligente.
    """
    eventos_pre = []
    for ev in eventos:
        tit_original = limpiar_texto(ev.get("titulo", ""))
        torneo_original = limpiar_texto(ev.get("torneo", ""))
        cat_original = ev.get("categoria", "")

        categoria_real = deducir_categoria(tit_original, torneo_original, cat_original)
        tit_limpio, loc, vis, ronda = desmontar_duelo(tit_original, categoria_real)

        if not loc and ev.get("equipo_local"):
            loc = limpiar_texto(ev.get("equipo_local", ""))
        if not vis and ev.get("equipo_visitante"):
            vis = limpiar_texto(ev.get("equipo_visitante", ""))

        if loc and vis:
            tipo_ev = "duelo"
            tit_limpio = f"{loc} vs {vis}"
        else:
            tipo_ev = ev.get("tipo_evento", "circuito")

        if categoria_real in ["Fútbol", "Baloncesto", "Balonmano"] and tipo_ev == "circuito":
            if len(tit_limpio.split()) <= 2 and not any(k in tit_limpio.upper() for k in ["CUP", "TOUR", "OPEN", "CIRCUITO"]):
                log.info("Descartando evento huérfano de %s: '%s'", categoria_real, tit_limpio)
                continue

        ev["titulo"] = tit_limpio
        ev["torneo"] = torneo_original or tit_limpio
        ev["categoria"] = categoria_real
        ev["tipo_evento"] = tipo_ev
        ev["equipo_local"] = loc
        ev["equipo_visitante"] = vis
        ev["ronda"] = ronda
        eventos_pre.append(ev)

    # Fusión inteligente de eventos idénticos (mismo duelo/título y desfase <= 30 minutos)
    eventos_fusionados = []
    for ev in eventos_pre:
        fusionado = False
        t_ev = _minutos_utc(ev.get("hora_utc", ""))
        clave_ev = _normalizar(ev["titulo"])

        for exist in eventos_fusionados:
            t_ex = _minutos_utc(exist.get("hora_utc", ""))
            clave_ex = _normalizar(exist["titulo"])

            if clave_ev == clave_ex and abs(t_ev - t_ex) <= 30:
                # Fusionar fuentes
                ids_existentes = {f.get("id_xtream") for f in exist.get("fuentes", [])}
                for f in ev.get("fuentes", []):
                    if f.get("id_xtream") not in ids_existentes and len(exist["fuentes"]) < 6:
                        exist["fuentes"].append(f)
                        ids_existentes.add(f.get("id_xtream"))

                # Priorizar torneo específico frente a 'Deportes' genérico
                if exist.get("torneo", "").lower() in ["deportes", "fútbol", "futbol"] and ev.get("torneo", "").lower() not in ["deportes", "fútbol", "futbol"]:
                    exist["torneo"] = ev["torneo"]
                    exist["subtitulo"] = ev.get("subtitulo", exist["subtitulo"])

                # Priorizar orígenes múltiples
                for o in ev.get("origenes", []):
                    if o not in exist.get("origenes", []):
                        exist["origenes"].append(o)

                fusionado = True
                break

        if not fusionado:
            eventos_fusionados.append(ev)

    eventos_pulidos = []
    for ev in eventos_fusionados:
        tit_limpio = ev["titulo"]
        torneo_original = ev["torneo"]
        categoria_real = ev["categoria"]
        loc = ev["equipo_local"]
        vis = ev["equipo_visitante"]
        ronda = ev.get("ronda", "")

        # Generar subtítulo deportivo limpio
        if ronda and torneo_original:
            subtitulo = f"{torneo_original} • {ronda}"
        elif ronda:
            subtitulo = f"{categoria_real} • {ronda}"
        elif torneo_original and torneo_original.lower() != tit_limpio.lower() and torneo_original.lower() not in ["deportes", "fútbol", "futbol"]:
            subtitulo = f"{torneo_original}"
        else:
            subtitulo = f"{categoria_real} en Directo"

        # Resolver logos
        logo_torneo = resolver_logo_torneo(torneo_original or tit_limpio, categoria_real)
        logo_loc = resolver_logo_equipo(loc, categoria_real, torneo_original) if loc else ""
        logo_vis = resolver_logo_equipo(vis, categoria_real, torneo_original) if vis else ""

        ev["subtitulo"] = subtitulo
        ev["logo_torneo"] = logo_torneo
        ev["logo_local"] = logo_loc
        ev["logo_visitante"] = logo_vis
        ev["banner"] = logo_torneo
        eventos_pulidos.append(ev)

    log.info("Pase de Curación Semántica completado: %d eventos procesados a la perfección", len(eventos_pulidos))
    return eventos_pulidos