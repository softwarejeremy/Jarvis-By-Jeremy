"""Captura del micrófono.

Un único stream de entrada abierto todo el tiempo, del que beben tres
consumidores a la vez: el wake word, el VAD y la grabación de tu frase. Abrir
y cerrar el micrófono en cada transición sería más simple de escribir, pero
introduce cortes de cientos de milisegundos justo en el peor momento —el
arranque de la frase— y en Windows a veces falla al reabrir.

El audio sale en bloques de 512 muestras a 16 kHz (32 ms), que es exactamente
lo que esperan tanto Silero como openWakeWord.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# Silero procesa frames de 512 muestras a 16 kHz. Todo el sistema se alinea
# a ese tamaño para no tener que rebufferizar en ningún punto.
FRAME_SAMPLES = 512
SAMPLE_RATE = 16_000
FRAME_MS = FRAME_SAMPLES * 1000 // SAMPLE_RATE  # 32 ms


class MicStream:
    """Micrófono siempre abierto, con búfer circular de contexto previo.

    El búfer circular (`pre_roll`) es lo que evita comerse la primera sílaba:
    cuando el VAD dice "ha empezado a hablar", esa sílaba ya pasó. Conservando
    los últimos 300 ms podemos recuperarla.
    """

    #: Hay hardware detrás. La interfaz web lo consulta para saber si tiene
    #: sentido ofrecer el botón de escuchar.
    es_real = True

    def __init__(
        self,
        samplerate: int = SAMPLE_RATE,
        device: int | str | None = None,
        pre_roll_ms: int = 300,
    ) -> None:
        self.samplerate = samplerate
        self._device = device
        self._pre_roll = deque(maxlen=max(1, pre_roll_ms // FRAME_MS))
        self._cola: asyncio.Queue[np.ndarray] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream = None
        self.desbordes = 0

    # ── ciclo de vida ───────────────────────────────────────────────────
    def start(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        self._loop = asyncio.get_running_loop()
        self._cola = asyncio.Queue(maxsize=128)
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    async def __aenter__(self) -> MicStream:
        self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.stop()

    # ── consumo ─────────────────────────────────────────────────────────
    async def frames(self):
        """Itera frames de 512 muestras (float32, mono, −1..1)."""
        if self._cola is None:
            raise RuntimeError("El micrófono no está abierto; llama a start().")
        while True:
            yield await self._cola.get()

    def pre_roll(self) -> list[np.ndarray]:
        """Los últimos ~300 ms capturados, para no perder el inicio de la frase."""
        return list(self._pre_roll)

    def vaciar(self) -> None:
        """Descarta lo acumulado. Se llama tras hablar, para no transcribir
        el eco de la propia voz de J.A.R.V.I.S."""
        if self._cola is None:
            return
        while not self._cola.empty():
            try:
                self._cola.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ── callback (hilo de audio de PortAudio) ───────────────────────────
    def _callback(self, indata, _frames: int, _time, status) -> None:  # noqa: ANN001
        if status:
            self.desbordes += 1

        bloque = indata[:, 0].copy()
        self._pre_roll.append(bloque)

        if self._loop is None or self._cola is None:
            return

        # El callback corre en otro hilo: hay que cruzar al loop de asyncio.
        self._loop.call_soon_threadsafe(self._encolar, bloque)

    def _encolar(self, bloque: np.ndarray) -> None:
        if self._cola is None:
            return
        if self._cola.full():
            try:
                self._cola.get_nowait()  # tiramos el más viejo
            except asyncio.QueueEmpty:
                pass
        try:
            self._cola.put_nowait(bloque)
        except asyncio.QueueFull:
            pass


class FakeMicStream:
    """Micrófono de mentira que reproduce un array de audio.

    Es lo que permite probar toda la cadena —VAD, transcripción, respuesta—
    en un servidor sin tarjeta de sonido, y en la CI.
    """

    es_real = False

    def __init__(self, audio: np.ndarray, samplerate: int = SAMPLE_RATE) -> None:
        import numpy as np

        self.samplerate = samplerate
        self._audio = np.asarray(audio, dtype=np.float32)
        self.desbordes = 0

    def start(self) -> None: ...
    def stop(self) -> None: ...

    async def __aenter__(self) -> FakeMicStream:
        return self

    async def __aexit__(self, *exc: object) -> None: ...

    async def frames(self):
        import numpy as np

        for i in range(0, len(self._audio), FRAME_SAMPLES):
            trozo = self._audio[i : i + FRAME_SAMPLES]
            if len(trozo) < FRAME_SAMPLES:
                trozo = np.pad(trozo, (0, FRAME_SAMPLES - len(trozo)))
            await asyncio.sleep(0)  # cede el control, sin esperar en tiempo real
            yield trozo

        # Cola de silencio, para que el VAD dé la frase por terminada.
        for _ in range(40):
            await asyncio.sleep(0)
            yield np.zeros(FRAME_SAMPLES, dtype=np.float32)

    def pre_roll(self) -> list[np.ndarray]:
        return []

    def vaciar(self) -> None: ...
