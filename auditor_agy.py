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
5. Descartar cualquier evento que sea un simple programa/magazine, partido aplazado o repetición diferida.
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

from sanitizador_nombres import es_texto_no_vivo_o_aplazado

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
Eres el Auditor y Curador Deportivo en Jefe. Tu misión es ser el JUEZ SUPREMO DE VERACIDAD Y VIGENCIA de la cartelera deportiva para una app de Android TV.
La app SOLO transmite eventos deportivos REALES EN VIVO Y EN DIRECTO que se disputan HOY ({fecha_hoy_iso}).

Tienes tanto el 'texto_original_crudo' (texto sucio original de la guía o canal) como los canales fuente, la hora local (Bogotá UTC-5) y los datos propuestos.

Reglas obligatorias de decisión:
1. 'descartar': true OBLIGATORIO si:
   - Es un partido APLAZADO, POSTERGADO, SUSPENDIDO o CANCELADO (ej. si el texto menciona 'APLAZADO', 'SUSPENDIDO', 'POSTERGADO').
   - Es una REPETICIÓN, DIFERIDO o RESUMEN (el texto original menciona 'Revive', 'Resumen', 'Mejores momentos', 'Diferido', 'Replay', 'Compacto', 'Clásico', etc.).
   - INCOHERENCIA DE HUSO HORARIO (REPETICIÓN ENCUBIERTA): Contrasta la sede real del torneo contra 'hora_local' (Bogotá UTC-5). Por ejemplo, un torneo en vivo en China/Japón (UTC+8/+9, 13 horas delante de Bogotá) se juega entre la medianoche y las 08:00 AM de Bogotá; una emisión a las 09:00 AM, 11:00 AM o en la tarde de Bogotá corresponde a las 22:00-03:00 AM en China y es una REPETICIÓN en diferido -> 'descartar': true.
   - Es un partido, duelo o evento que ya se disputó AYER o en fechas pasadas.
   - Es un programa de debate, noticiero, magazine, documental o previa de estudio (SportsCenter, F90, Primer Toque, etc.).
   - Es un canal de prueba, comodín vacío o evento falso/inventado.

2. 'descartar': false si y solo si:
   - Es una transmisión deportiva legítima de un evento o partido que se disputa HOY en vivo y en directo en ese horario real.

3. 'motivo_descarte': Si 'descartar' es true, indica la razón precisa (ej: 'partido_aplazado', 'repeticion_diferido_horario', 'partido_jugado_ayer', 'magazine_noticias'). Si es false, deja cadena vacía "".

4. 'categoria_exacta': Usa SIEMPRE el nombre canónico con tildes exactas:
   ('Fútbol', 'Baloncesto', 'Béisbol', 'Balonmano', 'Tenis', 'Pádel', 'Motor', 'Ciclismo', 'Combate', 'Golf', 'Rugby', 'Hockey', 'Natación', 'Snooker', 'Polo', 'Voleibol', 'Atletismo', 'Hípica', 'Fútbol Americano').
   Nota: Boxeo, MMA, UFC y Lucha Libre/WWE van SIEMPRE bajo la categoría 'Combate'. NUNCA uses 'Futbol' sin tilde ni 'Otros Deportes'.

5. 'tipo_evento' y REGLA SAGRADA DEPORTES DE EQUIPO:
   - DEPORTES DE EQUIPO (Béisbol / MLB, Fútbol Americano / NFL, Baloncesto / NBA, Fútbol, Hockey / NHL): Son OBLIGATORIAMENTE 'duelo'. NUNCA los dejes como 'circuito' ni con título huérfano como 'MLB' o 'NFL'. Si la guía dice 'MLB: Serie de Campeonato' o 'NFL Monday Night' o no menciona los dos equipos, INVESTIGA qué equipos juegan hoy ({fecha_hoy_iso}) a esa hora en esa liga:
     * 'tipo_evento': 'duelo'
     * 'titulo_pulido': 'Equipo Local vs. Equipo Visitante'
     * 'torneo_pulido': 'MLB' (o 'NFL', 'NBA', etc.)
   - 'circuito': ÚNICAMENTE veladas de combate (UFC, Boxeo, MMA, WWE), sesiones de carreras (Motor), torneos individuales de Tenis, Golf, Ciclismo, etc.

6. 'titulo_pulido':
   - Si es DUELO: 'Equipo Local vs. Equipo Visitante' (limpio, sin prefijos, números de dial ni viñetas).
   - Si es CIRCUITO / COMBATE / SHOW: Título conciso del evento, velada u organización para el botón de la TV (ej: 'UFC Fight Night', 'Rolex Shanghai Masters', 'WWE Raw', 'Campeonato Mundial de Ciclismo en Pista'). REGLA SAGRADA: NUNCA pongas nombres de deportistas individuales en el botón de circuito.

7. 'torneo_pulido': Nombre oficial del torneo, liga o velada (ej. 'UEFA Champions League', 'Liga BetPlay', 'Rolex Shanghai Masters', 'MLB', 'NFL').

8. 'subtitulo_pulido' (OBLIGATORIO Y DESCRIPTIVO PARA CIRCUITOS):
   - NUNCA lo dejes vacío en un circuito ni repitas el mismo texto de 'titulo_pulido' o 'torneo_pulido'.
   - Extrae del 'texto_original_crudo' o investiga la Ronda, Sesión, Cancha o Ciudad/Recinto (ej: 'Octavos de Final • Qi Zhong Tennis Center', 'Sesión Vespertina', 'TD Garden, Boston, MA', 'Apex, Las Vegas').

9. 'participantes': Para circuito o combate, contrincantes principales o descripción de la jornada para mostrar debajo del reproductor preview.

10. 'canales_confirmados' (ASIGNACIÓN EXACTA DE CANAL LINEAL):
    - Devuelve únicamente el canal exacto donde se emite el evento en directo según el 'texto_original_crudo' (ej: si dice 'por ESPN 2', devuelve ["ESPN 2 CO"] o ["ESPN 2 AR"] según el país; si dice 'por Win Sports+', devuelve ["WIN SPORTS+"]). Si no aplica, devuelve [].

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
    "subtitulo_pulido": "Ronda, Sesión, Sede o Cancha",
    "participantes": "Contrincantes o detalle bajo preview",
    "categoria_exacta": "Categoría con tilde exacta",
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
    """Audita los eventos huérfanos, circuitos o sospechosos en un ÚNICO lote consolidado con AGY."""
    if not eventos:
        return [], []

    eventos_a_auditar = []
    for ev in eventos:
        raw_tit = ev.get("texto_original_crudo") or ev.get("titulo_original_crudo") or ev.get("titulo_original") or ev.get("titulo") or ""
        sospechoso_no_vivo = es_texto_no_vivo_o_aplazado(raw_tit) or es_texto_no_vivo_o_aplazado(ev.get("titulo", ""))

        if ev.get("contrastado_api") is True and not sospechoso_no_vivo:
            continue

        cat = ev.get("categoria", "")
        tit = ev.get("titulo", "")
        tipo = ev.get("tipo_evento", "")

        tiene_vinetas = any(c in tit for c in ["•", "–", "—", ">", "|", "■", "►"])
        es_circuito = tipo == "circuito" or cat in ["Tenis", "Motor", "Combate", "Boxeo", "MMA", "Golf", "Ciclismo", "Pádel", "Padel"]
        cat_dudosa = cat in ["Otros Deportes", "", "Deportes en Vivo", "Deportes"]
        es_lineal = any(o in ["inyector_lineal", "mitv_colombia", "portada", "deporte"] for o in ev.get("origenes", []))
        necesita = sospechoso_no_vivo or (not ev.get("contrastado_api")) or es_lineal or es_circuito or tiene_vinetas or cat_dudosa
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
            })

    if not eventos_a_auditar:
        log.info("Todos los eventos están debidamente contrastados o clasificados (%d eventos). Se omite AGY.", len(eventos))
        return eventos, []

    tamano_lote = 10
    mapa_audit = {}
    total_lotes = (len(eventos_a_auditar) + tamano_lote - 1) // tamano_lote
    log.info("Iniciando auditoría AGY en lote consolidado para %d eventos (%d lotes)...", len(eventos_a_auditar), total_lotes)

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
            ev["titulo"] = (aud.get("titulo_pulido") or ev["titulo"]).strip()
            ev["categoria"] = _normalizar_categoria_agy(aud.get("categoria_exacta", ""), ev.get("categoria", ""))

            if aud.get("torneo_pulido"):
                ev["torneo"] = aud["torneo_pulido"].strip()
            elif aud.get("tipo_evento") == "circuito" and aud.get("titulo_pulido"):
                ev["torneo"] = aud["titulo_pulido"].strip()

            sub_agy = (aud.get("subtitulo_pulido") or "").strip()
            if sub_agy and sub_agy.lower() not in [ev["titulo"].lower(), ev["torneo"].lower(), ev["categoria"].lower()]:
                ev["subtitulo"] = sub_agy
            elif sub_previo and sub_previo.lower() not in [ev["titulo"].lower(), ev["torneo"].lower(), ev["categoria"].lower()]:
                ev["subtitulo"] = sub_previo

            # Poda quirúrgica de canales lineales según canales_confirmados por AGY
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
                if len(partes) == 2:
                    ev["equipo_local"] = partes[0].strip()
                    ev["equipo_visitante"] = partes[1].strip()
                ev["referencia"] = ev.get("subtitulo") or ev.get("torneo") or ev["titulo"]
            elif aud.get("tipo_evento") == "circuito":
                ev["tipo_evento"] = "circuito"
                ev["equipo_local"] = ""
                ev["equipo_visitante"] = ""
                partic = (aud.get("participantes") or "").strip()
                if partic and partic.lower() not in [ev["titulo"].lower(), ev["torneo"].lower()]:
                    ev["referencia"] = partic
                else:
                    ev["referencia"] = ev.get("referencia") or ev.get("subtitulo") or ev["torneo"]
                if ev.get("subtitulo"):
                    ev["ronda"] = ev["subtitulo"]

            ev["auditado_agy"] = True
        eventos_finales.append(ev)

    log.info("Auditoría AGY finalizada: %d aprobados (%d pulidos por AGY, %d descartados)", 
             len(eventos_finales), len([e for e in eventos_finales if e.get("auditado_agy")]), len(eventos_descartados))
    return eventos_finales, eventos_descartados


def revisar_y_enriquecer_eventos_incompletos_con_agy(eventos: List[Dict[str, Any]], fecha_hoy_iso: str) -> List[Dict[str, Any]]:
    """
    Revisión final de AGY sobre la cartelera:
    Si hay eventos de circuito o combate con información escasa o sin subtítulo/sede/cancha,
    AGY extrae o investiga los detalles para completar sede (línea 2 del botón) y cartelera (línea 3 de preview).
    """
    if not eventos:
        return []

    candidatos = []
    for ev in eventos:
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
Los siguientes eventos deportivos se disputan HOY en vivo, pero necesitan datos concretos de sede/cancha/ronda (para la línea 2 del botón) y cartelera/detalle (para la línea 3 debajo del preview):
{json.dumps(datos_candidatos, ensure_ascii=False, indent=1)}

Extrae del 'texto_original' o investiga los datos oficiales de HOY ({fecha_hoy_iso}) y responde ÚNICAMENTE con un JSON Array:
[
  {{
    "id": "id original del evento",
    "subtitulo_pulido": "Ronda/Sesión y Cancha/Recinto donde se realiza hoy (ej. 'Octavos de Final • Qi Zhong Tennis Center', 'Sesión Vespertina', 'TD Garden, Boston, MA')",
    "participantes": "Partidos destacados, peleas estelares o detalle de la jornada de hoy",
    "categoria_exacta": "Deporte canónico con tilde ('Fútbol', 'Tenis', 'Ciclismo', 'Combate', 'Motor', 'Baloncesto', 'Béisbol', 'Pádel', 'Golf')",
    "torneo_pulido": "Nombre oficial corto del torneo o show"
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

        if salida_texto:
            datos = _extraer_datos_json(salida_texto)
            if isinstance(datos, list):
                mapa_enr = {d.get("id"): d for d in datos if isinstance(d, dict) and d.get("id")}
                for ev in candidatos_lote:
                    item = mapa_enr.get(ev.get("id"))
                    if item:
                        if item.get("subtitulo_pulido"):
                            ev["subtitulo"] = item["subtitulo_pulido"].strip()
                            ev["ronda"] = ev["subtitulo"]
                        if item.get("participantes"):
                            ev["referencia"] = item["participantes"].strip()
                        if item.get("categoria_exacta"):
                            ev["categoria"] = _normalizar_categoria_agy(item["categoria_exacta"], ev.get("categoria", ""))
                        if item.get("torneo_pulido"):
                            ev["torneo"] = item["torneo_pulido"].strip()
                        log.info("AGY enriquecio con exito evento '%s': Sede='%s', Ref='%s'",
                                 ev.get("titulo"), ev.get("subtitulo"), ev.get("referencia"))
    except Exception as e:
        log.warning("Fallo al enriquecer eventos con AGY: %s", e)

    return eventos