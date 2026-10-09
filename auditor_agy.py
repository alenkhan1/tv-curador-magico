# -*- coding: utf-8 -*-
"""
Auditor Semántico Deportivo con Antigravity (AGY):
Envía ÚNICAMENTE los eventos huérfanos o no clasificados en un LOTE CONSOLIDADO ÚNICO a AGY en la VM para:
1. Reclasificar deportes huérfanos ('Otros Deportes' -> 'Natación', 'Snooker', 'Pádel', 'Golf', 'Tejo', etc.).
2. Reescribir títulos a nombres deportivos concisos y profesionales según EventoCard.kt:
   - Duelo: 'Local vs Visitante'
   - Circuito: 'Nombre del Evento/Torneo' (cero nombres individuales en la tarjeta de TV).
3. Eliminar redundancias (evitar que Título == Subtítulo).
4. Descartar cualquier evento que sea un simple programa/magazine o repetición diferida.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

log = logging.getLogger("auditor_agy")

AGY_PATH = os.environ.get("AGY_PATH", "/home/ubuntu/.local/bin/agy")
ORACLE_HOST = os.environ.get("ORACLE_HOST", "51.170.138.241")
ORACLE_USER = os.environ.get("ORACLE_USER", "ubuntu")

def _auditar_lote_con_agy(lote: List[Dict[str, Any]], fecha_hoy_iso: str) -> Dict[str, Dict[str, Any]]:
    prompt_texto = f"""Hoy es {fecha_hoy_iso}.
Eres el Auditor y Curador Deportivo en Jefe. Tu misión es depurar la cartelera para una app de Android TV.

Lista de eventos huérfanos a auditar:
{json.dumps(lote, ensure_ascii=False, indent=1)}

Reglas obligatorias:
1. 'descartar': true si es programa de debate, noticiero, magazine de estudio, resumen de TV o repetición en diferido. SOLO eventos deportivos reales en directo.
2. 'categoria_exacta': Corrige SIEMPRE a su disciplina real (Fútbol, Baloncesto, Béisbol, Balonmano, Tenis, Pádel, Motor, Ciclismo, Combate, Golf, Rugby, Natación, Snooker, Polo, Tejo, etc.). NUNCA 'Otros Deportes'.
3. 'titulo_pulido':
   - Si es DUELO: 'Equipo Local vs. Equipo Visitante' (sin prefijos de canal o jornada).
   - Si es CIRCUITO (MMA, F1, Boxeo, Tenis, Ciclismo, Golf, Pádel): Título limpio del evento u organizador (ej: 'UFC Fight Night', 'F1 Gran Premio de Singapur', 'Premier Padel Madrid', 'China Open'). REGLA: NUNCA pongas nombres de deportistas individuales en el título de circuito.
4. 'subtitulo_pulido':
   - Si es DUELO: Nombre del torneo (ej: 'Liga BetPlay', 'UEFA Nations League', 'Torneo Betplay').
   - Si es CIRCUITO: Estadio, circuito o fase (ej: 'Wembley Stadium', 'Main Card', 'Cuartos de final').

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
            cmd = [AGY_PATH, "-p", prompt_texto, "--effort", "low", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "45s"]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=95)
            if res.returncode == 0:
                salida_texto = res.stdout
        else:
            tmp_remote = f"/tmp/agy_prompt_{os.getpid()}_{int(hashlib.sha1(str(lote).encode()).hexdigest()[:6], 16)}.txt"
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
            res = subprocess.run(cmd_ssh, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
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
        m_json = re.search(r'\[\s*\{.*\}\s*\]', resp_str, flags=re.S)
        if m_json:
            datos = json.loads(m_json.group(0))
            return {item.get("id"): item for item in datos if "id" in item}
    except Exception as e:
        log.warning("No se pudo parsear JSON devuelto por AGY: %s", e)

    return {}

def auditar_catalogo_con_agy(eventos: List[Dict[str, Any]], fecha_hoy_iso: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Audita los eventos huérfanos o no contrastados en un ÚNICO lote consolidado con AGY."""
    if not eventos:
        return [], []

    eventos_a_auditar = []
    for ev in eventos:
        if ev.get("contrastado_api") is True:
            continue

        cat = ev.get("categoria", "")
        tit = ev.get("titulo", "")
        tipo = ev.get("tipo_evento", "")

        necesita = (
            cat in ["Otros Deportes", "", "Deportes en Vivo", "Deportes"] or
            cat in ["Pádel", "Padel", "Tejo", "Snooker", "Combate", "Polo", "Hípica", "Hipica", "Tiro", "Natación", "Natacion"] or
            (tipo == "circuito" and any(sep in tit.lower() for sep in [" vs ", " v ", " - "]))
        )
        if necesita:
            eventos_a_auditar.append({
                "id": ev.get("id"),
                "titulo": ev.get("titulo"),
                "subtitulo": ev.get("subtitulo"),
                "categoria": ev.get("categoria"),
                "torneo": ev.get("torneo"),
                "tipo_evento": ev.get("tipo_evento"),
                "hora": ev.get("hora_local_producto"),
            })

    if not eventos_a_auditar:
        log.info("Todos los eventos están debidamente contrastados o clasificados (%d eventos). Se omite AGY.", len(eventos))
        return eventos, []

    tamano_lote = 35
    mapa_audit = {}
    total_lotes = (len(eventos_a_auditar) + tamano_lote - 1) // tamano_lote
    log.info("Iniciando auditoría AGY en lote consolidado para %d eventos huérfanos (%d lotes)...", len(eventos_a_auditar), total_lotes)

    for i in range(0, len(eventos_a_auditar), tamano_lote):
        sub_lote = eventos_a_auditar[i:i + tamano_lote]
        num_lote = (i // tamano_lote) + 1
        log.info("Consultando AGY (Lote %d/%d, %d eventos)...", num_lote, total_lotes, len(sub_lote))
        res_lote = _auditar_lote_con_agy(sub_lote, fecha_hoy_iso)
        mapa_audit.update(res_lote)

    eventos_finales = []
    eventos_descartados = []
    for ev in eventos:
        aud = mapa_audit.get(ev.get("id"))
        if aud:
            if aud.get("descartar") is True:
                log.info("AGY descartó evento por no ser directo/oficial: '%s'", ev.get("titulo"))
                ev["motivo_descarte"] = "descartado_por_auditoria_agy"
                eventos_descartados.append(ev)
                continue
            ev["titulo"] = aud.get("titulo_pulido") or ev["titulo"]
            ev["subtitulo"] = aud.get("subtitulo_pulido") or ev["subtitulo"]
            ev["referencia"] = ev["subtitulo"]
            ev["categoria"] = aud.get("categoria_exacta") or ev["categoria"]
            ev["auditado_agy"] = True
        eventos_finales.append(ev)

    log.info("Auditoría AGY finalizada: %d aprobados (%d pulidos por AGY, %d descartados)", 
             len(eventos_finales), len([e for e in eventos_finales if e.get("auditado_agy")]), len(eventos_descartados))
    return eventos_finales, eventos_descartados
