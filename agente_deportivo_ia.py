# -*- coding: utf-8 -*-
"""
Modulo de Inteligencia Deportiva con Gemini.
1. Normaliza y enriquece eventos de la lista Xtream (ej: Polo, Tenis, F1, FPC, etc.).
2. Determina transmisiones deportivas REALES en DIRECTO para canales lineales específicos (Eurosport, Win+, DAZN, TDP, Movistar España).
3. Rate Limiter estricto (15 RPM -> mínimo 4.2s entre peticiones).
4. Modelos actualizados: gemini-2.5-flash, gemini-2.0-flash, gemini-1.5-flash.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("agente_deportivo_ia")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

MODELOS_DISPONIBLES = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

ARCHIVO_CACHE_IA = Path("cache_gemini_deportes.json")
_memoria_cache: Dict[str, Any] = {}
_ultimo_timestamp_llamada: float = 0.0

def _cargar_cache():
    global _memoria_cache
    if ARCHIVO_CACHE_IA.exists():
        try:
            _memoria_cache = json.loads(ARCHIVO_CACHE_IA.read_text(encoding="utf-8"))
        except Exception:
            _memoria_cache = {}

def _guardar_cache():
    try:
        ARCHIVO_CACHE_IA.write_text(json.dumps(_memoria_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("No se pudo guardar cache de IA: %s", e)

_cargar_cache()

def _llamar_gemini(prompt: str, json_mode: bool = True) -> Optional[str]:
    """Ejecuta consulta a Gemini con control de tasa (Rate Limit <= 15 RPM)."""
    global _ultimo_timestamp_llamada
    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY no configurada.")
        return None

    # Control de tasa estricto (15 RPM -> mínimo 4.2 seg entre llamadas)
    tiempo_transcurrido = time.time() - _ultimo_timestamp_llamada
    if tiempo_transcurrido < 4.2:
        tiempo_espera = 4.2 - tiempo_transcurrido
        time.sleep(tiempo_espera)

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    if json_mode:
        payload["generationConfig"] = {"responseMimeType": "application/json"}

    data = json.dumps(payload).encode("utf-8")

    for modelo in MODELOS_DISPONIBLES:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={GEMINI_API_KEY}"
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            _ultimo_timestamp_llamada = time.time()
            with urllib.request.urlopen(req, timeout=30) as resp:
                resultado = json.loads(resp.read().decode("utf-8"))
                candidatos = resultado.get("candidates", [])
                if candidatos:
                    texto = candidatos[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if texto.strip():
                        return texto.strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log.warning("Gemini modelo %s devolvio HTTP 429. Pausando 12s...", modelo)
                time.sleep(12)
            elif e.code == 503:
                log.warning("Gemini modelo %s ocupado (HTTP 503). Probando siguiente...", modelo)
                time.sleep(2)
            else:
                log.warning("Gemini modelo %s fallo con HTTP %s", modelo, e.code)
                time.sleep(1)
        except Exception as e:
            log.warning("Gemini modelo %s fallo (%s). Probando siguiente...", modelo, e)
            time.sleep(1)

    return None

_contador_llamadas = 0

def enriquecer_evento_independiente(nombre_ui: str, categoria_sugerida: str = "") -> Optional[Dict[str, Any]]:
    """
    Analiza un stream de evento de Xtream y extrae de forma determinista:
    - torneo oficial y limpio
    - categoria canonica con tildes (Fútbol, Baloncesto, Béisbol, etc.)
    - tipo_evento: "duelo" o "circuito"
    - si duelo: equipo_local y equipo_visitante
    - si circuito: referencia limpia (pista, etapa, manga), NUNCA nombres de atletas individuales
    """
    global _contador_llamadas
    clave = f"indep_{nombre_ui.strip()}"
    if clave in _memoria_cache:
        return _memoria_cache[clave]

    if _contador_llamadas >= 30:
        return None

    prompt = f"""Eres un curador deportivo de élite para televisión.
Analiza este evento deportivo emitido en televisión:
Título original: "{nombre_ui}"
Categoría sugerida: "{categoria_sugerida}"

REGLAS ESTRICTAS:
1. 'torneo': Nombre oficial y limpio de la competición, liga o campeonato (ej: "UEFA Nations League", "LaLiga EA Sports", "Liga BetPlay Dimayor", "Premier Padel", "ATP 250", "Campeonato de Polo de Palermo").
2. 'categoria': Debe ser exactamente una de estas cadenas:
   ["Fútbol", "Baloncesto", "Béisbol", "Tenis", "Ciclismo", "Motor", "Combate", "Golf", "Snooker", "Balonmano", "Rugby", "Pádel", "Hockey", "Voleibol", "Fútbol Americano", "Polo", "Otros Deportes"]
3. 'tipo_evento':
   - "duelo": enfrentamiento directo entre 2 equipos o 2 selecciones.
   - "circuito": carreras, torneos individuales, tenis, golf, ciclismo, motor, snooker, padel, atletismo, etc.
4. Si 'tipo_evento' es "duelo":
   - 'equipo_local': nombre limpio del equipo local (ej: "Armenia", "Leganés", "Pereira").
   - 'equipo_visitante': nombre limpio del equipo visitante (ej: "Montenegro", "Castellón", "Tolima").
   - 'referencia': "" o jornada/fase corta.
5. Si 'tipo_evento' es "circuito":
   - 'equipo_local': ""
   - 'equipo_visitante': ""
   - 'referencia': fase, etapa, pista, sesión o cancha (ej: "Pista Central", "Etapa 15", "Clasificación", "Semifinal"). NUNCA nombres de deportistas individuales aquí.
6. 'duracion_min': duración estimada en minutos (Fútbol: 120, Baloncesto: 130, Tenis: 150, Ciclismo: 240, Motor: 180, etc.).

Devuelve UNICAMENTE el objeto JSON:
{{
  "torneo": "...",
  "categoria": "...",
  "tipo_evento": "duelo" | "circuito",
  "equipo_local": "...",
  "equipo_visitante": "...",
  "referencia": "...",
  "duracion_min": 120
}}"""

    resp = _llamar_gemini(prompt, json_mode=True)
    _contador_llamadas += 1
    if not resp:
        return None

    try:
        data = json.loads(resp)
        if isinstance(data, dict) and data.get("torneo"):
            _memoria_cache[clave] = data
            _guardar_cache()
            return data
    except Exception as e:
        log.warning("Error decodificando enriquecimiento Gemini: %s", e)

    return None

def consultar_directos_canales_lineales(fecha_iso: str, canales_objetivo: List[str]) -> List[Dict[str, Any]]:
    """
    Determina qué eventos deportivos REALES se transmiten EN DIRECTO hoy en los canales lineales clave.
    Descarta de raíz:
    - Programas de opinión, debate, noticias (Sportscenter, Saque Largo, El Chiringuito, etc.)
    - Repeticiones históricas o diferidos
    - Señales secundarias o cortinillas
    FAIL-SAFE: Si Gemini falla, devuelve [] en lugar de aprobar basura.
    """
    if not GEMINI_API_KEY or not canales_objetivo:
        return []

    canales_str = ", ".join(canales_objetivo)
    prompt = f"""Eres un programador de transmisiones deportivas de televisión profesional.
Hoy es la fecha: {fecha_iso}.
Canales lineales a supervisar:
[{canales_str}]

Tu misión:
Identifica qué eventos deportivos destacados se transmiten EN VIVO / EN DIRECTO hoy en esos canales.
DESCARTE OBLIGATORIO:
- NO incluir programas de debate, tertulias o noticieros (ej: 'Saque Largo', 'Sportscenter', 'FShow', 'Estudio Estadio', 'El Chiringuito', etc.).
- NO incluir repeticiones de partidos pasados o carreras antiguas.
- Solo incluir transmisiones deportivas reales en directo de hoy.

Para cada evento en directo confirmado, genera este formato JSON:
[
  {{
    "canal_objetivo": "nombre del canal (ej: 'Win Sports+', 'Eurosport 1', 'DAZN F1', 'Teledeporte', 'DAZN LaLiga', '#Vamos', 'Movistar Deportes')",
    "torneo": "Nombre oficial de la competición (ej: 'Liga BetPlay Dimayor', 'LaLiga EA Sports', 'Fórmula 1', 'Vuelta a España')",
    "categoria": "Una de: ['Fútbol', 'Baloncesto', 'Béisbol', 'Tenis', 'Ciclismo', 'Motor', 'Combate', 'Golf', 'Snooker', 'Pádel', 'Otros Deportes']",
    "tipo_evento": "duelo" | "circuito",
    "equipo_local": "Nombre local si es duelo, sino ''",
    "equipo_visitante": "Nombre visitante si es duelo, sino ''",
    "referencia": "Pista, etapa o sesión si es circuito, sino ''",
    "hora_local_aprox": "HH:MM",
    "duracion_min": 120
  }}
]

Si para un canal hoy no hay evento en vivo o solo hay tertulias/repeticiones, NO lo incluyas en la lista.
Devuelve únicamente el array JSON."""

    resp = _llamar_gemini(prompt, json_mode=True)
    if not resp:
        return []

    try:
        data = json.loads(resp)
        if isinstance(data, list):
            log.info("Gemini identificó %d eventos en directo para canales lineales", len(data))
            return data
    except Exception as e:
        log.warning("Error decodificando parrilla Gemini: %s", e)

    return []
