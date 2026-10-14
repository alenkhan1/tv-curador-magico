# -*- coding: utf-8 -*-
"""
Adaptador Inteligente con AGY para Canales Deportivos sin EPG de Parrilla:
Investiga y certifica de forma autonoma las transmisiones deportivas EN VIVO de hoy para:
- Familia DSports: DSports (610), DSports 2 (612), DSports+ (613)
- TNT Sports Argentina
- Diales altos de ESPN: ESPN 4, ESPN 5, ESPN 6, ESPN 7 (Suramerica)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .modelos import EventoAgenda

log = logging.getLogger("canales_ciegos_agy")

AGY_PATH = os.environ.get("AGY_PATH", "/home/ubuntu/.local/bin/agy")
ORACLE_HOST = os.environ.get("ORACLE_HOST", "51.170.138.241")
ORACLE_USER = os.environ.get("ORACLE_USER", "ubuntu")
CACHE_DIR = Path(__file__).resolve().parent

def _extraer_datos_json(salida_texto: str) -> List[Dict[str, Any]]:
    if not salida_texto:
        return []
    try:
        obj = json.loads(salida_texto)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for k in ["response", "content", "text", "output", "result"]:
                if k in obj and isinstance(obj[k], str):
                    sub = _extraer_datos_json(obj[k])
                    if sub:
                        return sub
            for v in obj.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
    except Exception:
        pass

    m_codeblock = re.search(r'```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```', salida_texto, flags=re.S)
    if m_codeblock:
        try:
            return json.loads(m_codeblock.group(1))
        except Exception:
            pass

    m_array = re.search(r'\[\s*\{.*\}\s*\]', salida_texto, flags=re.S)
    if m_array:
        try:
            return json.loads(m_array.group(0))
        except Exception:
            pass

    return []

def _consultar_agy_en_vm(prompt_texto: str) -> str:
    salida_texto = ""
    try:
        if Path(AGY_PATH).exists():
            cmd = [AGY_PATH, "-p", prompt_texto, "--effort", "low", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "95s"]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=125)
            if res.returncode == 0:
                salida_texto = res.stdout
        else:
            h = hashlib.sha1(prompt_texto.encode("utf-8")).hexdigest()[:8]
            tmp_remote = f"/tmp/agy_prompt_lineales_{os.getpid()}_{h}.txt"
            subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", f"{ORACLE_USER}@{ORACLE_HOST}", f"cat > {tmp_remote}"],
                input=prompt_texto.encode("utf-8"),
                capture_output=True,
                timeout=15
            )
            cmd_ssh = [
                "ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
                f"{ORACLE_USER}@{ORACLE_HOST}",
                f"python3 /home/ubuntu/tv-curador-magico/agy_runner.py {tmp_remote}"
            ]
            res = subprocess.run(cmd_ssh, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=135)
            if res.returncode == 0:
                salida_texto = res.stdout
    except Exception as e:
        log.warning("Fallo en llamada a AGY para canales ciegos: %s", e)
    return salida_texto

def obtener_directos_canales_ciegos_agy(fecha_hoy_iso: str, forzar: bool = False) -> List[EventoAgenda]:
    cache_path = CACHE_DIR / f"cache_lineales_ciegos_{fecha_hoy_iso}.json"

    if not forzar and cache_path.exists():
        try:
            datos_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(datos_cache, list) and len(datos_cache) > 0:
                eventos_cache = []
                for item in datos_cache:
                    try:
                        eventos_cache.append(EventoAgenda(**item))
                    except Exception:
                        pass
                if eventos_cache:
                    log.info("Canales ciegos AGY cargados desde cache local (%s): %d eventos", fecha_hoy_iso, len(eventos_cache))
                    return eventos_cache
        except Exception as e:
            log.warning("Error leyendo cache de canales ciegos AGY: %s", e)

    prompt = f"""Investiga exhaustivamente la programacion deportiva EN VIVO y EN DIRECTO para HOY ({fecha_hoy_iso}) emitida exclusivamente por las siguientes senales lineales de television de Suramerica (Cono Sur / Argentina):
- DSports (Canal 610 / 1610)
- DSports 2 (Canal 612 / 1612)
- DSports+ (Canal 613 / 1613)
- TNT Sports Argentina
- ESPN 4, ESPN 5, ESPN 6, ESPN 7 (Suramerica)

REGLAS OBLIGATORIAS:
1. UNICAMENTE eventos deportivos que se disputen y transmitan EN VIVO HOY en tiempo real.
2. EXCLUYE estrictamente: noticieros (Total Sports, DSports Noticias, SportsCenter, TNT Data Sports), programas de debate/opinion (De Futbol Se Habla Asi, F90, Pelota Parada), revistas/magazines, repeticiones y diferidos.
3. Especifica con precision quirurgica UN SOLO canal exacto en el arreglo 'canales':
   - ['DSPORTS AR'] si es por el canal 610
   - ['DSPORTS 2'] si es por el canal 612
   - ['DSPORTS +'] si es por el canal 613
   - ['TNT SPORTS'] si es por TNT Sports Argentina
   - ['ESPN 4'], ['ESPN 5'], ['ESPN 6'], ['ESPN 7'] segun corresponda
4. Para deportes de equipo: 'tipo_evento': 'duelo', 'titulo': 'Equipo Local vs. Equipo Visitante', 'local': 'Equipo Local', 'visitante': 'Equipo Visitante'.
5. Provee hora_utc en formato ISO 8601 estandar (YYYY-MM-DDTHH:MM:SSZ).

Responde UNICAMENTE con un JSON Array valido:
[
  {{
    "titulo": "Equipo Local vs. Equipo Visitante",
    "deporte": "Fútbol",
    "torneo": "Liga o Competicion",
    "local": "Equipo Local",
    "visitante": "Equipo Visitante",
    "hora_utc": "{fecha_hoy_iso}T19:00:00Z",
    "canales": ["DSPORTS 2"],
    "tipo_evento": "duelo",
    "referencia": "Liga o Torneo",
    "texto_original_crudo": "Transmision en vivo oficial"
  }}
]
"""

    log.info("Consultando a AGY en la VM para programacion en directo de canales sin EPG (%s)...", fecha_hoy_iso)
    salida = _consultar_agy_en_vm(prompt)
    if not salida:
        log.warning("AGY no devolvio respuesta para canales ciegos.")
        return []

    items = _extraer_datos_json(salida)
    if not items:
        log.warning("No se pudo parsear el JSON de canales ciegos devuelto por AGY.")
        return []

    eventos: List[EventoAgenda] = []
    items_serializables = []

    for it in items:
        if not isinstance(it, dict):
            continue
        tit = it.get("titulo") or ""
        canales = [c.upper().strip() for c in it.get("canales", []) if c]
        canales = ["DSPORTS AR" if c == "DSPORTS" else c for c in canales]
        hora_utc = it.get("hora_utc") or ""
        if not tit or not canales or not hora_utc:
            continue

        deporte = it.get("deporte") or "Fútbol"
        torneo = it.get("torneo") or it.get("subtitulo") or deporte
        local = it.get("local") or ""
        visitante = it.get("visitante") or ""
        tipo_ev = it.get("tipo_evento") or ("duelo" if local and visitante else "circuito")
        ref = it.get("referencia") or it.get("subtitulo") or torneo
        raw = it.get("texto_original_crudo") or f"{tit} ({', '.join(canales)})"

        ev = EventoAgenda(
            titulo=tit,
            deporte=deporte,
            torneo=torneo,
            local=local,
            visitante=visitante,
            hora_utc=hora_utc,
            canales=canales[:1],
            fuente="agy_lineales",
            referencia=ref,
            tipo_evento=tipo_ev,
            texto_original_crudo=raw,
        )
        eventos.append(ev)
        items_serializables.append({
            "titulo": ev.titulo,
            "deporte": ev.deporte,
            "torneo": ev.torneo,
            "local": ev.local,
            "visitante": ev.visitante,
            "hora_utc": ev.hora_utc,
            "canales": ev.canales,
            "duracion_min": ev.duracion_min,
            "fuente": ev.fuente,
            "referencia": ev.referencia,
            "tipo_evento": ev.tipo_evento,
            "texto_original_crudo": ev.texto_original_crudo,
        })

    if items_serializables:
        try:
            cache_path.write_text(json.dumps(items_serializables, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Parrilla de canales ciegos AGY guardada en cache (%s): %d eventos", cache_path.name, len(items_serializables))
        except Exception as e:
            log.warning("Error guardando cache de canales ciegos AGY: %s", e)

    return eventos