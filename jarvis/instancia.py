"""Un solo J.A.R.V.I.S. por equipo.

Con el arranque automático activado esto deja de ser hipotético: el sistema
lanza uno al iniciar sesión y, en cuanto el usuario abre otro a mano, hay dos
peleándose por el micrófono, por el atajo global y por el puerto del HUD. Hoy
el segundo muere sin decir nada —uvicorn no puede bindear, hace ``sys.exit(1)``
y el traceback va a una consola que bajo ``pythonw.exe`` no existe—.

**El cerrojo es un socket, no un archivo.** Un archivo de bloqueo con el PID
dentro envejece mal: un corte de luz lo deja ahí y el siguiente arranque tiene
que decidir si ese PID sigue vivo. En Windows eso no se puede hacer:
``os.kill(pid, 0)`` no es una comprobación inocente como en Unix, sino que llama
a ``TerminateProcess`` para cualquier señal que no sea Ctrl-C, así que
«comprobar» mataría a un proceso ajeno que hubiera heredado el número. El
socket lo libera el sistema operativo cuando el proceso muere, pase lo que pase,
y no hay nada que limpiar.

El archivo ``instancia.json`` **no** es el cerrojo: es el buzón donde el que
está vivo deja la URL de su HUD, para que el segundo sepa qué abrir.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

# El puerto del HUD menos uno. Aquí no se sirve nada: el socket existe sólo
# para estar ocupado.
PUERTO_CONTROL = 8764

NOMBRE_HUELLA = "instancia.json"


@dataclass(slots=True, frozen=True)
class Huella:
    """Lo que el J.A.R.V.I.S. vivo deja dicho sobre sí mismo."""

    pid: int
    url: str | None = None


class Reserva:
    """El «aquí ya hay uno». Se suelta sola al morir el proceso."""

    def __init__(self, sock: socket.socket, huella: Path) -> None:
        self._sock: socket.socket | None = sock
        self._huella = huella

    def anunciar(self, url: str | None) -> None:
        """Deja constancia de dónde vive el HUD, para el que llegue después."""
        try:
            self._huella.parent.mkdir(parents=True, exist_ok=True)
            self._huella.write_text(
                json.dumps({"pid": os.getpid(), "url": url}), encoding="utf-8"
            )
        except OSError:
            # Sin buzón seguimos siendo la instancia única; el siguiente sólo
            # se quedará sin saber nuestra URL. No es motivo para no arrancar.
            pass

    def liberar(self) -> None:
        """Idempotente: se llama desde un `finally` que puede repetirse."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            self._huella.unlink(missing_ok=True)
        except OSError:
            pass


def reservar(data_dir: Path, *, puerto: int = PUERTO_CONTROL) -> Reserva | None:
    """Intenta ser el único. Devuelve ``None`` si ya hay otro."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # SO_REUSEADDR está deliberadamente ausente. En Windows no significa lo
    # mismo que en Unix: allí permite bindear un puerto que **ya está
    # escuchando**, que es justo lo que esta reserva existe para impedir.
    # SO_EXCLUSIVEADDRUSE es la forma de decirle a Windows que no lo permita;
    # en Linux no existe la constante y no hace falta.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]

    try:
        sock.bind(("127.0.0.1", puerto))
        sock.listen(1)
    except OSError:
        sock.close()
        return None

    return Reserva(sock, Path(data_dir) / NOMBRE_HUELLA)


def huella_ajena(data_dir: Path) -> Huella | None:
    """Lee lo que dejó dicho el que ya estaba. Nunca lanza.

    Que no haya huella no significa que no haya nadie: significa que quien
    ocupa el puerto **no somos nosotros**. Quien llama decide qué hacer con esa
    diferencia, y es una distinción que importa —un programa ajeno escuchando
    en el 8764 no puede dejar al usuario sin asistente—.
    """
    ruta = Path(data_dir) / NOMBRE_HUELLA
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(datos, dict):
        return None

    pid = datos.get("pid")
    url = datos.get("url")
    return Huella(
        pid=pid if isinstance(pid, int) else 0,
        url=url if isinstance(url, str) and url else None,
    )
