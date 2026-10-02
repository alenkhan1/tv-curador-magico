# -*- coding: utf-8 -*-
"""
Auditor Semántico Deportivo con Antigravity (AGY):
Envía los eventos en lotes ligeros de 15 eventos al CLI de AGY en la VM para:
1. Reclasificar deportes huérfanos ('Otros Deportes' -> 'Natación', 'Snooker', 'Pádel', 'Golf', etc.).
2. Reescribir títulos monstruosos o repetitivos a nombres deportivos concisos y profesionales.
3. Eliminar redundancias (evitar que Título == Subtítulo o que el deporte se repita en ambos).
4. Descartar cualquier evento huérfano, del pasado o que sea un simple programa/magazine.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger("auditor_agy")

AGY_PATH = os.environ.get("AGY_PATH", "/home/ubuntu/.local/bin/agy")
ORACLE_HOST = os.environ.get("ORACLE_HOST", "51.170.138.241")
ORACLE_USER = os.environ.get("ORACLE_USER", "ubuntu")

def _auditar_lote_con_agy(lote: List[Dict[str, Any]], fecha_hoy_iso: str) -> Dict[str, Dict[str, Any]]:
    prompt_texto = f"""Hoy es {fecha_hoy_iso}.
Eres el Auditor y Curador Deportivo en Jefe. Pule la calidad visual y semántica para una app de TV:

Lista de eventos:
{json.dumps(lote, ensure_ascii=False, indent=1)}

Reglas obligatorias:
1. 'categoria': Corrige SIEMPRE 'Otros Deportes' al deporte real (ej: Natación, Snooker, Tiro, Gimnasia, Golf, Pádel, Hípica, Balonmano, etc.).
2. 'titulo': Conciso, limpio y profesional. (Ej: 'Copa del Mundo: Bakú - Día 1').
3. 'subtitulo': Ronda, torneo o fase. NUNCA igual al título ni redundante.
4. 'descartar': true si es programa de debate/noticias de TV o evento de fecha pasada.

Responde ÚNICAMENTE con un JSON Array:
[
  {{
    "id": "id original",
    "titulo_pulido": "Título limpio",
    "subtitulo_pulido": "Subtítulo limpio",
    "categoria_exacta": "Deporte exacto",
    "descartar": false
  }}
]
"""
    salida_texto = None
    try:
        if Path(AGY_PATH).exists():
            cmd = [AGY_PATH, "-p", prompt_texto, "--output-format", "json", "--print-timeout", "2m"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                salida_texto = res.stdout
        else:
            cmd_ssh = [
                "ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
                f"{ORACLE_USER}@{ORACLE_HOST}",
                f"{AGY_PATH} -p {subprocess.list2cmdline([prompt_texto])} --output-format json --print-timeout 2m"
            ]
            res = subprocess.run(cmd_ssh, capture_output=True, text=True, timeout=120)
            if res.returncode == 0:
                salida_texto = res.stdout
    except Exception as e:
        log.warning("Fallo en llamada a AGY para lote: %s", e)
        return {}

    if not salida_texto:
        return {}

    try:
        data_env = json.loads(salida_texto)
        resp_str = data_env.get("response", "")
        m_json = re.search(r'(\[.*\])', resp_str, flags=re.S)
        if m_json:
            datos = json.loads(m_json.group(1))
            return {item.get("id"): item for item in datos if "id" in item}
    except Exception as e:
        log.warning("No se pudo parsear JSON devuelto por AGY: %s", e)

    return {}

def auditar_catalogo_con_agy(eventos: List[Dict[str, Any]], fecha_hoy_iso: str) -> List[Dict[str, Any]]:
    if not eventos:
        return []

    resumen_eventos = []
    for ev in eventos:
        resumen_eventos.append({
            "id": ev.get("id"),
            "titulo": ev.get("titulo"),
            "subtitulo": ev.get("subtitulo"),
            "categoria": ev.get("categoria"),
            "torneo": ev.get("torneo"),
            "hora": ev.get("hora_local_producto"),
        })

    tamano_lote = 15
    mapa_audit = {}
    total_lotes = (len(resumen_eventos) + tamano_lote - 1) // tamano_lote
    log.info("Iniciando auditoría AGY en %d lotes...", total_lotes)

    for i in range(0, len(resumen_eventos), tamano_lote):
        sub_lote = resumen_eventos[i:i + tamano_lote]
        num_lote = (i // tamano_lote) + 1
        log.info("Consultando AGY (Lote %d/%d, %d eventos)...", num_lote, total_lotes, len(sub_lote))
        res_lote = _auditar_lote_con_agy(sub_lote, fecha_hoy_iso)
        mapa_audit.update(res_lote)

    eventos_finales = []
    for ev in eventos:
        aud = mapa_audit.get(ev.get("id"))
        if aud:
            if aud.get("descartar") is True:
                log.info("AGY descartó evento por auditoría: '%s'", ev.get("titulo"))
                continue
            ev["titulo"] = aud.get("titulo_pulido") or ev["titulo"]
            ev["subtitulo"] = aud.get("subtitulo_pulido") or ev["subtitulo"]
            ev["categoria"] = aud.get("categoria_exacta") or ev["categoria"]
        eventos_finales.append(ev)

    log.info("Auditoría AGY finalizada: %d aprobados (%d modificados por AGY)", len(eventos_finales), len(mapa_audit))
    return eventos_finales
