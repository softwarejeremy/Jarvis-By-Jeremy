"""Diagnóstico del equipo.

Es lo primero que hay que ejecutar en una máquina nueva. Comprueba, por
separado y en orden, cada eslabón de la cadena, para que cuando algo no
funcione se sepa exactamente cuál falló en vez de tener que adivinar.

    python -m jarvis --diag
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
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


def _es_shim_de_lotes(cli: str) -> bool:
    """Si el CLI encontrado es un `.cmd`/`.bat` de Windows.

    Sólo cuenta en Windows: en Linux un archivo así no lo produce ningún
    instalador y no hay nada que avisar.
    """
    return platform.system() == "Windows" and cli.lower().endswith((".cmd", ".bat"))


def _comprobar_entorno() -> None:
    _seccion("Entorno")
    console.print(
        f"  {OK} Python {sys.version.split()[0]} "
        f"en {platform.system()} {platform.release()}"
    )

    # El Agent SDK lanza el CLI de Claude Code por debajo: sin él no hay cerebro.
    cli = shutil.which("claude")
    if cli and _es_shim_de_lotes(cli):
        # Encontrarlo no basta. `npm install -g` deja en Windows un envoltorio
        # `claude.cmd`, y el SDK se NIEGA a ejecutarlo (no es un .exe nativo):
        # J.A.R.V.I.S. arranca, escucha y transcribe, pero no contesta nunca.
        # Dar un ✓ aquí por el mero hecho de encontrarlo mandaba a buscar el
        # fallo justo donde no estaba.
        console.print(
            f"  {FALLO} El CLI encontrado es un envoltorio .cmd de npm:\n"
            f"      {cli}\n"
            "      El Agent SDK no lo ejecuta, así que Claude nunca responde.\n"
            "      Instala el ejecutable nativo (PowerShell):\n"
            "      [cyan]irm https://claude.ai/install.ps1 | iex[/cyan]"
        )
    elif cli:
        console.print(f"  {OK} CLI de Claude Code: {cli}")
    else:
        console.print(
            f"  {FALLO} No encuentro el CLI de Claude Code.\n"
            "      El Agent SDK lo necesita. Instálalo con:\n"
            "      [cyan]npm install -g @anthropic-ai/claude-code[/cyan]"
        )


def _comprobar_configuracion() -> None:
    """De dónde sale cada ajuste.

    Existe por un caso real: editar `config.example.toml` creyendo que era
    `config.toml`, y no entender por qué el modelo seguía siendo el de
    fábrica. Que el modelo efectivo salga junto a los archivos que de verdad
    se han leído convierte ese enredo en algo que se ve de un vistazo.
    """
    from .config import PROJECT_ROOT

    _seccion("Configuración")
    s = load_settings()

    toml = PROJECT_ROOT / "config.toml"
    if toml.is_file():
        console.print(f"  {OK} config.toml leído desde {toml}")
    else:
        console.print(
            f"  {AVISO} No hay config.toml en {PROJECT_ROOT}: se usan los valores "
            "de fábrica.\n"
            "      Editar [cyan]config.example.toml[/cyan] no sirve; hay que copiarlo:\n"
            "      [cyan]copy config.example.toml config.toml[/cyan]"
        )

    env = PROJECT_ROOT / ".env"
    console.print(
        f"  {OK} .env leído desde {env}"
        if env.is_file()
        else f"  {AVISO} No hay .env en {PROJECT_ROOT} (ahí van las claves)."
    )

    console.print(f"  {OK} Modelo efectivo: [bold]{s.agent.model}[/bold]")
    if not s.has_api_key:
        console.print(
            f"      {AVISO} Sin clave no se usará ese modelo: J.A.R.V.I.S. arranca "
            "en modo demostración y responde con frases de ejemplo."
        )


def _comprobar_credenciales() -> None:
    _seccion("Credenciales")
    s = load_settings()
    if s.has_api_key:
        # Ni prefijo ni sufijo: --diag es justo lo que se pega en un issue, y
        # ninguna porción de la clave real debería salir de la máquina.
        console.print(f"  {OK} ANTHROPIC_API_KEY presente ({len(s.anthropic_api_key)} caracteres)")
    else:
        console.print(
            f"  {AVISO} Sin ANTHROPIC_API_KEY: sólo funcionará el modo demostración.\n"
            "      Copia [cyan].env.example[/cyan] a [cyan].env[/cyan] y pon tu clave."
        )
    if s.elevenlabs_api_key:
        console.print(f"  {OK} ELEVENLABS_API_KEY presente (voz premium disponible)")


async def _hablar_con_claude(tope: float, quejas: list[str] | None = None):  # noqa: ANN202
    """Un turno mínimo de verdad. Devuelve (texto, error).

    `quejas` recoge el stderr del CLI: cuando el turno se cuelga o revienta,
    suele ser el único sitio donde el CLI dice qué le pasa.
    """
    from .core.agent import Agent, Delta, Done

    s = load_settings()
    agente = Agent(s, stderr=quejas.append if quejas is not None else None)
    await agente.start()
    try:
        trozos: list[str] = []

        async def _turno() -> str | None:
            async for chunk in agente.ask("Responde únicamente con la palabra: listo"):
                if isinstance(chunk, Delta):
                    trozos.append(chunk.text)
                elif isinstance(chunk, Done) and chunk.error:
                    return chunk.error
            return None

        error = await asyncio.wait_for(_turno(), timeout=tope)
        return "".join(trozos).strip(), error
    finally:
        with contextlib.suppress(Exception):
            await agente.stop()


def _mostrar_quejas(quejas: list[str], maximo: int = 12) -> None:
    """Lo que el CLI escribió por stderr, que es donde suele estar la pista."""
    lineas = [linea for q in quejas for linea in q.splitlines() if linea.strip()]
    if not lineas:
        console.print("      [dim](el CLI no ha dicho nada por stderr)[/dim]")
        return

    console.print("      [dim]El CLI ha dicho:[/dim]")
    if len(lineas) > maximo:
        # Las últimas: si algo falló, el motivo está al final, no al principio.
        console.print(f"      [dim]… ({len(lineas) - maximo} líneas antes)[/dim]")
        lineas = lineas[-maximo:]
    for linea in lineas:
        console.print(f"      [dim]│[/dim] {linea}")


def _probar_cerebro() -> None:
    """Hablar con Claude de verdad, no sólo comprobar que el CLI existe.

    Reportado en vivo: con el CLI presente, las credenciales puestas y todo
    el diagnóstico en verde, J.A.R.V.I.S. se quedaba en «pensando» sin
    responder jamás. Comprobar que el binario está ahí no dice nada sobre si
    contesta; sin este turno de prueba, el único sitio donde se veía el fallo
    era en mitad de una conversación.
    """
    _seccion("Prueba de Claude")
    s = load_settings()
    if not s.has_api_key:
        console.print(f"  {AVISO} Sin clave: se omite (sólo modo demostración).")
        return

    tope = max(30.0, s.agent.first_token_timeout_s)
    console.print(f"  Preguntando a {s.agent.model}… (hasta {tope:.0f} s)")

    quejas: list[str] = []
    t0 = time.perf_counter()
    try:
        texto, error = asyncio.run(_hablar_con_claude(tope, quejas))
    except (TimeoutError, asyncio.TimeoutError):
        console.print(
            f"  {FALLO} No ha respondido en {tope:.0f} s: el CLI arranca pero "
            "se queda esperando."
        )
        _mostrar_quejas(quejas)
        return
    except Exception as exc:  # noqa: BLE001 - aquí interesa el mensaje crudo
        console.print(f"  {FALLO} {type(exc).__name__}: {exc}")
        _mostrar_quejas(quejas)
        return

    tardanza = (time.perf_counter() - t0) * 1000
    if error:
        console.print(f"  {FALLO} {error}")
        _mostrar_quejas(quejas)
    elif texto:
        console.print(f"  {OK} Claude responde: «{texto}»  ({tardanza:.0f} ms)")
    else:
        console.print(f"  {AVISO} Turno completado pero sin texto ({tardanza:.0f} ms).")


def _comprobar_dependencias() -> None:
    _seccion("Dependencias")
    # El tercer campo es el extra que lo trae: sin él, el consejo de instalación
    # mandaba a todo el mundo a `[voice]`, aunque lo que faltara fuese la
    # bandeja. El cuarto marca lo prescindible: quedarse sin icono es una
    # merma, no una avería, y pintarlo en rojo asustaría para nada.
    modulos = [
        ("sounddevice", "captura y reproducción de audio", "voice", False),
        ("numpy", "procesado de señal", "voice", False),
        ("faster_whisper", "transcripción y VAD", "voice", False),
        ("onnxruntime", "motor del VAD y del wake word", "voice", False),
        ("openwakeword", 'palabra clave "Hey Jarvis"', "voice", False),
        ("edge_tts", "voz por defecto", "voice", False),
        ("av", "decodificación de audio", "voice", False),
        ("pynput", "atajo de teclado global", "voice", False),
        ("PIL", "dibujo del icono de la bandeja", "bandeja", True),
        ("pystray", "icono en el área de notificación", "bandeja", True),
    ]
    for nombre, para_que, extra, opcional in modulos:
        try:
            __import__(nombre)
        except Exception as exc:  # noqa: BLE001 - un paquete roto no puede tumbar esto
            # "No instalado" y "instalado pero no arranca" piden remedios
            # distintos: reinstalar con pip no arregla una DLL del sistema que
            # falta. Merece la pena distinguirlos.
            if _esta_instalado(nombre):
                detalle = f"→ instalado, pero no carga: {_una_linea(exc)}"
            else:
                # Los corchetes se escapan: rich los interpreta como marcado.
                detalle = rf'→ falta: [cyan]pip install -e ".\[{extra}]"[/cyan]'
            marca = AVISO if opcional else FALLO
            console.print(f"  {marca} {nombre:<16} [dim]{para_que}[/dim]  {detalle}")
        else:
            console.print(f"  {OK} {nombre:<16} [dim]{para_que}[/dim]")


def _esta_instalado(nombre: str) -> bool:
    """¿El paquete está en disco, aunque importarlo falle?"""
    try:
        return importlib.util.find_spec(nombre) is not None
    except Exception:  # noqa: BLE001 - paquete tan roto que ni se puede consultar
        return True


def _una_linea(exc: Exception, limite: int = 90) -> str:
    texto = " ".join(str(exc).split()) or type(exc).__name__
    return texto if len(texto) <= limite else texto[:limite] + "…"


def _comprobar_audio() -> None:
    _seccion("Dispositivos de audio")
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001 - falta el paquete o falta PortAudio
        console.print(f"  {FALLO} No se puede usar sounddevice: {exc}")
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


def _probar_microfono(transcriber=None, segundos: float = 3.0) -> None:  # noqa: ANN001
    """Graba, mide el nivel, comprueba el VAD y transcribe lo grabado."""
    _seccion(f"Prueba de micrófono ({segundos:.0f} s)")
    console.print("  [dim]Diga algo como: «Hola Jarvis, ¿qué tal estás?»[/dim]")
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} No se puede usar el micrófono: {exc}")
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
        console.print(
            f"  {AVISO} Saturación: la señal recorta y eso degrada la transcripción.\n"
            "      Ajustes de sonido de Windows → su micrófono → baje el volumen\n"
            "      de entrada a ~70 y desactive el refuerzo de micrófono."
        )
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

    # Transcribir lo que se acaba de grabar. Es la prueba de fuego: mide la
    # latencia real sobre voz real y enseña si de verdad le entiende.
    if transcriber is None or pico < 0.01:
        return
    motivo_antes = getattr(transcriber, "motivo_repliegue", None)
    try:
        t0 = time.perf_counter()
        texto = transcriber._transcribir_sync(señal)
        ms = (time.perf_counter() - t0) * 1000
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} Fallo al transcribir: {exc}")
        return

    # A diferencia del repliegue al cargar el modelo, éste sólo se ve aquí:
    # la GPU aceptó construir el modelo pero reventó en la primera
    # transcripción real (típico de cuBLAS a medio instalar). El aviso de
    # `_probar_transcripcion` no lo habría visto porque en ese momento la
    # carga sí había ido bien.
    motivo_ahora = getattr(transcriber, "motivo_repliegue", None)
    if motivo_ahora and motivo_ahora != motivo_antes:
        if _paquetes_cuda_instalados():
            console.print(
                f"  {AVISO} {motivo_ahora}\n"
                f"      Los tres paquetes de CUDA por pip ({_PAQUETES_CUDA_PIP}) ya están\n"
                "      instalados: no es lo que falta por instalar. Revise la versión de "
                "CUDA\n"
                "      que soporta su driver (`nvidia-smi`) o instale el CUDA Toolkit "
                "completo."
            )
        else:
            console.print(
                f"  {AVISO} {motivo_ahora}\n"
                f"      Pruebe [cyan]pip install {_PAQUETES_CUDA_PIP}[/cyan] "
                "y vuelva a intentarlo."
            )

    if texto:
        console.print(f'  {OK} Le he entendido: [bold]«{texto}»[/bold]  [dim]({ms:.0f} ms)[/dim]')
        if ms > 2000:
            console.print(
                f'  {AVISO} La transcripción tarda lo suyo. Pruebe '
                '[cyan]stt.model_size = "tiny"[/cyan] si nota la conversación lenta.'
            )
    else:
        console.print(
            f"  {AVISO} No se ha transcrito nada. Si habló, revise el nivel de entrada."
        )


def _diagnosticar_xtts() -> None:
    """XTTS-v2 es el motor con más formas de fallar por hardware: sin esto,
    un "no suena" con `engine = "xtts"` no dice si falta PyTorch, si no hay
    CUDA, o si la GPU se quedó sin memoria compartiéndola con Whisper."""
    try:
        import torch
    except ImportError:
        console.print(
            f"  {FALLO} PyTorch no está instalado — hace falta el extra `xtts` "
            '(`pip install -e ".[xtts]"`).'
        )
        return

    if not torch.cuda.is_available():
        console.print(
            f"  {FALLO} CUDA no disponible: XTTS correrá en CPU, "
            "mucho más lento por frase."
        )
        return

    nombre = torch.cuda.get_device_name(0)
    libre, total = torch.cuda.mem_get_info(0)
    console.print(
        f"  {OK} GPU: [cyan]{nombre}[/cyan] — "
        f"{libre / 2**30:.1f} GB libres de {total / 2**30:.1f} GB"
    )


def _probar_voz() -> None:
    _seccion("Prueba de voz")
    s = load_settings()

    if s.tts.engine == "xtts":
        _diagnosticar_xtts()

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


_PAQUETES_CUDA_PIP = "nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12"


def _paquetes_cuda_instalados() -> bool:
    """Si los tres paquetes de CUDA por pip ya están instalados.

    Sin esto, el aviso de GPU sin aprovechar repetía siempre el mismo
    `pip install`, incluso después de que el usuario ya lo hubiera seguido —
    indistinguible de que el consejo no hubiera servido de nada.

    Los tres, no dos: `nvidia-cuda-runtime-cu12` (`cudart64_12.dll`) se
    descubrió en vivo — cuBLAS/cuDNN cargaban perfectamente solos y la
    transcripción real seguía reventando, porque cuBLAS depende de ese
    runtime para inicializarse y nadie lo había pedido instalar.
    """
    # nvidia.cublas/nvidia.cudnn/nvidia.cuda_runtime, no ".lib": ese subnombre
    # es la estructura del wheel de Linux. En Windows el paquete deja bin/ e
    # include/, nunca lib/ (verificado con Get-ChildItem en una instalación
    # real) — buscar ".lib" ahí falla siempre, instalados o no. Ver
    # jarvis/audio/stt.py.
    for paquete in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
        try:
            if importlib.util.find_spec(paquete) is None:
                return False
        except ModuleNotFoundError:
            # find_spec exige que el paquete padre ("nvidia") ya exista;
            # si no, revienta en vez de devolver None.
            return False
    return True


def _probar_transcripcion():  # noqa: ANN201 - devuelve el Transcriber ya cargado
    """Carga el modelo y explica en qué dispositivo acabó y por qué."""
    _seccion("Modelo de transcripción")
    s = load_settings()
    try:
        from .audio.stt import Transcriber

        t = Transcriber(s)
        t0 = time.perf_counter()
        t.cargar()
        seg = time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} No se pudo cargar el modelo: {exc}")
        console.print(
            "      Pruebe a forzar CPU poniendo "
            '[cyan]device = "cpu"[/cyan] en la sección [cyan]\\[stt][/cyan] '
            "de config.toml."
        )
        return None

    console.print(
        f"  {OK} Whisper [cyan]{s.stt.model_size}[/cyan] cargado en {seg:.1f} s "
        f"([cyan]{t.device}[/cyan], {t.compute_type})"
    )

    # Si hubo repliegue, explicarlo: es mucho más útil que el error de la
    # librería, que no dice qué hacer al respecto.
    if t.motivo_repliegue:
        console.print(f"  {AVISO} {t.motivo_repliegue}")

    if t.gpu_detectada and t.device == "cpu":
        if _paquetes_cuda_instalados():
            console.print(
                f"  {AVISO} Tiene una GPU NVIDIA que no se está aprovechando, y los "
                "tres paquetes\n"
                f"      de CUDA por pip ({_PAQUETES_CUDA_PIP}) ya están instalados.\n"
                "      No es lo que falta por instalar: revise que el driver de "
                "NVIDIA soporte\n"
                "      CUDA 12 (`nvidia-smi` lo dice arriba a la derecha) o "
                "instale el CUDA\n"
                "      Toolkit completo (cuDNN 9 para CUDA 12) para descartar un "
                "problema de\n"
                "      versión en vez de de instalación."
            )
        else:
            console.print(
                f"  {AVISO} Tiene una GPU NVIDIA que no se está aprovechando.\n"
                f"      Pruebe [cyan]pip install {_PAQUETES_CUDA_PIP}[/cyan] "
                "y vuelva a arrancar:\n"
                "      son más ligeros que el CUDA Toolkit completo y J.A.R.V.I.S. ya sabe\n"
                "      encontrar sus DLL solo. Si aun así no arranca en GPU, instale el\n"
                "      CUDA Toolkit completo de NVIDIA (cuDNN 9 para CUDA 12)."
            )
    elif not t.gpu_detectada and s.stt.model_size in ("medium", "large-v3"):
        console.print(
            f"  {AVISO} Ese modelo en CPU va lento. Considere "
            '[cyan]stt.model_size = "small"[/cyan].'
        )

    return t


def _seguro(funcion, *args):  # noqa: ANN001, ANN202
    """Ejecuta una sección sin que un fallo suyo tumbe el diagnóstico.

    Esta es la herramienta a la que se acude cuando algo va mal: si se muere
    a la primera excepción, deja de servir justo cuando más falta hace. Una
    sección rota se reporta y se sigue con las demás.
    """
    try:
        return funcion(*args)
    except Exception as exc:  # noqa: BLE001
        console.print(f"  {FALLO} Esta comprobación ha fallado: {exc}")
        return None


def ejecutar_diagnostico() -> int:
    console.print("\n[bold cyan]J.A.R.V.I.S. — diagnóstico del sistema[/bold cyan]")

    _seguro(_comprobar_entorno)
    _seguro(_comprobar_configuracion)
    _seguro(_comprobar_credenciales)
    _seguro(_comprobar_dependencias)
    _seguro(_probar_cerebro)
    _seguro(_comprobar_audio)
    _seguro(_probar_voz)
    transcriber = _seguro(_probar_transcripcion)
    _seguro(_probar_microfono, transcriber)

    console.print(
        "\n[bold green]Diagnóstico terminado.[/bold green] "
        "Revise arriba cualquier [red]✗[/red] antes de arrancar.\n"
    )
    return 0
