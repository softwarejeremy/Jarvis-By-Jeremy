"""Atajo de teclado global: hablar sin decir la palabra clave.

Complementa al wake word. Sirve para cuando hay ruido, cuando no quieres que
te oigan decir "Hey Jarvis" en voz alta, o simplemente cuando prefieres no
tener el micrófono analizando audio todo el rato.

Funciona por *pulsación*, no manteniendo la tecla: pulsas y J.A.R.V.I.S.
empieza a escuchar hasta que detecta que terminaste de hablar. pynput sólo
notifica la pulsación de una combinación, no su liberación, y de todos modos
tener que mantener tres teclas mientras hablas resulta incómodo.

En Windows funciona sin permisos especiales. En macOS hay que conceder
Accesibilidad, y en Linux depende del entorno de escritorio (bajo Wayland
puede no funcionar).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable


class HotkeyListener:
    """Escucha una combinación global y avisa al loop de asyncio."""

    def __init__(self, combo: str, callback: Callable[[], None]) -> None:
        self._combo = combo
        self._callback = callback
        self._listener = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.activo = False
        self.error: str | None = None

    def start(self) -> None:
        """Arranca el escuchador. Si falla, lo registra y sigue: perder el
        atajo es molesto, pero no es motivo para no arrancar J.A.R.V.I.S."""
        if self._listener is not None:
            return

        self._loop = asyncio.get_running_loop()
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys({self._combo: self._disparar})
            self._listener.start()
            self.activo = True
        except Exception as exc:  # noqa: BLE001 - sin display, sin permisos, sin pynput…
            self.error = str(exc)
            self.activo = False

    def stop(self) -> None:
        if self._listener is not None:
            with contextlib.suppress(Exception):
                self._listener.stop()
            self._listener = None
            self.activo = False

    def _disparar(self) -> None:
        # Corre en el hilo de pynput: hay que cruzar al loop de asyncio.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._callback)
