"""Diagnóstico del equipo.

Es lo primero que hay que ejecutar en una máquina nueva. Comprueba, por
separado y en orden, cada eslabón de la cadena, para que cuando algo no
funcione se sepa exactamente cuál falló en vez de tener que adivinar.

    python -m jarvis --diag
"""

from __future__ import annotations

import asyncio
import platform
import shutil
import sys
import time

from rich.console import Console
from rich.table import Table

from .config import load_settings

console = Console()

OK = "[green]✓[/green]"
FALLO = "[red]✗[/red]"
AVISO = "[yellow]![/yellow]"


def _seccion(titulo: str) -> None:
    console.print(f"\n[bold cyan]── {titulo} ─────────────────────────[/bold cyan]")


def _comprobar_entorno() -> None:
    _seccion("Entorno")
    console.print(
        f"  {OK} Python {sys.version.split()[0]} "
        f"en {platform.system()} {platform.release()}"
    )

    # El Agent SDK lanza el CLI de Claude Code por debajo: sin él no hay cerebro.
    cli = shutil.which("claude")
    if cli:
        console.print(f"  {OK} CLI de Claude Code: {cli}")
    else:
        console.print(
            f"  {FALLO} No encuentro el CLI de Claude Code.\n"
            "      El Agent SDK lo necesita. Instálalo con:\n"
            "      [cyan]npm install -g @anthropic-ai/claude-code[/cyan]"
        )


def _comprobar_credenciales() -> None:
    _seccion("Credenciales")
    s = load_settings()
    if s.has_api_key:
        clave = s.anthropic_api_key
        console.print(f"  {OK} ANTHROPIC_API_KEY presente ({clave[:7]}…{clave[-4:]})")
    else:
        console.print(
            f"  {AVISO} Sin ANTHROPIC_API_KEY: sólo funcionará el modo demostración.\n"
            "      Copia [cyan].env.example[/cyan] a [cyan].env[/cyan] y pon tu clave."
        )
    if s.elevenlabs_api_key:
        console.print(f"  {OK} ELEVENLABS_API_KEY presente (voz premium disponible)")


def _comprobar_dependencias() -> None:
    _seccion("Dependencias")
    modulos = [
        ("sounddevice", "captura y reproducción de audio"),
        ("numpy", "procesado de señal"),
        ("faster_whisper", "transcripción y VAD"),
        ("onnxruntime", "motor del VAD y del wake word"),
        ("openwakeword", 'palabra clave "Hey Jarvis"'),
        ("edge_tts", "voz por defecto"),
        ("av", "decodificación de audio"),
        ("pynput", "atajo de teclado global"),
    ]
    for nombre, para_que in modulos:
        try:
            __import__(nombre)
            console.print(f"  {OK} {nombre:<16} [dim]{para_que}[/dim]")
        except ImportError:
            # Los corchetes hay que escaparlos: rich los trata como marcado.
            console.print(
                f"  {FALLO} {nombre:<16} [dim]{para_que}[/dim]  "
                r'→ [cyan]pip install -e ".\[voice]"[/cyan]'
            )


def _comprobar_audio() -> None:
    _seccion("Dispositivos de audio")
    try:
        import sounddevice as sd
    except ImportError:
        console.print(f"  {FALLO} sounddevice no está instalado.")
        return

    try:
        dispositivos = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} No se pueden consultar los dispositivos: {exc}")
        return

    tabla = Table(show_header=True, header_style="bold")
    tabla.add_column("#", justify="right")
    tabla.add_column("Dispositivo")
    tabla.add_column("In", justify="right")
    tabla.add_column("Out", justify="right")

    try:
        entrada_def, salida_def = sd.default.device
    except Exception:  # noqa: BLE001
        entrada_def = salida_def = None

    for i, d in enumerate(dispositivos):
        marcas = ""
        if i == entrada_def:
            marcas += " ←mic"
        if i == salida_def:
            marcas += " ←salida"
        tabla.add_row(
            str(i),
            d["name"][:44] + marcas,
            str(d["max_input_channels"]),
            str(d["max_output_channels"]),
        )
    console.print(tabla)
    console.print(
        "  [dim]Para forzar uno concreto pon su número en config.toml, "
        "en audio.input_device / audio.output_device.[/dim]"
    )


def _probar_microfono(segundos: float = 3.0) -> None:
    _seccion(f"Prueba de micrófono ({segundos:.0f} s)")
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        console.print(f"  {FALLO} Faltan sounddevice o numpy.")
        return

    console.print("  [bold]Hable ahora…[/bold]")
    try:
        grabacion = sd.rec(
            int(segundos * 16_000), samplerate=16_000, channels=1, dtype="float32"
        )
        sd.wait()
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} No se pudo grabar: {exc}")
        return

    señal = grabacion[:, 0]
    pico = float(np.abs(señal).max())
    rms = float(np.sqrt(np.mean(señal**2)))

    barra = "█" * int(pico * 40)
    console.print(f"  nivel  [cyan]{barra}[/cyan] pico {pico:.3f}  rms {rms:.4f}")

    if pico < 0.01:
        console.print(f"  {FALLO} No se ha oído nada. ¿Micrófono silenciado o mal elegido?")
    elif pico > 0.99:
        console.print(f"  {AVISO} Saturación: baje el volumen de entrada.")
    else:
        console.print(f"  {OK} El micrófono capta correctamente.")

    # Ahora el VAD sobre lo grabado: comprueba que detecta la voz.
    try:
        from .audio.vad import Endpointer

        ep = Endpointer()
        eventos = [
            ep.feed(señal[i : i + 512])
            for i in range(0, len(señal) - 512, 512)
        ]
        if "inicio" in eventos:
            console.print(f"  {OK} El detector de voz ha reconocido habla.")
        else:
            console.print(
                f"  {AVISO} El detector de voz no ha encontrado habla. "
                "Si habló, baje [cyan]vad.threshold[/cyan] en config.toml."
            )
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} Fallo en el detector de voz: {exc}")


def _probar_voz() -> None:
    _seccion("Prueba de voz")
    s = load_settings()

    async def hablar() -> None:
        from .audio.player import Player
        from .audio.tts.base import crear_motor

        motor = crear_motor(s)
        console.print(f"  motor: [cyan]{motor.nombre}[/cyan], voz [cyan]{s.tts.voice}[/cyan]")

        t0 = time.perf_counter()
        pcm = await motor.sintetizar(
            "Sistemas en línea. Todos los diagnósticos completados, señor."
        )
        ms = (time.perf_counter() - t0) * 1000

        if pcm.size == 0:
            console.print(f"  {FALLO} La síntesis no ha devuelto audio.")
            return

        console.print(
            f"  {OK} Sintetizado en {ms:.0f} ms "
            f"({pcm.size / 24_000:.1f} s de audio). Reproduciendo…"
        )
        player = Player(device=s.audio.output_device)
        player.start()
        player.encolar(pcm)
        await player.esperar_fin(timeout=30)
        await asyncio.sleep(0.3)
        player.stop()
        await motor.cerrar()
        console.print(f"  {OK} Si lo ha oído, la salida de audio funciona.")

    try:
        asyncio.run(hablar())
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} {exc}")


def _probar_transcripcion() -> None:
    _seccion("Carga del modelo de transcripción")
    s = load_settings()
    try:
        from .audio.stt import Transcriber

        t = Transcriber(s)
        t0 = time.perf_counter()
        t.cargar()
        seg = time.perf_counter() - t0
        console.print(
            f"  {OK} Whisper [cyan]{s.stt.model_size}[/cyan] cargado en {seg:.1f} s "
            f"({t.device}, {t.compute_type})"
        )
        if t.device == "cpu" and s.stt.model_size in ("medium", "large-v3"):
            console.print(
                f"  {AVISO} Ese modelo en CPU va lento. Considere "
                "[cyan]stt.model_size = \"small\"[/cyan]."
            )
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} {exc}")


def ejecutar_diagnostico() -> int:
    console.print("\n[bold cyan]J.A.R.V.I.S. — diagnóstico del sistema[/bold cyan]")

    _comprobar_entorno()
    _comprobar_credenciales()
    _comprobar_dependencias()
    _comprobar_audio()
    _probar_voz()
    _probar_transcripcion()
    _probar_microfono()

    console.print(
        "\n[bold green]Diagnóstico terminado.[/bold green] "
        "Revise arriba cualquier [red]✗[/red] antes de arrancar.\n"
    )
    return 0
