# -*- coding: utf-8 -*-
"""
Inyector Quirúrgico de Canales Lineales Deportivos:
Empareja los eventos confirmados de hoy con los streams lineales autorizados de la lista Xtream.
- Aislamiento territorial estricto 1 a 1:
  * Win Sports+, Win Sports, Caracol, RCN -> Colombia exclusivamente.
  * ESPN CO, ESPN 2 CO -> Feeds Colombia (CO) exclusivamente.
  * ESPN AR, ESPN 2 AR, ESPN Premium -> Feeds Argentina (AR) exclusivamente.
  * ESPN 3, ESPN 4, ESPN 5, ESPN 6, ESPN 7 -> Feeds Pan-Regionales Suramérica.
  * TyC Sports, TNT Sports -> Argentina exclusivamente.
  * Eurosport 1, Eurosport 2, Teledeporte -> España exclusivamente.
  * DAZN F1 y Sky Sports F1 -> Feeds autorizados de motor.
- Exclusión total de feeds de USA, Brasil, México y apps OTT.
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
from sanitizador_nombres import es_texto_no_vivo_o_aplazado

log = logging.getLogger("inyector_lineales")

FAMILIAS_CANALES_HERMANOS: List[List[str]] = [
    ["WIN SPORTS+", "WIN SPORTS"],
    ["DSPORTS 2", "DSPORTS +", "DSPORTS AR", "DSPORTS CO", "DSPORTS"],
    ["EUROSPORT 1", "EUROSPORT 2"],
    ["ESPN PREMIUM ARGENTINA", "ESPN 2 AR", "ESPN 3 AR", "ESPN 4", "ESPN 5", "ESPN 6", "ESPN 7", "ESPN AR"],
    ["ESPN 2 CO", "ESPN 3 CO", "ESPN CO"],
]

def clasificar_canal_xtream(nombre_crudo: str, cat_cruda: str) -> Optional[Tuple[str, str]]:
    """
    Clasifica un stream de Xtream en su canal canonico y su territorio exacto.
    Devuelve (canon, pais) o None.
    """
    n = (nombre_crudo or "").upper()
    c = (cat_cruda or "").upper()

    # Exclusiones de feeds ajenos y plataformas no deseadas
    if any(k in c for k in ["USA", "BRASIL", "BRAZIL", "FRANCE", "UK", "GERMANY", "MOVIES", "SERIES", "ADULT"]):
        return None
    if any(k in n for k in ["USA:", "US|", "FR|", "BR|", "UK|", "DE|"]):
        return None
    if any(k in n for k in ["INTERNACIONAL", "NOVELAS", "HD2", "HD 2", "RCN MAS"]):
        return None

    # Win Sports (Colombia)
    if "WIN" in n and "SPORTS" in n:
        if "+" in n:
            return ("WIN SPORTS+", "CO")
        return ("WIN SPORTS", "CO")

    # Caracol y RCN (Colombia)
    if "CARACOL" in n and ("COLOMBIA" in c or "CO" in n):
        return ("CARACOL", "CO")
    if "RCN" in n and ("COLOMBIA" in c or "CO" in n):
        return ("RCN", "CO")

    # TyC Sports (Argentina)
    if "TYC" in n and "SPORTS" in n:
        return ("TYC SPORTS", "AR")

    # TNT Sports (Argentina)
    if "TNT" in n and ("SPORT" in n or "PREMIUM" in n) and ("ARGENTINA" in c or "PREMIUM" in c):
        if not any(k in n for k in ["CHILE", "BRASIL", "BRAZIL", "| CH"]):
            return ("TNT SPORTS", "AR")

    # DSPORTS
    if "DSPORTS" in n or "DIRECTV SPORTS" in n:
        if "2" in n:
            return ("DSPORTS 2", "SUR")
        if "+" in n:
            return ("DSPORTS +", "SUR")
        if "CO" in n or "COLOMBIA" in c:
            return ("DSPORTS CO", "CO")
        if "AR" in n or "ARGENTINA" in c:
            return ("DSPORTS AR", "AR")
        return ("DSPORTS", "SUR")

    # España
    if any(k in c for k in ["ESPAÑA", "ESPANA", "SPAIN"]):
        if "EUROSPORT" in n:
            if "2" in n:
                return ("EUROSPORT 2", "ES")
            return ("EUROSPORT 1", "ES")
        if "TELEDEPORTE" in n or "TDP" in n:
            return ("TELEDEPORTE", "ES")
        if "DAZN" in n and "F1" in n:
            return ("DAZN F1", "ES")

    # ESPN
    if "ESPN" in n:
        if any(k in n for k in ["MEX", "MX", "USA", "DEPORTES", "NEWS", "ESPNU"]):
            return None

        if "PREMIUM" in n:
            if any(k in n for k in ["AR", "ARG"]) or "ARGENTINA" in c:
                return ("ESPN PREMIUM ARGENTINA", "AR")
            return None

        if "ESPN 2" in n or "ESPN2" in n:
            if any(k in n for k in ["| AR", "| ARG", "ARG", "AR "]) or "ARGENTINA" in c:
                return ("ESPN 2 AR", "AR")
            if "CO" in n or "COLOMBIA" in c:
                return ("ESPN 2 CO", "CO")
            return ("ESPN 2", "SUR")

        if "ESPN 3" in n or "ESPN3" in n:
            # ESPN 3 es pan-regional sudamericano
            return ("ESPN 3", "SUR")

        for num in ["4", "5", "6", "7"]:
            if f"ESPN {num}" in n or f"ESPN{num}" in n:
                return (f"ESPN {num}", "SUR")

        # ESPN 1 / basico
        if "ESPN 1" in n or "ESPN" in n:
            if any(k in n for k in ["| AR", "| ARG", "ARG", "AR "]) or "ARGENTINA" in c:
                return ("ESPN AR", "AR")
            if "CO" in n or "COLOMBIA" in c:
                return ("ESPN CO", "CO")
            return ("ESPN", "SUR")

    return None

def construir_indice_canales_lineales(canales_xtream: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """
    Agrupa los streams de Xtream bajo sus nombres canonicos respetando filtros territoriales estrictos.
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

    # Aliases pan-regionales para ESPN 3 (sirve a Colombia y Argentina sin conflicto)
    if "ESPN 3" in indice:
        indice.setdefault("ESPN 3 CO", indice["ESPN 3"])
        indice.setdefault("ESPN 3 AR", indice["ESPN 3"])

    # Ordenar por calidad
    for canon in indice:
        indice[canon].sort(key=lambda x: x.get("_prioridad", 0), reverse=True)
        for item in indice[canon]:
            item.pop("_prioridad", None)

    metricas = {k: len(v) for k, v in indice.items()}
    log.info("Canales lineales Xtream indexados con aislamiento territorial: %s", metricas)
    return indice

def _deducir_territorio_evento(ev: EventoAgenda) -> str:
    """
    Deduce con precisión el territorio de la fuente de guía ('CO', 'AR', 'ES' o 'SUR')
    para asignar exclusivamente el feed nacional correspondiente sin mezclar países.
    """
    fuente_low = (getattr(ev, "fuente", "") or "").lower()
    txt_crudo = (getattr(ev, "texto_original_crudo", "") or "").upper()
    canales_up = [c.upper() for c in (ev.canales or [])]

    # Indicadores explícitos de Colombia
    if any(k in fuente_low for k in ["_co", "colombia", "portada", "win"]):
        return "CO"
    if any(c in ["WIN SPORTS+", "WIN SPORTS", "CARACOL", "RCN", "ESPN CO", "ESPN 2 CO", "DSPORTS CO"] for c in canales_up):
        return "CO"

    # Indicadores explícitos de Argentina
    if any(k in fuente_low for k in ["_ar", "argentina", "tyc", "tnt"]):
        return "AR"
    if "(ARG)" in txt_crudo or "HS (ARG)" in txt_crudo:
        return "AR"
    if any(c in ["TNT SPORTS", "TYC SPORTS", "ESPN PREMIUM ARGENTINA", "ESPN AR", "ESPN 2 AR", "DSPORTS AR"] for c in canales_up):
        return "AR"

    # Indicadores explícitos de España
    if any(k in fuente_low for k in ["espana", "rtve", "teledeporte", "eurosport"]):
        return "ES"

    # Por defecto en producto America/Bogota, priorizar feed CO sobre AR
    return "CO"

def inyectar_eventos_lineales(
    agenda_hoy: List[EventoAgenda],
    indice_canales: Dict[str, List[Dict[str, str]]],
    zona_horaria: str = "America/Bogota"
) -> List[Dict[str, Any]]:
    """
    Cruza los eventos confirmados de la Agenda Maestra con los streams lineales de Xtream,
    garantizando que cada evento se conecte unicamente con los streams de su territorio exacto.
    """
    tz_prod = obtener_tz(zona_horaria)
    eventos_inyectados = []

    for ev in agenda_hoy:
        txt_crudo = getattr(ev, "texto_original_crudo", "") or ev.titulo
        if es_texto_no_vivo_o_aplazado(ev.titulo) or es_texto_no_vivo_o_aplazado(txt_crudo):
            log.info("Descartando evento lineal no vivo / aplazado desde raíz: '%s' (%s)", ev.titulo, txt_crudo)
            continue

        territorio_ev = _deducir_territorio_evento(ev)
        canales_a_procesar = []

        for c in ev.canales:
            cu = c.upper().strip()
            if cu == "ESPN":
                canon_esp = "ESPN AR" if territorio_ev == "AR" else "ESPN CO"
                canales_a_procesar.append(canon_esp if canon_esp in indice_canales else "ESPN")
            elif cu == "ESPN 2":
                canon_esp2 = "ESPN 2 AR" if territorio_ev == "AR" else "ESPN 2 CO"
                canales_a_procesar.append(canon_esp2 if canon_esp2 in indice_canales else "ESPN 2")
            elif cu == "ESPN 3":
                canales_a_procesar.append("ESPN 3")
            elif cu == "DSPORTS":
                canon_dsp = "DSPORTS AR" if territorio_ev == "AR" else "DSPORTS CO"
                canales_a_procesar.append(canon_dsp if canon_dsp in indice_canales else "DSPORTS")
            else:
                canales_a_procesar.append(cu)

        # Desambiguacion de Canales Hermanos Mutuamente Excluyentes
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
                canales_desambiguados.append(c_u)
        canales_a_procesar = canales_desambiguados

        fuentes_unicas = []
        vistas = set()
        canales_usados = []
        max_por_canal = 3 if len(canales_a_procesar) > 1 else 5

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

        evento_dict = {
            "id": id_evento,
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