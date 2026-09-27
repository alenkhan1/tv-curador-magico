# -*- coding: utf-8 -*-
"""
Módulo de Inteligencia Deportiva con Gemini.
Normaliza eventos, unifica transmisiones multi-feed y resuelve metadatos oficiales
para canales deportivos de España y América.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

log = logging.getLogger("agente_deportivo_ia")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

MODELOS_PREFERIDOS = [
    "gemini-flash-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash"
]


def _llamar_gemini(prompt: str, json_mode: bool = True) -> Optional[str]:
    """Ejecuta consulta a Gemini probando modelos resilientes en caso de 503 o 429."""
    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY no configurada.")
        return None

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    if json_mode:
        payload["generationConfig"] = {"responseMimeType": "application/json"}

    data = json.dumps(payload).encode("utf-8")

    for modelo in MODELOS_PREFERIDOS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                resultado = json.loads(resp.read().decode("utf-8"))
                candidatos = resultado.get("candidates", [])
                if candidatos:
                    texto = candidatos[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if texto.strip():
                        return texto.strip()
        except urllib.error.HTTPError as e:
            log.warning("Gemini modelo %s devolvió HTTP %s (%s). Probando siguiente...", modelo, e.code, e.reason)
            time.sleep(1)
        except Exception as e:
            log.warning("Gemini modelo %s falló (%s). Probando siguiente...", modelo, e)
            time.sleep(1)

    return None


def estructurar_canales_xtream(canales: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Toma canales de eventos Xtream crudos y los agrupa semánticamente
    en eventos canónicos únicos, resolviendo torneo, referencia y logos oficiales.
    """
    if not canales:
        return []

    # Procesar en lotes de 60 canales para máxima precisión y rapidez
    resultados_totales: List[Dict[str, Any]] = []
    tamano_lote = 60

    for i in range(0, len(canales), tamano_lote):
        lote = canales[i:i + tamano_lote]
        lineas_canales = [f"- ID:{c.get('id_xtream', '')} | {c.get('nombre_ui', '')}" for c in lote]
        texto_canales = "\n".join(lineas_canales)

        prompt = f"""Eres un curador deportivo de elite para televisión.
Analiza esta lista de canales y transmisiones deportivas en vivo.
Tu misión es normalizar, clasificar y unificar las señales en eventos deportivos canónicos.

REGLAS OBLIGATORIAS:
1. UNIFICACIÓN MULTI-FEED: Si varios canales transmiten el mismo evento (ej: 'DAZN La Vuelta Etapa 10' y 'Eurosport La Vuelta E10'), AGRÚPALOS en un solo objeto con sus IDs y nombres en 'canales_asociados'.
2. DEPORTES DE EQUIPO (Fútbol, Baloncesto, Béisbol, NFL, Rugby, Balonmano, Hockey, Voleibol):
   - tipo_evento: "duelo"
   - torneo: Nombre oficial de la liga (ej: "Liga BetPlay Dimayor", "LaLiga EA Sports", "UEFA Champions League")
   - referencia: Jornada o fase (ej: "Fecha 10", "Cuartos de final") o ""
   - equipo_local: Nombre formal del equipo local
   - equipo_visitante: Nombre formal del equipo visitante
3. DEPORTES DE CIRCUITO / INDIVIDUALES (Tenis, Ciclismo, Motor/F1/MotoGP, Snooker, Golf, Combate/MMA/Boxeo, Atletismo):
   - tipo_evento: "circuito"
   - torneo: Nombre oficial del torneo o Gran Premio (ej: "La Vuelta a España", "ATP Cincinnati Open", "GP de Italia", "British Open")
   - referencia: Etapa, Cancha, Sesión o Día (ej: "Etapa 10", "P&G Stadium Court", "Carrera", "Día 4").
   - IMPORTANTE: En deportes de circuito, equipo_local y equipo_visitante DEBEN ser cadenas vacías "". NO pongas nombres de deportistas individuales.
4. CATEGORÍA: Debe ser estrictamente una de: ["Fútbol", "Baloncesto", "Béisbol", "Tenis", "Ciclismo", "Motor", "Combate", "Golf", "Snooker", "Balonmano", "Rugby", "Pádel", "Hockey", "Voleibol", "Fútbol Americano", "Otros Deportes"].
5. LOGO OFICIAL: URL directa al logo oficial (SVG o PNG transparente en Wikimedia Commons o sitio oficial del torneo). Si no lo sabes con certeza, pon "".

CANALES A PROCESAR:
{texto_canales}

Devuelve un array JSON con los eventos normalizados."""

        resp = _llamar_gemini(prompt, json_mode=True)
        if not resp:
            continue

        try:
            eventos = json.loads(resp)
            if isinstance(eventos, list):
                resultados_totales.extend(eventos)
            elif isinstance(eventos, dict) and "eventos" in eventos:
                resultados_totales.extend(eventos["eventos"])
        except json.JSONDecodeError as e:
            log.error("Error parseando respuesta JSON de Gemini: %s", e)

    return resultados_totales


def filtrar_programacion_epg_directos(programas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filtra una lista de programas de televisión deportiva para quedarse
    exclusivamente con transmisiones en directo reales, descartando repeticiones y revistas.
    """
    if not programas:
        return []

    lineas = [f"- {p.get('id', idx)} | Canal: {p.get('canal', '')} | Título: {p.get('titulo', '')} | Sub: {p.get('subtitulo', '')}" for idx, p in enumerate(programas[:70])]
    texto = "\n".join(lineas)

    prompt = f"""Eres un supervisor de transmisiones deportivas de televisión en directo.
Analiza esta lista de programas de canales deportivos (Eurosport, Teledeporte, DAZN, Win Sports, DSports, TyC Sports, ESPN).
Identifica cuáles son eventos deportivos reales en DIRECTO o ESTRENO DE COMPETICIÓN y cuáles son repeticiones antiguas, resúmenes, noticias o tertulias.

Devuelve un array JSON con los IDs de los programas que SÍ son transmisiones deportivas en directo:
["id1", "id2", ...]
PROGRAMAS A EVALUAR:
{texto}"""

    resp = _llamar_gemini(prompt, json_mode=True)
    if not resp:
        return programas  # Fallback si no responde

    try:
        ids_directo = set(json.loads(resp))
        return [p for idx, p in enumerate(programas) if str(p.get("id", idx)) in ids_directo]
    except Exception:
        return programas
