"""Permisos: el freno de mano.

J.A.R.V.I.S. puede escribir archivos y ejecutar comandos en la computadora de
{user_name}. Eso es justo lo que lo hace útil y justo lo que lo hace
peligroso, porque el reconocimiento de voz se equivoca y un "borra eso mal
entendido" no tiene deshacer.

Tres capas de defensa, de más fuerte a más débil:

1. **Rutas.** Escribir fuera de las carpetas autorizadas se deniega sin
   preguntar. Ni siquiera llega a consultarte.
2. **Confirmación hablada.** Escribir, editar o ejecutar exige un "sí" tuyo,
   con el detalle leído en voz alta antes.
3. **Timeout que deniega.** Si no contestas, la respuesta es no. El silencio
   nunca autoriza nada.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..config import Settings
from ..events import EventBus, EventType

# Devuelve True si el usuario autoriza. Recibe la frase que hay que decirle.
Confirmador = Callable[[str], Awaitable[bool]]

# Herramientas propias que no necesitan confirmación. El criterio es que no
# puedan romper nada y que su efecto sea evidente e inmediatamente reversible:
# oír el volumen subir es su propia confirmación. Todo lo demás —abrir
# programas, bloquear la pantalla— pasa por el «sí» hablado como cualquier
# otra cosa.
PROPIAS_AUTOMATICAS = frozenset({
    "mcp__jarvis__recordar",
    "mcp__jarvis__olvidar",
    "mcp__jarvis__consultar_memoria",
    "mcp__jarvis__hora",
    "mcp__jarvis__volumen",
    "mcp__jarvis__estado_del_equipo",
    "mcp__jarvis__control_medios",
    "mcp__jarvis__poner_temporizador",
    # Buscar y leer un Google Doc no tocan nada del usuario; añadir,
    # reemplazar y crear sí, así que ésas pasan por el "sí" hablado.
    "mcp__jarvis__buscar_doc",
    "mcp__jarvis__leer_doc",
})

# Campos donde las distintas herramientas guardan la ruta que van a tocar.
_CAMPOS_RUTA = ("file_path", "path", "notebook_path", "filePath")


class PermissionGuard:
    """Decide si una herramienta se ejecuta, y con qué ceremonia."""

    def __init__(
        self,
        settings: Settings,
        confirmador: Confirmador,
        bus: EventBus | None = None,
    ) -> None:
        self._s = settings
        self._confirmar = confirmador
        self._bus = bus

    # ── punto de entrada del SDK ────────────────────────────────────────
    async def __call__(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        del context  # el SDK lo pasa, aquí no hace falta

        # 1. Herramientas de sólo lectura y las propias de J.A.R.V.I.S.
        if self._es_automatica(tool_name):
            return PermissionResultAllow(updated_input=input_data)

        # 2. Barrera de rutas: esto no se negocia ni se pregunta.
        ruta = self._ruta_afectada(input_data)
        if ruta is not None and not self._ruta_permitida(ruta):
            motivo = (
                f"La ruta {ruta} está fuera de las carpetas autorizadas. "
                "Añádela a `writable_paths` en config.toml si de verdad la necesitas."
            )
            self._emitir(EventType.PERMISSION_RESULT, tool=tool_name, allowed=False, reason=motivo)
            return PermissionResultDeny(message=motivo, interrupt=False)

        # 3. Confirmación hablada.
        pregunta = describir_para_voz(tool_name, input_data)
        self._emitir(
            EventType.PERMISSION_REQUEST,
            tool=tool_name,
            input=input_data,
            question=pregunta,
        )

        autorizado = await self._confirmar(pregunta)

        self._emitir(EventType.PERMISSION_RESULT, tool=tool_name, allowed=autorizado)

        if autorizado:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(
            message="El usuario no ha autorizado esta acción.", interrupt=False
        )

    # ── reglas ──────────────────────────────────────────────────────────
    def _es_automatica(self, tool_name: str) -> bool:
        if tool_name in self._s.permissions.auto_allow:
            return True
        # Las herramientas propias se autorizan **una por una**, no por
        # prefijo. Confiar en `mcp__jarvis__*` en bloque significaba que
        # cualquier herramienta nueva nacía autorizada, y no todas son
        # inofensivas: anotar un dato lo es, abrir un programa no.
        return tool_name in PROPIAS_AUTOMATICAS

    def _rutas_permitidas(self) -> list[Path]:
        rutas = [self._s.workspace, *self._s.permissions.writable_paths]
        resueltas = []
        for r in rutas:
            try:
                resueltas.append(Path(r).expanduser().resolve())
            except (OSError, RuntimeError):
                continue
        return resueltas

    def _ruta_permitida(self, ruta: Path) -> bool:
        try:
            objetivo = ruta.expanduser().resolve()
        except (OSError, RuntimeError):
            return False
        return any(objetivo.is_relative_to(base) for base in self._rutas_permitidas())

    @staticmethod
    def _ruta_afectada(input_data: dict[str, Any]) -> Path | None:
        for campo in _CAMPOS_RUTA:
            valor = input_data.get(campo)
            if isinstance(valor, str) and valor:
                return Path(valor)
        return None

    def _emitir(self, tipo: EventType, **data: Any) -> None:
        if self._bus is not None:
            self._bus.emit(tipo, **data)


# ── redacción de la pregunta ────────────────────────────────────────────
def describir_para_voz(tool_name: str, input_data: dict[str, Any]) -> str:
    """Convierte una llamada a herramienta en una pregunta pronunciable.

    Se dice el nombre del archivo, nunca la ruta completa: "config punto py"
    se entiende, "ce dos puntos barra usuarios barra..." no.

    Los comandos de shell son la excepción: se leen **enteros y literales**,
    porque es justo el detalle que necesitas oír para decidir.
    """
    if tool_name == "Bash":
        comando = str(input_data.get("command", "")).strip()
        return f"Voy a ejecutar el comando: {_acortar(comando, 200)}. ¿Lo autoriza?"

    if tool_name in ("Write", "Edit", "NotebookEdit"):
        ruta = input_data.get("file_path") or input_data.get("notebook_path") or ""
        nombre = Path(str(ruta)).name or "un archivo"
        verbo = "crear o sobrescribir" if tool_name == "Write" else "modificar"
        return f"Voy a {verbo} el archivo {nombre}. ¿Lo autoriza?"

    if tool_name == "KillShell":
        return "Voy a detener un proceso en ejecución. ¿Lo autoriza?"

    # Las herramientas propias se enuncian por lo que hacen. "Voy a usar la
    # herramienta mcp__jarvis__abrir" no le dice nada a nadie, y una pregunta
    # que no se entiende no es una confirmación: es un trámite.
    if tool_name == "mcp__jarvis__abrir":
        objetivo = str(input_data.get("objetivo", "")).strip() or "algo"
        return f"Voy a abrir {objetivo}. ¿Lo autoriza?"

    if tool_name == "mcp__jarvis__bloquear_pantalla":
        return "Voy a bloquear la pantalla. ¿Lo autoriza?"

    if tool_name == "mcp__jarvis__anadir_al_doc":
        nombre = str(input_data.get("nombre", "")).strip() or "un documento"
        return f"Voy a añadir texto al documento {nombre}. ¿Lo autoriza?"

    if tool_name == "mcp__jarvis__reemplazar_en_doc":
        nombre = str(input_data.get("nombre", "")).strip() or "un documento"
        return f"Voy a reemplazar texto en el documento {nombre}. ¿Lo autoriza?"

    if tool_name == "mcp__jarvis__crear_doc":
        titulo = str(input_data.get("titulo", "")).strip() or "uno nuevo"
        return f"Voy a crear el documento de Google {titulo}. ¿Lo autoriza?"

    return f"Voy a usar la herramienta {tool_name}. ¿Lo autoriza?"


def _acortar(texto: str, limite: int) -> str:
    texto = " ".join(texto.split())
    if len(texto) <= limite:
        return texto
    return texto[:limite] + "… y algo más, que he omitido por longitud"


# ── interpretación del sí y el no ───────────────────────────────────────
#
# Ojo con la tentación de hacer `if "no" in texto`: "no" es subcadena de
# "mano", "conocer" o "nosotros", y eso convertiría un sí en un no. Por eso
# las palabras sueltas se comparan como *tokens* y sólo las frases de varias
# palabras se buscan como subcadena.

_SI_PALABRAS = {
    "si", "sí", "sip", "claro", "dale", "ok", "okay", "vale", "correcto",
    "adelante", "hazlo", "hágalo", "hagalo", "confirmo", "afirmativo",
    "exacto", "venga", "procede", "autorizo", "perfecto", "listo",
}
_NO_PALABRAS = {
    "no", "nop", "nel", "cancela", "cancelar", "detente", "para", "espera",
    "negativo", "olvídalo", "olvidalo", "abortar", "aborta", "alto",
    "nunca", "jamás", "jamas",
}

_SI_FRASES = ("por supuesto", "adelante con eso", "está bien", "esta bien", "hazlo ya")
_NO_FRASES = ("mejor no", "ni se te ocurra", "de ninguna manera", "para nada",
              "déjalo", "dejalo", "no lo hagas", "no hagas")

# Dudas explícitas. Empiezan por "no", así que sin esta lista se leerían como
# negaciones. Denegar sería seguro, pero repreguntar es más útil y no baja la
# guardia: si a la segunda tampoco contesta claro, se deniega igual.
_DUDA_FRASES = ("no sé", "no se", "no lo sé", "no lo se", "ni idea",
                "no estoy seguro", "no estoy segura", "no sabría")

_TOKEN_LIMPIO = str.maketrans("", "", ".,!¡¿?;:\"'")


def interpretar_respuesta(texto: str) -> bool | None:
    """¿Dijo que sí, que no, o algo que no viene al caso?

    Devuelve ``True`` / ``False`` / ``None``. El ``None`` es deliberado y
    necesario: si no quedó claro, hay que repreguntar, nunca adivinar. En una
    confirmación para borrar archivos, adivinar mal cuesta caro.
    """
    limpio = texto.lower().strip()
    if not limpio:
        return None

    # Una duda declarada no es un no: merece que se le vuelva a preguntar.
    # Se busca en toda la frase, no sólo al principio: "mmm, no sé" y "pues
    # no sé yo" son dudas igual que "no sé" a secas.
    if any(f in limpio for f in _DUDA_FRASES):
        return None

    # Las frases de varias palabras mandan: "mejor no" gana a cualquier "sí".
    if any(f in limpio for f in _NO_FRASES):
        return False
    if any(f in limpio for f in _SI_FRASES):
        return True

    tokens = [t for t in (p.translate(_TOKEN_LIMPIO) for p in limpio.split()) if t]
    if not tokens:
        return None

    # La primera palabra es la que de verdad contesta: "no, déjalo así".
    if tokens[0] in _NO_PALABRAS:
        return False
    if tokens[0] in _SI_PALABRAS:
        return True

    # Si no, buscamos una afirmación o negación suelta en el resto.
    hay_no = any(t in _NO_PALABRAS for t in tokens)
    hay_si = any(t in _SI_PALABRAS for t in tokens)
    if hay_no and not hay_si:
        return False
    if hay_si and not hay_no:
        return True

    # Ambos o ninguno: no está claro, que lo repita.
    return None
