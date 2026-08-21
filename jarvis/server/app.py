"""Servidor web de J.A.R.V.I.S.

Un HUD en el navegador que se conecta al **mismo núcleo** que la terminal. No
duplica ni una línea de lógica: se suscribe al bus de eventos igual que el HUD
de consola, y manda órdenes de vuelta por el mismo WebSocket.

Esto es lo que justifica que el núcleo no sepa quién lo mira. Toda la interfaz
web cabe en este archivo más tres estáticos, precisamente porque no tiene que
saber nada de micrófonos, de Claude ni de permisos.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

# FastAPI se importa aquí arriba, no dentro de las funciones. Con
# `from __future__ import annotations` las anotaciones son cadenas, y FastAPI
# las resuelve contra las GLOBALES del módulo: un `WebSocket` importado dentro
# de una función es invisible para él, y el resultado es un 403 en el
# handshake que no dice nada. Este módulo sólo se importa cuando se pide
# `--web`, así que no encarece el arranque normal.
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from ..core.core import JarvisCore

ESTATICOS = Path(__file__).parent / "static"


def _a_json(dato: Any) -> Any:
    """Convierte lo que no sea serializable (rutas, enums) en texto."""
    return str(dato)


def crear_app(core: JarvisCore) -> FastAPI:
    """Monta la aplicación web sobre un núcleo ya construido."""
    app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)
    app.mount("/estaticos", StaticFiles(directory=ESTATICOS), name="estaticos")

    @app.get("/")
    async def raiz():  # noqa: ANN202
        return FileResponse(ESTATICOS / "index.html")

    @app.get("/api/estado")
    async def estado():  # noqa: ANN202
        """Estado actual, para que la página no arranque en blanco."""
        return {
            "state": core.state.value,
            "coste_usd": core.coste_usd,
            "modelo": core.s.agent.model,
            "voz": getattr(core.tts, "nombre", "?"),
            "wake_word": core.wakeword.enabled,
            "atajo": core.s.hotkey.combo if core.s.hotkey.enabled else None,
            # Sin micrófono real, ofrecer el botón de escuchar dejaría al
            # núcleo esperando un audio que nunca va a llegar.
            "microfono": getattr(core.mic, "es_real", False),
            "usuario": core.s.agent.user_name,
        }

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        await ws.accept()

        # El estado actual primero: si te conectas a media conversación, la
        # página debe reflejar lo que está pasando, no esperar al siguiente
        # evento para enterarse.
        await ws.send_text(
            json.dumps(
                {"type": "state_changed", "data": {"state": core.state.value}},
                default=_a_json,
            )
        )

        async def hacia_el_navegador() -> None:
            async for evento in core.bus.stream():
                await ws.send_text(
                    json.dumps(
                        {"type": evento.type.value, "data": evento.data},
                        default=_a_json,
                    )
                )

        async def desde_el_navegador() -> None:
            while True:
                mensaje = await ws.receive_json()
                await _atender(core, mensaje)

        salida = asyncio.create_task(hacia_el_navegador())
        entrada = asyncio.create_task(desde_el_navegador())
        try:
            # En cuanto una de las dos termina (el navegador cerró la pestaña),
            # se cancela la otra: si no, quedarían tareas huérfanas escribiendo
            # en un socket muerto.
            await asyncio.wait(
                {salida, entrada}, return_when=asyncio.FIRST_COMPLETED
            )
        except WebSocketDisconnect:
            pass
        finally:
            for tarea in (salida, entrada):
                tarea.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tarea

    return app


async def _atender(core: JarvisCore, mensaje: dict[str, Any]) -> None:
    """Ejecuta una orden llegada del navegador."""
    tipo = mensaje.get("type")

    if tipo == "texto":
        texto = str(mensaje.get("text", "")).strip()
        if texto:
            # En su propia tarea: el WebSocket tiene que seguir leyendo para
            # que el botón de interrumpir funcione mientras responde.
            asyncio.create_task(core.responder(texto))

    elif tipo == "escuchar":
        asyncio.create_task(core.escuchar_ahora())

    elif tipo == "interrumpir":
        core.player.interrumpir()


async def servir(core: JarvisCore, host: str = "0.0.0.0", puerto: int = 8765) -> None:
    """Arranca el servidor dentro del loop que ya está corriendo."""
    import uvicorn

    configuracion = uvicorn.Config(
        crear_app(core),
        host=host,
        port=puerto,
        log_level="warning",   # el HUD ya cuenta lo que pasa; el log sólo estorba
        access_log=False,
    )
    await uvicorn.Server(configuracion).serve()
