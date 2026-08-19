"""Punto de entrada de J.A.R.V.I.S.

Modos disponibles::

    python -m jarvis                 # voz completa: wake word + atajo
    python -m jarvis --texto         # escribes tú, él responde con voz
    python -m jarvis --demo          # sin API key, respuestas simuladas
    python -m jarvis --sim audio.wav # inyecta un WAV en vez del micrófono
    python -m jarvis --diag          # diagnóstico del equipo

El montaje de las piezas vive aquí a propósito: el núcleo recibe todo por
constructor y no sabe construir nada, que es lo que permite sustituir
cualquier componente por uno falso en los tests.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

from .config import Settings, load_settings
from .core.agent import Agent, DemoAgent
from .core.core import JarvisCore
from .core.memory import Memory
from .core.permissions import PermissionGuard
from .events import EventBus
from .ui.console import ConsoleHUD


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="jarvis", description="Asistente de voz J.A.R.V.I.S.")
    p.add_argument("--texto", action="store_true", help="conversar escribiendo, sin micrófono")
    p.add_argument("--demo", action="store_true", help="sin API key: respuestas simuladas")
    p.add_argument("--sim", metavar="WAV", help="usar un archivo WAV en vez del micrófono")
    p.add_argument("--muda", action="store_true", help="no reproducir audio (sólo texto)")
    p.add_argument("--diag", action="store_true", help="diagnosticar el equipo y salir")
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

    # El núcleo aún no existe, pero el guardián de permisos necesita poder
    # preguntarle por voz. Se resuelve con una indirección: el guardián llama
    # a una función que, cuando de verdad se invoque, ya tendrá el núcleo.
    contenedor: dict[str, JarvisCore] = {}

    async def confirmar(pregunta: str) -> bool:
        core = contenedor.get("core")
        if core is None:
            return False
        return await core.confirmar_por_voz(pregunta)

    if args.demo or not s.has_api_key:
        agent = DemoAgent()
    else:
        from .tools.memory_tool import construir_servidor_memoria

        agent = Agent(
            s,
            can_use_tool=PermissionGuard(s, confirmar, bus),
            mcp_servers={"jarvis": construir_servidor_memoria(memoria)},
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


async def _main_async(args: argparse.Namespace) -> int:
    s = load_settings(Path(args.config) if args.config else None)
    bus = EventBus()
    hud = ConsoleHUD(bus, verbose=args.verbose)

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

    tareas = [asyncio.create_task(core.run())]
    escuchador = None

    if args.texto:
        tareas.append(asyncio.create_task(_bucle_texto(core, hud)))
    elif s.hotkey.enabled:
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

    try:
        await asyncio.gather(*tareas)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if escuchador is not None:
            escuchador.stop()
        for t in tareas:
            t.cancel()
        await core.stop()
        hud.console.print("\n[dim]Sistemas fuera de línea.[/dim]")

    return 0


async def _bucle_texto(core: JarvisCore, hud: ConsoleHUD) -> None:
    """Modo teclado: escribes, y J.A.R.V.I.S. contesta con voz y texto."""
    hud.console.print("[dim]Escribe y pulsa Enter. «salir» para terminar.[/dim]\n")
    while True:
        linea = (await asyncio.to_thread(input, "› ")).strip()
        if linea.lower() in ("salir", "exit", "quit"):
            return
        if linea:
            await core.responder(linea)


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.diag:
        from .diag import ejecutar_diagnostico

        return ejecutar_diagnostico()

    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(run())
