# -*- coding: utf-8 -*-
"""
Auditor Semántico Deportivo con Antigravity (AGY):
1. Respeta la Bóveda Diaria: jamás re-audita eventos que ya tienen 'curado_integro': True.
2. Repara nombres corruptos de equipos en duelos a los que les falte algún escudo (ej. 'Sk Slavia Praha Era Nbk' -> 'ERA Nymburk').
3. En circuitos, exige títulos específicos en Title Case (ej. 'Wuhan Open', 'Dirtvision'), subtítulos cortos para el botón ('Millbridge Speedway', 'Court 1') y detalle en 'participantes'.
4. Descarta programas multicanal/carruseles ('UCL al instante'), partidos aplazados y repeticiones por huso horario.
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

from sanitizador_nombres import es_texto_no_vivo_o_aplazado, formatear_titulo_inteligente

log = logging.getLogger("auditor_agy")

AGY_PATH = os.environ.get("AGY_PATH", "/home/ubuntu/.local/bin/agy")
ORACLE_HOST = os.environ.get("ORACLE_HOST", "51.170.138.241")
ORACLE_USER = os.environ.get("ORACLE_USER", "ubuntu")

MAPA_CATEGORIAS_CANONICAS = {
    "FUTBOL": "Fútbol",
    "FÚTBOL": "Fútbol",
    "SOCCER": "Fútbol",
    "BEISBOL": "Béisbol",
    "BÉISBOL": "Béisbol",
    "BASEBALL": "Béisbol",
    "PADEL": "Pádel",
    "PÁDEL": "Pádel",
    "NATACION": "Natación",
    "NATACIÓN": "Natación",
    "FUTBOL AMERICANO": "Fútbol Americano",
    "FÚTBOL AMERICANO": "Fútbol Americano",
    "NFL": "Fútbol Americano",
    "BALONCESTO": "Baloncesto",
    "BASKETBALL": "Baloncesto",
    "BALONMANO": "Balonmano",
    "HANDBALL": "Balonmano",
    "TENIS": "Tenis",
    "TENNIS": "Tenis",
    "CICLISMO": "Ciclismo",
    "MOTOR": "Motor",
    "FORMULA 1": "Motor",
    "FÓRMULA 1": "Motor",
    "MOTOGP": "Motor",
    "COMBATE": "Combate",
    "LUCHA LIBRE": "Combate",
    "WRESTLING": "Combate",
    "WWE": "Combate",
    "BOXEO": "Combate",
    "MMA": "Combate",
    "UFC": "Combate",
    "GOLF": "Golf",
    "RUGBY": "Rugby",
    "HOCKEY": "Hockey",
    "SNOOKER": "Snooker",
    "POLO": "Polo",
    "VOLEIBOL": "Voleibol",
    "ATLETISMO": "Atletismo",
    "HIPICA": "Hípica",
    "HÍPICA": "Hípica",
}

def _normalizar_categoria_agy(cat: str, fallback: str = "") -> str:
    if not cat:
        return fallback
    c_up = cat.strip().upper()
    return MAPA_CATEGORIAS_CANONICAS.get(c_up, cat.strip())

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
Eres el Auditor y Curador Deportivo en Jefe para una app de Android TV.
La app SOLO transmite eventos deportivos REALES EN VIVO Y EN DIRECTO que se disputan HOY ({fecha_hoy_iso}).

Reglas obligatorias de decisión:
1. 'descartar': true OBLIGATORIO si:
   - EXCLUSIVO DE APP / STREAMING (ANTI-OTT): Esta aplicación SOLO cuenta con canales lineales de cable (ESPN 1 al 7 AR, ESPN Premium, TyC Sports, TNT Sports, DSports AR, Win Sports). Si el evento SOLO se transmite por aplicaciones de streaming (Disney+ Premium, DGO, Star+, WTA TV, Tennis TV, Paramount+, Apple TV) y NO se emite en las señales de cable mencionadas -> 'descartar': true, motivo: "exclusivo_app_streaming".
   - INCOHERENCIA GROSERA DE ENTIDADES: Si los equipos pertenecen a un país o liga totalmente ajena al torneo (ej. clubes holandeses asignados a la Serie A italiana).
   - Es un programa multicanal/carrusel de goles o resúmenes sin un partido específico (ej. 'UCL al instante', 'Champions Multi', 'Carrusel', 'Simulcast', 'RedZone', 'Goal Show').
   - Es un partido APLAZADO, POSTERGADO, SUSPENDIDO o CANCELADO.
   - Es una REPETICIÓN, DIFERIDO o RESUMEN.
   - INCOHERENCIA DE HUSO HORARIO (REPETICIÓN ENCUBIERTA): Contrasta la sede real del torneo contra 'hora_local' (Bogotá UTC-5). Un torneo jugado en China/Japón (UTC+8/+9) se juega de madrugada en Bogotá (00:00 a 08:00 AM); si se emite a las 09:00 AM o en la tarde de Bogotá es REPETICIÓN -> 'descartar': true.
   - Es un partido que ya se jugó en otra fecha o no existe hoy en el calendario real.
   - Es un programa de debate, noticiero, magazine o previa.

2. 'descartar': false si y solo si es una transmisión deportiva competitiva legítima que se disputa HOY en vivo en ese horario.

3. 'categoria_exacta': Usa SIEMPRE el nombre canónico con tildes exactas:
   ('Fútbol', 'Baloncesto', 'Béisbol', 'Balonmano', 'Tenis', 'Pádel', 'Motor', 'Ciclismo', 'Combate', 'Golf', 'Rugby', 'Hockey', 'Natación', 'Snooker', 'Polo', 'Voleibol', 'Atletismo', 'Hípica', 'Fútbol Americano').

4. 'tipo_evento' y LIMPIEZA DE NOMBRES DE CLUBES EN DUELOS:
   - DEPORTES DE EQUIPO (Fútbol, Baloncesto, Béisbol, Fútbol Americano, Hockey, Balonmano, Rugby): Son OBLIGATORIAMENTE 'duelo'.
   - Si el nombre de algún club viene corrupto, truncado o con patrocinadores raros que impiden hallar su escudo en bases de datos internacionales (ej. 'Surne Bilbao vs Sk Slavia Praha Era Nbk'), corrige en 'titulo_pulido' a sus nombres institucionales limpios e internacionalmente reconocidos ('Bilbao Basket vs. ERA Nymburk').

5. REGLAS DE ORO PARA CIRCUITOS (Tenis, Motor, Ciclismo, Pádel, Golf, Combate):
   - 'titulo_pulido' y 'torneo_pulido': Deben contener el NOMBRE ESPECÍFICO del evento en formato Title Case limpio (ej: 'Wuhan Open', 'Rolex Shanghai Masters', 'Dirtvision', 'Premier Padel Milán', 'Giro del Veneto', 'AEW Dynamite').
     * PROHIBIDO dejar nombres genéricos como 'WTA 1000', 'ATP 1000' o 'Motor' si el texto indica que es 'Wuhan Open' o 'Shanghai Masters'.
     * PROHIBIDO dejar palabras largas en MAYÚSCULAS ('DIRTVISION 01' -> 'Dirtvision'). Solo siglas cortas oficiales ('UEFA', 'WTA', 'ATP', 'UFC', 'AEW', 'UCI', 'F1') van en mayúsculas.
   - 'subtitulo_pulido' (Línea 2 del botón pequeño de TV, MÁXIMO 24 CARACTERES):
     * Solo el nombre corto de la Cancha, Pista, Circuito o Ronda (ej: 'Millbridge Speedway', 'Court 1', 'Centre Court', 'Sesión Vespertina', 'Etapa en Ruta').
     * PROHIBIDO repetir palabras que ya estén en 'titulo_pulido' y PROHIBIDO poner ciudades/estados largos separados por viñetas.
   - 'participantes' (Línea 3 debajo del reproductor preview):
     * Descripción concisa de la especialidad, sede o protagonistas (ej: 'Circuito de tierra • Outlaw Karts', 'Optics Valley Tennis Center • Cuadro Principal WTA 1000').

6. 'canales_confirmados': Si es un evento lineal, devuelve únicamente el canal exacto donde se emite. Si es stream de evento Xtream, devuelve [].

Lista de eventos a auditar:
{json.dumps(lote, ensure_ascii=False, indent=1)}

Responde ÚNICAMENTE con un JSON Array:
[
  {{
    "id": "id original",
    "descartar": false,
    "motivo_descarte": "",
    "titulo_pulido": "Título limpio (en duelos: Local vs. Visitante; en circuitos: Nombre específico del evento)",
    "torneo_pulido": "Torneo específico",
    "subtitulo_pulido": "Sede o Cancha corta (máx 24 chars, sin repetir título)",
    "participantes": "Detalle descriptivo bajo el preview",
    "categoria_exacta": "Categoría con tilde exacta",
    "tipo_evento": "duelo o circuito",
    "canales_confirmados": []
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
            res = subprocess.run(cmd_ssh, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=140)
            if res.returncode == 0:
                salida_texto = res.stdout
    except Exception as e:
        log.warning("Fallo en llamada a AGY para lote: %s", e)
        return {}

    if not salida_texto:
        return {}

    datos = _extraer_datos_json(salida_texto)
    res_map = {}
    if datos:
        id_set = {str(e.get("id")) for e in lote if e.get("id")}
        for idx, item in enumerate(datos):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if item_id and item_id in id_set:
                res_map[item_id] = item
            elif idx < len(lote):
                target_id = str(lote[idx].get("id") or "")
                if target_id and target_id not in res_map:
                    item["id"] = target_id
                    res_map[target_id] = item
    return res_map

def auditar_catalogo_con_agy(eventos: List[Dict[str, Any]], fecha_hoy_iso: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Audita únicamente los eventos nuevos o incompletos que no estén sellados en la Bóveda Diaria."""
    if not eventos:
        return [], []

    eventos_a_auditar = []
    for ev in eventos:
        # BÓVEDA DIARIA: Si el evento ya fue certificado como íntegro hoy, no se vuelve a auditar
        if ev.get("curado_integro") is True:
            continue

        raw_tit = ev.get("texto_original_crudo") or ev.get("titulo_original_crudo") or ev.get("titulo_original") or ev.get("titulo") or ""
        sospechoso_no_vivo = es_texto_no_vivo_o_aplazado(raw_tit) or es_texto_no_vivo_o_aplazado(ev.get("titulo", ""))

        tipo = ev.get("tipo_evento", "")
        falta_escudo_duelo = (tipo == "duelo") and (not ev.get("logo_local") or not ev.get("logo_visitante"))
        es_oficial_api = ev.get("contrastado_api") is True

        cat = ev.get("categoria", "")
        tit = ev.get("titulo", "")

        tiene_vinetas = any(c in tit for c in ["•", "–", "—", ">", "|", "■", "►"])
        es_circuito = tipo == "circuito" or cat in ["Tenis", "Motor", "Combate", "Boxeo", "MMA", "Golf", "Ciclismo", "Pádel", "Padel"]
        cat_dudosa = cat in ["Otros Deportes", "", "Deportes en Vivo", "Deportes"]
        es_lineal = any(o in ["inyector_lineal", "mitv_colombia", "portada", "deporte"] for o in ev.get("origenes", []))
        # Auditar si es sospechoso, si le faltan escudos, si es circuito/combate, si es lineal (para verificar exclusividad de app) o huérfano
        # AGY debe auditar el 100% de los eventos nuevos o pendientes no sellados en Bóveda
        necesita = True
        if necesita:
            if not ev.get("id"):
                ev["id"] = f"ev_audit_{len(eventos_a_auditar) + 1}_{hashlib.sha1((raw_tit + ev.get('hora_utc', '')).encode()).hexdigest()[:6]}"
            fuentes_nombres = [f.get("nombre") for f in ev.get("fuentes", []) if isinstance(f, dict) and f.get("nombre")][:3]
            canales_cands = ev.get("canales_candidatos") or list(dict.fromkeys([
                f.get("canal_lineal") for f in ev.get("fuentes", []) if isinstance(f, dict) and f.get("canal_lineal")
            ]))
            eventos_a_auditar.append({
                "id": ev.get("id"),
                "texto_original_crudo": raw_tit,
                "canales_fuente": fuentes_nombres,
                "canales_candidatos": canales_cands,
                "titulo_propuesto": ev.get("titulo"),
                "subtitulo_actual": ev.get("subtitulo"),
                "torneo": ev.get("torneo"),
                "categoria": ev.get("categoria"),
                "tipo_evento": ev.get("tipo_evento"),
                "hora_local": ev.get("hora_local_producto"),
                "hora_utc": ev.get("hora_utc"),
                "duracion_min": ev.get("duracion_min"),
                "oficial_api": ev.get("contrastado_api", False),
            })

    if not eventos_a_auditar:
        log.info("Todos los eventos están debidamente contrastados o sellados en Bóveda (%d eventos). Se omite AGY.", len(eventos))
        return eventos, []

    tamano_lote = 10
    mapa_audit = {}
    total_lotes = (len(eventos_a_auditar) + tamano_lote - 1) // tamano_lote
    log.info("Iniciando auditoría AGY en lote consolidado para %d eventos nuevos/incompletos (%d lotes)...", len(eventos_a_auditar), total_lotes)

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
                motivo = aud.get("motivo_descarte") or "descartado_por_auditoria_agy"
                log.info("AGY descartó evento ('%s'): '%s'", motivo, ev.get("titulo"))
                ev["motivo_descarte"] = motivo
                eventos_descartados.append(ev)
                continue

            sub_previo = (ev.get("subtitulo") or "").strip()
            es_oficial = ev.get("contrastado_api") is True

            # Si el evento ya viene contrastado oficialmente por API-Sports, AGY actúa como
            # control de calidad no destructivo: NO altera títulos ni torneos ya certificados:
            if not es_oficial:
                ev["titulo"] = formatear_titulo_inteligente((aud.get("titulo_pulido") or ev["titulo"]).strip())
                ev["categoria"] = _normalizar_categoria_agy(aud.get("categoria_exacta", ""), ev.get("categoria", ""))
                if aud.get("torneo_pulido"):
                    ev["torneo"] = formatear_titulo_inteligente(aud["torneo_pulido"].strip())
                elif aud.get("tipo_evento") == "circuito" and aud.get("titulo_pulido"):
                    ev["torneo"] = ev["titulo"]
            else:
                if not ev.get("categoria") or ev.get("categoria") in ["Deportes", "Otros Deportes"]:
                    ev["categoria"] = _normalizar_categoria_agy(aud.get("categoria_exacta", ""), ev.get("categoria", ""))

            sub_agy = formatear_titulo_inteligente((aud.get("subtitulo_pulido") or "").strip())
            if sub_agy and sub_agy.lower() not in [ev["titulo"].lower(), ev["torneo"].lower(), ev["categoria"].lower()]:
                ev["subtitulo"] = sub_agy
            elif sub_previo and sub_previo.lower() not in [ev["titulo"].lower(), ev["torneo"].lower(), ev["categoria"].lower()]:
                ev["subtitulo"] = sub_previo

            canales_conf = aud.get("canales_confirmados")
            if canales_conf and isinstance(canales_conf, list) and len(canales_conf) > 0:
                canales_conf_set = {c.strip().upper() for c in canales_conf if c and isinstance(c, str)}
                fuentes_filtradas = []
                for f in ev.get("fuentes", []):
                    c_lin = (f.get("canal_lineal") or "").strip().upper()
                    f_nom = (f.get("nombre") or "").strip().upper()
                    if c_lin:
                        if any(cc == c_lin for cc in canales_conf_set):
                            fuentes_filtradas.append(f)
                    else:
                        if any(cc in f_nom for cc in canales_conf_set):
                            fuentes_filtradas.append(f)
                if fuentes_filtradas:
                    ev["fuentes"] = fuentes_filtradas

            if aud.get("tipo_evento") == "duelo":
                ev["tipo_evento"] = "duelo"
                partes = re.split(r"\s+(?:vs\.?|contra|v\.)\s+", ev["titulo"], flags=re.I)
                if len(partes) == 2 and not es_oficial:
                    ev["equipo_local"] = formatear_titulo_inteligente(partes[0].strip())
                    ev["equipo_visitante"] = formatear_titulo_inteligente(partes[1].strip())
                ev["referencia"] = ev.get("subtitulo") or ev.get("torneo") or ev["titulo"]
            elif aud.get("tipo_evento") == "circuito":
                ev["tipo_evento"] = "circuito"
                # Preservar contendientes individuales si existen (Tenis, UFC, Boxeo, etc.)
                partic = (aud.get("participantes") or "").strip()
                if partic and partic.lower() not in [ev["titulo"].lower(), ev["torneo"].lower()]:
                    ev["referencia"] = partic
                else:
                    ev["referencia"] = ev.get("referencia") or ev.get("subtitulo") or ev["torneo"]
                ronda_asignada = False
                for campo in [ev.get("referencia"), ev.get("subtitulo")]:
                    if campo and any(r in campo.lower() for r in ["final", "semi", "cuarto", "ronda", "round", "jornada", "etapa", "race", "qualif"]):
                        ev["ronda"] = campo
                        ronda_asignada = True
                        break
                if not ronda_asignada and ev.get("subtitulo"):
                    ev["ronda"] = ev["subtitulo"]

            ev["auditado_agy"] = True
        eventos_finales.append(ev)

    log.info("Auditoría AGY finalizada: %d aprobados (%d pulidos por AGY, %d descartados)", 
             len(eventos_finales), len([e for e in eventos_finales if e.get("auditado_agy")]), len(eventos_descartados))
    return eventos_finales, eventos_descartados


def revisar_y_enriquecer_eventos_incompletos_con_agy(eventos: List[Dict[str, Any]], fecha_hoy_iso: str) -> List[Dict[str, Any]]:
    if not eventos:
        return []

    candidatos = []
    for ev in eventos:
        if ev.get("curado_integro") is True:
            continue
        tipo = ev.get("tipo_evento", "")
        tit = (ev.get("titulo") or "").strip().upper()
        sub = (ev.get("subtitulo") or "").strip()
        ref = (ev.get("referencia") or "").strip()
        tor = (ev.get("torneo") or "").strip()

        es_generico = any(k == tit or tit.startswith(k) for k in [
            "WWE RAW", "WWE SMACKDOWN", "WWE", "UFC", "BOXEO", "BOXING",
            "VELADA DE BOXEO", "DEPORTES EN VIVO", "COMBATE", "MMA"
        ])
        falta_sede = (not sub) or (sub.upper() == tor.upper()) or (sub.upper() == tit)
        falta_ref = (not ref) or (ref.upper() == tit) or (ref.upper() == tor.upper())

        if (tipo == "circuito" and (es_generico or falta_sede or falta_ref)) or (es_generico):
            candidatos.append(ev)

    if not candidatos:
        log.info("Revision final AGY: Ningun evento presenta datos genericos o incompletos.")
        return eventos

    candidatos_lote = candidatos[:8]
    log.info("Revision final AGY: Enriqueciendo %d eventos genericos/incompletos en lote...", len(candidatos_lote))
    datos_candidatos = [
        {
            "id": ev.get("id"),
            "titulo": ev.get("titulo"),
            "torneo": ev.get("torneo"),
            "categoria": ev.get("categoria"),
            "texto_original": ev.get("texto_original_crudo", ""),
        }
        for ev in candidatos_lote
    ]
    prompt_enriquecer = f"""Hoy es {fecha_hoy_iso}.
Eres el Investigador Deportivo en Jefe para Android TV.
Los siguientes eventos deportivos de circuito se disputan HOY en vivo, pero necesitan datos cortos de sede/cancha (para la línea 2 del botón, máximo 24 caracteres) y descripción (para la línea 3 debajo del preview):
{json.dumps(datos_candidatos, ensure_ascii=False, indent=1)}

Reglas:
- Si alguno NO es un evento competitivo real (ej. es un programa de resumen o carrusel como 'UCL al instante'), pon "descartar": true.
- 'torneo_pulido': Nombre específico del evento en Title Case limpio (ej. 'Dirtvision', 'Wuhan Open', 'Benelux Tour').
- 'subtitulo_pulido': SOLO la Sede, Pista, Circuito o Etapa corta sin repetir palabras del título (ej. 'Millbridge Speedway', 'Court 1', 'Etapa en Ruta').

Responde ÚNICAMENTE con un JSON Array:
[
  {{
    "id": "id original del evento",
    "descartar": false,
    "subtitulo_pulido": "Sede o Cancha corta (máx 24 chars)",
    "participantes": "Descripción concisa de la especialidad o jornada de hoy",
    "categoria_exacta": "Deporte canónico con tilde",
    "torneo_pulido": "Nombre específico corto del torneo o show"
  }}
]
"""
    salida_texto = None
    try:
        if Path(AGY_PATH).exists():
            cmd = [AGY_PATH, "-p", prompt_enriquecer, "--effort", "low", "--dangerously-skip-permissions", "--output-format", "json", "--print-timeout", "45s"]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=95)
            if res.returncode == 0:
                salida_texto = res.stdout
        else:
            tmp_remote = f"/tmp/agy_enrich_{os.getpid()}_{int(hashlib.sha1(str(datos_candidatos).encode()).hexdigest()[:6], 16)}.txt"
            subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", f"{ORACLE_USER}@{ORACLE_HOST}", f"cat > {tmp_remote}"],
                input=prompt_enriquecer.encode("utf-8"),
                capture_output=True,
                timeout=15
            )
            cmd_ssh = [
                "ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
                f"{ORACLE_USER}@{ORACLE_HOST}",
                f"python3 /home/ubuntu/tv-curador-magico/agy_runner.py {tmp_remote}"
            ]
            res = subprocess.run(cmd_ssh, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
            if res.returncode == 0:
                salida_texto = res.stdout

        ids_descartar = set()
        if salida_texto:
            datos = _extraer_datos_json(salida_texto)
            if isinstance(datos, list):
                mapa_enr = {d.get("id"): d for d in datos if isinstance(d, dict) and d.get("id")}
                for ev in candidatos_lote:
                    item = mapa_enr.get(ev.get("id"))
                    if item:
                        if item.get("descartar") is True:
                            ids_descartar.add(ev.get("id"))
                            continue
                        if item.get("torneo_pulido"):
                            ev["torneo"] = formatear_titulo_inteligente(item["torneo_pulido"].strip())
                            ev["titulo"] = ev["torneo"]
                        if item.get("subtitulo_pulido"):
                            ev["subtitulo"] = formatear_titulo_inteligente(item["subtitulo_pulido"].strip())
                            ev["ronda"] = ev["subtitulo"]
                        if item.get("participantes"):
                            ev["referencia"] = item["participantes"].strip()
                        if item.get("categoria_exacta"):
                            ev["categoria"] = _normalizar_categoria_agy(item["categoria_exacta"], ev.get("categoria", ""))
                        log.info("AGY enriquecio con exito evento '%s': Sede='%s', Ref='%s'",
                                 ev.get("titulo"), ev.get("subtitulo"), ev.get("referencia"))
        if ids_descartar:
            eventos = [e for e in eventos if e.get("id") not in ids_descartar]
    except Exception as e:
        log.warning("Fallo al enriquecer eventos con AGY: %s", e)

    return eventos