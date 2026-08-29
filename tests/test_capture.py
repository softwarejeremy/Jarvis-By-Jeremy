"""`MicStream._callback` corre en el hilo de PortAudio, ajeno al ciclo de
vida de asyncio: reportado en vivo, un Ctrl+C podía cerrar el loop mientras
ese hilo aún tenía un frame en camino, y `call_soon_threadsafe` sobre un loop
cerrado revienta con `RuntimeError` dentro de un callback de C del que Python
no puede recuperarse ("Exception ignored from cffi callback").
"""

from __future__ import annotations

import asyncio

import numpy as np

from jarvis.audio.capture import MicStream


def _mic_con_loop(loop: asyncio.AbstractEventLoop) -> MicStream:
    mic = MicStream()
    mic._loop = loop
    mic._cola = asyncio.Queue(maxsize=8)
    return mic


class TestCallbackConLoopCerrado:
    async def test_no_revienta_si_el_loop_ya_esta_cerrado(self):
        loop_ajeno = asyncio.new_event_loop()
        loop_ajeno.close()
        mic = _mic_con_loop(loop_ajeno)

        # No debe lanzar RuntimeError aunque el loop guardado esté cerrado.
        mic._callback(np.zeros((512, 1), dtype=np.float32), 512, None, None)

    async def test_no_revienta_si_se_cierra_justo_entre_medias(self, monkeypatch):
        # La carrera real: `is_closed()` dice que no, pero para cuando se
        # llama a `call_soon_threadsafe` el loop ya se cerró.
        loop = asyncio.get_running_loop()
        mic = _mic_con_loop(loop)

        def _explota(*_a, **_k):
            raise RuntimeError("Event loop is closed")

        monkeypatch.setattr(loop, "call_soon_threadsafe", _explota)

        mic._callback(np.zeros((512, 1), dtype=np.float32), 512, None, None)

    async def test_con_el_loop_abierto_sigue_encolando(self):
        mic = _mic_con_loop(asyncio.get_running_loop())

        mic._callback(np.full((512, 1), 0.5, dtype=np.float32), 512, None, None)
        await asyncio.sleep(0)  # deja correr el call_soon_threadsafe encolado

        assert mic._cola.qsize() == 1

    async def test_sin_status_ni_loop_no_revienta(self):
        # Antes de start(): documenta que el callback es defensivo incluso
        # sin loop ni cola asignados (no debería pasar en producción, pero
        # tampoco debe reventar si pasara).
        mic = MicStream()

        mic._callback(np.zeros((512, 1), dtype=np.float32), 512, None, None)
