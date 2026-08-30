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
import os
from collections.abc import Iterator
from pathlib import Path


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


async def abrir_cuando_escuche(
    url: str, puerto: int, *, timeout: float = 15.0, navegador: str = "chrome"
) -> bool:
    """Espera a que el HUD esté en pie y lo abre. Nunca lanza.

    Que no se pueda abrir el navegador no es motivo para tumbar nada: el HUD
    sigue estando ahí y la URL sigue siendo válida.
    """
    if not await esperar_puerto(puerto, timeout=timeout):
        return False
    # `webbrowser.open` puede tardar segundos en Windows mientras arranca el
    # navegador. En el loop, esos segundos son segundos sin leer el micrófono.
    return await asyncio.to_thread(abrir, url, navegador=navegador)


def abrir(url: str, *, navegador: str = "chrome") -> bool:
    """Abre una URL en el navegador del sistema. Devuelve si lo consiguió.

    `navegador="chrome"` (por defecto) intenta Chrome primero; `"sistema"`
    va directo al navegador por defecto de Windows (Edge, normalmente). En
    Windows, `webbrowser.open` a secas siempre delega al de por defecto —no
    hay forma de pedirle "el que yo quiera" sin decírselo explícitamente.
    """
    import webbrowser

    if navegador == "chrome":
        controlador = _chrome()
        if controlador is not None:
            with contextlib.suppress(Exception):
                if controlador.open(url, new=2):
                    return True
            # Chrome está pero no abrió: seguimos al navegador del sistema,
            # no nos rendimos.

    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - sin navegador utilizable, seguimos vivos
        return False


def _chrome():  # noqa: ANN202 - devuelve un webbrowser.BaseBrowser o None
    """Localiza Chrome, si lo encuentra. `None` si no hay forma de dar con él.

    En Linux, `webbrowser.get("chrome")` ya sabe buscarlo en el PATH. En
    Windows no hay ese registro automático salvo que el propio Chrome se
    haya registrado como navegador por defecto alguna vez, así que además
    se busca el ejecutable en las rutas típicas de instalación.
    """
    import webbrowser

    with contextlib.suppress(webbrowser.Error):
        return webbrowser.get("chrome")

    for ruta in _rutas_chrome_windows():
        if ruta.is_file():
            return webbrowser.BackgroundBrowser(str(ruta))
    return None


def _rutas_chrome_windows() -> Iterator[Path]:
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            yield Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
