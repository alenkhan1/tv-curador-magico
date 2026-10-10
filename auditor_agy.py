# -*- coding: utf-8 -*-
"""
Auditor Semántico Deportivo con Antigravity (AGY):
Envía ÚNICAMENTE los eventos huérfanos o no clasificados en un LOTE CONSOLIDADO ÚNICO a AGY en la VM para:
1. Reclasificar deportes huérfanos ('Otros Deportes' -> 'Natación', 'Snooker', 'Pádel', 'Golf', etc.).
2. Reescribir títulos a nombres deportivos concisos y profesionales según EventoCard.kt:
   - Duelo: 'Local vs Visitante'
   - Circuito: 'Nombre del Evento/Torneo' (cero nombres individuales en la tarjeta de TV).
3. Extraer los contrincantes/peleadores para el cajón preview del reproductor (Línea 3 en EventosPreviewPlayer.kt).
4. Eliminar redundancias (evitar que Título == Subtítulo).
5. Descartar cualquier evento que sea un simple programa/magazine o repetición diferida.
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

def _extraer_datos_json(salida_texto: str) -> List[Dict[str, Any]]:
    if not salida_texto:
        return []
    try:
        obj = json.loads(salida_texto)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            for k in ["response", "content", "text", "output"]:
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

def _auditar_lote_con_agy(lote: List[Dict[str, Any]], fecha_hoy_iso: str) -> Dict[str, Dict[str, Any]]:
    prompt_texto = f"""Hoy es {fecha_hoy_iso}.
Eres el Auditor y Curador Deportivo en Jefe. Tu misión es depurar la cartelera para una app de Android TV.
Tienes el texto original crudo de cada canal o feed.

Lista de eventos a auditar:
{json.dumps(lote, ensure_ascii=False, indent=1)}

Reglas obligatorias para la interfaz de Android TV:
1. 'descartar': true si es programa de debate, noticiero, magazine de estudio, resumen de TV o repetición en diferido. SOLO eventos deportivos reales en directo.
2. 'categoria_exacta': Corrige SIEMPRE a su disciplina real (Fútbol, Baloncesto, Béisbol, Balonmano, Tenis, Pádel, Motor, Ciclismo, Combate, Boxeo, MMA, Golf, Rugby, Natación, Snooker, etc.). NUNCA 'Otros Deportes'.
3. 'tipo_evento':
   - 'duelo' si son dos equipos, clubes o selecciones enfrentados.
   - 'circuito' si es velada de combate (Boxeo, UFC, MMA), sesión de carreras (Motor), torneo de tenis, golf, etc.
4. 'titulo_pulido':
   - Si es DUELO: 'Equipo Local vs. Equipo Visitante' (limpio, sin prefijos, números de dial ni viñetas).
   - Si es CIRCUITO / COMBATE: Título conciso del evento, velada u organización para el botón de la TV (ej: 'UFC Fight Night 246', 'Gran Noche de Boxeo', 'Rolex Shanghai Masters', 'NASCAR Cup Series'). REGLA SAGRADA: NUNCA pongas nombres de deportistas individuales en el botón de circuito.
5. 'torneo_pulido': Nombre oficial del torneo, liga o velada (ej. 'Amistoso Internacional Femenino', 'UFC Fight Night', 'Liga BetPlay').
6. 'subtitulo_pulido': Sede, coliseo, cancha, pista o sesión (ej. 'Stadium Court', 'Bogotá', 'Apex Las Vegas', 'Clasificación'). Si no hay sede conocida, deja cadena vacía "".
7. 'participantes':
   - Si es CIRCUITO / COMBATE y en el texto original vienen los contrincantes principales o protagonistas (ej. 'Happy Lora vs. Unhappy Lora', 'Brandon Moreno vs Amir Albazi', 'Alcaraz vs Sinner'), ponlos aquí limpios. Estos nombres van bajo la pantalla preview del reproductor, NUNCA en el botón pequeño.

Responde ÚNICAMENTE con un JSON Array:
[
  {{
    "id": "id original",
    "titulo_pulido": "Título limpio para el botón",
    "torneo_pulido": "Torneo o Liga oficial",
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

    datos = _extraer_datos_json(salida_texto)
    if datos:
        return {item.get("id"): item for item in datos if isinstance(item, dict) and "id" in item}

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
        tiene_vinetas = any(c in tit for c in ["•", "–", "—", ">", "|", "■", "►"])
        es_circuito = tipo == "circuito" or cat in ["Tenis", "Motor", "Combate", "Boxeo", "MMA", "Golf", "Ciclismo", "Pádel", "Padel"]
        cat_dudosa = cat in ["Otros Deportes", "", "Deportes en Vivo", "Deportes"]
        necesita = (not ev.get("contrastado_api")) or es_circuito or tiene_vinetas or cat_dudosa
        if necesita:
            # ENVIAR SIEMPRE EL TEXTO ORIGINAL CRUDO PARA QUE AGY RAZONE CON EL 100% DE INFORMACIÓN
            raw_tit = ev.get("titulo_original_crudo") or ev.get("titulo_original") or ev.get("titulo")
            eventos_a_auditar.append({
                "id": ev.get("id"),
                "texto_original": raw_tit,
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
            ev["categoria"] = aud.get("categoria_exacta") or ev["categoria"]

            # Sincronizar torneo oficial
            if aud.get("torneo_pulido"):
                ev["torneo"] = aud["torneo_pulido"]
            elif aud.get("tipo_evento") == "circuito" and aud.get("titulo_pulido"):
                ev["torneo"] = aud["titulo_pulido"]

            # Si es duelo, extraer equipos locales y visitantes para que la Fase 4 resuelva escudos
            if aud.get("tipo_evento") == "duelo":
                ev["tipo_evento"] = "duelo"
                partes = re.split(r"\s+(?:vs\.?|contra|v\.)\s+", ev["titulo"], flags=re.I)
                if len(partes) == 2:
                    ev["equipo_local"] = partes[0].strip()
                    ev["equipo_visitante"] = partes[1].strip()
                ev["referencia"] = ev["subtitulo"] or ev.get("torneo") or ev["titulo"]
            elif aud.get("tipo_evento") == "circuito":
                ev["tipo_evento"] = "circuito"
                ev["equipo_local"] = ""
                ev["equipo_visitante"] = ""
                partic = (aud.get("participantes") or "").strip()
                if partic:
                    ev["referencia"] = partic
                else:
                    ev["referencia"] = ev.get("referencia") or ev["subtitulo"] or ev["torneo"]

            ev["auditado_agy"] = True
        eventos_finales.append(ev)

    log.info("Auditoría AGY finalizada: %d aprobados (%d pulidos por AGY, %d descartados)", 
             len(eventos_finales), len([e for e in eventos_finales if e.get("auditado_agy")]), len(eventos_descartados))
    return eventos_finales, eventos_descartados
