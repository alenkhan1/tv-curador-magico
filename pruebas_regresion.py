# -*- coding: utf-8 -*-
"""Pruebas de regresión integrales del sistema curador e inyector."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import curador_eventos as curador
import inyector_epg as epg
from resolvedor_logos import resolver_logo_torneo


class PruebasSistemaCurador(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = curador.obtener_zona_aplicacion()
        self.hoy = datetime.now(self.tz).replace(hour=10, minute=0, second=0, microsecond=0)

    def test_logos_envueltos_en_cdn_proxy_para_evitar_error_403(self) -> None:
        logo = resolver_logo_torneo("WTA Cincinnati Open", "Tenis")
        self.assertTrue(logo.startswith("https://wsrv.nl/?url="))

    def test_epg_sin_marca_de_directo_es_rechazado_estrictamente(self) -> None:
        ahora = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "UFC 330: Makhachev vs Machado Garry", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 2 HD", "id_xtream": "10"}], [], ahora, consenso_directo=False,
            canal_epg="E2"
        )
        self.assertIsNone(tarjeta)

    def test_epg_con_consenso_directo_crea_evento_competicion(self) -> None:
        ahora = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
        tarjeta = epg.construir_evento_epg(
            "Snooker : Wuhan Open - Día 1", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 1 HD", "id_xtream": "10"}], [], ahora, consenso_directo=True,
            canal_epg="E1"
        )
        self.assertIsNotNone(tarjeta)
        self.assertEqual(tarjeta["categoria"], "Snooker")
        self.assertEqual(tarjeta["tipo_evento"], "sencillo")
        self.assertEqual(tarjeta["modo_presentacion"], "competicion")
        self.assertEqual(tarjeta["canal_epg"], "E1")
        self.assertTrue(tarjeta["logo_torneo"].startswith("https://wsrv.nl/?url="))

    def test_clave_canal_epg_no_confunde_primera_federacion_con_eurosport(self) -> None:
        canal_rfef = "SP: ES: 1ª Federación FHD"
        self.assertIsNone(epg.clave_canal_epg(canal_rfef))

        canal_e1 = "SP: ES: Eurosport 1 FHD"
        canal_e2 = "SP: ES: Eurosport 2 HD"
        canal_tdp = "SP: ES: Teledeporte FHD"

        self.assertEqual(epg.clave_canal_epg(canal_e1), "E1")
        self.assertEqual(epg.clave_canal_epg(canal_e2), "E2")
        self.assertEqual(epg.clave_canal_epg(canal_tdp), "TDP")

    def test_filtro_canales_espana_descarta_senales_extranjeras(self) -> None:
        self.assertTrue(epg.es_canal_espana("SP: ES: Eurosport 1 FHD"))
        self.assertTrue(epg.es_canal_espana("SP: ES: Teledeporte (Movil)"))
        self.assertFalse(epg.es_canal_espana("SP: DE: Eurosport 1"))
        self.assertFalse(epg.es_canal_espana("SP: FR: Eurosport 2"))
        self.assertFalse(epg.es_canal_espana("SP: UK: Eurosport 1 HD"))
        self.assertFalse(epg.es_canal_espana("SP: PL: Eurosport 2 HD"))

    def test_consolidar_eventos_epg_mantiene_aislados_e1_y_e2(self) -> None:
        ahora = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
        evento_e1 = epg.construir_evento_epg(
            "DIRECTO Snooker: Wuhan Open", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 1 FHD", "id_xtream": "101"}], [], ahora, consenso_directo=True,
            canal_epg="E1"
        )
        evento_e2 = epg.construir_evento_epg(
            "DIRECTO Snooker: Wuhan Open", "", ahora, ahora + timedelta(hours=2),
            [{"nombre": "SP: ES: Eurosport 2 HD", "id_xtream": "202"}], [], ahora, consenso_directo=True,
            canal_epg="E2"
        )
        self.assertIsNotNone(evento_e1)
        self.assertIsNotNone(evento_e2)

        consolidados = epg.consolidar_eventos_epg([evento_e1, evento_e2])
        self.assertEqual(len(consolidados), 2)
        self.assertEqual(len(consolidados[0]["fuentes"]), 1)
        self.assertEqual(len(consolidados[1]["fuentes"]), 1)
        self.assertEqual(consolidados[0]["fuentes"][0]["id_xtream"], "101")
        self.assertEqual(consolidados[1]["fuentes"][0]["id_xtream"], "202")

    def test_canal_xtream_con_duelo_en_subtitulo_se_analiza_correctamente(self) -> None:
        canal = {
            "id_xtream": "530165",
            "nombre_ui": "13:00 ▪ Little League Baseball ▪ Willemstad Curacao vs. Seoul South Korea ▪ HD ▪▪",
            "texto_normalizado": "13 00 LITTLE LEAGUE BASEBALL WILLEMSTAD CURACAO VS SEOUL SOUTH KOREA HD",
            "tokens": curador.tokenizar("Little League Baseball Willemstad Curacao vs Seoul South Korea"),
            "hora_local": self.hoy.replace(hour=13),
            "categoria_inferida": "Béisbol",
            "sesion": None,
            "motivo_vigencia": "fecha_xtream_hoy"
        }
        evento = curador.crear_probable_xtream(canal, self.tz, {})
        self.assertIsNotNone(evento)
        self.assertEqual(evento["tipo_evento"], "duelo")
        self.assertEqual(evento["equipo_local"], "Willemstad Curacao")
        self.assertEqual(evento["equipo_visitante"], "Seoul South Korea")


if __name__ == "__main__":
    unittest.main(verbosity=2)
