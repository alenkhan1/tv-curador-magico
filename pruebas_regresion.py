# -*- coding: utf-8 -*-
"""Pruebas de regresión integrales del sistema multicanal oficial."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import curador_eventos as curador
import inyector_epg as inyector
from resolvedor_logos import resolver_logo_torneo


class PruebasSistemaMulticanal(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = curador.obtener_zona_aplicacion()
        self.hoy = datetime.now(self.tz).replace(hour=10, minute=0, second=0, microsecond=0)

    def test_clasificador_descarta_dazn_mascarado_y_aisla_paises(self) -> None:
        canal_dazn_falso = "04 | DAZN (ES) EUROSPORTS 2"
        self.assertIsNone(inyector.clasificar_canal_lineal(canal_dazn_falso))

        canal_tdp_valido = "Spain: ES: TELEDEPORTE FHD"
        self.assertEqual(inyector.clasificar_canal_lineal(canal_tdp_valido), "TDP")

        canal_es1_valido = "Spain: ES: EUROSPORT 1 HD"
        self.assertEqual(inyector.clasificar_canal_lineal(canal_es1_valido), "E1")

    def test_clasificador_canales_espn_win_dsports(self) -> None:
        self.assertEqual(inyector.clasificar_canal_lineal("CO | WIN SPORTS+ HD"), "WIN_PLUS")
        self.assertEqual(inyector.clasificar_canal_lineal("CO | WIN SPORTS HD"), "WIN_BASICO")
        self.assertEqual(inyector.clasificar_canal_lineal("CO | DIRECTV SPORTS 2 HD"), "DSPORTS_2")
        self.assertEqual(inyector.clasificar_canal_lineal("LATAM | ESPN 3 FHD"), "ESPN_3")

    def test_huella_canonica_unifica_duelos_independiente_del_orden(self) -> None:
        ev1 = {"categoria": "Fútbol", "equipo_local": "Real Madrid", "equipo_visitante": "Barcelona", "hora_utc": "2026-08-24T19:00:00Z"}
        ev2 = {"categoria": "Fútbol", "equipo_local": "Barcelona", "equipo_visitante": "Real Madrid", "hora_utc": "2026-08-24T19:00:00Z"}
        self.assertEqual(curador.generar_huella_canonica(ev1), curador.generar_huella_canonica(ev2))

    def test_huella_canonica_unifica_ciclismo_misma_etapa(self) -> None:
        ev_es = {"categoria": "Ciclismo", "torneo": "La Vuelta", "subtitulo": "Etapa 2 - Mónaco", "hora_utc": "2026-08-24T13:00:00Z"}
        ev_espn = {"categoria": "Ciclismo", "torneo": "La Vuelta", "subtitulo": "Etapa 2 - Mónaco", "hora_utc": "2026-08-24T13:30:00Z"}
        self.assertEqual(curador.generar_huella_canonica(ev_es), curador.generar_huella_canonica(ev_espn))

    def test_fusion_multicanal_unifica_fuentes(self) -> None:
        ev1 = {
            "id": "e1", "titulo": "La Vuelta", "torneo": "La Vuelta", "categoria": "Ciclismo",
            "subtitulo": "Etapa 2", "hora_utc": "2026-08-24T13:00:00Z", "fuentes": [{"nombre": "Eurosport 1", "id_xtream": "101"}]
        }
        ev2 = {
            "id": "e2", "titulo": "La Vuelta", "torneo": "La Vuelta", "categoria": "Ciclismo",
            "subtitulo": "Etapa 2", "hora_utc": "2026-08-24T13:00:00Z", "fuentes": [{"nombre": "ESPN 2", "id_xtream": "202"}]
        }
        fusionados = inyector.fusionar_eventos_multicanal([ev1], [ev2])
        self.assertEqual(len(fusionados), 1)
        self.assertEqual(len(fusionados[0]["fuentes"]), 2)
        self.assertEqual({f["id_xtream"] for f in fusionados[0]["fuentes"]}, {"101", "202"})

    def test_resolucion_logo_con_proxy_cdn(self) -> None:
        logo_cincinnati = resolver_logo_torneo("WTA Cincinnati Open", "Tenis")
        self.assertTrue(logo_cincinnati.startswith("https://wsrv.nl/?url="))


if __name__ == "__main__":
    unittest.main(verbosity=2)
