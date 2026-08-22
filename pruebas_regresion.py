# -*- coding: utf-8 -*-
"""Pruebas sin red del contrato de fecha, EPG y correlación del curador v11 EN CANAL."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import curador_eventos as curador
import inyector_epg as epg


class PruebasJornadaColombia(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = curador.obtener_zona_aplicacion()
        self.hoy = datetime.now(self.tz).replace(hour=10, minute=0, second=0, microsecond=0)

    def test_fecha_xtream_de_ayer_es_rechazada(self) -> None:
        ayer = self.hoy - timedelta(days=1)
        aceptado, motivo = curador.es_candidato(
            {"name": f"{ayer.strftime('%d/%m')} 19:00 Fútbol Equipo A vs Equipo B", "category_id": ""},
            set(), self.hoy,
        )
        self.assertFalse(aceptado)
        self.assertEqual(motivo, "fecha_xtream_fuera_de_jornada")

    def test_categoria_xtream_de_ayer_es_rechazada_aunque_el_nombre_tenga_hora(self) -> None:
        aceptado, motivo = curador.es_candidato(
            {"name": "19:00 Fútbol Equipo A vs Equipo B", "category_id": "ayer"},
            set(), self.hoy, {"ayer"},
        )
        self.assertFalse(aceptado)
        self.assertEqual(motivo, "categoria_xtream_fuera_de_jornada")

    def test_fecha_xtream_de_hoy_es_aceptada(self) -> None:
        aceptado, motivo = curador.es_candidato(
            {"name": f"{self.hoy.strftime('%d/%m')} 19:00 Fútbol Equipo A vs Equipo B", "category_id": ""},
            set(), self.hoy,
        )
        self.assertTrue(aceptado)
        self.assertEqual(motivo, "fecha_xtream_hoy")

    def test_desfase_de_veintitres_horas_no_puede_confirmar(self) -> None:
        canal = {"hora_local": self.hoy, "categoria_inferida": "Fútbol", "sesion": None}
        evento = {"hora_utc": curador.iso_utc(self.hoy - timedelta(hours=23))}
        cercano, diferencia = curador._proximidad_horaria(canal, evento)
        self.assertFalse(cercano)
        self.assertGreater(diferencia or 0, curador.MAX_DIFERENCIA_MIN)

    def test_epg_de_madrugada_espanola_puede_ser_tarde_colombiana(self) -> None:
        # 23 de agosto, 00:30 CEST corresponde a 22 de agosto, 17:30 en Bogotá.
        inicio = epg.parse_timestamp_epg("20260823003000 +0200")
        self.assertIsNotNone(inicio)
        self.assertEqual(inicio.astimezone(self.tz).date().isoformat(), "2026-08-22")
        self.assertEqual(inicio.astimezone(self.tz).strftime("%H:%M"), "17:30")

    def test_epg_de_madrugada_espanola_puede_pertenecer_a_ayer_colombia(self) -> None:
        # 22 de agosto, 06:30 CEST corresponde a 21 de agosto, 23:30 en Bogotá.
        inicio = epg.parse_timestamp_epg("20260822063000 +0200")
        self.assertIsNotNone(inicio)
        self.assertEqual(inicio.astimezone(self.tz).date().isoformat(), "2026-08-21")

    def test_sesion_formula_uno_no_confunde_sprint_con_clasificacion(self) -> None:
        canal = {"sesion": "clasificacion_sprint", "categoria_inferida": "Motor"}
        carrera = {"titulo": "Gran Premio de Países Bajos Carrera Sprint", "subtitulo": "", "categoria": "Motor"}
        self.assertFalse(curador._sesiones_compatibles(canal, carrera))

    def test_normalizador_api_y_match_xtream_crean_evento_confirmado(self) -> None:
        inicio = self.hoy.replace(hour=19)
        bruto = {
            "fixture": {"id": 501, "timestamp": int(inicio.timestamp()), "status": {"short": "NS"}},
            "league": {"name": "Liga de Prueba", "logo": "liga.png"},
            "teams": {"home": {"name": "Equipo Azul", "logo": "azul.png"}, "away": {"name": "Equipo Rojo", "logo": "rojo.png"}},
        }
        evento = curador.normalizar_evento_api("football", bruto, curador.API_CONFIGS["football"], self.tz)
        self.assertIsNotNone(evento)
        canal = {
            "id_xtream": "99", "nombre_ui": f"{self.hoy.strftime('%d/%m')} 19:00 Fútbol Equipo Azul vs Equipo Rojo",
            "texto_normalizado": "FUTBOL EQUIPO AZUL VS EQUIPO ROJO", "tokens": curador.tokenizar("Fútbol Equipo Azul vs Equipo Rojo"),
            "hora_local": inicio, "categoria_inferida": "Fútbol", "sesion": None, "motivo_vigencia": "fecha_xtream_hoy",
        }
        eventos, descartados, _ = curador.curar_eventos([evento], [canal], self.hoy.date())
        self.assertEqual(len(descartados), 0)
        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0]["estado"], "confirmado")
        self.assertEqual(eventos[0]["fuentes"][0]["id_xtream"], "99")

    def test_tarjeta_probable_conserva_hora_colombia(self) -> None:
        canal = {
            "id_xtream": "77", "nombre_ui": f"{self.hoy.strftime('%d/%m')} 19:00 UFC Fight Night",
            "texto_normalizado": "UFC FIGHT NIGHT", "hora_local": self.hoy.replace(hour=19),
            "categoria_inferida": "Combate", "motivo_vigencia": "fecha_xtream_hoy",
        }
        tarjeta = curador.crear_probable_xtream(canal, self.tz)
        self.assertIsNotNone(tarjeta)
        self.assertEqual(tarjeta["estado"], "probable")
        self.assertEqual(tarjeta["hora_local_producto"], "19:00")

    def test_snooker_sin_directo_se_publica_en_canal_con_fuente_es_primero(self) -> None:
        ahora = datetime(2026, 8, 22, 22, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "Snooker: China Open · Final", "Snooker | 2026", ahora - timedelta(minutes=30), ahora + timedelta(minutes=90),
            [{"nombre": "EUROSPORT 1 DE", "id_xtream": "20"}, {"nombre": "EUROSPORT 1 ES", "id_xtream": "10"}], [], ahora,
        )
        self.assertIsNotNone(tarjeta)
        self.assertEqual(tarjeta["categoria"], "Snooker")
        self.assertEqual(tarjeta["estado"], "en_canal")
        self.assertEqual(tarjeta["metodo_correlacion"], "epg_deportivo_sin_etiqueta_directo")
        self.assertEqual(tarjeta["fuentes"][0]["id_xtream"], "10")
        self.assertEqual(tarjeta["fuentes"][0]["idioma"], "ES")

    def test_vuelta_futura_sin_directo_se_publica_programada(self) -> None:
        ahora = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "Rad: Vuelta a España · Etapa 1", "Etappenrennen | 2026", ahora + timedelta(minutes=60), ahora + timedelta(minutes=240),
            [{"nombre": "EUROSPORT 1 ES", "id_xtream": "10"}], [], ahora,
        )
        self.assertIsNotNone(tarjeta)
        self.assertEqual(tarjeta["categoria"], "Ciclismo")
        self.assertEqual(tarjeta["estado"], "programado")
        self.assertEqual(tarjeta["modo_presentacion"], "competicion")

    def test_repeticion_alemana_de_snooker_se_descarta(self) -> None:
        ahora = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "Wiederholung Snooker: China Open", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "EUROSPORT 1 ES", "id_xtream": "10"}], [], ahora,
        )
        self.assertIsNone(tarjeta)

    def test_guia_espanola_prevalece_sobre_alemana(self) -> None:
        programas = [
            {"clave": "E1", "canal": "DE | Eurosport 1", "titulo": "Snooker"},
            {"clave": "E1", "canal": "ES | Eurosport 1", "titulo": "Snooker"},
        ]
        elegidos = epg.seleccionar_guias_principales(programas)
        self.assertEqual(len(elegidos), 1)
        self.assertEqual(elegidos[0]["canal"], "ES | Eurosport 1")

    def test_nombre_eurosport_sin_prefijo_no_se_confunde_con_pais(self) -> None:
        self.assertEqual(epg.pais_epg("Eurosport 1 HD"), "")

    def test_championship_de_golf_no_es_futbol(self) -> None:
        self.assertEqual(curador.inferir_deporte("DP World Tour Nexo Championship Golf"), "Golf")

    def test_extraccion_en_canal_usa_guia_espanola_y_omite_repeticion(self) -> None:
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <tv>
          <programme channel="ES | Eurosport 1" start="20260822220000 +0000" stop="20260823010000 +0000"><title>Snooker: China Open · Final</title><desc>Snooker</desc></programme>
          <programme channel="DE | Eurosport 1" start="20260822220000 +0000" stop="20260823010000 +0000"><title>Snooker: China Open · Finale</title><desc>Snooker</desc></programme>
          <programme channel="ES | Eurosport 1" start="20260823000000 +0000" stop="20260823030000 +0000"><title>Rad: Vuelta a Espa\xc3\xb1a · Etapa 1</title><desc>Etappenrennen</desc></programme>
          <programme channel="ES | Eurosport 1" start="20260822230000 +0000" stop="20260823010000 +0000"><title>Wiederholung Snooker: China Open</title><desc>Snooker</desc></programme>
        </tv>'''.encode("utf-8")

        class Respuesta:
            content = xml
            def raise_for_status(self) -> None:
                return None

        ahora = datetime(2026, 8, 22, 22, 30, tzinfo=timezone.utc)
        mapa = {"E1": [{"nombre": "Eurosport 1 DE", "id_xtream": "2"}, {"nombre": "Eurosport 1 ES", "id_xtream": "1"}], "E2": [], "TDP": []}
        with patch.object(epg.requests, "get", return_value=Respuesta()):
            eventos, metricas = epg.extraer_eventos_epg(mapa, [], ahora.astimezone(self.tz), ahora)
        self.assertEqual(len(eventos), 2)
        self.assertEqual({e["categoria"] for e in eventos}, {"Snooker", "Ciclismo"})
        snooker = next(e for e in eventos if e["categoria"] == "Snooker")
        self.assertEqual(snooker["estado"], "en_canal")
        self.assertEqual(snooker["fuentes"][0]["id_xtream"], "1")
        self.assertEqual(metricas.get("veto_explicito"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
