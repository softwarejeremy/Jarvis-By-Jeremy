"""La bandeja del sistema.

Arrancando con Windows no hay consola: este icono es lo único que dice que
J.A.R.V.I.S. está vivo y la única forma de pararlo. Lo que más se prueba aquí
no es que pinte bonito, sino que **cruce bien la frontera entre hilos**: el
menú corre en el hilo de la bandeja y el núcleo en el del loop, y equivocarse
significa o congelar el menú o congelar el audio.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from jarvis import inicio
from jarvis.audio.player import NullPlayer
from jarvis.audio.stt import FakeTranscriber
from jarvis.audio.wakeword import NullWakeWord
from jarvis.core.core import JarvisCore
from jarvis.events import EventType, State
from jarvis.ui import bandeja as mod
from jarvis.ui.bandeja import Bandeja, Mandos, NullBandeja, Puente, construir_acciones

from .conftest import ControlledMic, FakeAgent, FakeTTS, PystrayFalso

pytest.importorskip("PIL", reason="el icono necesita Pillow (extra `bandeja`)")


def nucleo(settings):  # noqa: ANN001
    return JarvisCore(
        settings,
        agent=FakeAgent(),
        tts=FakeTTS(),
        player=NullPlayer(),
        mic=ControlledMic(10),
        transcriber=FakeTranscriber(["hola"]),
        wakeword=NullWakeWord(),
    )


async def montar(settings, **kwargs):  # noqa: ANN001, ANN003
    """Una bandeja arrancada con el backend falso. Devuelve (bandeja, núcleo)."""
    core = nucleo(settings)
    kwargs.setdefault("salir", lambda: None)
    b = Bandeja(core, backend=PystrayFalso, **kwargs)
    b.arrancar()
    await asyncio.sleep(0.05)  # que el hilo llegue a `setup`
    return b, core


# ── el puente entre hilos ───────────────────────────────────────────────
class TestPuente:
    async def test_ejecuta_en_el_hilo_del_loop(self):
        # Si el núcleo se tocara desde el hilo de la bandeja, la máquina de
        # estados dejaría de tener un solo dueño.
        puente = Puente(asyncio.get_running_loop())
        del_loop = threading.get_ident()
        visto: list[int] = []

        def desde_otro_hilo() -> None:
            puente.llamar(lambda: visto.append(threading.get_ident()))

        hilo = threading.Thread(target=desde_otro_hilo)
        hilo.start()
        hilo.join()
        await asyncio.sleep(0.05)

        assert visto == [del_loop]

    async def test_no_espera_al_resultado(self):
        # Bloquear el hilo de la bandeja congelaría la bomba de mensajes del
        # icono, y Windows lo marcaría como "no responde".
        puente = Puente(asyncio.get_running_loop())
        empezado = threading.Event()

        def llamador() -> None:
            puente.llamar(lambda: None)
            empezado.set()

        hilo = threading.Thread(target=llamador)
        hilo.start()
        assert empezado.wait(timeout=1.0), "el puente ha bloqueado a quien lo llamó"
        hilo.join()

    async def test_lanza_corrutinas_del_nucleo(self):
        puente = Puente(asyncio.get_running_loop())
        hecho = []

        async def trabajo() -> None:
            hecho.append(True)

        puente.lanzar(trabajo)
        await asyncio.sleep(0.05)
        assert hecho == [True]

    async def test_con_el_loop_cerrado_no_revienta(self):
        # Pasa al pulsar "Salir" dos veces seguidas.
        loop = asyncio.new_event_loop()
        loop.close()
        Puente(loop).llamar(lambda: None)  # no debe lanzar


# ── el menú, como datos ─────────────────────────────────────────────────
def _mandos(**cambios):  # noqa: ANN003
    base = {
        "abrir_hud": lambda: None,
        "escuchar": lambda: None,
        "callar": lambda: None,
        "alternar_pausa": lambda: None,
        "esta_pausado": lambda: False,
        "alternar_inicio": lambda: None,
        "inicio_instalado": lambda: False,
        "salir": lambda: None,
    }
    base.update(cambios)
    return Mandos(**base)


class TestMenu:
    def test_el_hud_solo_aparece_si_hay_web(self):
        con = [a.etiqueta for a in construir_acciones(_mandos())]
        sin = [a.etiqueta for a in construir_acciones(_mandos(abrir_hud=None))]
        assert "Abrir el HUD" in con
        assert "Abrir el HUD" not in sin

    def test_el_hud_es_la_accion_del_doble_clic(self):
        porDefecto = [a.etiqueta for a in construir_acciones(_mandos()) if a.por_defecto]
        assert porDefecto == ["Abrir el HUD"]

    def test_el_arranque_no_se_ofrece_fuera_de_windows(self):
        etiquetas = [a.etiqueta for a in construir_acciones(_mandos(alternar_inicio=None))]
        assert "Arrancar con Windows" not in etiquetas
        # Y no por eso desaparece lo demás.
        assert "Salir" in etiquetas

    def test_salir_siempre_esta(self):
        etiquetas = [a.etiqueta for a in construir_acciones(_mandos(abrir_hud=None))]
        assert etiquetas[-1] == "Salir"

    def test_la_pausa_es_un_conmutador(self):
        acciones = {a.etiqueta: a for a in construir_acciones(_mandos(esta_pausado=lambda: True))}
        pausa = acciones["Micrófono en pausa"]
        assert pausa.marcada is not None
        assert pausa.marcada() is True


# ── el menú, conectado al núcleo ────────────────────────────────────────
class TestMenuConectado:
    async def test_escuchar_llega_al_nucleo(self, settings):
        b, core = await montar(settings)
        llamadas = []
        core.escuchar_ahora = lambda: _corrutina(llamadas)
        try:
            b._icono.menu.por_etiqueta("Escuchar ahora").accion()
            await asyncio.sleep(0.05)
            assert llamadas == [True]
        finally:
            b.detener()

    async def test_silenciar_interrumpe_al_reproductor(self, settings):
        b, core = await montar(settings)
        cortes = []
        core.player.interrumpir = lambda: cortes.append(True)
        try:
            b._icono.menu.por_etiqueta("Silenciar").accion()
            await asyncio.sleep(0.05)
            assert cortes == [True]
        finally:
            b.detener()

    async def test_la_pausa_alterna_de_verdad(self, settings):
        b, core = await montar(settings)
        try:
            entrada = b._icono.menu.por_etiqueta("Micrófono en pausa")
            assert entrada.marcado is False

            entrada.accion()
            await asyncio.sleep(0.05)
            assert core.pausado is True
            # pystray reevalúa la casilla al abrir el menú: sin refrescar nada,
            # tiene que reflejar el estado nuevo.
            assert entrada.marcado is True

            entrada.accion()
            await asyncio.sleep(0.05)
            assert core.pausado is False
        finally:
            b.detener()

    async def test_abrir_el_hud_usa_la_url(self, settings, monkeypatch):
        abiertas = []
        monkeypatch.setattr("jarvis.ui.navegador.abrir", lambda u: abiertas.append(u) or True)

        b, _ = await montar(settings, url="http://localhost:8765")
        try:
            b._icono.menu.por_etiqueta("Abrir el HUD").accion()
            assert abiertas == ["http://localhost:8765"]
        finally:
            b.detener()

    async def test_salir_avisa_al_loop_antes_de_cerrar_el_icono(self, settings):
        # El orden importa: al revés, si el marshalling fallara, el usuario se
        # quedaría sin el único mando que tenía.
        orden = []
        b, _ = await montar(settings, salir=lambda: orden.append("loop"))
        icono_falso = b._icono
        original = icono_falso.stop

        def stop_apuntando() -> None:
            orden.append("icono")
            original()

        icono_falso.stop = stop_apuntando
        b._icono.menu.por_etiqueta("Salir").accion()
        await asyncio.sleep(0.05)

        assert orden and orden[0] == "icono", "el icono se cierra tras encolar el aviso"
        assert "loop" in orden, "no ha avisado al loop"

    async def test_el_conmutador_de_arranque_instala_y_desinstala(
        self, settings, tmp_path, monkeypatch
    ):
        destino = tmp_path / "inicio"
        monkeypatch.setattr(inicio, "carpeta_inicio", lambda: destino)

        b, _ = await montar(settings, argumentos_inicio=["-m", "jarvis", "--web"])
        try:
            entrada = b._icono.menu.por_etiqueta("Arrancar con Windows")
            assert entrada.marcado is False

            entrada.accion()
            assert inicio.esta_instalado() is True
            assert entrada.marcado is True

            entrada.accion()
            assert inicio.esta_instalado() is False
        finally:
            b.detener()

    async def test_el_conmutador_avisa_por_globo(self, settings, tmp_path, monkeypatch):
        # El mensaje de `instalar()` se imprimía en una consola que bajo
        # pythonw no existe. Por globo sí se ve.
        monkeypatch.setattr(inicio, "carpeta_inicio", lambda: tmp_path / "inicio")
        b, _ = await montar(settings)
        try:
            b._icono.notificaciones.clear()
            b._icono.menu.por_etiqueta("Arrancar con Windows").accion()
            assert b._icono.notificaciones, "no ha dicho nada al instalar"
        finally:
            b.detener()


# ── seguir el estado ────────────────────────────────────────────────────
class TestEstado:
    async def test_repinta_al_cambiar_de_estado(self, settings):
        b, core = await montar(settings)
        try:
            antes = b._icono.icon
            core._set_state(State.PENSANDO)
            await _esperar(lambda: b._icono.icon is not antes)
            assert "pensando" in b._icono.title
        finally:
            b.detener()

    async def test_no_pinta_en_el_hilo_del_loop(self, settings):
        # Asignar `icon.icon` escribe un .ico temporal en disco. Hacerlo en el
        # loop metería esa E/S en el bucle que lee el micrófono cada 32 ms.
        b, core = await montar(settings)
        try:
            b._icono.hilos_de_pintado.clear()
            core._set_state(State.HABLANDO)
            await _esperar(lambda: bool(b._icono.hilos_de_pintado))
            assert threading.get_ident() not in b._icono.hilos_de_pintado
        finally:
            b.detener()

    async def test_fusiona_los_cambios_seguidos(self, settings):
        # En `pensando → hablando → dormido` sólo se ven el primero y el
        # último; repintar los intermedios es disco tirado.
        b, core = await montar(settings)
        try:
            b._icono.hilos_de_pintado.clear()
            for estado in (State.PENSANDO, State.HABLANDO, State.TRANSCRIBIENDO,
                           State.PENSANDO, State.DORMIDO):
                core._set_state(estado)
            await asyncio.sleep(0.3)
            assert len(b._icono.hilos_de_pintado) < 5
        finally:
            b.detener()

    async def test_un_fallo_al_pintar_no_tumba_el_bus(self, settings):
        b, core = await montar(settings)
        try:
            def explota(_valor) -> None:  # noqa: ANN001
                raise RuntimeError("el icono se ha roto")

            type(b._icono).icon = property(lambda s: None, explota)
            core._set_state(State.PENSANDO)
            await asyncio.sleep(0.2)
            # El bus sigue funcionando.
            recibidos = []
            core.bus.on(recibidos.append)
            core.bus.emit(EventType.LOG, message="sigo vivo")
            assert recibidos
        finally:
            b.detener()
            # Restaurar la propiedad para no contaminar otros tests.
            from .conftest import IconoFalso

            type(b._icono).icon = IconoFalso.__dict__["icon"]

    async def test_no_se_suscribe_dos_veces_ni_deja_rastro(self, settings):
        b, core = await montar(settings)
        b.detener()
        antes = len(b._icono.hilos_de_pintado)
        core._set_state(State.PENSANDO)
        await asyncio.sleep(0.15)
        assert len(b._icono.hilos_de_pintado) == antes, "sigue pintando tras detenerse"


# ── arranque, parada y degradación ──────────────────────────────────────
class TestArranqueYParada:
    async def test_arranca_visible_y_con_el_estado_actual(self, settings):
        b, _ = await montar(settings)
        try:
            assert b.activa is True
            assert b._icono.visible is True
            assert "dormido" in b._icono.title
        finally:
            b.detener()

    async def test_el_globo_de_bienvenida_espera_a_que_exista_el_icono(self, settings):
        # `notify()` sobre un icono que aún no existe no hace nada y tampoco
        # avisa de que no lo ha hecho.
        core = nucleo(settings)
        b = Bandeja(core, backend=PystrayFalso, salir=lambda: None)
        b.notificar("J.A.R.V.I.S. en línea")  # antes de arrancar
        b.arrancar()
        await asyncio.sleep(0.05)
        try:
            assert [m for m, _ in b._icono.notificaciones] == ["J.A.R.V.I.S. en línea"]
        finally:
            b.detener()

    async def test_detener_dos_veces_no_revienta(self, settings):
        b, _ = await montar(settings)
        b.detener()
        b.detener()
        assert b.activa is False

    async def test_un_backend_roto_no_tumba_el_arranque(self, settings):
        class Roto:
            @staticmethod
            def Icon(*_a, **_k):  # noqa: ANN003, N802
                raise RuntimeError("no hay área de notificación")

            Menu = PystrayFalso.Menu
            MenuItem = PystrayFalso.MenuItem

        b = Bandeja(nucleo(settings), backend=Roto, salir=lambda: None)
        b.arrancar()

        assert b.activa is False
        assert b.error and "notificación" in b.error


class TestDegradacion:
    def test_la_bandeja_nula_acepta_todo_sin_hacer_nada(self):
        n = NullBandeja()
        n.arrancar()
        n.notificar("hola")
        n.detener()
        assert n.activa is False

    def test_sin_pystray_devuelve_la_nula(self, settings, monkeypatch):
        def no_hay():
            raise ImportError("no such module")

        monkeypatch.setattr(mod, "_cargar_pystray", no_hay)
        creada = mod.crear_bandeja(nucleo(settings), salir=lambda: None)
        assert isinstance(creada, NullBandeja)

    def test_en_macos_devuelve_la_nula(self, settings, monkeypatch):
        # pystray exige el hilo principal en macOS y aquí lo ocupa el loop.
        monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
        creada = mod.crear_bandeja(nucleo(settings), salir=lambda: None)
        assert isinstance(creada, NullBandeja)

    def test_se_puede_apagar_por_entorno(self, settings, monkeypatch):
        monkeypatch.setenv("JARVIS_BANDEJA", "0")
        creada = mod.crear_bandeja(nucleo(settings), salir=lambda: None)
        assert isinstance(creada, NullBandeja)


# ── utilidades del test ─────────────────────────────────────────────────
async def _corrutina(destino: list) -> None:
    destino.append(True)


async def _esperar(condicion, timeout: float = 3.0) -> None:
    limite = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < limite:
        if condicion():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("la condición no se cumplió a tiempo")
