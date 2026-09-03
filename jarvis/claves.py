"""Las API keys en el almacén de credenciales del sistema operativo.

Vía `keyring` (Credential Manager en Windows, Keychain en macOS, Secret
Service en Linux): un paso más seguro que dejarlas en texto plano en `.env`.
Extra opcional (`pip install -e ".[keyring]"`) con import perezoso, como el
resto de dependencias opcionales del proyecto — y no basta con que
`keyring` esté instalado: sin un backend de verdad configurado (este
sandbox, por ejemplo) puede fallar al abrir el almacén, no sólo al
importarse. Cualquier fallo degrada en silencio a lo que venga después
(`.env`), nunca revienta el arranque.
"""

from __future__ import annotations

SERVICIO = "jarvis"

# Nombre corto (el que usa `--guardar-clave`) -> alias de entorno con el que
# `config.py` ya sabe leer cada clave.
CLAVES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
}


def leer(nombre_clave: str) -> str | None:
    """La clave guardada en el almacén del sistema, o `None` si no hay
    ninguna, `keyring` no está instalado, o no hay backend disponible."""
    try:
        import keyring
    except Exception:
        return None
    try:
        return keyring.get_password(SERVICIO, CLAVES[nombre_clave])
    except Exception:
        return None


def guardar(nombre_clave: str, valor: str) -> str:
    """Guarda `valor` en el almacén del sistema. Mensaje listo para imprimir."""
    try:
        import keyring
    except ImportError:
        return (
            "Keyring no está instalado: `pip install -e \".[keyring]\"` y "
            "vuelve a intentarlo."
        )
    try:
        keyring.set_password(SERVICIO, CLAVES[nombre_clave], valor)
    except Exception as exc:
        return f"No he podido guardar la clave en el almacén del sistema: {exc}"
    return f"Clave de {nombre_clave} guardada en el almacén de credenciales del sistema."
