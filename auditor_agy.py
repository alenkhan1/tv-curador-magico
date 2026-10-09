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

Reglas obligatorias para la interfaz de Android TV:
1. 'descartar': true si es programa de debate, noticiero, magazine de estudio, resumen de TV o repetición en diferido. SOLO eventos deportivos reales en directo.
2. 'categoria_exacta': Corrige SIEMPRE a su disciplina real (Fútbol, Baloncesto, Béisbol, Balonmano, Tenis, Pádel, Motor, Ciclismo, Combate, Boxeo, MMA, Golf, Rugby, Natación, Snooker, etc.). NUNCA 'Otros Deportes'.
3. 'tipo_evento': 'duelo' si son dos equipos/rivales enfrentados, o 'circuito' si es torneo/sesión (UFC, Motor, Tenis, Golf, etc.).
4. 'titulo_pulido':
   - Si es DUELO: 'Equipo Local vs. Equipo Visitante' (limpio, sin viñetas ni basura).
   - Si es CIRCUITO: Título conciso del evento u organizador para el botón pequeño de la TV (ej: 'UFC Fight Night 324', 'Rolex Shanghai Masters', 'NASCAR Ecosave 200'). REGLA: NUNCA pongas nombres de deportistas en el título de circuito.
5. 'subtitulo_pulido': Sede, estadio, cancha o pista (ej: 'Crystal Stadium', 'Stadium Court', 'Court 4', 'Bristol Motor Speedway').
6. 'participantes':
   - Si es CIRCUITO y se conocen los contrincantes principales o plato fuerte (ej: 'McGregor vs Pepito Pereza', 'Novak Djokovic - Hubert Hurkacz'), ponlos aquí. Estos nombres van bajo la pantalla preview del reproductor, NO en el botón pequeño. Si no se conocen, deja cadena vacía.

Responde ÚNICAMENTE con un JSON Array:
[
  {{
    "id": "id original",
    "titulo_pulido": "Título limpio para el botón",
    "subtitulo_pulido": "Sede o Cancha",
    "participantes": "Contrincantes bajo preview si aplica",
    "categoria_exacta": "Deporte exacto",
    "tipo_evento": "duelo o circuito",
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

        # Auditar cualquier evento huérfano (no contrastado por API de duelos),
        # eventos de circuito (para asegurar cancha limpia y participantes bajo preview),
        # o eventos con viñetas residuales o nombres genéricos
        tiene_viñetas = any(c in tit for c in ["◘", "■", "♦", "►", "●", "▼", "▲", "|"])
        es_circuito = tipo == "circuito" or cat in ["Tenis", "Motor", "Combate", "Boxeo", "MMA", "Golf", "Ciclismo", "Pádel", "Padel"]
        cat_dudosa = cat in ["Otros Deportes", "", "Deportes en Vivo", "Deportes"]
        necesita = (not ev.get("contrastado_api")) or es_circuito or tiene_viñetas or cat_dudosa
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
            # Los contrincantes van bajo la pantalla preview (cajón inferior en EventosPreviewPlayer.kt)
            partic = (aud.get("participantes") or "").strip()
            if partic:
                ev["referencia"] = partic
            else:
                ev["referencia"] = ev["subtitulo"]
            ev["categoria"] = aud.get("categoria_exacta") or ev["categoria"]
            if aud.get("tipo_evento") in ["duelo", "circuito"]:
                ev["tipo_evento"] = aud["tipo_evento"]
            ev["auditado_agy"] = True
        eventos_finales.append(ev)

    log.info("Auditoría AGY finalizada: %d aprobados (%d pulidos por AGY, %d descartados)", 
             len(eventos_finales), len([e for e in eventos_finales if e.get("auditado_agy")]), len(eventos_descartados))
    return eventos_finales, eventos_descartados
