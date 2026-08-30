"""Herramientas de sistema: volumen, abrir, bloquear, hora.

Lo que toca Windows no puedo probarlo aquí, así que está separado de lo que sí:
la validación de lo que se pide, la lista de intérpretes prohibidos y la
redacción de la hora se ejercitan en cualquier sistema. Las llamadas al
sistema operativo se simulan.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from jarvis.tools import sistema


class TestVolumen:
    @pytest.mark.parametrize("accion", ["subir", "bajar", "silenciar"])
    def test_acepta_las_tres_acciones(self, accion, monkeypatch):
        llamadas = []
        monkeypatch.setattr(sistema, "SISTEMA", "Windows")
        monkeypatch.setattr(
            sistema, "_volumen_windows",
            lambda a, p: llamadas.append((a, p)) or "hecho",
        )
        assert sistema.ajustar_volumen(accion) == "hecho"
        assert llamadas == [(accion, sistema.PASOS_VOLUMEN)]

    def test_rechaza_una_accion_inventada(self):
        respuesta = sistema.ajustar_volumen("triplicar")
        assert "No sé hacer" in respuesta

    def test_en_un_sistema_desconocido_lo_dice(self, monkeypatch):
        monkeypatch.setattr(sistema, "SISTEMA", "Plan9")
        assert "Plan9" in sistema.ajustar_volumen("subir")

    def test_silenciar_manda_una_sola_pulsación(self, monkeypatch):
        """Repetir «silenciar» lo alternaría varias veces y quedaría igual."""
        enviados = []
        monkeypatch.setattr(sistema, "SISTEMA", "Windows")

        class FakeUser32:
            @staticmethod
            def GetForegroundWindow():  # noqa: N802
                return 1

            @staticmethod
            def SendMessageW(*a):  # noqa: N802
                enviados.append(a)

        class FakeWindll:
            user32 = FakeUser32()

        import ctypes

        monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
        sistema.ajustar_volumen("silenciar")
        assert len(enviados) == 1

        enviados.clear()
        sistema.ajustar_volumen("subir", pasos=3)
        assert len(enviados) == 3


class TestAbrir:
    @pytest.mark.parametrize(
        "peligroso",
        ["cmd", "cmd.exe", "PowerShell.exe", "pwsh", "bash",
         "C:/Windows/System32/cmd.exe", "/usr/bin/bash", "regedit"],
    )
    def test_no_abre_interpretes_de_comandos(self, peligroso):
        """La defensa clave.

        Todo comando de shell se lee en voz alta antes de ejecutarse. Si
        J.A.R.V.I.S. pudiera «abrir» una consola, bastaría eso para ejecutar
        cualquier cosa sin que nadie la enunciara: el sistema de permisos
        entero quedaría sin efecto.
        """
        respuesta = sistema.abrir(peligroso)
        assert "No abro" in respuesta
        assert "intérprete" in respuesta

    def test_no_se_burla_con_una_ruta_larga(self):
        assert "No abro" in sistema.abrir(r"C:\Users\TU-USUARIO\..\..\Windows\System32\cmd.exe")

    def test_vacio_lo_dice(self):
        assert "qué abrir" in sistema.abrir("   ")

    def test_abre_lo_normal(self, monkeypatch):
        abiertos = []
        monkeypatch.setattr(sistema, "SISTEMA", "Linux")
        monkeypatch.setattr(
            sistema.subprocess, "Popen", lambda cmd: abiertos.append(cmd)
        )
        respuesta = sistema.abrir("https://ejemplo.com")
        assert abiertos == [["xdg-open", "https://ejemplo.com"]]
        assert "Abriendo" in respuesta

    def test_si_no_existe_lo_dice_sin_reventar(self, monkeypatch):
        monkeypatch.setattr(sistema, "SISTEMA", "Linux")

        def falla(_cmd):
            raise FileNotFoundError

        monkeypatch.setattr(sistema.subprocess, "Popen", falla)
        assert "No he encontrado" in sistema.abrir("programa-fantasma")


class TestBloquear:
    def test_sistema_desconocido_no_revienta(self, monkeypatch):
        monkeypatch.setattr(sistema, "SISTEMA", "Plan9")
        monkeypatch.setattr(sistema.shutil, "which", lambda _: None)
        assert "No sé bloquear" in sistema.bloquear_pantalla()


class TestHora:
    def test_se_lee_en_español(self):
        texto = sistema.decir_hora(datetime(2026, 8, 22, 9, 5))
        assert "Son las 9 y 05" in texto
        assert "sábado 22 de agosto de 2026" in texto

    def test_no_depende_del_idioma_del_equipo(self):
        """`strftime("%A")` daría "Friday" en un Windows en inglés."""
        texto = sistema.decir_hora(datetime(2026, 1, 1, 0, 0))
        assert "jueves" in texto and "enero" in texto
        assert not any(c in texto for c in ("Thursday", "January"))


class TestEstadoDelEquipo:
    """Forzado con `monkeypatch` en vez de leer la máquina real: los
    porcentajes de CPU/RAM/disco de este sandbox no son parte del contrato,
    sólo el formato con el que se leen.
    """

    def _simular(self, monkeypatch, *, cpu=42.0, ram_percent=60.0,
                 ram_used=6 * 1024**3, ram_total=10 * 1024**3,
                 disco_percent=50.0, disco_free=20 * 1024**3, bateria=None):
        import types

        monkeypatch.setattr(sistema.psutil, "cpu_percent", lambda interval=None: cpu)
        monkeypatch.setattr(
            sistema.psutil, "virtual_memory",
            lambda: types.SimpleNamespace(
                percent=ram_percent, used=ram_used, total=ram_total
            ),
        )
        monkeypatch.setattr(
            sistema.psutil, "disk_usage",
            lambda _ruta: types.SimpleNamespace(percent=disco_percent, free=disco_free),
        )
        monkeypatch.setattr(sistema.psutil, "sensors_battery", lambda: bateria)

    def test_incluye_cpu_ram_y_disco(self, monkeypatch):
        self._simular(monkeypatch, cpu=42.0, ram_percent=60.0, disco_percent=50.0)

        texto = sistema.estado_del_equipo()

        assert "CPU al 42 por ciento" in texto
        assert "Memoria al 60 por ciento" in texto
        assert "Disco al 50 por ciento" in texto

    def test_sin_bateria_no_la_menciona(self, monkeypatch):
        # Un equipo de sobremesa: psutil.sensors_battery() devuelve None.
        self._simular(monkeypatch, bateria=None)

        assert "batería" not in sistema.estado_del_equipo().lower()

    def test_con_bateria_dice_el_porcentaje_y_si_carga(self, monkeypatch):
        import types

        self._simular(
            monkeypatch,
            bateria=types.SimpleNamespace(percent=77.0, power_plugged=True),
        )

        texto = sistema.estado_del_equipo()

        assert "Batería al 77 por ciento" in texto
        assert "cargando" in texto

    def test_bateria_sin_cargador_lo_dice(self, monkeypatch):
        import types

        self._simular(
            monkeypatch,
            bateria=types.SimpleNamespace(percent=30.0, power_plugged=False),
        )

        assert "sin cargador" in sistema.estado_del_equipo()

    def test_disco_invalido_no_revienta(self, monkeypatch):
        # Una ruta de workspace que ya no existe en este equipo: no debe
        # tumbar el resto del informe.
        self._simular(monkeypatch)
        monkeypatch.setattr(
            sistema.psutil, "disk_usage",
            lambda _ruta: (_ for _ in ()).throw(OSError("ruta no válida")),
        )

        texto = sistema.estado_del_equipo()

        assert "CPU" in texto
        assert "Disco" not in texto


class TestRegistro:
    def test_las_herramientas_quedan_registradas(self):
        nombres = {t.name for t in sistema.herramientas_de_sistema()}
        assert nombres == {
            "volumen", "abrir", "bloquear_pantalla", "hora", "estado_del_equipo",
        }

    def test_van_en_el_mismo_servidor_que_la_memoria(self, tmp_path):
        # Dos servidores MCP con el mismo nombre se pisarían.
        from jarvis.core.memory import Memory
        from jarvis.tools.memory_tool import construir_servidor_jarvis

        servidor = construir_servidor_jarvis(Memory(tmp_path))
        assert servidor["name"] == "jarvis"


class TestPermisos:
    """Qué se ejecuta solo y qué exige un «sí» hablado."""

    def test_lo_inofensivo_no_pregunta(self):
        from jarvis.core.permissions import PROPIAS_AUTOMATICAS

        assert "mcp__jarvis__volumen" in PROPIAS_AUTOMATICAS
        assert "mcp__jarvis__hora" in PROPIAS_AUTOMATICAS
        assert "mcp__jarvis__estado_del_equipo" in PROPIAS_AUTOMATICAS

    def test_abrir_y_bloquear_siempre_preguntan(self):
        from jarvis.core.permissions import PROPIAS_AUTOMATICAS

        assert "mcp__jarvis__abrir" not in PROPIAS_AUTOMATICAS
        assert "mcp__jarvis__bloquear_pantalla" not in PROPIAS_AUTOMATICAS
