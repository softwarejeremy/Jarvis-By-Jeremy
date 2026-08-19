"""Reproducción de audio interrumpible.

La clave está en la palabra *interrumpible*. Un `reproducir_y_esperar()` que
bloquea hasta el final impide cortar a J.A.R.V.I.S. a media frase, y sin eso
la conversación se siente como hablar con un contestador automático.

Por eso mantenemos un stream de salida abierto permanentemente y le vamos
metiendo trozos en una cola: interrumpir es simplemente vaciarla.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Frecuencia común a todos los motores de voz. Los tres (edge, SAPI,
# ElevenLabs) pueden generar a 24 kHz mono, así que no hace falta remuestrear.
TTS_SAMPLE_RATE = 24_000


class Player:
    """Cola de reproducción con corte inmediato."""

    def __init__(
        self,
        samplerate: int = TTS_SAMPLE_RATE,
        device: int | str | None = None,
        blocksize: int = 1024,
    ) -> None:
        self.samplerate = samplerate
        self._device = device
        self._blocksize = blocksize

        self._cola: queue.Queue[np.ndarray | None] = queue.Queue()
        self._actual: np.ndarray | None = None
        self._offset = 0
        self._lock = threading.Lock()
        self._stream = None
        self._inactivo = threading.Event()
        self._inactivo.set()

    # ── ciclo de vida ───────────────────────────────────────────────────
    def start(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="int16",
            blocksize=self._blocksize,
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        """Cierra el dispositivo. Para cortar el habla usa :meth:`interrumpir`."""
        self.interrumpir()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # ── uso ─────────────────────────────────────────────────────────────
    def encolar(self, pcm: np.ndarray) -> None:
        """Añade audio a la cola. No bloquea."""
        if pcm.size == 0:
            return
        self._inactivo.clear()
        self._cola.put(pcm)

    def interrumpir(self) -> None:
        """Corta el audio ahora mismo y tira lo que quedaba pendiente.

        Esto es el barge-in: se llama en cuanto el VAD detecta que el usuario
        ha empezado a hablar encima.
        """
        with self._lock:
            self._actual = None
            self._offset = 0
        while True:
            try:
                self._cola.get_nowait()
            except queue.Empty:
                break
        self._inactivo.set()

    @property
    def hablando(self) -> bool:
        return not self._inactivo.is_set()

    async def esperar_fin(self, timeout: float | None = None) -> bool:
        """Espera a que se vacíe la cola. Devuelve False si venció el timeout."""
        return await asyncio.to_thread(self._inactivo.wait, timeout)

    # ── callback de sounddevice (corre en el hilo de audio) ─────────────
    def _callback(self, outdata, frames: int, _time, _status) -> None:  # noqa: ANN001

        escritos = 0
        with self._lock:
            while escritos < frames:
                if self._actual is None:
                    try:
                        siguiente = self._cola.get_nowait()
                    except queue.Empty:
                        break
                    if siguiente is None:
                        break
                    self._actual = siguiente
                    self._offset = 0

                disponible = len(self._actual) - self._offset
                n = min(disponible, frames - escritos)
                outdata[escritos : escritos + n, 0] = self._actual[
                    self._offset : self._offset + n
                ]
                escritos += n
                self._offset += n

                if self._offset >= len(self._actual):
                    self._actual = None
                    self._offset = 0

        if escritos < frames:
            # Nos hemos quedado sin audio: silencio para el resto del bloque.
            outdata[escritos:, 0] = 0
            if self._cola.empty():
                self._inactivo.set()


class NullPlayer:
    """Reproductor de mentira: descarta el audio.

    Se usa en los tests y en el modo simulación, donde no hay tarjeta de
    sonido pero sí queremos ejercitar toda la cadena.
    """

    samplerate = TTS_SAMPLE_RATE

    def __init__(self, *_a: object, **_k: object) -> None:
        self.reproducido: list[np.ndarray] = []

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def interrumpir(self) -> None: ...

    def encolar(self, pcm: np.ndarray) -> None:
        self.reproducido.append(pcm)

    @property
    def hablando(self) -> bool:
        return False

    async def esperar_fin(self, timeout: float | None = None) -> bool:
        del timeout
        return True
