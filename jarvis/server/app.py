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
import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

# FastAPI se importa aquí arriba, no dentro de las funciones. Con
# `from __future__ import annotations` las anotaciones son cadenas, y FastAPI
# las resuelve contra las GLOBALES del módulo: un `WebSocket` importado dentro
# de una función es invisible para él, y el resultado es un 403 en el
# handshake que no dice nada. Este módulo sólo se importa cuando se pide
# `--web`, así que no encarece el arranque normal.
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.historial import Historial
from ..core.memory import CATEGORIAS, Memory
from ..core.permissions import interpretar_respuesta
from ..events import EventType

if TYPE_CHECKING:
    from ..core.core import JarvisCore

ESTATICOS = Path(__file__).parent / "static"

# Cuántos turnos se reponen al conectar. El registro completo vive en disco
# (`jarvis/core/historial.py`) sin límite; esto sólo acota lo que se manda de
# golpe por el WebSocket al abrir el HUD.
HISTORIAL_MAXLEN = 80


class _PeticionOlvidar(BaseModel):
    texto: str = ""


# `Memory.recordar` guarda cada hecho como "- {hecho}  _(anotado el
# AAAA-MM-DD)_": es útil al abrir el archivo a mano, pero en el HUD, sin
# renderizar Markdown, sólo se verían los guiones bajos crudos.
_FECHA_ANOTADO = re.compile(r"\s*_\(anotado el [\d-]+\)_\s*$")

# El día llega como parámetro de la URL y se usa para construir una ruta de
# archivo (`Historial._archivo`): sin esta validación, algo como "../../etc"
# se colaría como recorrido de directorios en vez de un nombre de archivo.
_FORMATO_DIA_VALIDO = re.compile(r"\d{4}-\d{2}-\d{2}")


def ip_local() -> str | None:
    """La IP de este equipo en la red local, para entrar desde el móvil.

    El truco es abrir un socket UDP "hacia" una dirección de internet: no se
    envía ningún paquete, pero el sistema elige la interfaz de salida y con
    ella la IP que los demás equipos de la red ven. Es mucho más fiable que
    `gethostname()`, que en Windows suele devolver 127.0.0.1, y le ahorra al
    usuario tener que interpretar la salida de `ipconfig`.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(0.2)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except OSError:
            return None

    return ip if ip and not ip.startswith("127.") else None


def _a_json(dato: Any) -> Any:
    """Convierte lo que no sea serializable (rutas, enums) en texto."""
    return str(dato)


def _qr_svg(url: str) -> str | None:
    """El QR de una URL como SVG en línea, para el overlay del HUD.

    Mismo espíritu que `main._qr_para_terminal`: `qrcode` es parte del extra
    `web`, pero si algo falla generándolo, la URL en texto plano del overlay
    sigue siendo suficiente para copiarla a mano.
    """
    try:
        import io

        import qrcode
        from qrcode.image.svg import SvgPathImage

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)

        buffer = io.BytesIO()
        qr.make_image(image_factory=SvgPathImage).save(buffer)
        return buffer.getvalue().decode("utf-8")
    except Exception:  # noqa: BLE001 - sin QR, la URL en texto basta
        return None


def crear_app(core: JarvisCore) -> FastAPI:
    """Monta la aplicación web sobre un núcleo ya construido."""
    app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)
    app.mount("/estaticos", StaticFiles(directory=ESTATICOS), name="estaticos")

    # El registro en sí lo escucha `main.py` (para que quede constancia haya
    # o no HUD web mirando); aquí sólo se lee de vuelta.
    historial = Historial(core.s.data_dir / "conversaciones")

    def _memoria_actual() -> dict[str, list[str]]:
        m = Memory(core.s.memory_dir)
        return {
            categoria: [
                _FECHA_ANOTADO.sub("", linea[2:].strip())
                for linea in m.leer(categoria).splitlines()
                if linea.startswith("- ")
            ]
            for categoria in CATEGORIAS
        }

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
            "pausado": core.pausado,
            "usuario": core.s.agent.user_name,
        }

    @app.get("/api/memoria")
    async def memoria():  # noqa: ANN202
        return _memoria_actual()

    @app.post("/api/memoria/olvidar")
    async def memoria_olvidar(peticion: _PeticionOlvidar):  # noqa: ANN202
        mensaje = Memory(core.s.memory_dir).olvidar(peticion.texto)
        return {"mensaje": mensaje, "memoria": _memoria_actual()}

    @app.get("/api/conversaciones")
    async def conversaciones_dias():  # noqa: ANN202
        """Qué días hay conversación registrada, más reciente primero."""
        return {"dias": historial.dias()}

    @app.get("/api/conversaciones/{dia}")
    async def conversaciones_del_dia(dia: str):  # noqa: ANN202
        """Los turnos de un día. Uno sin registro o mal formado da vacío."""
        if not _FORMATO_DIA_VALIDO.fullmatch(dia):
            return {"turnos": []}
        return {"turnos": historial.leer(dia)}

    @app.get("/api/movil")
    async def movil(request: Request):  # noqa: ANN202
        """La URL y el QR para abrir este mismo HUD desde el móvil.

        El esquema y el puerto se leen de la propia petición, no de un
        parámetro guardado al arrancar: es exactamente por dónde el
        navegador que pide esto está mirando la página ahora mismo, así que
        no puede desincronizarse de `--https`/`--puerto`.
        """
        ip = ip_local()
        puerto = request.url.port
        if not ip or not puerto:
            return {"url": None, "qr_svg": None}

        url = f"{request.url.scheme}://{ip}:{puerto}"
        return {"url": url, "qr_svg": _qr_svg(url)}

    @app.get("/estaticos-generados/icono-180.png")
    async def icono_apple():  # noqa: ANN202
        """PNG del reactor para `apple-touch-icon`: iOS no acepta SVG ahí."""
        from ..ui.icono import dibujar_reactor, hay_pillow

        if not hay_pillow():
            raise HTTPException(status_code=404, detail="Pillow no está instalado")

        import io

        buffer = io.BytesIO()
        dibujar_reactor("dormido", 180).save(buffer, format="PNG")
        return Response(content=buffer.getvalue(), media_type="image/png")

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
        # Sólo lo de hoy: mezclar turnos de ayer sin ningún separador visual
        # de por medio confundiría más de lo que ayudaría. Para eso está el
        # selector de días (`/api/conversaciones`).
        turnos_de_hoy = historial.leer(date.today().isoformat(), limite=HISTORIAL_MAXLEN)
        await ws.send_text(
            json.dumps(
                {"type": "historial", "data": {"turnos": turnos_de_hoy}},
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
                # El navegador manda órdenes en JSON y la voz en binario, por
                # el mismo socket. `receive()` da ambos sin tener que abrir una
                # segunda conexión sólo para el audio.
                paquete = await ws.receive()

                if paquete.get("type") == "websocket.disconnect":
                    return

                crudo = paquete.get("bytes")
                if crudo is not None:
                    await _atender_audio(core, crudo)
                    continue

                texto = paquete.get("text")
                if texto:
                    await _atender(core, json.loads(texto))

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


# Un minuto de voz a 16 kHz en 16 bits son ~1,9 MB. Más que eso no es una
# frase, es un error o alguien probando cosas raras: se descarta.
MAX_AUDIO_BYTES = 2_000_000
# Menos de esto no da ni para una palabra; casi siempre es un botón pulsado
# sin querer. Transcribirlo sólo gastaría tiempo para devolver vacío.
MIN_AUDIO_BYTES = 4_000  # ~0,12 s


async def _atender_audio(core: JarvisCore, crudo: bytes) -> None:
    """Recibe una grabación del navegador: PCM 16 bits, mono, 16 kHz."""
    import numpy as np

    if len(crudo) < MIN_AUDIO_BYTES:
        core.bus.emit(EventType.LOG, message="Grabación demasiado corta; la ignoro.")
        return

    if len(crudo) > MAX_AUDIO_BYTES:
        core.bus.emit(
            EventType.ERROR,
            message="La grabación es demasiado larga. Inténtelo en frases más cortas.",
        )
        return

    # Un número impar de bytes no puede ser PCM de 16 bits: llegó cortado.
    if len(crudo) % 2:
        crudo = crudo[:-1]

    audio = np.frombuffer(crudo, dtype=np.int16).astype(np.float32) / 32768.0
    await core.escuchar_audio(audio)


async def _atender(core: JarvisCore, mensaje: dict[str, Any]) -> None:
    """Ejecuta una orden llegada del navegador."""
    tipo = mensaje.get("type")

    if tipo == "texto":
        texto = str(mensaje.get("text", "")).strip()
        if not texto:
            return

        # Con una confirmación pendiente, lo escrito responde a esa pregunta,
        # no dispara un turno nuevo: es el mismo camino que la voz, sólo que
        # tecleado en vez de dicho.
        if core.confirmacion_pendiente:
            decision = interpretar_respuesta(texto)
            if decision is None:
                core.bus.emit(EventType.LOG, message="No le he entendido. ¿Sí o no?")
            else:
                core.responder_confirmacion(decision)
            return

        # `core.responder()` no emite `final_transcript` —el modo `--texto`
        # de la terminal no lo necesita, se ve escrito en la propia consola—,
        # pero el HUD sí: es la señal que alimenta el historial que se repone
        # al reconectar. Sin esto, un turno tecleado desaparecería al recargar
        # aunque la respuesta de J.A.R.V.I.S. sí sobreviviera.
        core.bus.emit(EventType.FINAL_TRANSCRIPT, text=texto)

        # En su propia tarea: el WebSocket tiene que seguir leyendo para
        # que el botón de interrumpir funcione mientras responde.
        asyncio.create_task(core.responder(texto))

    elif tipo == "escuchar":
        asyncio.create_task(core.escuchar_ahora())

    elif tipo == "interrumpir":
        core.player.interrumpir()

    elif tipo == "pausa":
        # El mismo mando que la bandeja del sistema. Los dos frentes tienen que
        # poder lo mismo, o el HUD se convierte en una interfaz de segunda.
        core.alternar_pausa()

    elif tipo == "confirmar":
        # El mismo "sí"/"no" que la voz, para cuando contestar hablando no es
        # una opción: desde el móvil, o sin micrófono en el navegador.
        core.responder_confirmacion(bool(mensaje.get("allowed")))


async def servir(
    core: JarvisCore,
    host: str = "0.0.0.0",
    puerto: int = 8765,
    *,
    https: bool = False,
) -> None:
    """Arranca el servidor dentro del loop que ya está corriendo.

    Con ``https=True`` se sirve con un certificado autofirmado. Es feo —el
    navegador avisa la primera vez— pero es la única forma de que el móvil
    pueda usar el micrófono: fuera de `localhost`, los navegadores sólo
    exponen `getUserMedia` en contexto seguro.
    """
    import uvicorn

    extra: dict[str, Any] = {}
    if https:
        from .tls import asegurar_certificado

        ip = ip_local()
        cert, clave = asegurar_certificado(core.s.data_dir, [ip] if ip else [])
        extra = {"ssl_certfile": str(cert), "ssl_keyfile": str(clave)}

    configuracion = uvicorn.Config(
        crear_app(core),
        host=host,
        port=puerto,
        log_level="warning",   # el HUD ya cuenta lo que pasa; el log sólo estorba
        access_log=False,
        **extra,
    )
    await uvicorn.Server(configuracion).serve()
