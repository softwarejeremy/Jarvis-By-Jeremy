"""`en_hilo_daemon`: como `asyncio.to_thread`, pero sin colgar el cierre.

Reportado en vivo, tres veces con el mismo síntoma (Ctrl+C dejaba el proceso
"congelado" o soltaba un `KeyboardInterrupt` sin manejar al cerrar): el modo
texto esperando al teclado, el login de Google esperando un redirect que no
llegaba, y el reproductor de audio esperando a que Jarvis terminara de
hablar. Las tres pasan por aquí ahora.
"""

from __future__ import annotations

import threading

import pytest

from jarvis.hilos import en_hilo_daemon


class TestEnHiloDaemon:
    async def test_usa_un_hilo_daemon(self, monkeypatch):
        creados: list[bool | None] = []
        hilo_real = threading.Thread

        class HiloEspia(hilo_real):
            def __init__(self, *a, **kw):  # noqa: ANN002, ANN003
                creados.append(kw.get("daemon"))
                super().__init__(*a, **kw)

        monkeypatch.setattr(threading, "Thread", HiloEspia)

        resultado = await en_hilo_daemon(lambda x: x * 2, 21)

        assert resultado == 42
        assert creados == [True]

    async def test_propaga_la_excepcion_tal_cual(self):
        def explota() -> None:
            raise ValueError("fallo de verdad")

        with pytest.raises(ValueError, match="fallo de verdad"):
            await en_hilo_daemon(explota)

    async def test_sin_argumentos_tambien_funciona(self):
        assert await en_hilo_daemon(lambda: "listo") == "listo"
