"""El icono de la bandeja del sistema: la cara de J.A.R.V.I.S.

Arrancando con Windows, el proceso corre bajo ``pythonw.exe`` con la ventana
oculta. No hay consola, así que todo lo que se imprima se descarta: este icono
es lo único que le dice al usuario que está vivo, en qué anda y cómo pararlo.

**Tres hilos, y cada objeto lo toca sólo el suyo:**

======================  ==========================================
hilo principal          el loop de asyncio: el núcleo, el bus, uvicorn
hilo ``bandeja``        ``icon.run()`` —su bomba de mensajes— y los
                        callbacks del menú
ejecutor                el repintado del icono
======================  ==========================================

Cruzar mal esa frontera tiene consecuencias concretas, y cada una está anotada
donde toca. Las dos que más caro salen:

- **Repintar desde el bus congelaría el audio.** ``EventBus.emit`` llama a sus
  suscriptores de forma síncrona en el hilo del loop, dentro del bucle que
  consume frames cada 32 ms; y asignar ``icon.icon`` en el backend de Windows
  escribe un ``.ico`` temporal en disco. Por eso el repintado va al ejecutor.
- **Esperar al núcleo congelaría el menú.** El hilo de la bandeja es el que
  atiende la bomba de mensajes: si se bloquea esperando a que Claude conteste,
  Windows marca el icono como «no responde». Por eso el puente no devuelve
  nada nunca.
"""

from __future__ import annotations

import contextlib
import os
import platform
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import inicio
from ..events import Event, EventType
from . import icono

if TYPE_CHECKING:
    import asyncio

    from ..core.core import JarvisCore

TITULO = "J.A.R.V.I.S."

# El icono se entrega a 64 px: Pillow empaqueta el .ico con todos los tamaños
# hasta 16 y Windows escoge el que le toque según el escalado de pantalla.
TAMANO_ICONO = 64


# ── lo que se puede probar sin pantalla ─────────────────────────────────
@dataclass(slots=True)
class Accion:
    """Una entrada del menú, descrita sin saber nada de pystray."""

    etiqueta: str
    ejecutar: Callable[[], None]
    marcada: Callable[[], bool] | None = None  # None = no es un conmutador
    por_defecto: bool = False  # la que responde al doble clic
    separador_antes: bool = False


@dataclass(slots=True)
class Mandos:
    """Lo que el menú puede hacer. Se inyecta para poder probarlo."""

    abrir_hud: Callable[[], None] | None
    escuchar: Callable[[], None]
    callar: Callable[[], None]
    alternar_pausa: Callable[[], None]
    esta_pausado: Callable[[], bool]
    alternar_inicio: Callable[[], None] | None
    inicio_instalado: Callable[[], bool] = field(default=lambda: False)
    salir: Callable[[], None] = field(default=lambda: None)


def construir_acciones(mandos: Mandos) -> list[Accion]:
    """El menú, en forma de datos.

    Las entradas que no aplican **no aparecen**, en vez de aparecer
    deshabilitadas: un menú corto que sólo ofrece lo posible se lee mejor que
    uno largo lleno de opciones muertas.
    """
    acciones: list[Accion] = []

    if mandos.abrir_hud is not None:
        acciones.append(
            Accion("Abrir el HUD", mandos.abrir_hud, por_defecto=True)
        )

    acciones.append(Accion("Escuchar ahora", mandos.escuchar))
    acciones.append(Accion("Silenciar", mandos.callar))
    acciones.append(
        Accion(
            "Micrófono en pausa",
            mandos.alternar_pausa,
            marcada=mandos.esta_pausado,
        )
    )

    if mandos.alternar_inicio is not None:
        acciones.append(
            Accion(
                "Arrancar con Windows",
                mandos.alternar_inicio,
                marcada=mandos.inicio_instalado,
                separador_antes=True,
            )
        )

    acciones.append(Accion("Salir", mandos.salir, separador_antes=True))
    return acciones


class Puente:
    """Cruza del hilo de la bandeja al loop de asyncio.

    No devuelve nada y no espera a nada. Es deliberado: el hilo de la bandeja
    atiende la bomba de mensajes del icono, y bloquearlo esperando un resultado
    dejaría el menú congelado mientras Claude piensa.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def llamar(self, funcion: Callable[[], Any]) -> None:
        try:
            self._loop.call_soon_threadsafe(funcion)
        except RuntimeError:
            # El loop ya está cerrado: pasa si se pulsa "Salir" dos veces.
            pass

    def lanzar(self, fabrica: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        """Arranca una corrutina del núcleo.

        Recibe una **fábrica**, no una corrutina ya creada. Si se pasara
        `core.escuchar_ahora()` ya evaluado y el loop estuviera cerrado, se
        quedaría un objeto corrutina sin esperar —con su aviso y su tarea a
        medio construir—. Así, o nace dentro del loop o no nace.
        """

        def _arrancar() -> None:
            import asyncio as _asyncio

            _asyncio.get_running_loop().create_task(fabrica())

        self.llamar(_arrancar)


# ── el envoltorio de pystray ────────────────────────────────────────────
class Bandeja:
    """El icono de verdad. Degrada a no hacer nada si algo falla."""

    def __init__(
        self,
        core: JarvisCore,
        *,
        url: str | None = None,
        salir: Callable[[], None],
        argumentos_inicio: list[str] | None = None,
        backend: Any = None,
    ) -> None:
        self._core = core
        self._url = url
        self._salir_pedido = salir
        self._argumentos_inicio = argumentos_inicio
        self._backend = backend

        self.activa = False
        self.error: str | None = None

        self._icono: Any = None
        self._hilo: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._puente: Puente | None = None
        self._baja: Callable[[], None] | None = None
        self._saludo: str | None = None

        # Fusión de repintados: en `pensando → hablando → dormido` sólo
        # interesan el primero y el último.
        self._pendiente: str | None = None
        self._pintando = False

    # ── arranque y parada ───────────────────────────────────────────────
    def arrancar(self) -> None:
        """Se llama **desde dentro del loop**, como `HotkeyListener.start()`.

        Es el único momento en que `get_running_loop()` es válido, y sin él no
        habría a dónde marshalar los clics del menú.
        """
        import asyncio

        try:
            if self._backend is None:
                self._backend = _cargar_pystray()

            self._loop = asyncio.get_running_loop()
            self._puente = Puente(self._loop)

            estado = self._core.state.value
            self._icono = self._backend.Icon(
                "jarvis",
                icon=icono.dibujar_reactor(estado, TAMANO_ICONO),
                title=icono.texto_tooltip(estado),
                menu=self._menu(),
            )
            self._baja = self._core.bus.on(self._al_evento)

            # Demonio obligatorio: si `icon.run()` se atasca, un hilo normal
            # impediría que el intérprete termine y dejaría el proceso de
            # zombi en el Administrador de tareas — justo el síntoma que este
            # icono viene a arreglar.
            self._hilo = threading.Thread(target=self._correr, name="bandeja", daemon=True)
            self._hilo.start()
            self.activa = True
        except Exception as exc:  # noqa: BLE001 - quedarse sin icono no es quedarse sin asistente
            self.error = str(exc) or type(exc).__name__
            self.activa = False

    def _correr(self) -> None:
        # `icon.run()` es bloqueante: es la bomba de mensajes del icono. Por
        # eso tiene un hilo para él solo.
        with contextlib.suppress(Exception):
            self._icono.run(setup=self._listo)

    def _listo(self, icono_) -> None:  # noqa: ANN001 - lo llama pystray
        with contextlib.suppress(Exception):
            icono_.visible = True
        if self._saludo:
            # El globo va aquí y no antes: `notify()` sobre un icono que aún no
            # existe no hace nada y tampoco avisa de que no lo ha hecho.
            with contextlib.suppress(Exception):
                icono_.notify(self._saludo, TITULO)
            self._saludo = None

    def detener(self) -> None:
        # Primero la baja del bus: si el ejecutor repintara después de cerrar
        # el icono, tocaría un objeto ya destruido.
        if self._baja is not None:
            with contextlib.suppress(Exception):
                self._baja()
            self._baja = None
        if self._icono is not None:
            with contextlib.suppress(Exception):
                self._icono.stop()
        self.activa = False

    def notificar(self, mensaje: str, titulo: str = TITULO) -> None:
        """Un globo junto al reloj. Es el único canal que funciona sin consola."""
        if self._icono is None:
            self._saludo = mensaje  # aún no ha arrancado: se enseña en `setup`
            return
        with contextlib.suppress(Exception):
            self._icono.notify(mensaje, titulo)

    # ── el menú ─────────────────────────────────────────────────────────
    def _mandos(self) -> Mandos:
        core, puente = self._core, self._puente
        assert puente is not None

        # Abrir el navegador se hace **en el hilo de la bandeja**, al revés de
        # lo habitual y a propósito: puede tardar segundos en arrancar, y esos
        # segundos son mucho más baratos aquí que en el loop, que estaría
        # dejando de leer el micrófono.
        abrir = (lambda: _abrir_url(self._url)) if self._url else None

        return Mandos(
            abrir_hud=abrir,
            escuchar=lambda: puente.lanzar(core.escuchar_ahora),
            callar=lambda: puente.llamar(core.player.interrumpir),
            alternar_pausa=lambda: puente.llamar(core.alternar_pausa),
            esta_pausado=lambda: core.pausado,
            alternar_inicio=self._alternar_inicio if _hay_carpeta_de_inicio() else None,
            inicio_instalado=inicio.esta_instalado,
            salir=self._salir,
        )

    def _menu(self) -> Any:
        acciones = construir_acciones(self._mandos())
        elementos = []
        for accion in acciones:
            if accion.separador_antes:
                elementos.append(self._backend.Menu.SEPARATOR)
            elementos.append(
                self._backend.MenuItem(
                    accion.etiqueta,
                    _envolver(accion.ejecutar),
                    # pystray reevalúa `checked` cada vez que se abre el menú,
                    # así que la casilla no necesita refrescarse a mano.
                    checked=(lambda _i, m=accion.marcada: m()) if accion.marcada else None,
                    default=accion.por_defecto,
                )
            )
        return self._backend.Menu(*elementos)

    def _alternar_inicio(self) -> None:
        # E/S de archivos en el hilo de la bandeja: correcto, no toca nada del
        # loop. Y el mensaje que devuelven se enseña por globo, porque la
        # consola donde antes se imprimía puede no existir.
        if inicio.esta_instalado():
            mensaje = inicio.desinstalar()
        else:
            mensaje = inicio.instalar(self._argumentos_inicio)
        self.notificar(_primera_linea(mensaje))

    def _salir(self) -> None:
        # El orden importa: primero se avisa al loop y sólo después se cierra
        # el icono. Al revés, si el marshalling fallara, el usuario se habría
        # quedado sin el único mando que tenía.
        if self._puente is not None:
            self._puente.llamar(self._salir_pedido)
        if self._icono is not None:
            with contextlib.suppress(Exception):
                self._icono.stop()

    # ── seguir el estado del núcleo ─────────────────────────────────────
    def _al_evento(self, ev: Event) -> None:
        if ev.type is not EventType.STATE_CHANGED:
            return
        self._pendiente = str(ev.data.get("state", ""))
        if self._pintando or self._loop is None:
            return  # ya hay uno en vuelo; pintará el último estado
        self._pintando = True
        self._loop.run_in_executor(None, self._pintar)

    def _pintar(self) -> None:
        """En el ejecutor, nunca en el loop: esto escribe en disco."""
        try:
            estado = self._pendiente or ""
            self._icono.icon = icono.dibujar_reactor(estado, TAMANO_ICONO)
            self._icono.title = icono.texto_tooltip(estado, coste_usd=self._core.coste_usd)
        except Exception:  # noqa: BLE001 - un icono que no se repinta no tumba nada
            pass
        finally:
            self._pintando = False


class NullBandeja:
    """La bandeja de mentira, como `NullPlayer` o `NullWakeWord`."""

    activa = False
    error: str | None = None

    def __init__(self, *_a: object, **_k: object) -> None: ...
    def arrancar(self) -> None: ...
    def detener(self) -> None: ...
    def notificar(self, mensaje: str, titulo: str = TITULO) -> None: ...


def crear_bandeja(
    core: JarvisCore,
    *,
    url: str | None = None,
    salir: Callable[[], None],
    argumentos_inicio: list[str] | None = None,
) -> Bandeja | NullBandeja:
    """Devuelve una bandeja de verdad, o una que no hace nada. Nunca lanza."""
    if os.environ.get("JARVIS_BANDEJA") == "0":
        return NullBandeja()

    # En macOS, pystray necesita el hilo principal (AppKit lo exige) y aquí lo
    # ocupa el loop de asyncio. Mejor quedarse sin icono que colgar el arranque.
    if platform.system() == "Darwin":
        return NullBandeja()

    try:
        _cargar_pystray()
    except Exception:  # noqa: BLE001
        return NullBandeja()
    if not icono.hay_pillow():
        return NullBandeja()

    return Bandeja(core, url=url, salir=salir, argumentos_inicio=argumentos_inicio)


# ── utilidades ──────────────────────────────────────────────────────────
def _cargar_pystray() -> Any:
    """Importa pystray.

    Nunca a nivel de módulo: en Linux sin pantalla, `import pystray` resuelve
    el backend con ansia y lanza `Xlib.error.DisplayNameError` —que no es un
    `ImportError`—, de modo que importarlo arriba tumbaría todo el arranque en
    un servidor.
    """
    import pystray

    return pystray


def _hay_carpeta_de_inicio() -> bool:
    """¿Tiene sentido ofrecer el arranque automático en este sistema?"""
    try:
        return inicio.carpeta_inicio() is not None
    except Exception:  # noqa: BLE001
        return False


def _abrir_url(url: str | None) -> None:
    if not url:
        return
    from .navegador import abrir

    abrir(url)


def _envolver(funcion: Callable[[], None]) -> Callable[..., None]:
    """pystray llama a los callbacks con (icono, elemento); a nosotros nos sobran."""

    def _llamada(*_args: object) -> None:
        with contextlib.suppress(Exception):
            funcion()

    return _llamada


def _primera_linea(texto: str) -> str:
    """Los mensajes de `inicio` son de varias líneas; en un globo cabe una."""
    for linea in texto.splitlines():
        limpia = linea.strip()
        if limpia:
            return limpia
    return texto.strip()
