"""El cableado de `main.py`: instancia única, y que `asyncio.wait` no se trague
ni conversaciones ni excepciones.

El motivo de que estos tests existan es un fallo real: cambiar
`asyncio.gather(*tareas)` por `asyncio.wait(..., FIRST_COMPLETED)` --necesario
para que `--sim --web` deje de colgarse-- rompía el modo `--texto` sin que
ningún test lo hubiera avisado. Con `FakeMicStream` (audio vacío), el bucle de
audio termina casi al instante; tratar eso como señal de apagado cortaba la
conversación antes de que Claude respondiera. Se descubrió probándolo de
verdad en la terminal, no leyendo el código.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import socket
import sys
import threading
import time

import pytest

from jarvis import instancia, main
from jarvis.audio.player import NullPlayer
from jarvis.audio.stt import FakeTranscriber
from jarvis.audio.wakeword import NullWakeWord
from jarvis.core.core import JarvisCore
from jarvis.events import EventBus
from jarvis.ui.console import ConsoleHUD

from .conftest import ControlledMic, FakeAgent, FakeTTS


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _args(**cambios) -> argparse.Namespace:
    base = dict(
        texto=False, demo=True, sim=None, muda=True, web=False, puerto=8765,
        https=False, lan=False, sin_navegador=False, sin_bandeja=True, diag=False,
        arrancar_con_windows=False, quitar_del_inicio=False, config=None, verbose=False,
    )
    base.update(cambios)
    return argparse.Namespace(**base)


class TestBanderasNuevas:
    def test_por_defecto_desactivadas(self):
        args = main._parse_args([])
        assert args.sin_navegador is False
        assert args.sin_bandeja is False

    def test_se_pueden_activar(self):
        args = main._parse_args(["--sin-navegador", "--sin-bandeja"])
        assert args.sin_navegador is True
        assert args.sin_bandeja is True


class TestInstanciaUnica:
    """Con el arranque automático, tener dos J.A.R.V.I.S. deja de ser
    hipotético. El chequeo va antes de tocar audio ni cargar modelos."""

    async def test_no_carga_los_modelos_si_ya_hay_otra(self, settings, monkeypatch):
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(instancia, "reservar", lambda *_a, **_k: None)
        monkeypatch.setattr(instancia, "huella_ajena", lambda *_a, **_k: None)

        def explota(*_a, **_k):  # noqa: ANN002, ANN003
            raise AssertionError("no debería construir nada con otra instancia viva")

        monkeypatch.setattr(main, "_construir", explota)

        assert await main._main_async(_args(), []) == 0

    async def test_abre_el_hud_de_la_que_ya_vive(self, settings, monkeypatch):
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(instancia, "reservar", lambda *_a, **_k: None)
        monkeypatch.setattr(
            instancia,
            "huella_ajena",
            lambda *_a, **_k: instancia.Huella(pid=1, url="http://localhost:8765"),
        )
        abiertas = []
        monkeypatch.setattr(
            "jarvis.ui.navegador.abrir", lambda u, **_k: abiertas.append(u) or True
        )

        assert await main._main_async(_args(), []) == 0
        assert abiertas == ["http://localhost:8765"]

    async def test_respeta_sin_navegador(self, settings, monkeypatch):
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(instancia, "reservar", lambda *_a, **_k: None)
        monkeypatch.setattr(
            instancia,
            "huella_ajena",
            lambda *_a, **_k: instancia.Huella(pid=1, url="http://localhost:8765"),
        )
        abiertas = []
        monkeypatch.setattr(
            "jarvis.ui.navegador.abrir", lambda u, **_k: abiertas.append(u) or True
        )

        assert await main._main_async(_args(sin_navegador=True), []) == 0
        assert abiertas == []

    async def test_sin_huella_no_revienta(self, settings, monkeypatch):
        # Puede que la instancia viva arrancara sin --web: no hay URL, y aun
        # así no es motivo para fallar.
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(instancia, "reservar", lambda *_a, **_k: None)
        monkeypatch.setattr(instancia, "huella_ajena", lambda *_a, **_k: None)

        assert await main._main_async(_args(), []) == 0

    async def test_dos_procesos_reales_se_reparten_bien_el_cerrojo(self, settings):
        # Sin monkeypatchear nada: la reserva real, en un puerto que no
        # colisione con nada del equipo donde corra el test.
        puerto = _puerto_libre()
        primera = instancia.reservar(settings.data_dir, puerto=puerto)
        assert primera is not None
        try:
            primera.anunciar("http://localhost:8765")
            segunda = instancia.reservar(settings.data_dir, puerto=puerto)
            assert segunda is None
            assert instancia.huella_ajena(settings.data_dir).url == "http://localhost:8765"
        finally:
            primera.liberar()


def _construir_con_dobles(agente=None):
    """Un `_construir` de mentira: nunca toca audio real, tarjeta de sonido
    ni red. Cierra sobre el núcleo montado para poder inspeccionarlo luego."""
    montado: dict[str, JarvisCore] = {}

    def _construir(args, s, bus):  # noqa: ANN001, ARG001
        core = JarvisCore(
            s,
            agent=agente or FakeAgent(),
            tts=FakeTTS(),
            player=NullPlayer(),
            mic=ControlledMic(200_000),
            transcriber=FakeTranscriber(),
            wakeword=NullWakeWord(),
            bus=bus,
        )
        montado["core"] = core
        return core

    return _construir, montado


class TestModoTexto:
    """El agujero real: `FakeMicStream` (audio vacío en modo texto) hace que
    `core.run()` termine casi al instante, y eso no es una señal de apagado."""

    async def test_la_conversacion_llega_a_completarse(self, settings, monkeypatch):
        _construir, montado = _construir_con_dobles()
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(main, "_construir", _construir)

        respuestas = iter(["hola", "salir"])
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respuestas))

        resultado = await main._main_async(_args(texto=True), [])

        assert resultado == 0
        # Si el bucle de audio vacío se hubiera confundido con el apagado,
        # "hola" nunca habría llegado a `agent.ask`.
        assert montado["core"].agent.preguntas == ["hola"]

    async def test_texto_mas_web_no_depende_del_bucle_de_texto(self, settings, monkeypatch):
        # Con --web la entrada va por el navegador: no hay bucle de teclado, y
        # el audio vacío (en accesorias) tampoco puede terminar el programa.
        # Sin nada que dispare la parada, el servidor tiene que seguir vivo.
        _construir, _montado = _construir_con_dobles()
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(main, "_construir", _construir)
        monkeypatch.setattr("jarvis.ui.navegador.abrir_cuando_escuche", _no_hacer_nada)

        puerto = _puerto_libre()
        tarea = asyncio.create_task(
            main._main_async(_args(texto=True, web=True, puerto=puerto), [])
        )
        try:
            await asyncio.sleep(0.3)
            assert not tarea.done(), "el servidor no debería haber terminado solo"
        finally:
            tarea.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarea


class TestPropagacionDeErrores:
    """`asyncio.wait` no lanza las excepciones de las tareas por su cuenta:
    sin recorrerlas a mano, un servidor que no arranca desaparece en
    silencio, que es justo el fallo que este cableado vino a arreglar."""

    async def test_un_fallo_en_el_servidor_se_ve_y_se_notifica(self, settings, monkeypatch, capsys):
        _construir, _montado = _construir_con_dobles()
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(main, "_construir", _construir)

        async def servidor_roto(*_a, **_k):  # noqa: ANN002, ANN003
            raise RuntimeError("el puerto ya está en uso")

        monkeypatch.setattr("jarvis.server.app.servir", servidor_roto)
        monkeypatch.setattr("jarvis.server.app.ip_local", lambda: None)

        resultado = await main._main_async(
            _args(web=True, puerto=_puerto_libre(), sin_navegador=True), []
        )

        assert resultado == 0
        # Sin recorrer las tareas terminadas a mano, `asyncio.wait` se traga
        # esta excepción y el fallo desaparece sin dejar rastro.
        assert "el puerto ya está en uso" in capsys.readouterr().out


async def _no_hacer_nada(*_a, **_k) -> bool:  # noqa: ANN002, ANN003
    return False


class TestLimpiezaFinalAcotada:
    """`asyncio.run()` hace bien casi todo, salvo un detalle que aquí
    importa: al terminar, espera **sin límite** a que mueran las tareas que
    queden vivas en el loop. `_arrancar_todo` acota su propia limpieza, pero
    eso no protege de una tarea huérfana que se resista a morir del todo:
    ese último paso queda fuera de su alcance, y ahí seguiría colgado el
    Ctrl+C aunque el resto del arranque estuviera bien hecho.

    `_correr_hasta_el_final` sustituye a `asyncio.run()` por eso mismo: para
    poder acotar también la limpieza final. Se prueba con una tarea
    deliberadamente inmortal —que nunca obedece a la cancelación— corriendo
    en un hilo aparte con un `join` acotado, precisamente para que este test
    no pueda colgarse ni siquiera si el arreglo estuviera mal hecho."""

    def test_una_tarea_inmortal_no_impide_terminar(self):
        async def coordinadora() -> int:
            async def inmortal() -> None:
                while True:
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.sleep(1000)

            asyncio.get_running_loop().create_task(inmortal(), name="inmortal")
            return 0

        resultado: dict[str, object] = {}

        def objetivo() -> None:
            resultado["valor"] = main._correr_hasta_el_final(coordinadora())

        hilo = threading.Thread(target=objetivo, daemon=True)
        t0 = time.monotonic()
        hilo.start()
        hilo.join(timeout=15.0)

        if hilo.is_alive():
            raise AssertionError(
                "_correr_hasta_el_final no ha vuelto en 15 s: una tarea "
                "inmortal cuelga la limpieza final, el mismo síntoma "
                "reportado con Ctrl+C"
            )
        assert resultado.get("valor") == 0
        assert time.monotonic() - t0 < 10.0, "ha tardado más de lo que permite el límite"


class _CoreFalsoConConfirmacion:
    """Doble mínimo: sólo lo que `_bucle_texto` de verdad usa."""

    def __init__(self, *, pendiente: bool) -> None:
        self._pendiente = pendiente
        self.preguntas: list[str] = []
        self.respuestas: list[bool] = []

    @property
    def confirmacion_pendiente(self) -> bool:
        return self._pendiente

    def responder_confirmacion(self, permitir: bool) -> bool:
        self.respuestas.append(permitir)
        self._pendiente = False
        return True

    async def responder(self, texto: str) -> None:
        self.preguntas.append(texto)


class TestConfirmacionEnModoTexto:
    """Reportado en vivo: un «sí» tecleado mientras había un permiso
    esperando confirmación no lo autorizaba — se mandaba como una pregunta
    nueva a Claude, y el permiso real (que espera voz) se denegaba solo por
    el timeout. Sin este enrutado, ningún permiso se puede autorizar nunca
    en modo `--texto`, aunque el usuario conteste que sí."""

    async def test_un_si_responde_al_permiso_pendiente_no_es_pregunta_nueva(self, monkeypatch):
        core = _CoreFalsoConConfirmacion(pendiente=True)
        hud = ConsoleHUD(EventBus())

        respuestas = iter(["sí", "salir"])
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respuestas))

        await main._bucle_texto(core, hud)

        assert core.respuestas == [True]
        assert core.preguntas == []

    async def test_una_respuesta_ambigua_no_resuelve_nada_y_sigue_esperando(self, monkeypatch):
        core = _CoreFalsoConConfirmacion(pendiente=True)
        hud = ConsoleHUD(EventBus())

        respuestas = iter(["mmm no sé", "salir"])
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respuestas))

        await main._bucle_texto(core, hud)

        assert core.respuestas == []
        assert core.preguntas == []

    async def test_sin_confirmacion_pendiente_se_comporta_como_siempre(self, monkeypatch):
        core = _CoreFalsoConConfirmacion(pendiente=False)
        hud = ConsoleHUD(EventBus())

        respuestas = iter(["hola", "salir"])
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respuestas))

        await main._bucle_texto(core, hud)

        assert core.preguntas == ["hola"]
        assert core.respuestas == []


class _CoreFalsoConTurnoLargo:
    """A diferencia de `_CoreFalsoConConfirmacion`, aquí `responder()` no
    vuelve solo: se queda esperando a que alguien conteste el permiso que
    él mismo levanta, exactamente como pasa de verdad con `JarvisCore` y su
    `confirmar_por_voz`. Sirve para probar que el bucle puede seguir
    leyendo líneas mientras ese turno sigue en curso."""

    def __init__(self) -> None:
        self._pendiente = False
        self.preguntas: list[str] = []
        self.respuestas: list[bool] = []
        self._resuelto = asyncio.Event()

    @property
    def confirmacion_pendiente(self) -> bool:
        return self._pendiente

    def responder_confirmacion(self, permitir: bool) -> bool:
        self.respuestas.append(permitir)
        self._pendiente = False
        self._resuelto.set()
        return True

    async def responder(self, texto: str) -> None:
        self.preguntas.append(texto)
        self._pendiente = True
        await self._resuelto.wait()


class TestUnTurnoEnCursoNoBloqueaLeerLaConfirmacion:
    """La causa real, más profunda que el enrutado de `interpretar_respuesta`
    (que ya se probó en `TestConfirmacionEnModoTexto` con dobles que
    devuelven al instante): si `_bucle_texto` le hace `await` directo a
    `core.responder(linea)`, el bucle no puede volver a pedir una línea
    nueva hasta que ese turno completo termine — y un turno con un permiso
    pendiente no termina hasta que se conteste ese permiso, que es
    justamente lo que este bucle tendría que leer mientras tanto. Reportado
    en vivo: contestar «sí» una y otra vez nunca autorizaba nada."""

    async def test_puede_leer_y_resolver_mientras_el_turno_sigue_vivo(self, monkeypatch):
        core = _CoreFalsoConTurnoLargo()
        hud = ConsoleHUD(EventBus())

        respuestas = iter(["abre algo", "sí", "salir"])
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(respuestas))

        await asyncio.wait_for(main._bucle_texto(core, hud), timeout=5.0)

        assert core.preguntas == ["abre algo"]
        assert core.respuestas == [True]


class TestLecturaDeTextoNoCuelgaElCierre:
    """Reportado en vivo por Jeremy: tras Ctrl+C en modo `--texto`, el
    proceso se quedaba congelado en vez de cerrar.

    Causa: `asyncio.to_thread(input, ...)` corre en el executor por defecto
    de asyncio, cuyos hilos **no** son daemon. Si Ctrl+C llega con `input()`
    todavía bloqueado esperando al teclado, cancelar la tarea no interrumpe
    la llamada de verdad —el hilo sigue bloqueado—, y un hilo no daemon
    impide que el intérprete termine (`concurrent.futures` lo espera al
    salir). `_leer_linea` usa un hilo propio marcado `daemon=True` para que,
    si nadie vuelve a escribir, el proceso pueda cerrar de todas formas."""

    async def test_el_hilo_de_lectura_es_daemon(self, monkeypatch):
        creados: list[bool | None] = []
        hilo_real = threading.Thread

        class HiloEspia(hilo_real):
            def __init__(self, *a, **kw):  # noqa: ANN002, ANN003
                creados.append(kw.get("daemon"))
                super().__init__(*a, **kw)

        monkeypatch.setattr(threading, "Thread", HiloEspia)
        monkeypatch.setattr("builtins.input", lambda *_a, **_k: "hola")

        resultado = await main._leer_linea("› ")

        assert resultado == "hola"
        assert creados == [True]


class TestSalirDelProceso:
    """Capturado con `faulthandler` en el Windows de Jeremy: tras Ctrl+C,
    con todo lo nuestro ya cerrado y hasta con el mensaje de despedida
    impreso, el hilo principal se quedaba para siempre en
    `threading._shutdown()` esperando al «AnyIO worker thread» que el SDK de
    Claude deja aparcado en `queue.get()`. Es un hilo no daemon de un
    tercero: no podemos marcarlo ni despertarlo, sólo dejar de esperarlo."""

    def test_termina_el_proceso_con_el_codigo_dado(self, monkeypatch):
        codigos: list[int] = []
        monkeypatch.setattr(os, "_exit", codigos.append)

        main.salir_del_proceso(3)

        assert codigos == [3]

    def test_vacia_los_flujos_antes_de_salir(self, monkeypatch):
        orden: list[str] = []
        monkeypatch.setattr(os, "_exit", lambda _c: orden.append("salir"))
        monkeypatch.setattr(
            sys, "stdout", _FlujoEspia(lambda: orden.append("flush stdout"))
        )
        monkeypatch.setattr(
            sys, "stderr", _FlujoEspia(lambda: orden.append("flush stderr"))
        )

        main.salir_del_proceso(0)

        # El orden importa: `os._exit` no vacía nada por su cuenta, así que
        # salir antes de tiempo se comería la última línea impresa.
        assert orden == ["flush stdout", "flush stderr", "salir"]

    def test_un_flujo_roto_no_impide_salir(self, monkeypatch):
        codigos: list[int] = []
        monkeypatch.setattr(os, "_exit", codigos.append)

        def explota() -> None:
            raise ValueError("consola cerrada")

        monkeypatch.setattr(sys, "stdout", _FlujoEspia(explota))

        main.salir_del_proceso(0)

        assert codigos == [0]


class _FlujoEspia:
    def __init__(self, al_vaciar) -> None:  # noqa: ANN001
        self._al_vaciar = al_vaciar

    def flush(self) -> None:
        self._al_vaciar()


class TestElSegundoCtrlCTampocoEnsucia:
    """Reportado en vivo por Jeremy, con el traceback pegado entero: al salir
    con Ctrl+C aparecía un `KeyboardInterrupt` sin manejar, lanzado desde
    dentro de la propia limpieza (`GetQueuedCompletionStatus`, bajo el bucle
    Proactor de Windows).

    La causa era `contextlib.suppress(Exception)`: `KeyboardInterrupt` hereda
    de `BaseException`, no de `Exception`, así que el segundo Ctrl+C —el de
    quien se impacienta durante los cinco segundos de espera— se colaba justo
    por el paso que existe para que el cierre sea limpio."""

    def test_un_ctrl_c_durante_la_limpieza_no_sale_como_traceback(self):
        async def coordinadora() -> int:
            async def se_queja_al_cancelar() -> None:
                try:
                    await asyncio.sleep(1000)
                except asyncio.CancelledError:
                    raise KeyboardInterrupt from None

            asyncio.get_running_loop().create_task(
                se_queja_al_cancelar(), name="impaciente"
            )
            await asyncio.sleep(0)
            return 0

        # Si el `suppress` no atrapa `BaseException`, esta llamada no
        # devuelve nada: propaga el `KeyboardInterrupt` y el test falla.
        assert main._correr_hasta_el_final(coordinadora()) == 0


class TestElCtrlCSiempreVuelve:
    """Reportado en vivo: `Ctrl+C` dejaba la terminal colgada tras `--web
    --https`. La causa era cancelar las tareas y esperarlas sin límite de
    tiempo: si una tarda en obedecer a la cancelación —un WebSocket a medio
    cerrar es de lo más plausible bajo el bucle Proactor de Windows con
    TLS—, el `await` esperaba lo que hiciera falta y el proceso no volvía.

    El servidor de mentira tarda, pero **termina**: uno que ignorase la
    cancelación para siempre colgaría también la propia limpieza final de
    `asyncio.run()` —fuera ya del alcance de este bloque—, y eso se cubre
    aparte en `TestLimpiezaFinalAcotada`."""

    async def test_una_tarea_lenta_en_cerrar_no_bloquea_la_salida(self, settings, monkeypatch):
        _construir, _montado = _construir_con_dobles()
        monkeypatch.setattr(main, "load_settings", lambda *_a, **_k: settings)
        monkeypatch.setattr(main, "_construir", _construir)

        async def servidor_lento_al_cerrar(*_a, **_k):  # noqa: ANN002, ANN003
            try:
                await asyncio.sleep(1000)
            except asyncio.CancelledError:
                # Más lento que el límite de la limpieza (5 s), pero acaba
                # cerrando: es el caso real, no uno inmortal.
                await asyncio.sleep(8.0)
                raise

        monkeypatch.setattr("jarvis.server.app.servir", servidor_lento_al_cerrar)
        monkeypatch.setattr("jarvis.server.app.ip_local", lambda: None)
        monkeypatch.setattr("jarvis.ui.navegador.abrir_cuando_escuche", _no_hacer_nada)

        tarea = asyncio.create_task(
            main._main_async(
                _args(web=True, puerto=_puerto_libre(), sin_navegador=True), []
            )
        )
        await asyncio.sleep(0.2)
        inicio = asyncio.get_running_loop().time()

        # El equivalente al Ctrl+C: se pide que termine sin más avisos.
        tarea.cancel()

        try:
            resultado = await asyncio.wait_for(tarea, timeout=7.0)
        except asyncio.CancelledError:
            resultado = None  # también es una salida válida

        transcurrido = asyncio.get_running_loop().time() - inicio
        assert transcurrido < 7.0, (
            f"tardó {transcurrido:.1f}s: el límite de 5 s en la limpieza no se respetó"
        )
        if resultado is not None:
            assert resultado == 0


class TestFlujosValidos:
    """Reportado en vivo: el arranque automático (`pythonw.exe`, sin consola)
    revienta con "Unable to configure formatter 'default'" antes de levantar
    nada. `pythonw.exe` deja `sys.stdout`/`sys.stderr` en `None`, y uvicorn
    monta un `logging.StreamHandler` sobre `sys.stdout` al arrancar el HUD
    web —siempre presente en el arranque automático—, que revienta con
    `None` ahí."""

    def test_sustituye_stdout_y_stderr_en_none(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)

        main._asegurar_flujos_validos()

        assert sys.stdout is not None
        assert sys.stderr is not None
        sys.stdout.write("")  # no debe reventar: es un flujo real
        sys.stderr.write("")

    def test_no_toca_flujos_que_ya_existen(self, monkeypatch):
        stdout_original = sys.stdout
        stderr_original = sys.stderr

        main._asegurar_flujos_validos()

        assert sys.stdout is stdout_original
        assert sys.stderr is stderr_original


class TestQrParaLaTerminal:
    """Escanear la cámara del móvil contra la terminal, sin teclear la IP."""

    def test_devuelve_un_qr_con_qrcode_instalado(self):
        pytest.importorskip("qrcode", reason="el QR necesita el paquete qrcode (extra `web`)")

        qr = main._qr_para_terminal("http://192.168.1.37:8765")

        assert qr
        assert "\n" in qr, "un QR de una sola línea no es un QR"

    def test_sin_qrcode_no_revienta(self, monkeypatch):
        # Forzado en vez de confiar en que este sandbox no tenga `qrcode`: así
        # el test es igual de fiable aquí, en la CI y en el Windows de Jeremy.
        monkeypatch.setitem(sys.modules, "qrcode", None)

        assert main._qr_para_terminal("http://192.168.1.37:8765") is None
