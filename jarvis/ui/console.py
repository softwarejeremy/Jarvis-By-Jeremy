"""HUD de terminal.

Se suscribe al bus de eventos y pinta lo que va pasando. No decide nada: si
mañana quieres una interfaz web, se escribe otro suscriptor y el núcleo no se
entera.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from ..events import Event, EventBus, EventType, State

# Un color por estado, para poder leer de un vistazo en qué anda.
_COLOR_ESTADO = {
    State.DORMIDO.value: "grey50",
    State.ESCUCHANDO.value: "bright_cyan",
    State.TRANSCRIBIENDO.value: "cyan",
    State.PENSANDO.value: "yellow",
    State.HABLANDO.value: "bright_green",
    State.CONFIRMANDO.value: "bright_magenta",
    State.ERROR.value: "bright_red",
}

_ICONO_ESTADO = {
    State.DORMIDO.value: "○",
    State.ESCUCHANDO.value: "◉",
    State.TRANSCRIBIENDO.value: "◍",
    State.PENSANDO.value: "◐",
    State.HABLANDO.value: "◆",
    State.CONFIRMANDO.value: "⚠",
    State.ERROR.value: "✖",
}

BANNER = r"""
     ██  █████  ██████  ██    ██ ██ ███████
     ██ ██   ██ ██   ██ ██    ██ ██ ██
     ██ ███████ ██████  ██    ██ ██ ███████
██   ██ ██   ██ ██   ██  ██  ██  ██      ██
 █████  ██   ██ ██   ██   ████   ██ ███████
"""


class ConsoleHUD:
    """Salida de terminal para J.A.R.V.I.S."""

    def __init__(self, bus: EventBus, *, verbose: bool = False) -> None:
        self.console = Console()
        self._verbose = verbose
        self._en_respuesta = False
        bus.on(self._manejar)

    # ── presentación ────────────────────────────────────────────────────
    def bienvenida(self, *, motor_voz: str, modelo: str, wake: bool, atajo: str | None) -> None:
        self.console.print(Text(BANNER, style="bold cyan"))
        self.console.print("  [bold]J.A.R.V.I.S.[/bold] — a su disposición\n")
        self.console.print(f"  cerebro   [cyan]{modelo}[/cyan]")
        self.console.print(f"  voz       [cyan]{motor_voz}[/cyan]")
        if wake:
            self.console.print('  despertar [cyan]di "Hey Jarvis"[/cyan]')
        if atajo:
            self.console.print(f"  atajo     [cyan]{atajo}[/cyan]")
        self.console.print("  salir     [cyan]Ctrl+C[/cyan]\n")

    # ── eventos ─────────────────────────────────────────────────────────
    def _manejar(self, ev: Event) -> None:
        t = ev.type
        d = ev.data

        if t is EventType.STATE_CHANGED:
            estado = d.get("state", "")
            color = _COLOR_ESTADO.get(estado, "white")
            icono = _ICONO_ESTADO.get(estado, "·")
            self._cerrar_respuesta()
            self.console.print(f"[{color}]{icono} {estado}[/{color}]")

        elif t is EventType.WAKE_DETECTED:
            self.console.print("[bold bright_cyan]▶ «Hey Jarvis» detectado[/bold bright_cyan]")

        elif t is EventType.FINAL_TRANSCRIPT:
            etiqueta = "confirmación" if d.get("kind") == "confirmacion" else "usted"
            self.console.print(f"[bold white]{etiqueta}:[/bold white] {d.get('text','')}")

        elif t is EventType.ASSISTANT_DELTA:
            if not self._en_respuesta:
                self.console.print("[bold cyan]jarvis:[/bold cyan] ", end="")
                self._en_respuesta = True
            self.console.print(d.get("text", ""), end="", highlight=False)

        elif t is EventType.ASSISTANT_DONE:
            self._cerrar_respuesta()

        elif t is EventType.TOOL_USE:
            self.console.print(
                f"  [dim]herramienta:[/dim] [magenta]{d.get('name','')}[/magenta] "
                f"[dim]{_resumen(d.get('input', {}))}[/dim]"
            )

        elif t is EventType.PERMISSION_REQUEST:
            self._cerrar_respuesta()
            self.console.print(f"[bold yellow]⚠ permiso:[/bold yellow] {d.get('question','')}")

        elif t is EventType.PERMISSION_RESULT:
            ok = d.get("allowed")
            marca = "[green]autorizado[/green]" if ok else "[red]denegado[/red]"
            razon = d.get("reason")
            self.console.print(f"  {marca}" + (f" [dim]{razon}[/dim]" if razon else ""))

        elif t is EventType.COST_UPDATE:
            self.console.print(f"  [dim]coste de la sesión: ${d.get('total_usd', 0):.4f}[/dim]")

        elif t is EventType.ERROR:
            self._cerrar_respuesta()
            self.console.print(f"[bold red]✖ {d.get('message','')}[/bold red]")

        elif t is EventType.LOG and self._verbose:
            self.console.print(f"  [dim]{d.get('message','')}[/dim]")

    def _cerrar_respuesta(self) -> None:
        if self._en_respuesta:
            self.console.print()
            self._en_respuesta = False


def _resumen(entrada: dict) -> str:
    """Resume los argumentos de una herramienta en una línea legible."""
    for campo in ("command", "file_path", "pattern", "query", "hecho", "url"):
        valor = entrada.get(campo)
        if valor:
            texto = str(valor).replace("\n", " ")
            return texto[:70] + ("…" if len(texto) > 70 else "")
    return ""
