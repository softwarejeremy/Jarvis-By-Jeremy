"""Temporizadores: "avísame en 10 minutos".

No se prueban esperando minutos de verdad. `_disparar` recibe la espera como
parámetro (`dormir`) justo para eso; y para probar la herramienta de
principio a fin se usa una fracción de segundo real en vez de monkeypatchear
`asyncio.sleep` — su valor por defecto en `_disparar` queda fijado al
definirse la función, así que parchear `asyncio.sleep` después no lo tocaría.
"""

from __future__ import annotations

import asyncio

from jarvis.tools import temporizadores as t


class TestTextos:
    def test_confirmacion_en_plural(self):
        assert t._texto_confirmacion(10) == "Vale, le aviso en 10 minutos."

    def test_confirmacion_en_singular(self):
        assert t._texto_confirmacion(1) == "Vale, le aviso en un minuto."

    def test_confirmacion_con_fraccion(self):
        assert "1.5" in t._texto_confirmacion(1.5)

    def test_aviso_con_mensaje(self):
        texto = t._texto_aviso(10, "revisar el horno")
        assert texto == "Han pasado 10 minutos: revisar el horno."

    def test_aviso_sin_mensaje_es_generico(self):
        texto = t._texto_aviso(5, None)
        assert texto == "Han pasado los 5 minutos que pidió."

    def test_aviso_en_singular(self):
        assert "1 minuto:" in t._texto_aviso(1, "algo")

    def test_aviso_con_fraccion_no_muestra_el_float_crudo(self):
        # 1/6000 minuto es una fracción larga (0.00016666...); sin redondeo
        # explícito se colaría entera en el texto que se lee en voz alta.
        texto = t._texto_aviso(1 / 6000, "algo")
        assert "0.0" in texto
        assert "00016666" not in texto


class TestDisparar:
    async def test_espera_y_luego_avisa(self):
        avisos: list[str] = []
        orden: list[str] = []

        async def dormir_falso(segundos):
            orden.append(f"dormir({segundos})")

        async def avisar_falso(texto):
            orden.append("avisar")
            avisos.append(texto)

        await t._disparar(600, "hola", avisar_falso, dormir=dormir_falso)

        assert avisos == ["hola"]
        assert orden == ["dormir(600)", "avisar"], "debe esperar antes de avisar, no al revés"

    async def test_no_avisa_si_dormir_lanza(self):
        # Si algo cancela la espera, no debe avisar de todos modos.
        avisado = False

        async def avisar_falso(_texto):
            nonlocal avisado
            avisado = True

        async def dormir_que_falla(_segundos):
            raise asyncio.CancelledError

        try:
            await t._disparar(1, "x", avisar_falso, dormir=dormir_que_falla)
        except asyncio.CancelledError:
            pass

        assert not avisado


class TestPonerTemporizador:
    def _construir(self):
        avisos: list[str] = []

        async def avisar(texto: str) -> None:
            avisos.append(texto)

        (herramienta,) = t.herramientas_de_temporizador(avisar)
        return herramienta, avisos

    async def test_confirma_de_inmediato_sin_esperar(self):
        herramienta, avisos = self._construir()

        # 1/6000 de minuto = 0.01 s reales: rápido de verdad, sin
        # monkeypatchear asyncio.sleep (su valor por defecto en _disparar
        # queda fijado al definirse la función, no al llamarla).
        resultado = await herramienta.handler({"minutos": 1 / 6000})

        assert "aviso en" in resultado["content"][0]["text"]
        assert avisos == [], "no debe avisar todavía, sólo confirmar"

    async def test_avisa_pasado_el_tiempo(self):
        herramienta, avisos = self._construir()

        await herramienta.handler({"minutos": 1 / 6000, "mensaje": "revisar el horno"})
        await asyncio.sleep(0.2)  # deja correr la tarea de fondo

        assert len(avisos) == 1
        assert "revisar el horno" in avisos[0]

    async def test_minutos_cero_no_programa_nada(self):
        herramienta, avisos = self._construir()

        resultado = await herramienta.handler({"minutos": 0})
        await asyncio.sleep(0.1)

        assert "No he entendido" in resultado["content"][0]["text"]
        assert avisos == []

    async def test_minutos_negativos_no_programa_nada(self):
        herramienta, avisos = self._construir()

        resultado = await herramienta.handler({"minutos": -5})
        await asyncio.sleep(0.1)

        assert "No he entendido" in resultado["content"][0]["text"]
        assert avisos == []

    async def test_minutos_no_numericos_no_revienta(self):
        herramienta, avisos = self._construir()

        resultado = await herramienta.handler({"minutos": "un rato"})
        await asyncio.sleep(0.1)

        assert "No he entendido" in resultado["content"][0]["text"]
        assert avisos == []

    async def test_no_bloquea_el_turno(self):
        # La garantía central: pedir un temporizador de 5 minutos no puede
        # dejar la conversación esperando esos 5 minutos.
        herramienta, _avisos = self._construir()

        import time

        t0 = time.monotonic()
        await herramienta.handler({"minutos": 5})
        transcurrido = time.monotonic() - t0

        assert transcurrido < 1.0


class TestRegistro:
    def test_devuelve_poner_temporizador(self):
        async def avisar(_texto: str) -> None: ...

        nombres = {h.name for h in t.herramientas_de_temporizador(avisar)}
        assert nombres == {"poner_temporizador"}
