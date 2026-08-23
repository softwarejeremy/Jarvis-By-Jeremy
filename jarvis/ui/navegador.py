"""Abrir el HUD en el navegador cuando el servidor esté listo.

Hasta ahora `--web` sólo imprimía la URL por consola. Sirve si la lanzaste tú
desde una terminal; no sirve de nada arrancando con el sistema, donde no hay
consola a la que mirar.

Hay que esperar a que el servidor escuche: abrir el navegador antes le muestra
al usuario un error de conexión, y la primera impresión de que «no funciona»
cuesta más de arreglar que los dos segundos de espera.
"""

from __future__ import annotations

import asyncio
import contextlib


async def esperar_puerto(
    puerto: int,
    host: str = "127.0.0.1",
    *,
    timeout: float = 15.0,
    intervalo: float = 0.1,
) -> bool:
    """¿Hay ya alguien escuchando ahí? Sondea hasta que sí, o hasta rendirse.

    Basta con que la conexión TCP se complete, también con ``--https``: uvicorn
    construye el contexto TLS al crear la configuración, antes de abrir el
    socket, así que si acepta conexiones el handshake también va a funcionar.
    No hace falta hablar TLS para saberlo.
    """
    limite = asyncio.get_running_loop().time() + timeout
    while True:
        if await _hay_alguien(host, puerto):
            return True
        if asyncio.get_running_loop().time() >= limite:
            return False
        await asyncio.sleep(intervalo)


async def _hay_alguien(host: str, puerto: int) -> bool:
    try:
        lector, escritor = await asyncio.wait_for(
            asyncio.open_connection(host, puerto), timeout=2.0
        )
    except (OSError, asyncio.TimeoutError):
        return False

    del lector
    escritor.close()
    with contextlib.suppress(Exception):
        await escritor.wait_closed()
    return True


async def abrir_cuando_escuche(url: str, puerto: int, *, timeout: float = 15.0) -> bool:
    """Espera a que el HUD esté en pie y lo abre. Nunca lanza.

    Que no se pueda abrir el navegador no es motivo para tumbar nada: el HUD
    sigue estando ahí y la URL sigue siendo válida.
    """
    if not await esperar_puerto(puerto, timeout=timeout):
        return False
    # `webbrowser.open` puede tardar segundos en Windows mientras arranca el
    # navegador. En el loop, esos segundos son segundos sin leer el micrófono.
    return await asyncio.to_thread(abrir, url)


def abrir(url: str) -> bool:
    """Abre una URL en el navegador del sistema. Devuelve si lo consiguió."""
    import webbrowser

    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - sin navegador utilizable, seguimos vivos
        return False
