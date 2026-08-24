"""El atajo de teclado global, sin haberlo probado nunca.

`HotkeyListener` no es un borrador: cruza de verdad del hilo de pynput al loop
de asyncio, y degrada con elegancia cuando pynput falla —sin display, sin
permisos, o simplemente sin estar instalado, que es justo lo que le pasó a
Jeremy en su Windows real ("this platform is not supported...")—.

`pynput` se sustituye siempre por un doble instalado a mano en
``sys.modules``, nunca confiando en que esta máquina en particular carezca de
`$DISPLAY`: así el mismo test es igual de fiable aquí, en la CI y en un
Windows donde pynput sí funciona.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from jarvis.hotkey import HotkeyListener


def _instalar_pynput_falso(monkeypatch: pytest.MonkeyPatch, *, construir):
    """Sustituye `pynput.keyboard` por un doble en sys.modules.

    `construir` es lo que hará las veces de `GlobalHotKeys(mapping)`: puede
    devolver un objeto (arranque con éxito) o lanzar (arranque fallido).
    """
    modulo = types.ModuleType("pynput")
    submodulo = types.ModuleType("pynput.keyboard")
    submodulo.GlobalHotKeys = construir
    modulo.keyboard = submodulo
    monkeypatch.setitem(sys.modules, "pynput", modulo)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", submodulo)


class _ListenerFalso:
    """El objeto que devolvería `keyboard.GlobalHotKeys(...)` si funcionara."""

    def __init__(self) -> None:
        self.arrancado = False
        self.parado = False

    def start(self) -> None:
        self.arrancado = True

    def stop(self) -> None:
        self.parado = True


class TestDegradaConElegancia:
    async def test_sin_pynput_no_revienta(self, monkeypatch):
        # None en sys.modules fuerza un ImportError de verdad, no simulado a
        # medias: es exactamente lo que ocurre en un equipo sin pynput.
        monkeypatch.setitem(sys.modules, "pynput", None)

        oyente = HotkeyListener("<ctrl>+<alt>+j", lambda: None)
        oyente.start()

        assert oyente.activo is False
        assert oyente.error, "debería explicar por qué no se pudo registrar"

    async def test_un_fallo_al_construir_el_listener_tambien_degrada(self, monkeypatch):
        # Reproduce el caso real: pynput se importa bien, pero GlobalHotKeys()
        # revienta al construirse (DisplayNameError, sin permisos, etc.).
        def construir_roto(_mapping):
            raise OSError('failed to acquire X connection: Bad display name ""')

        _instalar_pynput_falso(monkeypatch, construir=construir_roto)

        oyente = HotkeyListener("<ctrl>+<alt>+j", lambda: None)
        oyente.start()

        assert oyente.activo is False
        assert "display" in oyente.error.lower()

    async def test_llamar_start_dos_veces_no_construye_dos_veces(self, monkeypatch):
        construcciones = []

        def construir(mapping):
            construcciones.append(mapping)
            return _ListenerFalso()

        _instalar_pynput_falso(monkeypatch, construir=construir)

        oyente = HotkeyListener("<ctrl>+<alt>+j", lambda: None)
        oyente.start()
        oyente.start()

        assert len(construcciones) == 1
        assert oyente.activo is True


class TestDisparar:
    async def test_cruza_al_hilo_del_loop(self, monkeypatch):
        # Con pynput fallando, `_listener` queda en None pero `_loop` sí se
        # fija —eso ocurre antes en el código—, que es lo único que
        # `_disparar` necesita.
        monkeypatch.setitem(sys.modules, "pynput", None)

        llamadas = []
        oyente = HotkeyListener("<ctrl>+<alt>+j", lambda: llamadas.append(1))
        oyente.start()
        assert oyente.activo is False  # confirma que estamos en el caso sin listener

        oyente._disparar()
        await asyncio.sleep(0)  # deja correr lo que `call_soon_threadsafe` programó

        assert llamadas == [1]

    async def test_sin_loop_no_revienta(self):
        llamadas = []
        oyente = HotkeyListener("<ctrl>+<alt>+j", lambda: llamadas.append(1))

        oyente._disparar()  # nunca hubo start(): _loop sigue en None

        assert llamadas == []


class TestStop:
    async def test_parar_sin_arrancar_no_revienta(self):
        oyente = HotkeyListener("<ctrl>+<alt>+j", lambda: None)
        oyente.stop()  # no debe lanzar

    async def test_parar_llama_al_listener_real(self, monkeypatch):
        _instalar_pynput_falso(monkeypatch, construir=lambda _mapping: _ListenerFalso())

        oyente = HotkeyListener("<ctrl>+<alt>+j", lambda: None)
        oyente.start()
        listener_de_mentira = oyente._listener
        assert listener_de_mentira.arrancado is True

        oyente.stop()

        assert listener_de_mentira.parado is True
        assert oyente.activo is False
