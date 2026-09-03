"""Bus de eventos.

El núcleo de J.A.R.V.I.S. no sabe si lo está mirando una terminal, un
navegador o un test: sólo publica eventos aquí. Cada frente (HUD de consola,
servidor web, grabador de tests) se suscribe y decide qué hacer con ellos.

Es lo que permite tener escritorio y web sin duplicar una sola línea de
lógica.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class State(str, Enum):
    """Estados de la máquina de voz."""

    DORMIDO = "dormido"          # esperando "Hey Jarvis" o el atajo
    ESCUCHANDO = "escuchando"    # capturando tu voz
    TRANSCRIBIENDO = "transcribiendo"
    PENSANDO = "pensando"        # Claude está trabajando
    HABLANDO = "hablando"
    CONFIRMANDO = "confirmando"  # esperando tu "sí" para algo delicado
    PAUSADO = "pausado"          # sordo a propósito: ni siquiera la wake word
    ERROR = "error"


class EventType(str, Enum):
    STATE_CHANGED = "state_changed"
    WAKE_DETECTED = "wake_detected"
    PARTIAL_TRANSCRIPT = "partial_transcript"
    FINAL_TRANSCRIPT = "final_transcript"
    ASSISTANT_DELTA = "assistant_delta"      # texto de Claude, token a token
    ASSISTANT_SENTENCE = "assistant_sentence"  # frase completa lista para TTS
    ASSISTANT_DONE = "assistant_done"
    TOOL_USE = "tool_use"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESULT = "permission_result"
    COST_UPDATE = "cost_update"
    LOG = "log"
    ERROR = "error"


@dataclass(slots=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # útil en logs y tests
        return f"{self.type.value}({self.data})"


Subscriber = Callable[[Event], Any]


class EventBus:
    """Pub/sub asíncrono, deliberadamente simple.

    Los suscriptores lentos no pueden frenar el núcleo: cada uno tiene su
    propia cola acotada y, si se llena, se descartan sus eventos más viejos.
    Perder una línea del HUD es preferible a que se atasque el audio.
    """

    def __init__(self, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._queues: list[asyncio.Queue[Event]] = []
        self._callbacks: list[Subscriber] = []

    # ── publicar ────────────────────────────────────────────────────────
    def emit(self, type_: EventType, /, **data: Any) -> None:
        """Publica un evento. Nunca bloquea ni lanza excepciones."""
        event = Event(type_, data)

        for queue in self._queues:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()  # descarta el más viejo
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

        for callback in self._callbacks:
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    asyncio.get_running_loop().create_task(result)
            except RuntimeError:
                # No hay loop corriendo (p. ej. en un test síncrono).
                pass
            except Exception:  # noqa: BLE001 - un suscriptor roto no tumba el núcleo
                pass

    # ── suscribirse ─────────────────────────────────────────────────────
    def on(self, callback: Subscriber) -> Callable[[], None]:
        """Registra un callback. Devuelve una función para darse de baja."""
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._callbacks.remove(callback)

        return unsubscribe

    async def stream(self) -> AsyncIterator[Event]:
        """Itera los eventos según van llegando. Ideal para el WebSocket."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._queues.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            with contextlib.suppress(ValueError):
                self._queues.remove(queue)
