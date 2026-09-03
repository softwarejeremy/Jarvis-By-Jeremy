"""El icono de la bandeja: un reactor arc en miniatura.

Se dibuja por código en vez de guardar un `.ico` porque el icono **cambia de
color con el estado**: si fuera una imagen fija no diría nada que no dijera un
acceso directo en el escritorio.

Este módulo no sabe nada de `pystray`. Sólo produce colores, textos e imágenes,
que es justo lo que se puede probar sin pantalla ni Windows.

Sobre el dibujo hay tres decisiones que parecen detalles y no lo son:

1. Se dibuja a 4× y se reduce con LANCZOS. `ImageDraw` no tiene suavizado, y a
   16 píxeles un anillo con los bordes dentados se ve como una cruz sucia.
2. El halo va en su propia capa y se funde con `alpha_composite`. `ImageDraw`
   sobre RGBA **sustituye** el píxel, alfa incluido: pintar el halo
   semitransparente encima del anillo le abriría un agujero en vez de fundirse.
3. Los estados que más importan se distinguen **sin depender del color**:
   «pausado» lleva el núcleo hueco —está deliberadamente sordo, y un centro
   vacío lo dice— y «error» parte el anillo exterior. A 16 píxeles el matiz se
   pierde, y hay quien no distingue el ámbar del verde.

Y una lección aprendida mirando el resultado: los grosores **no** pueden ser
sólo una fracción del lado. A 64 píxeles quedaban bien y a 16 —que es el tamaño
que de verdad usa la bandeja de Windows— el anillo exterior se quedaba en 1,3
píxeles y desaparecía. Por eso cada trazo tiene también un mínimo expresado en
píxeles finales.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - sólo para los tipos
    from PIL.Image import Image

# Los mismos colores que el reactor del HUD web, copiados de
# `jarvis/server/static/estilo.css`. Están duplicados a propósito —el CSS no se
# puede importar desde Python— pero hay un test que los compara con el archivo
# real: si alguien retoca la hoja de estilos, el icono y el HUD dejarían de
# contar lo mismo del mismo estado y nadie se enteraría.
COLOR_ESTADO: dict[str, str] = {
    "dormido": "#6b4a24",
    "escuchando": "#f0a83c",
    "transcribiendo": "#ffcf70",
    "pensando": "#e2792b",
    "hablando": "#ffdca0",
    "confirmando": "#cc4a1f",
    "pausado": "#5c5346",
    "error": "#ef5350",
}

# Un estado que no conocemos se pinta como el reposo: es lo menos alarmante.
COLOR_DESCONOCIDO = COLOR_ESTADO["dormido"]

# Windows corta el tooltip de la bandeja por su cuenta. Mejor cortarlo nosotros,
# que al menos respetamos la palabra.
LIMITE_TOOLTIP = 63

# Con el núcleo hueco se lee «estoy sordo a propósito» aunque no haya color.
# Sólo la pausa: en reposo sigue atento al «Hey Jarvis», y decir lo contrario
# con un centro vacío sería mentir.
_NUCLEO_HUECO = frozenset({"pausado"})

# Cuánto se agranda el lienzo antes de reducirlo. Cuatro basta: con ocho, el
# LANCZOS emborrona los trazos finos en vez de afinarlos.
_ESCALA = 4

# Mínimos en píxeles *finales*, los que verá el usuario. Son lo que hace que el
# icono siga leyéndose a 16 px sin volverlo un mazacote a 64.
_MIN_ANILLO_PX = 1.7
_MIN_NUCLEO_PX = 2.4


def color_de(estado: str) -> str:
    """El color del estado, en hexadecimal. Nunca falla."""
    return COLOR_ESTADO.get(estado, COLOR_DESCONOCIDO)


def texto_tooltip(estado: str, *, coste_usd: float | None = None) -> str:
    """Lo que se lee al posar el ratón sobre el icono."""
    texto = f"J.A.R.V.I.S. — {estado}"
    if coste_usd:
        texto += f" · ${coste_usd:.4f}"
    if len(texto) <= LIMITE_TOOLTIP:
        return texto
    return texto[: LIMITE_TOOLTIP - 1].rstrip() + "…"


def hay_pillow() -> bool:
    """¿Se puede dibujar? Sin Pillow no hay icono, pero tampoco un fallo."""
    try:
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001 - da igual por qué; si no está, no está
        return False
    return True


@functools.lru_cache(maxsize=32)
def dibujar_reactor(estado: str, tamano: int = 64) -> Image:
    """El reactor del estado dado, como imagen RGBA cuadrada.

    Va cacheado porque `state_changed` se emite varias veces por turno y cada
    repintado son cuatro elipses a 256×256 más una reducción.
    """
    from PIL import Image as _Image
    from PIL import ImageDraw

    acento = _a_rgb(color_de(estado))
    lado = tamano * _ESCALA
    centro = lado / 2
    minimo = _ESCALA  # un píxel final, ya escalado

    base = _Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    pincel = ImageDraw.Draw(base)

    # Anillo exterior. En «error» se parte en dos arcos: un aro roto se
    # reconoce como avería aunque no se distinga que es rojo. El hueco es
    # ancho (60°) porque a 16 px una muesca fina no se ve.
    radio_fuera = centro * 0.96
    grosor_fuera = _grosor(lado, 0.085, _MIN_ANILLO_PX)
    caja_fuera = _caja(centro, radio_fuera)
    if estado == "error":
        for desde, hasta in ((210, 330), (30, 150)):
            pincel.arc(caja_fuera, desde, hasta, fill=(*acento, 205), width=grosor_fuera)
    else:
        pincel.ellipse(caja_fuera, outline=(*acento, 205), width=grosor_fuera)

    # Anillo interior, el que da el aspecto de reactor.
    pincel.ellipse(
        _caja(centro, centro * 0.60),
        outline=(*acento, 255),
        width=_grosor(lado, 0.115, _MIN_ANILLO_PX),
    )

    # El halo, en su propia capa (ver el punto 2 del docstring del módulo).
    halo = _Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse(_caja(centro, centro * 0.36), fill=(*acento, 70))
    base = _Image.alpha_composite(base, halo)

    # El núcleo: relleno cuando escucha, hueco cuando está en pausa.
    pincel = ImageDraw.Draw(base)
    radio_nucleo = max(centro * 0.26, _MIN_NUCLEO_PX * _ESCALA)
    caja_nucleo = _caja(centro, radio_nucleo)
    if estado in _NUCLEO_HUECO:
        pincel.ellipse(caja_nucleo, outline=(*acento, 255), width=max(minimo, round(lado * 0.05)))
    else:
        pincel.ellipse(caja_nucleo, fill=(*_aclarar(acento, 0.45), 255))

    return base.resize((tamano, tamano), _Image.LANCZOS)


# ── utilidades de dibujo ────────────────────────────────────────────────
def _caja(centro: float, radio: float) -> tuple[float, float, float, float]:
    return (centro - radio, centro - radio, centro + radio, centro + radio)


def _grosor(lado: int, fraccion: float, minimo_final: float) -> int:
    """Grosor de trazo que respeta un mínimo en píxeles finales.

    Sin el mínimo, un trazo del 8,5 % del lado son 5 px a tamaño 64 —correcto—
    pero 1,3 px a tamaño 16, y ahí se esfuma tras la reducción.
    """
    return max(round(minimo_final * _ESCALA), round(lado * fraccion))


def _a_rgb(hexadecimal: str) -> tuple[int, int, int]:
    crudo = hexadecimal.lstrip("#")
    return (int(crudo[0:2], 16), int(crudo[2:4], 16), int(crudo[4:6], 16))


def _aclarar(rgb: tuple[int, int, int], cuanto: float) -> tuple[int, int, int]:
    """Acerca el color al blanco. El núcleo brilla más que su propio anillo."""
    return tuple(round(c + (255 - c) * cuanto) for c in rgb)  # type: ignore[return-value]


# ── el puente con la hoja de estilos ────────────────────────────────────
_RUTA_CSS = Path(__file__).resolve().parent.parent / "server" / "static" / "estilo.css"
_VARIABLE = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")
_REGLA_ESTADO = re.compile(r'\.panel-reactor\[data-estado="(\w+)"\]\s*\{\s*--acento:\s*([^;]+);')


def colores_del_hud(ruta: Path | None = None) -> dict[str, str]:
    """Lee del CSS del HUD qué color le toca a cada estado.

    Existe para un test: es la forma de detectar que el icono de la bandeja y
    el reactor del navegador se han desincronizado.
    """
    css = (ruta or _RUTA_CSS).read_text(encoding="utf-8")
    variables = {nombre: valor.strip() for nombre, valor in _VARIABLE.findall(css)}

    colores: dict[str, str] = {}
    for estado, crudo in _REGLA_ESTADO.findall(css):
        valor = crudo.strip()
        # El CSS mezcla literales (`#2a5a7a`) con indirecciones (`var(--cian)`).
        if valor.startswith("var(") and valor.endswith(")"):
            nombre = valor[len("var(") : -1].strip().removeprefix("--")
            valor = variables.get(nombre, valor)
        colores[estado] = valor.strip().lower()
    return colores
