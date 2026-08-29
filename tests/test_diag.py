"""El diagnóstico es la herramienta a la que se acude cuando algo va mal.

Si se muere a la primera excepción deja de servir justo cuando más falta hace.
Estos tests fijan esa propiedad: pase lo que pase, llega al final y reporta.

Nacen de un fallo real: `sounddevice` lanza `OSError` (no `ImportError`) cuando
falta PortAudio, y eso tumbaba el diagnóstico entero con un traceback en lugar
de marcar esa línea con una equis y continuar.
"""

from __future__ import annotations

import jarvis.diag as diag


class TestSeguro:
    def test_devuelve_el_valor_cuando_no_hay_fallo(self):
        assert diag._seguro(lambda: 42) == 42

    def test_traga_la_excepcion_y_devuelve_none(self):
        def revienta():
            raise RuntimeError("PortAudio library not found")

        assert diag._seguro(revienta) is None

    def test_pasa_los_argumentos(self):
        assert diag._seguro(lambda a, b: a + b, 2, 3) == 5

    def test_sobrevive_a_cualquier_tipo_de_excepcion(self):
        for error in (OSError, ValueError, KeyboardInterrupt, MemoryError):
            def revienta(e=error):
                raise e("lo que sea")

            # KeyboardInterrupt y MemoryError no heredan de Exception en todos
            # los casos; comprobamos que al menos los normales no escapan.
            if issubclass(error, Exception):
                assert diag._seguro(revienta) is None


class TestEstaInstalado:
    def test_reconoce_un_paquete_presente(self):
        assert diag._esta_instalado("json")

    def test_reconoce_uno_ausente(self):
        assert not diag._esta_instalado("paquete_que_no_existe_en_absoluto_xyz")


class TestUnaLinea:
    def test_aplana_saltos_de_linea(self):
        assert "\n" not in diag._una_linea(ValueError("una\nlínea\nrota"))

    def test_recorta_lo_muy_largo(self):
        assert len(diag._una_linea(ValueError("x" * 500))) < 100

    def test_una_excepcion_sin_mensaje_da_su_tipo(self):
        assert diag._una_linea(ValueError()) == "ValueError"


class TestNoSeCae:
    def test_termina_aunque_todas_las_secciones_revienten(self, monkeypatch):
        """La garantía central: siempre llega al final y devuelve 0."""
        secciones = [
            "_comprobar_entorno", "_comprobar_credenciales",
            "_comprobar_dependencias", "_comprobar_audio",
            "_probar_voz", "_probar_transcripcion", "_probar_microfono",
        ]
        for nombre in secciones:
            def revienta(*_a, _n=nombre):
                raise OSError(f"{_n} está roto")

            monkeypatch.setattr(diag, nombre, revienta)

        assert diag.ejecutar_diagnostico() == 0

    def test_una_seccion_rota_no_impide_las_siguientes(self, monkeypatch):
        ejecutadas: list[str] = []

        def revienta(*_a):
            raise OSError("PortAudio library not found")

        for nombre in ("_comprobar_entorno", "_comprobar_credenciales",
                       "_comprobar_dependencias", "_probar_voz",
                       "_probar_transcripcion"):
            monkeypatch.setattr(diag, nombre, lambda *_a, _n=nombre: ejecutadas.append(_n))

        monkeypatch.setattr(diag, "_comprobar_audio", revienta)
        monkeypatch.setattr(diag, "_probar_microfono", lambda *_a: ejecutadas.append("mic"))

        diag.ejecutar_diagnostico()

        assert "mic" in ejecutadas, "lo posterior al fallo debe ejecutarse igual"
        assert len(ejecutadas) == 6


class TestShimDeLotesDeWindows:
    """Reportado en vivo: `--diag` daba ✓ al CLI de Claude Code y aun así
    J.A.R.V.I.S. escuchaba, transcribía y no contestaba nunca. Lo que `npm
    install -g` deja en Windows es un envoltorio `claude.cmd`, y el Agent SDK
    se niega a ejecutar un .cmd (exige el .exe nativo). El ✓ mandaba a buscar
    el fallo justo donde no estaba.

    Windows se fuerza siempre: en el sandbox y en la CI esto es Linux, y un
    test que dependa de esa casualidad no probaría nada.
    """

    def test_reconoce_el_cmd_de_npm_en_windows(self, monkeypatch):
        monkeypatch.setattr(diag.platform, "system", lambda: "Windows")

        assert diag._es_shim_de_lotes(r"C:\Users\x\AppData\Roaming\npm\claude.CMD")
        assert diag._es_shim_de_lotes(r"C:\Users\x\claude.bat")

    def test_el_exe_nativo_es_valido(self, monkeypatch):
        monkeypatch.setattr(diag.platform, "system", lambda: "Windows")

        assert not diag._es_shim_de_lotes(r"C:\Program Files\claude\claude.exe")

    def test_fuera_de_windows_no_avisa(self, monkeypatch):
        # Un .cmd en Linux no lo produce ningún instalador: avisar ahí sería
        # ruido. Se fuerza el sistema en vez de confiar en dónde corre esto.
        monkeypatch.setattr(diag.platform, "system", lambda: "Linux")

        assert not diag._es_shim_de_lotes("/home/x/claude.cmd")

    def test_el_entorno_lo_reporta_como_fallo(self, monkeypatch, capsys):
        monkeypatch.setattr(diag.platform, "system", lambda: "Windows")
        monkeypatch.setattr(diag.shutil, "which", lambda _: r"C:\npm\claude.CMD")

        diag._comprobar_entorno()

        salida = capsys.readouterr().out
        assert "install.ps1" in salida, "hay que decir cómo arreglarlo"
        assert "claude.CMD" in salida
