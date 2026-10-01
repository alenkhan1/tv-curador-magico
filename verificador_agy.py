# -*- coding: utf-8 -*-
"""
Verificador de Eventos Deportivos Huérfanos con Antigravity (AGY):
Para deportes no cubiertos por las APIs estándar (pádel, snooker, microfútbol, tejo, MMA, golf, vóley, etc.),
se consulta a AGY en la VM de Oracle Cloud mediante stdin para verificar mediante búsqueda web en tiempo real
si el evento se disputa HOY en vivo en la vida real.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("verificador_agy")

AGY_PATH = os.environ.get("AGY_PATH", "/home/ubuntu/.local/bin/agy")
ORACLE_HOST = os.environ.get("ORACLE_HOST", "51.170.138.241")
ORACLE_USER = os.environ.get("ORACLE_USER", "ubuntu")

def verificar_lote_eventos_con_agy(
    eventos_candidatos: List[Dict[str, Any]],
    fecha_hoy_iso: str
) -> Dict[str, Dict[str, Any]]:
    """
    Consulta a Antigravity (AGY) para verificar un lote de eventos.
    Retorna un diccionario {nombre_stream: {'es_directo_hoy': bool, 'torneo': str, 'deporte': str, 'motivo': str}}.
    """
    if not eventos_candidatos:
        return {}

    # Filtrar solo eventos únicos para no duplicar consultas
    nombres_unicos = {}
    for ev in eventos_candidatos:
        nom = ev.get("name") or ev.get("titulo") or ""
        if nom and nom not in nombres_unicos:
            nombres_unicos[nom] = ev.get("category_name", "")

    lista_para_prompt = [
        {"nombre_stream": nom, "categoria_xtream": grp}
        for nom, grp in list(nombres_unicos.items())[:30]
    ]

    prompt_texto = f"""Hoy es {fecha_hoy_iso}.
Eres un verificador deportivo estricto. Tu tarea es contrastar mediante búsqueda web si los siguientes eventos encontrados en listas de televisión corresponden a un evento deportivo EN VIVO que ocurre HOY {fecha_hoy_iso} en la vida real.
Si es una repetición de un torneo pasado, un evento de ayer o de fechas futuras, debes marcarlo como false.
Solo marca true si el evento se juega HOY en la vida real.

Eventos a verificar:
{json.dumps(lista_para_prompt, ensure_ascii=False, indent=2)}

Responde ESTRICTAMENTE con un JSON en formato array, sin explicaciones ni markdown:
[
  {{
    "nombre_stream": "string exacto",
    "es_directo_hoy": true/false,
    "deporte": "Pádel / Tenis / Golf / Snooker / etc.",
    "torneo": "Nombre oficial del torneo",
    "equipo_local": "Nombre o null",
    "equipo_visitante": "Nombre o null",
    "motivo": "Explicación breve de la búsqueda"
  }}
]
"""

    log.info("Consultando a AGY para verificar %d eventos huérfanos por stdin...", len(lista_para_prompt))

    salida_texto = None
    try:
        # Caso 1: Dentro de la VM de Oracle Cloud
        if Path(AGY_PATH).exists():
            cmd = [AGY_PATH, "--output-format", "json", "--print-timeout", "15m"]
            res = subprocess.run(cmd, input=prompt_texto, capture_output=True, text=True, timeout=600)
            if res.returncode == 0:
                salida_texto = res.stdout
            else:
                log.warning("AGY local fallo (rc=%d): %s", res.returncode, res.stderr)

        # Caso 2: Desde fuera de la VM -> stdin a través de SSH
        else:
            cmd_ssh = [
                "ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
                f"{ORACLE_USER}@{ORACLE_HOST}",
                f"{AGY_PATH} --output-format json --print-timeout 15m"
            ]
            res = subprocess.run(cmd_ssh, input=prompt_texto, capture_output=True, text=True, timeout=600)
            if res.returncode == 0:
                salida_texto = res.stdout
            else:
                log.warning("AGY via SSH fallo (rc=%d): %s", res.returncode, res.stderr)
    except Exception as e:
        log.warning("Error en llamada a AGY: %s", e)
        return {}

    if not salida_texto:
        log.warning("AGY no devolvio salida. Se mantendran eventos con precaucion.")
        return {}

    try:
        agy_envelope = json.loads(salida_texto)
        resp_content = agy_envelope.get("response", "")
        m_json = re.search(r'(\[.*\])', resp_content, flags=re.S)
        if m_json:
            datos = json.loads(m_json.group(1))
            res_dict = {item.get("nombre_stream", ""): item for item in datos}
            log.info("AGY verifico con exito %d eventos", len(res_dict))
            return res_dict
    except Exception as e:
        log.warning("No se pudo parsear JSON devuelto por AGY: %s", e)

    return {}
