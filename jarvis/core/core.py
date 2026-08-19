"""El núcleo de J.A.R.V.I.S.: la máquina de estados.

Coordina micrófono, wake word, VAD, transcripción, Claude y voz. No sabe nada
de terminales ni de navegadores: sólo publica eventos en el bus, y quien
quiera pintarlos que se suscriba.

Hay dos tareas corriendo en paralelo, y la separación es deliberada:

- **El bucle de audio** consume frames del micrófono sin parar nunca. Es lo
  que permite interrumpir a J.A.R.V.I.S. mientras habla: si el bucle se
  bloqueara esperando a Claude, el micrófono quedaría sordo justo cuando más
  falta hace.
- **El turno** (transcribir → pensar → hablar) corre aparte y puede tardar
  segundos sin afectar a la captura.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from typing import TYPE_CHECKING, Any

from ..audio.capture import FRAME_MS
from ..audio.vad import Endpointer, VoiceDetector
from ..config import Settings
from ..events import EventBus, EventType, State
from ..text import SentenceChunker, limpiar_para_voz
from .agent import Delta, Done, ToolCall
from .permissions import interpretar_respuesta
from .personality import NO_ENTENDI, SALUDOS_DESPERTAR

if TYPE_CHECKING:
    import numpy as np

# Durante el barge-in exigimos más evidencia que en la escucha normal: si el
# usuario no lleva auriculares, el micrófono capta la propia voz de
# J.A.R.V.I.S. y sin este margen se interrumpiría a sí mismo constantemente.
BARGE_IN_THRESHOLD = 0.85
BARGE_IN_MS = 450


class JarvisCore:
    """Une todas las piezas y las gobierna."""

    def __init__(
        self,
        settings: Settings,
        *,
        agent: Any,
        tts: Any,
        player: Any,
        mic: Any,
        transcriber: Any,
        wakeword: Any,
        bus: EventBus | None = None,
        endpointer: Endpointer | None = None,
        barge_detector: VoiceDetector | None = None,
    ) -> None:
        self.s = settings
        self.bus = bus or EventBus()
        self.agent = agent
        self.tts = tts
        self.player = player
        self.mic = mic
        self.transcriber = transcriber
        self.wakeword = wakeword

        self.state = State.DORMIDO
        self.coste_usd = 0.0

        # Se pueden inyectar desde fuera para poder ejercitar la máquina de
        # estados en los tests sin depender de audio real.
        self._endpointer = endpointer or Endpointer(
            threshold=settings.vad.threshold,
            silence_ms=settings.vad.silence_ms,
            min_speech_ms=settings.vad.min_speech_ms,
            max_utterance_s=settings.vad.max_utterance_s,
        )
        # Detector independiente para el barge-in: no puede compartir estado
        # con el de la escucha normal porque los dos corren en momentos
        # distintos y con umbrales distintos.
        self._barge_det = barge_detector or VoiceDetector()
        self._barge_frames = 0

        self._grabacion: list[np.ndarray] = []
        self._captura: asyncio.Future[np.ndarray] | None = None
        self._turno: asyncio.Task | None = None
        self._cola_voz: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker_voz: asyncio.Task | None = None
        self._parar = asyncio.Event()

    # ── estado ──────────────────────────────────────────────────────────
    def _set_state(self, nuevo: State) -> None:
        if nuevo is self.state:
            return
        anterior, self.state = self.state, nuevo
        self.bus.emit(EventType.STATE_CHANGED, state=nuevo.value, previous=anterior.value)

    # ── arranque y parada ───────────────────────────────────────────────
    async def start(self) -> None:
        self.mic.start()
        self.player.start()
        await self.agent.start()
        self._worker_voz = asyncio.create_task(self._bucle_voz())

    async def stop(self) -> None:
        self._parar.set()
        await self._cancelar_turno()

        if self._worker_voz is not None:
            await self._cola_voz.put(None)
            with contextlib.suppress(asyncio.CancelledError):
                self._worker_voz.cancel()
                await self._worker_voz
            self._worker_voz = None

        with contextlib.suppress(Exception):
            await self.agent.stop()
        with contextlib.suppress(Exception):
            await self.tts.cerrar()
        self.player.stop()
        self.mic.stop()

    async def run(self) -> None:
        """Bucle principal. Corre hasta que se llame a :meth:`stop`."""
        async for frame in self.mic.frames():
            if self._parar.is_set():
                break
            try:
                self._procesar_frame(frame)
            except Exception as exc:  # noqa: BLE001 - un frame malo no tumba el sistema
                self.bus.emit(EventType.ERROR, message=f"Fallo procesando audio: {exc}")

    # ── el bucle de audio ───────────────────────────────────────────────
    def _procesar_frame(self, frame: np.ndarray) -> None:
        if self.state is State.DORMIDO:
            if self.wakeword.feed(frame):
                self.bus.emit(EventType.WAKE_DETECTED)
                self._lanzar(self._ciclo_conversacion(saludar=True))
            return

        if self.state in (State.ESCUCHANDO, State.CONFIRMANDO):
            self._grabar(frame)
            return

        if self.state is State.HABLANDO and self.s.audio.barge_in:
            self._detectar_interrupcion(frame)

    def _grabar(self, frame: np.ndarray) -> None:
        # Si nadie está esperando audio (p. ej. venció el tiempo de una
        # confirmación), no tiene sentido seguir acumulando frames.
        if self._captura is None:
            return

        self._grabacion.append(frame)
        if self._endpointer.feed(frame) != "fin":
            return

        import numpy as np

        audio = (
            np.concatenate(self._grabacion)
            if self._grabacion
            else np.zeros(0, dtype=np.float32)
        )
        self._grabacion = []
        if self._captura is not None and not self._captura.done():
            self._captura.set_result(audio)

    def _detectar_interrupcion(self, frame: np.ndarray) -> None:
        """¿El usuario ha empezado a hablar encima? Entonces cállate."""
        if self._barge_det.probabilidad(frame) >= BARGE_IN_THRESHOLD:
            self._barge_frames += 1
        else:
            self._barge_frames = 0

        if self._barge_frames * FRAME_MS >= BARGE_IN_MS:
            self._barge_frames = 0
            self.bus.emit(EventType.LOG, message="Interrumpido por el usuario")
            self._lanzar(self._interrumpir_y_escuchar())

    # ── captura de una frase ────────────────────────────────────────────
    async def _capturar_frase(self, estado: State, timeout: float | None = None) -> np.ndarray:
        """Escucha hasta que el usuario termine de hablar. Devuelve el audio."""
        import numpy as np

        self.mic.vaciar()
        self._grabacion = list(self.mic.pre_roll())
        self._endpointer.reset()
        self._captura = asyncio.get_running_loop().create_future()
        self._set_state(estado)

        try:
            if timeout is None:
                return await self._captura
            return await asyncio.wait_for(asyncio.shield(self._captura), timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return np.zeros(0, dtype=np.float32)
        finally:
            self._captura = None
            self._grabacion = []

    # ── el turno completo ───────────────────────────────────────────────
    async def _ciclo_conversacion(self, *, saludar: bool = False) -> None:
        """Despierta, escucha una frase, responde. Y vuelve a dormir."""
        try:
            if saludar:
                saludo = random.choice(SALUDOS_DESPERTAR).format(
                    user_name=self.s.agent.user_name
                )
                await self._decir_ahora(saludo)

            audio = await self._capturar_frase(State.ESCUCHANDO)

            self._set_state(State.TRANSCRIBIENDO)
            texto = (await self.transcriber.transcribir(audio)).strip()

            if not texto:
                await self._decir_ahora(random.choice(NO_ENTENDI))
                self._set_state(State.DORMIDO)
                return

            self.bus.emit(EventType.FINAL_TRANSCRIPT, text=texto)
            await self.responder(texto)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.bus.emit(EventType.ERROR, message=str(exc))
            self._set_state(State.DORMIDO)

    async def responder(self, texto: str) -> None:
        """Manda un turno a Claude y va hablando según llega la respuesta.

        Es público a propósito: así la UI web y los tests pueden inyectar
        texto sin pasar por el micrófono.
        """
        self._set_state(State.PENSANDO)
        chunker = SentenceChunker()
        completo: list[str] = []

        try:
            async for chunk in self.agent.ask(texto):
                if isinstance(chunk, Delta):
                    completo.append(chunk.text)
                    self.bus.emit(EventType.ASSISTANT_DELTA, text=chunk.text)
                    for frase in chunker.feed(chunk.text):
                        await self._encolar_voz(frase)

                elif isinstance(chunk, ToolCall):
                    self.bus.emit(EventType.TOOL_USE, name=chunk.name, input=chunk.input)

                elif isinstance(chunk, Done):
                    if chunk.error:
                        self.bus.emit(EventType.ERROR, message=chunk.error)
                        await self._encolar_voz(chunk.error)
                    if chunk.cost_usd:
                        self.coste_usd = chunk.cost_usd
                        self.bus.emit(EventType.COST_UPDATE, total_usd=chunk.cost_usd)

            for frase in chunker.flush():
                await self._encolar_voz(frase)

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.bus.emit(EventType.ERROR, message=str(exc))
            await self._encolar_voz("He tenido un problema al procesar eso.")

        self.bus.emit(EventType.ASSISTANT_DONE, text="".join(completo))
        await self._esperar_silencio()
        self._set_state(State.DORMIDO)

    # ── voz ─────────────────────────────────────────────────────────────
    async def _encolar_voz(self, texto: str) -> None:
        """Manda una frase a la cola de síntesis.

        Al ir por cola, la síntesis de la frase siguiente se solapa con la
        reproducción de la actual: J.A.R.V.I.S. habla sin pausas aunque cada
        síntesis tarde medio segundo.
        """
        limpio = limpiar_para_voz(texto)
        if limpio:
            self.bus.emit(EventType.ASSISTANT_SENTENCE, text=limpio)
            await self._cola_voz.put(limpio)

    async def _bucle_voz(self) -> None:
        """Worker que sintetiza y reproduce, en orden, lo que se le encole."""
        while True:
            texto = await self._cola_voz.get()
            if texto is None:
                return
            try:
                pcm = await self.tts.sintetizar(texto)
                if pcm.size:
                    if self.state is not State.CONFIRMANDO:
                        self._set_state(State.HABLANDO)
                    self._barge_frames = 0
                    self._barge_det.reset()
                    self.player.encolar(pcm)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - quedarse mudo no debe tumbar nada
                self.bus.emit(EventType.ERROR, message=f"Fallo en la voz: {exc}")

    async def _decir_ahora(self, texto: str) -> None:
        """Dice una frase y espera a que termine de sonar."""
        await self._encolar_voz(texto)
        await self._esperar_silencio()

    async def _esperar_silencio(self, timeout: float = 120.0) -> None:
        """Espera a que se vacíe la cola de voz y el reproductor."""
        limite = asyncio.get_running_loop().time() + timeout
        while not self._cola_voz.empty() and asyncio.get_running_loop().time() < limite:
            await asyncio.sleep(0.05)
        await self.player.esperar_fin(timeout=max(0.1, limite - asyncio.get_running_loop().time()))
        # El micrófono ha estado oyendo a J.A.R.V.I.S.; ese eco no debe
        # transcribirse como si lo hubiera dicho el usuario.
        self.mic.vaciar()

    # ── interrupciones ──────────────────────────────────────────────────
    async def _interrumpir_y_escuchar(self) -> None:
        """Barge-in: corta el audio, cancela el turno y vuelve a escuchar."""
        self.player.interrumpir()
        await self._cancelar_turno()
        self._vaciar_cola_voz()
        self._lanzar(self._ciclo_conversacion(saludar=False))

    async def escuchar_ahora(self) -> None:
        """Punto de entrada del push-to-talk y del botón de la interfaz web."""
        self.player.interrumpir()
        await self._cancelar_turno()
        self._vaciar_cola_voz()
        self._lanzar(self._ciclo_conversacion(saludar=True))

    async def _cancelar_turno(self) -> None:
        if self._turno is not None and not self._turno.done():
            self._turno.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._turno
        self._turno = None
        with contextlib.suppress(Exception):
            await self.agent.interrupt()

    def _vaciar_cola_voz(self) -> None:
        while not self._cola_voz.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._cola_voz.get_nowait()

    def _lanzar(self, coro) -> None:  # noqa: ANN001
        """Arranca un turno, sustituyendo al anterior si lo hubiera."""
        anterior = self._turno
        if anterior is not None and not anterior.done():
            anterior.cancel()
        self._turno = asyncio.create_task(coro)

    # ── confirmación hablada de permisos ────────────────────────────────
    async def confirmar_por_voz(self, pregunta: str) -> bool:
        """Lee la pregunta en voz alta y espera un sí o un no.

        Denegar por defecto no es pereza, es la decisión correcta: si no te
        oyó bien o te fuiste de la habitación, lo seguro es no hacer nada.
        Da dos oportunidades antes de rendirse.
        """
        estado_previo = self.state

        for intento in range(2):
            texto = pregunta if intento == 0 else "No le he entendido. ¿Sí o no?"
            await self._decir_ahora(texto)

            audio = await self._capturar_frase(
                State.CONFIRMANDO, timeout=self.s.permissions.confirm_timeout_s
            )
            if audio.size == 0:
                break  # se agotó el tiempo: no

            respuesta = (await self.transcriber.transcribir(audio)).strip()
            self.bus.emit(EventType.FINAL_TRANSCRIPT, text=respuesta, kind="confirmacion")

            decision = interpretar_respuesta(respuesta)
            if decision is not None:
                self._set_state(estado_previo)
                if not decision:
                    await self._decir_ahora("De acuerdo, no lo hago.")
                return decision

        self._set_state(estado_previo)
        await self._decir_ahora("Sin confirmación. No hago nada.")
        return False
