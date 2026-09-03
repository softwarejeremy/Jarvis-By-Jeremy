"""`Player.esperar_fin`: no debe colgar el cierre si nunca termina de sonar.

Reportado en vivo: un segundo Ctrl+C mientras Jarvis todavía hablaba salía
como `KeyboardInterrupt` sin manejar, dentro de
`concurrent.futures.thread._python_exit` esperando un `t.join()` que nunca
volvía. Causa: `asyncio.to_thread` corre en el executor por defecto, cuyos
hilos no son daemon — con `_inactivo.wait(timeout)` bloqueado de verdad ahí
dentro, el intérprete se queda esperándolo al cerrar aunque el `timeout` de
la propia espera esté acotado.
"""

from __future__ import annotations

import asyncio
import threading

from jarvis.audio.player import Player


class TestEsperarFin:
    async def test_devuelve_true_en_cuanto_se_pone_inactivo(self):
        player = Player()
        player._inactivo.clear()  # "hablando"

        async def _liberar() -> None:
            await asyncio.sleep(0.05)
            player._inactivo.set()

        asyncio.create_task(_liberar())

        assert await player.esperar_fin(timeout=2.0) is True

    async def test_devuelve_false_si_vence_el_timeout(self):
        player = Player()
        player._inactivo.clear()

        assert await player.esperar_fin(timeout=0.05) is False

    async def test_usa_un_hilo_daemon(self, monkeypatch):
        creados: list[bool | None] = []
        hilo_real = threading.Thread

        class HiloEspia(hilo_real):
            def __init__(self, *a, **kw):  # noqa: ANN002, ANN003
                creados.append(kw.get("daemon"))
                super().__init__(*a, **kw)

        monkeypatch.setattr(threading, "Thread", HiloEspia)

        player = Player()
        await player.esperar_fin(timeout=0.05)

        assert creados == [True]
