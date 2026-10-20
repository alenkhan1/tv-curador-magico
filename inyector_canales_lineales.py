# -*- coding: utf-8 -*-
"""
Inyector Quirúrgico de Canales Lineales Deportivos:
Empareja los eventos confirmados de hoy con los streams lineales autorizados de la lista Xtream.
- Jurisdicción exacta verificada contra el Explorador M3U:
  * Win Sports+, Win Sports, Caracol, RCN -> Colombia (CO) exclusivamente.
  * ESPN AR, ESPN 2 AR, ESPN 3 AR, ESPN Premium -> Feeds Argentina (AR) exclusivamente.
  * ESPN 4, ESPN 5, ESPN 6, ESPN 7 -> Feeds Suramérica/Argentina (SUR/AR) exclusivamente (excluyendo MX y Brasil).
  * DSports AR, DSports 2, DSports +, TyC Sports, TNT Sports -> Cono Sur / Argentina exclusivamente.
  * Eurosport 1, Eurosport 2, Teledeporte, DAZN F1 -> España (ES) exclusivamente.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from adaptadores.modelos import EventoAgenda, obtener_tz
from resolvedor_logos import (
    resolver_logo_equipo,
    resolver_logo_torneo,
)
from sanitizador_nombres import es_texto_no_vivo_o_aplazado, es_estado_aplazado_o_repeticion

log = logging.getLogger("inyector_lineales")

FAMILIAS_CANALES_HERMANOS: List[List[str]] = [
    ["WIN SPORTS+", "WIN SPORTS"],
    ["DSPORTS 2", "DSPORTS +", "DSPORTS AR", "DSPORTS"],
    ["EUROSPORT 1", "EUROSPORT 2"],
    ["ESPN PREMIUM ARGENTINA", "ESPN AR", "ESPN 2 AR", "ESPN 3 AR", "ESPN 4", "ESPN 5", "ESPN 6", "ESPN 7"],
]

def clasificar_canal_xtream(nombre_crudo: str, cat_cruda: str) -> Optional[Tuple[str, str]]:
    """
    Clasifica un stream de Xtream en su canal canónico y su territorio exacto.
    Devuelve (canon, pais) o None.
    """
    n = (nombre_crudo or "").upper().strip()
    c = (cat_cruda or "").upper().strip()

    # 1. Exclusiones globales de países y carpetas ajenas
    if any(k in c for k in [
        "USA", "BRASIL", "BRAZIL", "FRANCE", "UK", "GERMANY", "NETHERLANDS",
        "CHILE", "PERU", "ECUADOR", "URUGUAY", "MEXICO", "MOVIES", "SERIES", "ADULT"
    ]):
        return None
    if any(k in n for k in [
        "USA:", "US|", "FR|", "BR|", "UK :", "UK:", "DE|", "NL :", "NL:",
        "FCL", "ESPN PE", "ESPN 2 PE", "ESPN EXTRA", "ESPN URUGUAY", "BT SPORT"
    ]):
        return None
    if re.search(r"\b(MEX|MX|CH|PE|ECUADOR|URUGUAY|BRASIL|DEPORTES|ESPNU|NEWS)\b", n):
        return None
    if any(k in n for k in ["INTERNACIONAL", "NOVELAS", "HD2", "HD 2", "RCN MAS", "NUESTRA TELE"]):
        return None

    # 2. Colombia: Win Sports, Caracol y RCN
    if "WIN" in n and "SPORTS" in n:
        if "+" in n:
            return ("WIN SPORTS+", "CO")
        return ("WIN SPORTS", "CO")

    if "CARACOL" in n and ("COLOMBIA" in c or "CO" in n):
        return ("CARACOL", "CO")
    if "RCN" in n and ("COLOMBIA" in c or "CO" in n):
        return ("RCN", "CO")

    # 3. Argentina: TyC Sports y TNT Sports
    if "TYC" in n and "SPORTS" in n:
        return ("TYC SPORTS", "AR")

    if "TNT" in n and ("SPORT" in n or "PREMIUM" in n) and ("ARGENTINA" in c or "PREMIUM" in c or "AR" in n):
        return ("TNT SPORTS", "AR")

    # 4. DSports (Exclusivamente feeds Cono Sur / Argentina)
    if "DSPORTS" in n or "DIRECTV SPORTS" in n:
        if re.search(r"\b(?:CO|COLOMBIA|EC|ECUADOR|PE|PERU|CL|CHILE|UY|URUGUAY|VE|VENEZUELA)\b", n):
            return None
        if any(exc in c for exc in ["COLOMBIA", "ECUADOR", "PERU", "CHILE", "URUGUAY", "VENEZUELA"]):
            return None
        # Excluir canales secundarios 3, 4, 5, etc. que no son señales lineales continuas en Xtream
        if re.search(r"\b(?:DSPORTS|DIRECTV\s*SPORTS)\s*[3456789]\b", n):
            return None
        if "2" in n:
            return ("DSPORTS 2", "AR")
        if "+" in n or "PLUS" in n:
            return ("DSPORTS +", "AR")
        return ("DSPORTS AR", "AR")

    # 5. España: Eurosport, Teledeporte y DAZN F1
    if any(k in c for k in ["ESPAÑA", "ESPANA", "SPAIN"]):
        if "EUROSPORT" in n:
            if "2" in n:
                return ("EUROSPORT 2", "ES")
            return ("EUROSPORT 1", "ES")
        if "TELEDEPORTE" in n or "TDP" in n:
            return ("TELEDEPORTE", "ES")
        if "DAZN" in n and "F1" in n:
            return ("DAZN F1", "ES")

    # 6. Familia ESPN: Exclusivamente Feed Cono Sur / Argentina (AR / SUR)
    if "ESPN" in n:
        es_marcador_ar = bool(re.search(r"\b(AR|ARG)\b", n)) or ("ARGENTINA" in c)
        es_carpeta_premium_o_ar = ("DEPORTES PREMIUM" in c) or ("ARGENTINA" in c)

        if "PREMIUM" in n:
            if es_marcador_ar:
                return ("ESPN PREMIUM ARGENTINA", "AR")
            return None

        if "ESPN 2" in n or "ESPN2" in n:
            if es_marcador_ar:
                return ("ESPN 2 AR", "AR")
            return None

        if "ESPN 3" in n or "ESPN3" in n:
            if es_marcador_ar:
                return ("ESPN 3 AR", "AR")
            return None

        if "ESPN 4" in n or "ESPN4" in n:
            if es_marcador_ar or "SUR" in n:
                return ("ESPN 4", "AR")
            return None

        for num in ["5", "6", "7"]:
            if f"ESPN {num}" in n or f"ESPN{num}" in n:
                if es_carpeta_premium_o_ar or "SUR" in n or es_marcador_ar:
                    return (f"ESPN {num}", "AR")
                return None

        # ESPN 1 / básico: Exigir explícitamente marcador AR o ARG (nunca CO, CH, PE ni MEX)
        if ("ESPN 1" in n or "ESPN" in n) and es_marcador_ar:
            return ("ESPN AR", "AR")

    return None

def construir_indice_canales_lineales(canales_xtream: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """
    Agrupa los streams de Xtream bajo sus nombres canónicos respetando filtros territoriales estrictos.
    """
    indice: Dict[str, List[Dict[str, str]]] = {}

    for c in canales_xtream:
        nombre = c.get("name") or c.get("stream_name") or ""
        sid = str(c.get("stream_id") or c.get("id") or "")
        cat_nombre = c.get("category_name") or ""
        if not nombre or not sid:
            continue

        res = clasificar_canal_xtream(nombre, cat_nombre)
        if not res:
            continue
        canon, pais = res

        n_u = nombre.upper()
        if "4K" in n_u or "UHD" in n_u or "FHD" in n_u:
            prioridad = 30
        elif "HD" in n_u:
            prioridad = 20
        else:
            prioridad = 10

        item = {
            "nombre": nombre.strip(),
            "id_xtream": sid,
            "canal_lineal": canon,
            "pais": pais,
            "_prioridad": prioridad,
        }
        indice.setdefault(canon, []).append(item)

    for canon in indice:
        indice[canon].sort(key=lambda x: x.get("_prioridad", 0), reverse=True)
        for item in indice[canon]:
            item.pop("_prioridad", None)

    metricas = {k: len(v) for k, v in indice.items()}
    log.info("Canales lineales Xtream indexados con aislamiento territorial: %s", metricas)
    return indice

def inyectar_eventos_lineales(
    agenda_hoy: List[EventoAgenda],
    indice_canales: Dict[str, List[Dict[str, str]]],
    zona_horaria: str = "America/Bogota"
) -> List[Dict[str, Any]]:
    """
    Cruza los eventos confirmados de la Agenda Maestra con los streams lineales de Xtream:
    - Toda mención a ESPN, ESPN 2, ESPN 3 o DSports se conecta al Feed Cono Sur (Argentina).
    - Permite combinar canales de distintas regiones (ej. Teledeporte ES + RCN CO + ESPN AR),
      pero jamás mezcla dos canales distintos de la misma cadena en un mismo evento.
    """
    tz_prod = obtener_tz(zona_horaria)
    eventos_inyectados = []

    for ev in agenda_hoy:
        txt_crudo = getattr(ev, "texto_original_crudo", "") or ev.titulo
        if es_texto_no_vivo_o_aplazado(ev.titulo) or es_estado_aplazado_o_repeticion(txt_crudo):
            log.info("Descartando evento lineal no vivo / aplazado desde raíz: '%s' (%s)", ev.titulo, txt_crudo)
            continue

        canales_a_procesar = []
        for c in ev.canales:
            cu = c.upper().strip()
            if cu in {"ESPN", "ESPN 1", "ESPN CO", "ESPN AR"}:
                canales_a_procesar.append("ESPN AR")
            elif cu in {"ESPN 2", "ESPN 2 CO", "ESPN 2 AR"}:
                canales_a_procesar.append("ESPN 2 AR")
            elif cu in {"ESPN 3", "ESPN 3 CO", "ESPN 3 AR"}:
                canales_a_procesar.append("ESPN 3 AR")
            elif cu in {"DSPORTS", "DSPORTS CO", "DSPORTS AR"}:
                canales_a_procesar.append("DSPORTS AR")
            else:
                canales_a_procesar.append(cu)

        # Si ambas señales de Win Sports están listadas para el mismo evento, priorizar WIN SPORTS+
        if "WIN SPORTS+" in canales_a_procesar and "WIN SPORTS" in canales_a_procesar:
            canales_a_procesar = [c for c in canales_a_procesar if c != "WIN SPORTS"]

        # Desambiguación estricta: máximo 1 canal por familia de cadena
        canales_desambiguados = []
        familias_vistas = set()
        for c_u in canales_a_procesar:
            familia_encontrada = None
            for idx_f, fam in enumerate(FAMILIAS_CANALES_HERMANOS):
                if c_u in fam:
                    familia_encontrada = idx_f
                    break
            if familia_encontrada is not None:
                if familia_encontrada not in familias_vistas:
                    familias_vistas.add(familia_encontrada)
                    canales_desambiguados.append(c_u)
            else:
                if c_u not in canales_desambiguados:
                    canales_desambiguados.append(c_u)
        canales_a_procesar = canales_desambiguados

        fuentes_unicas = []
        vistas = set()
        canales_usados = []
        max_por_canal = 3

        for canon in canales_a_procesar:
            if canon in indice_canales:
                canales_usados.append(canon)
                count_c = 0
                for f in indice_canales[canon]:
                    if f["id_xtream"] not in vistas:
                        vistas.add(f["id_xtream"])
                        item_f = dict(f)
                        item_f["canal_lineal"] = canon
                        fuentes_unicas.append(item_f)
                        count_c += 1
                        if count_c >= max_por_canal:
                            break

        if not fuentes_unicas:
            continue

        try:
            dt_utc = datetime.fromisoformat(ev.hora_utc.replace("Z", "+00:00"))
            dt_local = dt_utc.astimezone(tz_prod)
            hora_local_prod = dt_local.strftime("%H:%M")
        except Exception:
            hora_local_prod = "00:00"

        logo_torneo = resolver_logo_torneo(ev.torneo or ev.titulo, ev.deporte, local=ev.local, visitante=ev.visitante)
        logo_loc = resolver_logo_equipo(ev.local, ev.deporte, ev.torneo) if ev.local else ""
        logo_vis = resolver_logo_equipo(ev.visitante, ev.deporte, ev.torneo) if ev.visitante else ""

        slug_canal = canales_usados[0].lower().replace(" ", "_").replace("+", "plus")
        hash_evento = hashlib.sha1(f"{ev.titulo}_{ev.hora_utc}_{canales_usados[0]}".encode()).hexdigest()[:8]
        id_evento = f"lineal_{slug_canal}_{hash_evento}"

        canales_mencionados_fuente = getattr(ev, "canales_mencionados", []) or []

        evento_dict = {
            "id": id_evento,
            "canales_mencionados_fuente": canales_mencionados_fuente,
            "agenda_id": "",
            "titulo": ev.titulo,
            "torneo": ev.torneo or ev.titulo,
            "categoria": ev.deporte,
            "tipo_evento": ev.tipo_evento,
            "equipo_local": ev.local,
            "equipo_visitante": ev.visitante,
            "subtitulo": f"{ev.torneo}",
            "referencia": ev.referencia or ev.torneo,
            "hora_utc": ev.hora_utc,
            "hora_local_producto": hora_local_prod,
            "duracion_min": ev.duracion_min,
            "logo_torneo": logo_torneo,
            "logo_local": logo_loc,
            "logo_visitante": logo_vis,
            "banner": logo_torneo,
            "tier": 1 if any(k in (ev.torneo or "").upper() for k in ["LALIGA", "PREMIER", "CHAMPIONS", "BETPLAY", "CONMEBOL", "NBA", "MLB"]) else 2,
            "origen": "inyector_lineal",
            "origenes": ["inyector_lineal", ev.fuente],
            "estado": "confirmado",
            "estado_evento": "confirmado",
            "confianza": "alta",
            "puntuacion_confianza": 0.95,
            "fuentes": fuentes_unicas,
            "canales_candidatos": list(dict.fromkeys(canales_usados)),
            "texto_original_crudo": txt_crudo,
            "titulo_original_crudo": txt_crudo,
        }
        eventos_inyectados.append(evento_dict)

    log.info("Total eventos lineales inyectados con streams: %d", len(eventos_inyectados))
    return eventos_inyectados