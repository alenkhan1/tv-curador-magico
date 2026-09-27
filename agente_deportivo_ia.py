# -*- coding: utf-8 -*-
"""
Modulo de Inteligencia Deportiva con Gemini.
1. Normaliza y enriquece eventos de la lista Xtream no cubiertos por API-Sports (ej: Tejo, deportes locales).
2. Supervisa la parrilla EPG de canales lineales deportivos (Win Sports, Win+, DSports, Eurosport, ESPN, etc.)
   descartando repeticiones, tertulias y noticieros, confirmando solo emisiones en directo y asignando
   metadatos y logos limpios.
Incorpora control de tasa (Rate Limiting) estricto para respetar el límite de 15 RPM del tier gratuito de Gemini.
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
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-flash-latest",
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
    """
    Ejecuta consulta a Gemini con control de tasa (Rate Limit <= 15 RPM).
    Espera un mínimo de 4.2 segundos entre peticiones para evitar HTTP 429.
    """
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
            with urllib.request.urlopen(req, timeout=25) as resp:
                resultado = json.loads(resp.read().decode("utf-8"))
                candidatos = resultado.get("candidates", [])
                if candidatos:
                    texto = candidatos[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    if texto.strip():
                        return texto.strip()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log.warning("Gemini modelo %s devolvio HTTP 429 (Cuota por minuto alcanzada). Pausando 12s...", modelo)
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


_contador_llamadas_indep = 0

def enriquecer_evento_independiente(nombre_ui: str, categoria_sugerida: str = "") -> Optional[Dict[str, Any]]:
    """
    Toma un evento deportivo de Xtream no cubierto por API-Sports y extrae con Gemini:
    - torneo oficial
    - categoria valida
    - tipo_evento (duelo o circuito)
    - equipos o referencia limpia (cancha, etapa, sesion)
    - logo oficial (si existe certeza)
    Usa caché en disco y limita a un máximo de 15 llamadas nuevas por ejecución para no saturar.
    """
    global _contador_llamadas_indep
    clave = f"indep_{nombre_ui.strip()}"
    if clave in _memoria_cache:
        return _memoria_cache[clave]

    if _contador_llamadas_indep >= 15:
        # Límite de llamadas por corrida para mantener la ejecución rápida y segura
        return None

    prompt = f"""Eres un curador deportivo de elite para television.
Analiza este evento deportivo emitido hoy en la television:
Titulo original en la lista: "{nombre_ui}"
Categoria inferida: "{categoria_sugerida}"

Tu mision es estructurar y limpiar los metadatos para que se vean profesionales en pantalla.
Reglas:
1. 'torneo': Nombre oficial y limpio de la competicion, liga o campeonato (ej: "Campeonato Nacional de Tejo", "Premier Padel", "Copa Libertadores").
2. 'categoria': Debe ser una de: ["Futbol", "Baloncesto", "Beisbol", "Tenis", "Ciclismo", "Motor", "Combate", "Golf", "Snooker", "Balonmano", "Rugby", "Padel", "Hockey", "Voleibol", "Futbol Americano", "Otros Deportes"].
3. 'tipo_evento': "duelo" si es enfrentamiento entre dos equipos/selecciones, o "circuito" si es deporte individual/carrera/torneo.
4. Si es duelo: 'equipo_local' y 'equipo_visitante' con sus nombres limpios.
5. Si es circuito: 'equipo_local'="" y 'equipo_visitante'="". En 'referencia' pon cancha, etapa, ronda o sesion (ej: "Cancha Bavaria 5", "Semifinal", "Etapa 4").
6. 'logo_oficial': URL de Wikimedia Commons o sitio oficial del torneo o federacion (PNG transparente o SVG). Si no existe o dudas, pon "".

Devuelve unicamente el JSON correspondiente."""

    resp = _llamar_gemini(prompt, json_mode=True)
    _contador_llamadas_indep += 1
    if not resp:
        return None

    try:
        data = json.loads(resp)
        if isinstance(data, dict) and data.get("torneo"):
            _memoria_cache[clave] = data
            _guardar_cache()
            return data
    except Exception as e:
        log.warning("Error decodificando respuesta de enriquecimiento IA: %s", e)

    return None


def supervisar_parrilla_canales_en_vivo(programas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Supervisa la lista de programas candidatos de canales lineales deportivos.
    Gemini analiza en lotes optimizados (máx 3 lotes) y descarta:
    - Repeticiones antiguas
    - Noticieros, tertulias y programas de debate (ej: Saque Largo, Sportscenter, FShow, Estudio Estadio, etc.)
    Y confirma solo los que son TRANSMISIONES DEPORTIVAS REALES EN VIVO.
    """
    if not programas or not GEMINI_API_KEY:
        return programas

    resultados_aprobados: List[Dict[str, Any]] = []
    lote_tam = 35
    max_lotes = 3  # Máximo 3 lotes (105 programas) por corrida para no agotar cuota

    for i in range(0, min(len(programas), lote_tam * max_lotes), lote_tam):
        lote = programas[i:i + lote_tam]
        lineas = []
        for idx, p in enumerate(lote):
            lineas.append(f"{idx}. Canal: {p.get('canal_epg', '')} | Titulo: '{p.get('titulo_raw', '')}' | Desc: '{p.get('descripcion', '')[:100]}'")
        texto_lote = "\n".join(lineas)

        prompt = f"""Eres un supervisor de transmisiones deportivas de television en directo.
Analiza la siguiente lista de programas programados para emitirse hoy en canales deportivos:
{texto_lote}

Tu mision:
Identifica CUALES son eventos deportivos REALES EN DIRECTO / VIVO (partidos de futbol, tenis, padel, etapas de ciclismo, carreras, etc.).
DESCARTE OBLIGATORIO:
- Programas de opinion, tertulias, debate o noticieros (ej: 'Saque Largo', 'Planeta Futbol', 'Sportscenter', 'FShow', 'Estudio Estadio', 'La Montonera', 'Conexion TDP', etc.).
- Repeticiones de partidos o carreras antiguas o historicas.
- Programas de resumen o highlights.

Devuelve un JSON array con los indices aprobados y sus metadatos limpios:
[
  {{
    "idx": 0,
    "es_directo": true,
    "torneo": "Nombre limpio del torneo o competicion",
    "categoria": "Una de: Futbol, Baloncesto, Beisbol, Tenis, Ciclismo, Motor, Combate, Golf, Snooker, Balonmano, Rugby, Padel, Hockey, Voleibol, Futbol Americano, Otros Deportes",
    "tipo_evento": "duelo" o "circuito",
    "equipo_local": "Nombre o vacio",
    "equipo_visitante": "Nombre o vacio",
    "referencia": "Fase, etapa, cancha o sesion (ej: 'Pista Central', 'Fecha 12', 'Semifinal')",
    "logo_oficial": "URL del logo del torneo o federacion si existe certeza, o vacio ''"
  }}
]"""

        resp = _llamar_gemini(prompt, json_mode=True)
        if not resp:
            # Si falla la IA por cuota o red, conservar el lote para no perder cartelera
            resultados_aprobados.extend(lote)
            continue

        try:
            aprobados = json.loads(resp)
            if isinstance(aprobados, list):
                for item in aprobados:
                    if not isinstance(item, dict) or not item.get("es_directo"):
                        continue
                    idx = item.get("idx")
                    if isinstance(idx, int) and 0 <= idx < len(lote):
                        prog_original = dict(lote[idx])
                        if item.get("torneo"):
                            prog_original["torneo_ia"] = item.get("torneo")
                        if item.get("categoria"):
                            prog_original["categoria_ia"] = item.get("categoria")
                        if item.get("tipo_evento"):
                            prog_original["tipo_evento_ia"] = item.get("tipo_evento")
                        if item.get("equipo_local"):
                            prog_original["equipo_local_ia"] = item.get("equipo_local")
                        if item.get("equipo_visitante"):
                            prog_original["equipo_visitante_ia"] = item.get("equipo_visitante")
                        if item.get("referencia"):
                            prog_original["referencia_ia"] = item.get("referencia")
                        if item.get("logo_oficial"):
                            prog_original["logo_oficial_ia"] = item.get("logo_oficial")
                        prog_original["confirmado_ia"] = True
                        resultados_aprobados.append(prog_original)
            else:
                resultados_aprobados.extend(lote)
        except Exception as e:
            log.warning("Error parseando aprobaciones de Gemini: %s", e)
            resultados_aprobados.extend(lote)

    # Si había más programas fuera del límite de lotes, preservarlos
    if len(programas) > lote_tam * max_lotes:
        resultados_aprobados.extend(programas[lote_tam * max_lotes:])

    return resultados_aprobados
