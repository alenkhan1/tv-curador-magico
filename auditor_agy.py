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
Eres el Auditor y Curador Deportivo en Jefe. Tu misión es ser el JUEZ SUPREMO DE VERACIDAD Y VIGENCIA de la cartelera deportiva para una app de Android TV.
La app SOLO transmite eventos deportivos REALES EN VIVO Y EN DIRECTO que se disputan HOY ({fecha_hoy_iso}).

Tienes tanto el 'texto_original_crudo' (texto sucio original de la guía o canal) como los canales fuente y datos propuestos.

Reglas obligatorias de decisión:
1. 'descartar': true OBLIGATORIO si:
   - Es una REPETICIÓN, DIFERIDO o RESUMEN (el texto original o la fuente menciona 'Revive', 'Resumen', 'Mejores momentos', 'Diferido', 'Replay', 'Clásico', etc.).
   - Es un partido, duelo o evento que ya se disputó AYER o en fechas pasadas (ej: partidos de fútbol de fin de semana que los canales lineales retransmiten los lunes).
   - Es un evento con fecha explícita ajena a hoy (ej. nombres con fechas pasadas o futuras como '11.26' o años antiguos).
   - Es un programa de debate, noticiero, magazine, documental o previa de estudio (SportsCenter, F90, Primer Toque, etc.).
   - Es un canal de prueba, comodín vacío o evento falso/inventado.

2. 'descartar': false si y solo si:
   - Es una transmisión deportiva legítima de un evento o partido que se disputa HOY en vivo en la vida real. Esto incluye:
     * Duelos y partidos oficiales de hoy (liga local, copas, torneos nacionales e internacionales de hoy).
     * Torneos locales, regionales o formativos/juveniles (ej. Torneo de Las Américas, ligas femeninas, fútbol base) que se emitan hoy en directo.
     * Torneos en curso hoy (ej. Rolex Shanghai Masters, tenis ATP/WTA, veladas de combate, NFL Monday Night Football, MLB).
     * Cualquier deporte minoritario o local (billar, tejo, dados, etc.) transmitido EN VIVO hoy. NUNCA descartes un evento solo por ser un deporte o torneo minoritario o local.
3. 'motivo_descarte': Si 'descartar' es true, indica la razon precisa (ej: 'partido_jugado_ayer', 'repeticion_resumen', 'fecha_ajena', 'magazine_noticias'). Si es false, deja cadena vacia "".

4. 'categoria_exacta': Corrige SIEMPRE a su disciplina real (Futbol, Baloncesto, Beisbol, Balonmano, Tenis, Padel, Motor, Ciclismo, Combate, Boxeo, MMA, Golf, Rugby, Natacion, Snooker, etc.). NUNCA 'Otros Deportes'.

5. 'tipo_evento' y REGLA SAGRADA DEPORTES DE EQUIPO:
   - DEPORTES DE EQUIPO (Beisbol / MLB, Futbol Americano / NFL, Baloncesto / NBA, Futbol, Hockey / NHL): Son OBLIGATORIAMENTE 'duelo'. NUNCA los dejes como 'circuito' ni con titulo huerfano como 'MLB' o 'NFL'. Si la guia dice 'MLB: Serie de Campeonato' o 'NFL Monday Night' o no menciona los dos equipos, INVESTIGA que equipos juegan hoy ({fecha_hoy_iso}) a esa hora en esa liga:
     * 'tipo_evento': 'duelo'
     * 'titulo_pulido': 'Equipo Local vs. Equipo Visitante'
     * 'torneo_pulido': 'MLB' (o 'NFL', 'NBA', etc.)
   - 'circuito': UNICAMENTE veladas de combate (UFC, Boxeo, MMA, WWE), sesion de carreras (Motor), torneos individuales de Tenis, Golf, Ciclismo, etc.

6. 'titulo_pulido':
   - Si es DUELO: 'Equipo Local vs. Equipo Visitante' (limpio, sin prefijos, numeros de dial ni vinetas).
   - Si es CIRCUITO / COMBATE / SHOW: Titulo conciso del evento, velada u organizacion para el boton de la TV (ej: 'UFC Fight Night 246', 'Gran Noche de Boxeo', 'Rolex Shanghai Masters', 'WWE Raw', 'NASCAR Cup Series'). REGLA SAGRADA: NUNCA pongas nombres de deportistas individuales en el boton de circuito.

7. 'torneo_pulido': Nombre oficial del torneo, liga o velada (ej. 'Amistoso Internacional Femenino', 'UFC Fight Night', 'Liga BetPlay', 'MLB', 'NFL').

8. 'subtitulo_pulido' (OBLIGATORIO Y DESCRIPTIVO):
   - Para SHOWS SEMANALES O VELADAS (ej: WWE Raw, WWE SmackDown, UFC Fight Night): NUNCA lo dejes vacio ni repitas el titulo. Provee SIEMPRE la ciudad/recinto (ej: 'TD Garden, Boston, MA' o 'Apex, Las Vegas') y/o los combates estelares anunciados.
   - Para otros circuitos o torneos: Sede, coliseo, cancha, pista o sesion (ej. 'Stadium Court', 'Bogota', 'Clasificacion'). Si no hay sede conocida, deja cadena vacia "".

9. 'participantes': Para circuito o combate, contrincantes principales extraidos del texto original.

10. 'canales_confirmados' (ASIGNACION EXACTA DE CANAL LINEAL):
    - Si el evento tiene canales lineales asignados con variantes o senales hermanas (ej: Win Sports vs Win Sports+, ESPN vs ESPN 2/3/4/5/6/7/Premium, Eurosport 1 vs Eurosport 2, DSports vs DSports 2/+):
      Determina con precision en que canal o canales se emite en directo.
      Si se transmite por 'Win Sports+', el canal confirmado debe ser ["WIN SPORTS+"] y NO "Win Sports".
      Si se transmite por 'ESPN 2', el canal confirmado debe ser ["ESPN 2"] y NO "ESPN" o "ESPN 3".
      Devuelve 'canales_confirmados': ["CANAL EXACTO"]. Si no aplica o es stream general de evento, devuelve [].

Lista de eventos a auditar:
{json.dumps(lote, ensure_ascii=False, indent=1)}

Responde ÚNICAMENTE con un JSON Array:
[
  {{
    "id": "id original",
    "descartar": false,
    "motivo_descarte": "",
    "titulo_pulido": "Título limpio para el botón",
    "torneo_pulido": "Torneo o Liga oficial",
    "subtitulo_pulido": "Sede o Cancha",
    "participantes": "Contrincantes bajo preview si aplica",
    "categoria_exacta": "Deporte exacto",
    "tipo_evento": "duelo o circuito",
    "canales_confirmados": ["CANAL EXACTO"]
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
        # Auditar eventos huérfanos, eventos lineales no contrastados, circuitos y deportes dudosos
        es_lineal = any(o in ["inyector_lineal", "mitv_colombia", "portada", "deporte"] for o in ev.get("origenes", []))
        necesita = (not ev.get("contrastado_api")) or es_lineal or es_circuito or tiene_vinetas or cat_dudosa
        if necesita:
            raw_tit = ev.get("texto_original_crudo") or ev.get("titulo_original_crudo") or ev.get("titulo_original") or ev.get("titulo") or ""
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
                "torneo": ev.get("torneo"),
                "categoria": ev.get("categoria"),
                "tipo_evento": ev.get("tipo_evento"),
                "hora_local": ev.get("hora_local_producto"),
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
                motivo = aud.get("motivo_descarte") or "descartado_por_auditoria_agy"
                log.info("AGY descartó evento ('%s'): '%s'", motivo, ev.get("titulo"))
                ev["motivo_descarte"] = motivo
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

            # Poda quirurgica de canales lineales segun canales_confirmados por AGY
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
