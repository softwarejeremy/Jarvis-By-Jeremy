"""Punto de entrada de J.A.R.V.I.S.

Modos disponibles::

    python -m jarvis                 # voz completa: wake word + atajo
    python -m jarvis --texto         # escribes tú, él responde con voz
    python -m jarvis --demo          # sin API key, respuestas simuladas
    python -m jarvis --sim audio.wav # inyecta un WAV en vez del micrófono
    python -m jarvis --web           # además, HUD en el navegador
    python -m jarvis --diag          # diagnóstico del equipo

El montaje de las piezas vive aquí a propósito: el núcleo recibe todo por
constructor y no sabe construir nada, que es lo que permite sustituir
cualquier componente por uno falso en los tests.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from . import instancia
from .config import Settings, load_settings
from .core.agent import Agent, DemoAgent
from .core.core import JarvisCore
from .core.gasto import Gasto
from .core.historial import Historial
from .core.memory import Memory
from .core.permissions import PermissionGuard
from .events import EventBus
from .ui.bandeja import Bandeja, NullBandeja, crear_bandeja
from .ui.console import ConsoleHUD


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jarvis", description="Asistente de voz J.A.R.V.I.S.")
    p.add_argument("--texto", action="store_true", help="conversar escribiendo, sin micrófono")
    p.add_argument("--demo", action="store_true", help="sin API key: respuestas simuladas")
    p.add_argument("--sim", metavar="WAV", help="usar un archivo WAV en vez del micrófono")
    p.add_argument("--muda", action="store_true", help="no reproducir audio (sólo texto)")
    p.add_argument("--web", action="store_true", help="abrir el HUD en el navegador")
    p.add_argument("--puerto", type=int, default=8765, help="puerto del HUD (por defecto 8765)")
    p.add_argument(
        "--https",
        action="store_true",
        help="servir el HUD con TLS: hace falta para usar el micrófono desde el móvil",
    )
    p.add_argument(
        "--sin-navegador",
        action="store_true",
        help="con --web, no abrir el navegador solo al arrancar",
    )
    p.add_argument(
        "--sin-bandeja",
        action="store_true",
        help="no poner el icono en la bandeja del sistema",
    )
    p.add_argument("--diag", action="store_true", help="diagnosticar el equipo y salir")
    p.add_argument(
        "--arrancar-con-windows",
        action="store_true",
        help="que J.A.R.V.I.S. se inicie solo al encender el equipo",
    )
    p.add_argument(
        "--quitar-del-inicio",
        action="store_true",
        help="deshacer el arranque automático",
    )
    p.add_argument("--config", metavar="TOML", help="ruta a un config.toml alternativo")
    p.add_argument("-v", "--verbose", action="store_true", help="salida detallada")
    return p.parse_args(argv)


def _construir(args: argparse.Namespace, s: Settings, bus: EventBus):  # noqa: ANN202
    """Ensambla el núcleo con los componentes que toquen según el modo."""
    from .audio.player import NullPlayer, Player
    from .audio.tts.base import crear_motor
    from .audio.wakeword import NullWakeWord, WakeWordDetector

    # ── voz ─────────────────────────────────────────────────────────────
    tts = crear_motor(s)
    player = NullPlayer() if args.muda else Player(device=s.audio.output_device)

    # ── oído ────────────────────────────────────────────────────────────
    if args.sim:
        from .audio.capture import FakeMicStream
        from .audio.stt import Transcriber

        mic = FakeMicStream(_leer_wav(Path(args.sim)))
        transcriber = Transcriber(s)
        wakeword = NullWakeWord()
    elif args.texto:
        import numpy as np

        from .audio.capture import FakeMicStream
        from .audio.stt import FakeTranscriber

        mic = FakeMicStream(np.zeros(0, dtype=np.float32))
        transcriber = FakeTranscriber()
        wakeword = NullWakeWord()
    else:
        from .audio.capture import MicStream
        from .audio.stt import Transcriber

        mic = MicStream(
            samplerate=s.audio.sample_rate,
            device=s.audio.input_device,
            pre_roll_ms=s.vad.pre_roll_ms,
        )
        transcriber = Transcriber(s)
        wakeword = (
            WakeWordDetector(s) if s.wakeword.enabled else NullWakeWord()
        )

    # ── cerebro ─────────────────────────────────────────────────────────
    memoria = Memory(s.memory_dir)

    # Se escucha aquí y no en el servidor web para que quede registro haya o
    # no HUD mirando: el bus es la fuente de verdad de todo el proyecto.
    Historial(s.data_dir / "conversaciones").escuchar(bus)
    Gasto(s.data_dir / "gasto.json").escuchar(bus)

    # El núcleo aún no existe, pero el guardián de permisos necesita poder
    # preguntarle por voz. Se resuelve con una indirección: el guardián llama
    # a una función que, cuando de verdad se invoque, ya tendrá el núcleo.
    contenedor: dict[str, JarvisCore] = {}

    async def confirmar(pregunta: str) -> bool:
        core = contenedor.get("core")
        if core is None:
            return False
        return await core.confirmar_por_voz(pregunta)

    # Mismo truco que `confirmar`: un temporizador se dispara mucho después
    # de registrarse la herramienta, cuando el núcleo ya existe de sobra.
    async def avisar(texto: str) -> None:
        core = contenedor.get("core")
        if core is not None:
            await core._decir_ahora(texto)

    if args.demo or not s.has_api_key:
        agent = DemoAgent()
    else:
        from .tools.memory_tool import construir_servidor_jarvis

        agent = Agent(
            s,
            can_use_tool=PermissionGuard(s, confirmar, bus),
            mcp_servers={"jarvis": construir_servidor_jarvis(memoria, avisar)},
            memoria=memoria.cargar(),
        )

    core = JarvisCore(
        s,
        agent=agent,
        tts=tts,
        player=player,
        mic=mic,
        transcriber=transcriber,
        wakeword=wakeword,
        bus=bus,
    )
    contenedor["core"] = core
    return core


def _leer_wav(ruta: Path):  # noqa: ANN202
    """Carga un WAV mono de 16 kHz como float32. Para el modo simulación."""
    import wave

    import numpy as np

    with wave.open(str(ruta), "rb") as wav:
        if wav.getframerate() != 16_000 or wav.getnchannels() != 1:
            raise SystemExit(
                f"{ruta}: hace falta un WAV mono a 16 kHz "
                f"(este es {wav.getnchannels()} canal(es) a {wav.getframerate()} Hz)."
            )
        crudo = wav.readframes(wav.getnframes())
    return np.frombuffer(crudo, dtype=np.int16).astype(np.float32) / 32768.0


def _qr_para_terminal(url: str) -> str | None:
    """El QR de una URL, listo para pegar en la terminal.

    Apuntar la cámara del móvil a la terminal abre el HUD sin teclear la IP.
    `qrcode` es parte del extra `web`; si falta —o algo más sale mal
    generándolo— no hay QR, pero la URL en texto de la línea de arriba sigue
    ahí: no es motivo para tumbar el arranque.
    """
    try:
        import io

        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)

        salida = io.StringIO()
        qr.print_ascii(out=salida, invert=True)
        return salida.getvalue()
    except Exception:  # noqa: BLE001 - sin QR se sigue viendo la URL en texto
        return None


async def _main_async(args: argparse.Namespace, argv_crudo: list[str]) -> int:
    s = load_settings(Path(args.config) if args.config else None)
    bus = EventBus()
    hud = ConsoleHUD(bus, verbose=args.verbose)

    # Con el arranque automático, esto deja de ser hipotético: uno lo lanza el
    # sistema al iniciar sesión y otro lo lanza el usuario a mano. Se comprueba
    # antes de cargar los modelos (segundos y medio giga) y sin importar el
    # modo: dos instancias compiten por el micrófono y el atajo global aunque
    # ninguna lleve --web.
    reserva = instancia.reservar(s.data_dir)
    if reserva is None:
        return await _avisar_de_la_otra_instancia(s, hud, args)

    try:
        return await _arrancar_todo(args, argv_crudo, s, bus, hud, reserva)
    finally:
        reserva.liberar()


async def _avisar_de_la_otra_instancia(
    s: Settings, hud: ConsoleHUD, args: argparse.Namespace
) -> int:
    """Ya hay un J.A.R.V.I.S. vivo. Se le señala al usuario y se sale.

    Código 0, no 1: para quien hizo doble clic dos veces, el objetivo —que
    J.A.R.V.I.S. esté funcionando— está cumplido. Un código de error haría que
    cualquier lanzador (el propio `.vbs`) lo tratara como un fallo.
    """
    huella = instancia.huella_ajena(s.data_dir)
    hud.console.print("[yellow]Ya hay un J.A.R.V.I.S. funcionando en este equipo.[/yellow]")

    if huella and huella.url:
        hud.console.print(f"  [cyan]Su HUD está en {huella.url}[/cyan]")
        # El mensaje por consola puede ir a un NULL_FILE si esto se lanzó sin
        # terminal (pythonw): abrir el navegador es el canal que de verdad
        # informa en ese caso.
        if not args.sin_navegador:
            from .ui.navegador import abrir

            await asyncio.to_thread(abrir, huella.url)
    else:
        hud.console.print(
            "  [dim]No sé cuál es su HUD; probablemente arrancó sin --web.[/dim]"
        )

    return 0


async def _arrancar_todo(
    args: argparse.Namespace,
    argv_crudo: list[str],
    s: Settings,
    bus: EventBus,
    hud: ConsoleHUD,
    reserva: instancia.Reserva,
) -> int:
    core = _construir(args, s, bus)

    hud.bienvenida(
        motor_voz=getattr(core.tts, "nombre", "?") + (" (muda)" if args.muda else ""),
        modelo="modo demostración" if args.demo or not s.has_api_key else s.agent.model,
        wake=core.wakeword.enabled,
        atajo=s.hotkey.combo if (s.hotkey.enabled and not args.texto) else None,
    )

    if not s.has_api_key and not args.demo:
        hud.console.print(
            "[yellow]No hay ANTHROPIC_API_KEY: arranco en modo demostración.[/yellow]\n"
            "[dim]Copia .env.example a .env y pon tu clave para usar Claude de verdad.[/dim]\n"
        )

    # Cargar los modelos pesados antes de empezar, para que la primera frase
    # no tarde diez segundos.
    if not args.texto:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(core.transcriber.cargar)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(core.wakeword.cargar)

    await core.start()

    audio = asyncio.create_task(core.run(), name="audio")
    # En modo texto el "micrófono" es un `FakeMicStream` sin audio: su bucle
    # termina casi al instante, y eso no es una señal de apagado, es que no
    # hay nada que procesar. Tratarlo como principal cortaría la conversación
    # antes de que Claude respondiera. La vida real de este modo la marca
    # `_bucle_texto` (o el servidor web, si lo hay).
    tareas: list[asyncio.Task] = [] if args.texto else [audio]
    accesorias: list[asyncio.Task] = [audio] if args.texto else []
    escuchador = None
    url_local: str | None = None

    if args.web:
        from .server.app import ip_local, servir

        tareas.append(
            asyncio.create_task(
                servir(core, puerto=args.puerto, https=args.https), name="servidor"
            )
        )

        esquema = "https" if args.https else "http"
        url_local = f"{esquema}://localhost:{args.puerto}"
        hud.console.print(f"  [bold cyan]HUD aquí:[/bold cyan]      {url_local}")

        # La IP se calcula sola: pedirle al usuario que interprete `ipconfig`
        # es trasladarle un trabajo que la máquina hace mejor.
        ip = ip_local()
        if ip:
            url_movil = f"{esquema}://{ip}:{args.puerto}"
            hud.console.print(
                f"  [bold cyan]desde el móvil:[/bold cyan] {url_movil}"
                "  [dim](misma red wifi)[/dim]"
            )
            qr = _qr_para_terminal(url_movil)
            if qr:
                hud.console.print(qr)

        if args.https:
            hud.console.print(
                "  [dim]El certificado es autofirmado: el navegador avisará la primera\n"
                "  vez. Acepte para continuar; es su propio equipo.[/dim]"
            )
        elif ip:
            hud.console.print(
                "  [yellow]Para hablarle desde el móvil hace falta --https:[/yellow]\n"
                "  [dim]los navegadores sólo dan acceso al micrófono en contexto seguro.[/dim]"
            )
        hud.console.print()

        if not args.sin_navegador:
            from .ui.navegador import abrir_cuando_escuche

            # Tarea aparte y fuera de `tareas`: tiene que correr en paralelo
            # con `servir()`, que no vuelve nunca, y no puede ser motivo para
            # terminar el programa en cuanto el navegador se abre.
            accesorias.append(
                asyncio.create_task(
                    abrir_cuando_escuche(url_local, args.puerto), name="navegador"
                )
            )

    reserva.anunciar(url_local)

    # Con el HUD abierto, la entrada de texto va por el navegador: dos bucles
    # leyendo a la vez se pisarían.
    if args.texto and not args.web:
        tareas.append(asyncio.create_task(_bucle_texto(core, hud), name="texto"))
    elif s.hotkey.enabled and not args.texto:
        from .hotkey import HotkeyListener

        def pulsado() -> None:
            asyncio.create_task(core.escuchar_ahora())

        escuchador = HotkeyListener(s.hotkey.combo, pulsado)
        escuchador.start()
        if not escuchador.activo:
            hud.console.print(
                f"[yellow]No se pudo registrar el atajo {s.hotkey.combo}: "
                f"{escuchador.error}[/yellow]"
            )

    parada = asyncio.Event()
    bandeja: Bandeja | NullBandeja = (
        NullBandeja()
        if args.sin_bandeja
        else crear_bandeja(
            core,
            url=url_local,
            salir=parada.set,
            argumentos_inicio=_inicio_orden(argv_crudo),
        )
    )
    bandeja.arrancar()
    if bandeja.activa:
        saludo = f"J.A.R.V.I.S. en línea.\n{url_local}" if url_local else "J.A.R.V.I.S. en línea."
        bandeja.notificar(saludo)
    elif not args.sin_bandeja:
        hud.console.print(f"[yellow]Sin icono en la bandeja: {bandeja.error}[/yellow]")

    espera = asyncio.create_task(parada.wait(), name="parada")

    try:
        hechas, pendientes = await asyncio.wait(
            {*tareas, espera}, return_when=asyncio.FIRST_COMPLETED
        )
        # `asyncio.wait` no propaga las excepciones: sin este repaso, un fallo
        # en el servidor (puerto ocupado, certificado ilegible) desaparecería
        # sin dejar rastro, que es justo el problema que este módulo vino a
        # arreglar.
        for t in hechas:
            if t is espera or t.cancelled():
                continue
            exc = t.exception()
            if exc is not None:
                hud.console.print(f"[bold red]✖ {exc}[/bold red]")
                bandeja.notificar(f"Se ha detenido: {exc}")
        del pendientes
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        bandeja.detener()
        if escuchador is not None:
            escuchador.stop()

        todas = (*tareas, *accesorias, espera)
        for t in todas:
            t.cancel()
        if todas:
            # Con límite: cancelar no garantiza que la tarea obedezca a
            # tiempo. Un WebSocket a medio cerrar puede dejar a uvicorn
            # esperando su propia limpieza interna, y eso no puede convertir
            # un Ctrl+C en un proceso que no vuelve a la terminal. Lo que
            # quede pendiente lo remata `asyncio.run` al cerrar el loop.
            with contextlib.suppress(Exception):
                await asyncio.wait(todas, timeout=5.0)

        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(core.stop(), timeout=10.0)
        hud.console.print("\n[dim]Sistemas fuera de línea.[/dim]")

    return 0


def _inicio_orden(argv_crudo: list[str]) -> list[str]:
    """La orden que se guardará si se activa el arranque automático hoy."""
    from . import inicio

    return inicio.orden_para_el_inicio(argv_crudo)


async def _bucle_texto(core: JarvisCore, hud: ConsoleHUD) -> None:
    """Modo teclado: escribes, y J.A.R.V.I.S. contesta con voz y texto."""
    hud.console.print("[dim]Escribe y pulsa Enter. «salir» para terminar.[/dim]\n")
    while True:
        linea = (await asyncio.to_thread(input, "› ")).strip()
        if linea.lower() in ("salir", "exit", "quit"):
            return
        if linea:
            await core.responder(linea)


def _asegurar_flujos_validos() -> None:
    """`pythonw.exe` dice que no hay consola dejando `sys.stdout`/`sys.stderr`
    en `None`, y buena parte del código de alrededor —no sólo el nuestro—
    da por hecho que existen. El caso real: uvicorn monta un
    `logging.StreamHandler` sobre `sys.stdout` al arrancar el HUD web, y con
    `None` ahí revienta con "Unable to configure formatter 'default'" antes
    de levantar nada. Es justo el arranque automático (`inicio.py`), que
    lanza con `pythonw.exe` y siempre incluye `--web`.

    Sin consola nadie va a leer esa salida de todos modos, así que se manda
    a la nada en vez de dejar que quien la escriba reviente.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - vive todo el proceso
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115 - vive todo el proceso


def run(argv: list[str] | None = None) -> int:
    _asegurar_flujos_validos()
    args = _parse_args(argv)
    argv_crudo = argv if argv is not None else sys.argv[1:]

    if args.diag:
        from .diag import ejecutar_diagnostico

        return ejecutar_diagnostico()

    if args.arrancar_con_windows or args.quitar_del_inicio:
        from rich.console import Console

        from . import inicio

        # Se conservan las banderas del arranque para que J.A.R.V.I.S. se
        # inicie tal como lo pidió el usuario: si instala con `--web --https`,
        # eso es lo que debe levantarse cada mañana.
        extras = [a for a in argv_crudo
                  if a not in ("--arrancar-con-windows", "--quitar-del-inicio")]
        orden = inicio.orden_para_el_inicio(extras)

        Console().print(
            inicio.desinstalar() if args.quitar_del_inicio else inicio.instalar(orden)
        )
        return 0

    return _correr_hasta_el_final(_main_async(args, argv_crudo))


def _correr_hasta_el_final(corutina: Coroutine[Any, Any, int]) -> int:
    """Como `asyncio.run()`, pero con la limpieza final acotada.

    `asyncio.run()` deja bien resuelto todo lo demás, salvo un detalle que
    aquí importa: al terminar, cancela y espera **sin ningún límite** las
    tareas que queden vivas en el loop. `_arrancar_todo` ya acota su propia
    limpieza, pero eso no evita que una tarea huérfana y lenta en cerrar
    —una conexión WebSocket a medio terminar es el caso real que motivó
    esto— deje colgado justo este último paso, fuera ya de nuestro alcance:
    el Ctrl+C nunca devolvería la terminal, que es exactamente el síntoma
    reportado.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        try:
            return loop.run_until_complete(corutina)
        except KeyboardInterrupt:
            return 0
    finally:
        try:
            _cerrar_el_loop(loop)
        finally:
            asyncio.set_event_loop(None)
            with contextlib.suppress(BaseException):
                loop.close()


def _cerrar_el_loop(loop: asyncio.AbstractEventLoop) -> None:
    # `BaseException` y no `Exception`: `KeyboardInterrupt` no hereda de
    # `Exception`, así que un segundo Ctrl+C —el de quien se impacienta
    # mientras esperamos estos cinco segundos— se escapaba de aquí y salía
    # como traceback, justo en el paso que existe para que el cierre sea
    # limpio. Durante la limpieza final ya no hay nada que salvar: se sale.
    pendientes = asyncio.all_tasks(loop)
    for t in pendientes:
        t.cancel()
    if pendientes:
        with contextlib.suppress(BaseException):
            loop.run_until_complete(asyncio.wait(pendientes, timeout=5.0))
    with contextlib.suppress(BaseException):
        loop.run_until_complete(loop.shutdown_asyncgens())


if __name__ == "__main__":
    sys.exit(run())
