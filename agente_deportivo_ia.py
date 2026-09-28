# -*- coding: utf-8 -*-
"""
Modulo de Inteligencia Deportiva con Gemini.
1. Filtra y normaliza eventos de la lista Xtream (ej: FPC, MLS, Tenis, Polo, Eliminatorias, etc.).
2. Valida si un evento es realmente en vivo hoy o si es un partido viejo/repetido/cortinilla.
3. Determina transmisiones deportivas REALES en DIRECTO para canales lineales.
4. Fallback integrado con parser robusto para tolerancia total a fallos.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("agente_deportivo_ia")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

MODELOS_DISPONIBLES = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
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
        log.debug("GEMINI_API_KEY no configurada.")
        return None

    # Control de tasa estricto (15 RPM -> mínimo 4.2 seg entre llamadas)
    tiempo_transcurrido = time.time() - _ultimo_timestamp_llamada
    if tiempo_transcurrido < 4.2:
        time.sleep(4.2 - tiempo_transcurrido)

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    if json_mode:
        payload["generationConfig"] = {"responseMimeType": "application/json"}

    data = json.dumps(payload).encode("utf-8")
    api_key_quoted = urllib.parse.quote(GEMINI_API_KEY)

    for modelo in MODELOS_DISPONIBLES:
        endpoints = [
            f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent?key={api_key_quoted}",
            f"https://generativelanguage.googleapis.com/v1/models/{modelo}:generateContent?key={api_key_quoted}",
        ]
        for url in endpoints:
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
                err_body = e.read().decode("utf-8", errors="replace")
                if e.code == 429:
                    log.warning("Gemini modelo %s devolvio HTTP 429. Pausando 10s...", modelo)
                    time.sleep(10)
                elif e.code == 404:
                    log.debug("Gemini endpoint %s 404: %s", url, err_body[:100])
                    break # Probar siguiente modelo
                else:
                    log.warning("Gemini modelo %s fallo con HTTP %s: %s", modelo, e.code, err_body[:150])
            except Exception as e:
                log.debug("Gemini modelo %s fallo (%s).", modelo, e)
                break
    return None

TORNEOS_CONOCIDOS = [
    ("CONCACAF NATIONS LEAGUE", "CONCACAF Nations League", "Fútbol"),
    ("UEFA NATIONS LEAGUE", "UEFA Nations League", "Fútbol"),
    ("NATIONS LEAGUE", "UEFA Nations League", "Fútbol"),
    ("LIGA BETPLAY", "Liga BetPlay", "Fútbol"),
    ("BETPLAY", "Liga BetPlay", "Fútbol"),
    ("MLS", "MLS", "Fútbol"),
    ("PREMIER LEAGUE", "Premier League", "Fútbol"),
    ("LALIGA HYPERMOTION", "Segunda División de España", "Fútbol"),
    ("LALIGA SMARTBANK", "Segunda División de España", "Fútbol"),
    ("LALIGA", "LaLiga", "Fútbol"),
    ("LIGA MX FEMENIL", "Liga MX Femenil", "Fútbol"),
    ("LIGA MX", "Liga MX", "Fútbol"),
    ("SEGUNDA URUGUAY", "Segunda División Uruguay", "Fútbol"),
    ("PRIMERA URUGUAY", "Primera División Uruguay", "Fútbol"),
    ("CLASIF COPA AFRICANA", "Clasificación Copa Africana", "Fútbol"),
    ("COPA AFRICANA", "Clasificación Copa Africana", "Fútbol"),
    ("NCAA MEN'S SOCCER", "NCAA Soccer", "Fútbol"),
    ("NCAA", "NCAA Soccer", "Fútbol"),
    ("CLASIFICACION A PALERMO", "Campeonato Argentino de Polo", "Polo"),
    ("CLASIFICACIÓN A PALERMO", "Campeonato Argentino de Polo", "Polo"),
    ("PALERMO", "Campeonato Argentino de Polo", "Polo"),
    ("NFL", "NFL", "Fútbol Americano"),
    ("WWE RAW", "WWE Raw", "Combate"),
    ("WRESTLING", "WWE Raw", "Combate"),
    ("ATP", "ATP Tour", "Tenis"),
    ("WTA", "WTA Tour", "Tenis"),
    ("CHENGDU OPEN", "ATP Chengdu Open", "Tenis"),
    ("HANGZHOU OPEN", "ATP Hangzhou Open", "Tenis"),
    ("AMISTOSO", "Amistoso Internacional", "Fútbol"),
]

def limpiar_string_equipo(s: str) -> str:
    s = re.sub(r"[◘▪■►▼▲★♦•|~]", " ", s)
    s = re.sub(r"^\s*\d{1,2}[:.]\d{2}\s*(?:AM|PM|am|pm)?\s*", "", s)
    s = re.sub(r"\b(FHD|HD|SD|OP1|OP2|OP3|OP4|4K|HEVC|MULTI|ES|SPAIN|LATAM|ENGLISH|SPANISH|EVENTOS?|DIRECTO|LIVE)\b", "", s, flags=re.I)
    for pat, _, _ in TORNEOS_CONOCIDOS:
        s = re.sub(r"\b" + re.escape(pat) + r"\b", "", s, flags=re.I)
    return " ".join(s.split()).strip(" -:?/")

def parsear_stream_robusto(nombre_ui: str) -> Dict[str, Any]:
    """Parseador de alta precisión que limpia caracteres sucios y separa torneo y equipos sin ambigüedad."""
    torneo = ""
    deporte = ""
    upper = nombre_ui.upper()
    for pat, t_nom, dep in TORNEOS_CONOCIDOS:
        if pat in upper:
            torneo = t_nom
            deporte = dep
            break

    partes = [p.strip() for p in re.split(r"[|◘▪■►▼▲★♦•~]", nombre_ui) if p.strip()]
    duelo_parte = ""
    for p in partes:
        if re.search(r"\b(?:vs\.?|v\.?|versus)\b", p, flags=re.I):
            duelo_parte = p
            break

    if not duelo_parte:
        duelo_match = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus)\s+(.+)", nombre_ui, flags=re.I)
        if duelo_match:
            loc = limpiar_string_equipo(duelo_match.group(1))
            vis = limpiar_string_equipo(duelo_match.group(2))
            return {
                "tipo": "duelo",
                "deporte": deporte or "Fútbol",
                "torneo": torneo or "Fútbol",
                "local": loc,
                "visitante": vis,
                "subtitulo": ""
            }
        else:
            return {
                "tipo": "circuito",
                "deporte": deporte or "Otros Deportes",
                "torneo": torneo or limpiar_string_equipo(nombre_ui),
                "local": "",
                "visitante": "",
                "subtitulo": ""
            }

    m = re.search(r"(.+?)\s+(?:vs\.?|v\.?|versus)\s+(.+)", duelo_parte, flags=re.I)
    loc = limpiar_string_equipo(m.group(1))
    vis = limpiar_string_equipo(m.group(2))
    return {
        "tipo": "duelo",
        "deporte": deporte or "Fútbol",
        "torneo": torneo or "Fútbol",
        "local": loc,
        "visitante": vis,
        "subtitulo": ""
    }

def curar_canales_con_ia(canales: List[Dict[str, Any]], fecha_local: str) -> List[Dict[str, Any]]:
    """
    Curación global de streams independientes de Xtream:
    1. Aplica el parser robusto para garantizar nombres 100% limpios.
    2. Consulta a Gemini en lotes para verificar vigencia real de hoy y validar deporte y torneo.
    """
    if not canales:
        return []

    # Aplicar parseo robusto inicial a todos
    curados = []
    for c in canales:
        parsed = parsear_stream_robusto(c["nombre_ui"])
        c_info = {
            "canal": c,
            "es_valido": True,
            "deporte": parsed["deporte"],
            "torneo": parsed["torneo"],
            "tipo_evento": parsed["tipo"],
            "equipo_local": parsed["local"],
            "equipo_visitante": parsed["visitante"],
            "subtitulo": parsed["subtitulo"],
        }
        curados.append(c_info)

    if not GEMINI_API_KEY:
        return curados

    # Lote de consulta a Gemini para descartar eventos caducados y afinar metadatos
    streams_texto = []
    for i, item in enumerate(curados):
        streams_texto.append(f"[{i}] {item['canal']['nombre_ui']}")

    prompt = f"""Eres un curador deportivo de élite para TV en vivo (zona horaria Bogotá/Colombia).
Fecha local de hoy: {fecha_local}.

Analiza la siguiente lista de nombres de streams:
{chr(10).join(streams_texto)}

Para cada stream responde en JSON un array de objetos con:
- indice: número entero
- es_valido_hoy: boolean (false si es un partido que ya se jugó en días pasados, repetición vieja, noticiero o canal sin evento hoy)
- deporte: categoría canónica ('Fútbol', 'Tenis', 'Baloncesto', 'Polo', 'Ciclismo', 'Motor', 'Combate', 'Fútbol Americano', 'Béisbol', 'Golf', 'Snooker', 'Rugby', 'Pádel', 'Otros Deportes'). Nota: selecciones nacionales o copas africanas son 'Fútbol', NUNCA 'Otros Deportes'.
- torneo: nombre limpio oficial del torneo
- equipo_local: nombre limpio del equipo local (sin la palabra vs, sin el torneo)
- equipo_visitante: nombre limpio del equipo visitante (sin la palabra vs, sin el torneo)
"""
    try:
        respuesta = _llamar_gemini(prompt, json_mode=True)
        if respuesta:
            datos_ia = json.loads(respuesta)
            if isinstance(datos_ia, list):
                for d in datos_ia:
                    idx = d.get("indice")
                    if idx is not None and 0 <= idx < len(curados):
                        if "es_valido_hoy" in d:
                            curados[idx]["es_valido"] = bool(d["es_valido_hoy"])
                        if d.get("deporte"):
                            curados[idx]["deporte"] = str(d["deporte"]).strip()
                        if d.get("torneo"):
                            curados[idx]["torneo"] = str(d["torneo"]).strip()
                        if d.get("equipo_local"):
                            curados[idx]["equipo_local"] = str(d["equipo_local"]).strip()
                        if d.get("equipo_visitante"):
                            curados[idx]["equipo_visitante"] = str(d["equipo_visitante"]).strip()
    except Exception as e:
        log.debug("Error procesando lote de Gemini: %s", e)

    return curados


def consultar_directos_canales_lineales(fecha_local: str, canales_activos: List[str]) -> List[Dict[str, Any]]:
    """Consulta con Gemini las transmisiones deportivas en directo programadas hoy para canales lineales clave."""
    if not GEMINI_API_KEY or not canales_activos:
        return []

    prompt = f"""Eres un experto en programación deportiva de TV para Colombia y España.
Fecha de hoy: {fecha_local}. Zona horaria de referencia: Colombia (UTC-5).

Los siguientes canales lineales deportivos están disponibles en la plataforma:
{json.dumps(canales_activos, ensure_ascii=False)}

Indica las transmisiones deportivas REALES en DIRECTO (excluyendo repeticiones, programas de debate, noticieros y cortinillas) que se emiten hoy en esos canales.

Responde estrictamente en formato JSON con un array de objetos con las siguientes claves:
- canal_objetivo: nombre exacto del canal de la lista (ej: "WIN SPORTS+", "DAZN 1", "EUROSPORT 1")
- tipo_evento: "duelo" (si es equipo vs equipo) o "circuito" (torneo de tenis, ciclismo, golf, etc.)
- torneo: nombre oficial del torneo (ej: "Liga BetPlay", "LaLiga", "ATP Chengdu Open")
- categoria: deporte ("Fútbol", "Tenis", "Ciclismo", "Baloncesto", etc.)
- equipo_local: nombre del local (si es duelo) o vacío
- equipo_visitante: nombre del visitante (si es duelo) o vacío
- referencia: descripción breve o ronda (ej: "Final", "Jornada 12", "Semifinales")
- hora_local_aprox: hora estimada en formato militar HH:MM (zona horaria Bogotá)
"""
    try:
        texto = _llamar_gemini(prompt, json_mode=True)
        if texto:
            datos = json.loads(texto)
            if isinstance(datos, list):
                return datos
    except Exception as e:
        log.warning("Error consultando directos lineales con Gemini: %s", e)
    return []
