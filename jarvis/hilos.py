"""Como `asyncio.to_thread`, pero en un hilo que no bloquea el cierre.

`asyncio.to_thread` corre en el executor por defecto de asyncio, cuyos hilos
**no** son daemon: si una llamada se queda bloqueada de verdad —esperando al
teclado, un consentimiento OAuth que nunca llega, o simplemente que termine
de sonar la voz—, cancelar la tarea de asyncio no interrumpe la llamada real,
y ese hilo no daemon impide que el intérprete termine (`concurrent.futures`
lo espera al salir vía `atexit`). Reportado en vivo, en tres sitios
distintos: el modo texto (`jarvis/main.py`), el login de Google
(`jarvis/tools/google_docs.py`) y la espera a que termine de hablar
(`jarvis/audio/player.py`) — de ahí que viva centralizado aquí en vez de
repetido en cada uno.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def en_hilo_daemon(func: Callable[..., T], *args: Any) -> T:
    """Ejecuta `func(*args)` en un hilo `daemon=True` y espera el resultado."""
    loop = asyncio.get_running_loop()
    futuro: asyncio.Future[T] = loop.create_future()

    def _correr() -> None:
        try:
            resultado = func(*args)
        except BaseException as exc:  # noqa: BLE001 - se reenvía tal cual
            if not futuro.done():
                loop.call_soon_threadsafe(futuro.set_exception, exc)
            return
        if not futuro.done():
            loop.call_soon_threadsafe(futuro.set_result, resultado)

    threading.Thread(target=_correr, daemon=True).start()
    return await futuro
