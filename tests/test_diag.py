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


class TestPruebaDelCerebro:
    """`--diag` daba todo verde con el cerebro muerto: comprobaba que el CLI
    existía, nunca que Claude contestara. El único sitio donde se veía el
    fallo era en mitad de una conversación, atascado en «pensando».
    """

    def _con_clave(self, monkeypatch):
        s = diag.load_settings()
        s.anthropic_api_key = "sk-ant-de-prueba"
        monkeypatch.setattr(diag, "load_settings", lambda *_a, **_k: s)
        return s

    def _responde(self, monkeypatch, resultado=None, error=None, stderr=None):
        """Sustituye el turno real por uno de mentira, sin tocar asyncio.run:
        así se ejercita el camino de verdad, corrutina incluida."""
        async def _falso(_tope, quejas=None):
            if quejas is not None:
                quejas.extend(stderr or [])
            if error is not None:
                raise error
            return resultado

        monkeypatch.setattr(diag, "_hablar_con_claude", _falso)

    def test_sin_clave_se_omite(self, monkeypatch, capsys):
        s = diag.load_settings()
        s.anthropic_api_key = ""
        monkeypatch.setattr(diag, "load_settings", lambda *_a, **_k: s)

        diag._probar_cerebro()

        assert "modo demostración" in capsys.readouterr().out

    def test_reporta_la_respuesta(self, monkeypatch, capsys):
        self._con_clave(monkeypatch)
        self._responde(monkeypatch, resultado=("listo", None))

        diag._probar_cerebro()

        assert "Claude responde" in capsys.readouterr().out

    def test_un_cuelgue_se_reporta_como_fallo(self, monkeypatch, capsys):
        # El caso real: el CLI arranca y nunca produce nada.
        self._con_clave(monkeypatch)
        self._responde(monkeypatch, error=TimeoutError())

        diag._probar_cerebro()

        salida = capsys.readouterr().out
        assert "No ha respondido" in salida
        assert "claude" in salida, "hay que decir qué probar a continuación"

    def test_una_excepcion_se_muestra_cruda(self, monkeypatch, capsys):
        # El mensaje del SDK es justo lo que hace falta para diagnosticar:
        # tragárselo dejaría otra vez un fallo sin explicación.
        self._con_clave(monkeypatch)
        self._responde(monkeypatch, error=RuntimeError("CLI process exited with code 1"))

        diag._probar_cerebro()

        assert "CLI process exited with code 1" in capsys.readouterr().out

    def test_un_error_de_claude_se_reporta(self, monkeypatch, capsys):
        self._con_clave(monkeypatch)
        self._responde(monkeypatch, resultado=("", "No hay saldo en la cuenta."))

        diag._probar_cerebro()

        assert "No hay saldo" in capsys.readouterr().out

    def test_un_turno_mudo_no_pasa_por_bueno(self, monkeypatch, capsys):
        self._con_clave(monkeypatch)
        self._responde(monkeypatch, resultado=("", None))

        diag._probar_cerebro()

        assert "sin texto" in capsys.readouterr().out


class TestQuejasDelCli:
    """El stderr del CLI es lo único que queda cuando arranca pero no
    contesta: el turno no da error, simplemente no llega nada. Perderlo
    dejaba el fallo sin una sola pista que leer.
    """

    def test_muestra_lo_que_dijo_el_cli(self, capsys):
        diag._mostrar_quejas(["Error: unknown option --effort\n"])

        salida = capsys.readouterr().out
        assert "unknown option --effort" in salida

    def test_dice_expresamente_que_no_dijo_nada(self, capsys):
        # Silencio y "no lo hemos mirado" son diagnósticos distintos.
        diag._mostrar_quejas([])

        assert "no ha dicho nada" in capsys.readouterr().out

    def test_recorta_conservando_el_final(self, capsys):
        # Si algo falla, el motivo está en las últimas líneas, no en las
        # primeras: recortar por el otro lado tiraría justo la pista.
        diag._mostrar_quejas(["\n".join(f"linea {i}" for i in range(40))], maximo=5)

        salida = capsys.readouterr().out
        assert "linea 39" in salida
        assert "linea 0" not in salida
        assert "35 líneas antes" in salida

    def test_el_cuelgue_incluye_las_quejas(self, monkeypatch, capsys):
        s = diag.load_settings()
        s.anthropic_api_key = "sk-ant-de-prueba"
        monkeypatch.setattr(diag, "load_settings", lambda *_a, **_k: s)

        async def _cuelga(_tope, quejas=None):
            if quejas is not None:
                quejas.append("Error: something went wrong in the CLI")
            raise TimeoutError

        monkeypatch.setattr(diag, "_hablar_con_claude", _cuelga)

        diag._probar_cerebro()

        assert "something went wrong" in capsys.readouterr().out


class TestPaquetesCudaInstalados:
    """Sin esto, el aviso de GPU sin aprovechar repetía siempre el mismo
    `pip install`, incluso después de que Jeremy ya lo hubiera seguido —
    indistinguible de que el consejo no hubiera servido de nada. Se fuerza
    la presencia/ausencia vía `sys.modules` en vez de depender de si este
    sandbox los tiene instalados de verdad.
    """

    def _simular_instalado(self, monkeypatch, *nombres):
        import importlib.util
        import sys
        import types

        for nombre in nombres:
            modulo = types.ModuleType(nombre)
            modulo.__spec__ = importlib.util.spec_from_loader(nombre, loader=None)
            monkeypatch.setitem(sys.modules, nombre, modulo)

    def _simular_ausente(self, monkeypatch, *nombres):
        import sys

        for nombre in nombres:
            monkeypatch.delitem(sys.modules, nombre, raising=False)

    def test_ambos_instalados(self, monkeypatch):
        self._simular_instalado(monkeypatch, "nvidia.cublas", "nvidia.cudnn")

        assert diag._paquetes_cuda_instalados() is True

    def test_ninguno_instalado(self, monkeypatch):
        self._simular_ausente(monkeypatch, "nvidia.cublas", "nvidia.cudnn", "nvidia")

        assert diag._paquetes_cuda_instalados() is False

    def test_solo_uno_instalado_no_cuenta(self, monkeypatch):
        # Un cuBLAS a medio instalar es justo el caso real que motivó esto.
        self._simular_instalado(monkeypatch, "nvidia.cublas")
        self._simular_ausente(monkeypatch, "nvidia.cudnn")

        assert diag._paquetes_cuda_instalados() is False


class _TranscriberDeMentira:
    """Doble mínimo: sólo lo que `_probar_transcripcion` lee de verdad."""

    def __init__(self, *, device: str, compute_type: str, gpu_detectada: bool) -> None:
        self.device = device
        self.compute_type = compute_type
        self.gpu_detectada = gpu_detectada
        self.motivo_repliegue: str | None = None

    def cargar(self) -> None:
        return None


class TestAvisoGpuSinAprovechar:
    """El aviso de `_probar_transcripcion` no puede repetir un `pip install`
    que ya se siguió: hay que distinguir "falta instalar" de "ya instalado,
    el problema es otro"."""

    def _forzar_transcriber(self, monkeypatch, doble):
        monkeypatch.setattr("jarvis.audio.stt.Transcriber", lambda *_a, **_k: doble)

    def test_sin_paquetes_sugiere_instalarlos(self, monkeypatch, capsys):
        monkeypatch.delitem(__import__("sys").modules, "nvidia.cublas", raising=False)
        monkeypatch.delitem(__import__("sys").modules, "nvidia.cudnn", raising=False)
        doble = _TranscriberDeMentira(device="cpu", compute_type="int8", gpu_detectada=True)
        self._forzar_transcriber(monkeypatch, doble)

        diag._probar_transcripcion()

        salida = capsys.readouterr().out
        assert "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12" in salida

    def test_con_paquetes_no_repite_el_pip_install(self, monkeypatch, capsys):
        import importlib.util
        import sys
        import types

        for nombre in ("nvidia.cublas", "nvidia.cudnn"):
            modulo = types.ModuleType(nombre)
            modulo.__spec__ = importlib.util.spec_from_loader(nombre, loader=None)
            monkeypatch.setitem(sys.modules, nombre, modulo)
        doble = _TranscriberDeMentira(device="cpu", compute_type="int8", gpu_detectada=True)
        self._forzar_transcriber(monkeypatch, doble)

        diag._probar_transcripcion()

        salida = capsys.readouterr().out
        assert "pip install" not in salida
        assert "ya están instalados" in salida

    def test_en_gpu_no_dice_nada_de_paquetes(self, monkeypatch, capsys):
        doble = _TranscriberDeMentira(device="cuda", compute_type="float16", gpu_detectada=True)
        self._forzar_transcriber(monkeypatch, doble)

        diag._probar_transcripcion()

        salida = capsys.readouterr().out
        assert "aprovechando" not in salida


class _MicTranscriberDeMentira:
    """Simula el repliegue en la primera transcripción real (el caso de
    Jeremy: la carga tuvo éxito en GPU, y sólo reventó al transcribir)."""

    def __init__(self, motivo_nuevo: str) -> None:
        self.motivo_repliegue: str | None = None
        self._motivo_nuevo = motivo_nuevo

    def _transcribir_sync(self, audio):  # noqa: ANN001, ANN202, ARG002
        self.motivo_repliegue = self._motivo_nuevo
        return "hola jarvis"


class TestAvisoCudaEnPruebaDeMicrofono:
    """El repliegue detectado a media transcripción (no al cargar) tiene su
    propio aviso en `_probar_microfono`, con el mismo fallo: repetía el
    `pip install` aunque ya se hubiera seguido."""

    def _forzar_sounddevice(self, monkeypatch):
        import sys
        import types

        import numpy as np

        falso = types.SimpleNamespace(
            rec=lambda n, samplerate, channels, dtype: np.full(  # noqa: ARG005
                (n, channels), 0.5, dtype=dtype
            ),
            wait=lambda: None,
        )
        monkeypatch.setitem(sys.modules, "sounddevice", falso)

    def test_sin_paquetes_sugiere_instalarlos(self, monkeypatch, capsys):
        import sys

        monkeypatch.delitem(sys.modules, "nvidia.cublas", raising=False)
        monkeypatch.delitem(sys.modules, "nvidia.cudnn", raising=False)
        self._forzar_sounddevice(monkeypatch)
        doble = _MicTranscriberDeMentira("cuda/float32 falló transcribiendo (...)")

        diag._probar_microfono(doble, segundos=0.1)

        salida = capsys.readouterr().out
        assert "pip install nvidia-cublas-cu12 nvidia-cudnn-cu12" in salida

    def test_con_paquetes_no_repite_el_pip_install(self, monkeypatch, capsys):
        import importlib.util
        import sys
        import types

        for nombre in ("nvidia.cublas", "nvidia.cudnn"):
            modulo = types.ModuleType(nombre)
            modulo.__spec__ = importlib.util.spec_from_loader(nombre, loader=None)
            monkeypatch.setitem(sys.modules, nombre, modulo)
        self._forzar_sounddevice(monkeypatch)
        doble = _MicTranscriberDeMentira("cuda/float32 falló transcribiendo (...)")

        diag._probar_microfono(doble, segundos=0.1)

        salida = capsys.readouterr().out
        assert "pip install" not in salida
        assert "no es" in salida and "lo que falta por instalar" in salida
