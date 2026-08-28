"""Gasto acumulado por día.

`max_budget_usd` corta por sesión: diez sesiones de $2 no avisan de nada,
aunque ese mismo día se hayan gastado $20. Esto lleva la cuenta aparte, por
día, en disco — y hay que convertir el acumulado de sesión que manda el SDK
(`COST_UPDATE`) en un incremento, o sumar el total tal cual doblaría el
gasto en cada turno.
"""

from __future__ import annotations

from datetime import date

import pytest

from jarvis.core.gasto import Gasto
from jarvis.events import EventBus, EventType


@pytest.fixture
def gasto(tmp_path):
    return Gasto(tmp_path / "gasto.json")


class TestEscuchar:
    def test_un_turno_suma_al_dia_de_hoy(self, gasto):
        bus = EventBus()
        gasto.escuchar(bus)

        bus.emit(EventType.COST_UPDATE, total_usd=0.0142)

        assert gasto.hoy() == pytest.approx(0.0142)

    def test_solo_suma_el_incremento_no_el_acumulado(self, gasto):
        # El caso que motiva todo esto: el SDK manda el total DE LA SESIÓN en
        # cada turno, no lo gastado en ese turno. Sumar el valor tal cual
        # doblaría (y trilicaría...) el gasto de cada sesión.
        bus = EventBus()
        gasto.escuchar(bus)

        bus.emit(EventType.COST_UPDATE, total_usd=0.05)
        bus.emit(EventType.COST_UPDATE, total_usd=0.08)
        bus.emit(EventType.COST_UPDATE, total_usd=0.12)

        assert gasto.hoy() == pytest.approx(0.12)

    def test_otros_eventos_no_afectan(self, gasto):
        bus = EventBus()
        gasto.escuchar(bus)

        bus.emit(EventType.LOG, message="hola")

        assert gasto.hoy() == 0.0

    def test_persiste_entre_instancias(self, tmp_path):
        archivo = tmp_path / "gasto.json"
        bus = EventBus()
        Gasto(archivo).escuchar(bus)
        bus.emit(EventType.COST_UPDATE, total_usd=0.05)

        # Una `Gasto` nueva, como la que crea cada petición a /api/gasto.
        assert Gasto(archivo).hoy() == pytest.approx(0.05)


class TestLectura:
    def test_sin_archivo_da_cero(self, gasto):
        assert gasto.hoy() == 0.0
        assert gasto.mes_actual() == 0.0
        assert gasto.ultimos_dias(7) == {}

    def test_mes_actual_suma_solo_los_dias_de_este_mes(self, gasto):
        mes = date.today().strftime("%Y-%m")
        gasto._escribir({
            f"{mes}-01": 1.0,
            f"{mes}-02": 2.0,
            "1999-01-01": 100.0,  # de otro mes: no debe contar
        })

        assert gasto.mes_actual() == pytest.approx(3.0)

    def test_ultimos_dias_mas_reciente_primero(self, gasto):
        gasto._escribir({"2024-01-01": 1.0, "2024-03-01": 3.0, "2024-02-01": 2.0})

        assert list(gasto.ultimos_dias(2)) == ["2024-03-01", "2024-02-01"]

    def test_un_archivo_corrupto_no_revienta(self, gasto):
        gasto.archivo.parent.mkdir(parents=True, exist_ok=True)
        gasto.archivo.write_text("esto no es json", encoding="utf-8")

        assert gasto.hoy() == 0.0
