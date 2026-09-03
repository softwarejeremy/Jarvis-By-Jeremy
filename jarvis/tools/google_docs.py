"""Google Docs: leer, añadir texto, reemplazar y crear documentos.

## Por qué hace falta autenticación de verdad

A diferencia del resto de `jarvis/tools/`, esto no habla con el propio equipo:
habla con la cuenta de Google del usuario. Eso exige OAuth2 —un
``client_secret.json`` sacado de Google Cloud Console (ver el README) y un
consentimiento en el navegador la primera vez—, no una API key suelta en
``.env``. El token resultante (con ``refresh_token``) se guarda en
``data_dir`` junto a la memoria, no en ``.env``: cambia solo en cada refresco,
y ``.env`` es para secretos estáticos.

## Por qué las funciones reciben el "servicio", no `settings`, en su núcleo

Las funciones que de verdad hablan con la API (`_buscar_doc_id`,
`_leer_doc_texto`, `_anadir_texto`, `_reemplazar_texto`, `_crear_doc`) toman
un objeto "servicio" de `googleapiclient` como parámetro, en vez de construir
uno ellas mismas. Es el mismo principio que separa "qué se pide" de "cómo se
hace" en `sistema.py`: así se pueden probar con un doble que imita la cadena
`.files().list(...).execute()` sin necesidad de credenciales, red, ni siquiera
tener `google-api-python-client` instalado en la máquina que corre los tests.

## Sobre los permisos

Buscar y leer no tocan nada del usuario: van a `PROPIAS_AUTOMATICAS`. Añadir,
reemplazar y crear sí modifican un documento real, así que piden el "sí"
hablado como `Write`/`Edit` — el modelo de rutas del resto del proyecto
(`permissions.py:_ruta_permitida`) no aplica a un id de Google Docs, así que
aquí la única barrera real es la confirmación por voz.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import tool

from ..hilos import en_hilo_daemon

if TYPE_CHECKING:
    from ..config import Settings

_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.readonly",
]


class _GoogleNoDisponible(Exception):
    """Falta el extra `google`, o no hay `client_secret.json` configurado."""


def _importar_google():  # noqa: ANN202
    """Los cuatro paquetes del extra `google`, o `_GoogleNoDisponible`.

    Un único punto de import para las tres funciones que hablan con Google:
    `_credenciales` necesita los tres primeros; `_servicio_drive`/
    `_servicio_docs` además necesitan `build`. Todos vienen del mismo extra
    opcional, así que fallan juntos con el mismo mensaje.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise _GoogleNoDisponible(
            'Google Docs no está instalado: hace falta el extra `google` '
            '(`pip install -e ".[google]"`).'
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def _credenciales(settings: Settings):  # noqa: ANN202
    Request, Credentials, InstalledAppFlow, _build = _importar_google()

    ruta_secreto = settings.google.client_secret_path
    if not ruta_secreto:
        raise _GoogleNoDisponible(
            "Falta configurar `google.client_secret_path` en config.toml con "
            "la ruta al client_secret.json de Google Cloud Console (ver el "
            "README)."
        )

    ruta_token = settings.data_dir / "google_token.json"
    credenciales = None
    if ruta_token.is_file():
        credenciales = Credentials.from_authorized_user_file(str(ruta_token), _SCOPES)

    if credenciales and credenciales.expired and credenciales.refresh_token:
        credenciales.refresh(Request())

    if not credenciales or not credenciales.valid:
        # Primera vez, o el refresh token dejó de servir: hace falta pasar
        # otra vez por el consentimiento en el navegador. `timeout_seconds`
        # es la única red de seguridad real: si el consentimiento nunca
        # llega —una `client_secret.json` de tipo "Aplicación web" en vez de
        # "Aplicación de escritorio" es la causa real que motivó esto, el
        # puerto aleatorio nunca va a coincidir con una lista blanca—, sin
        # esto se queda escuchando para siempre.
        flujo = InstalledAppFlow.from_client_secrets_file(ruta_secreto, _SCOPES)
        credenciales = flujo.run_local_server(port=0, timeout_seconds=180)

    ruta_token.parent.mkdir(parents=True, exist_ok=True)
    ruta_token.write_text(credenciales.to_json(), encoding="utf-8")
    # Lleva un refresh_token: mismo criterio que la clave TLS (tls.py), sólo
    # su dueño debe poder leerlo.
    with contextlib.suppress(OSError, NotImplementedError):
        ruta_token.chmod(0o600)
    return credenciales


def _servicio_drive(settings: Settings):  # noqa: ANN202
    _R, _C, _F, build = _importar_google()
    return build("drive", "v3", credentials=_credenciales(settings))


def _servicio_docs(settings: Settings):  # noqa: ANN202
    _R, _C, _F, build = _importar_google()
    return build("docs", "v1", credentials=_credenciales(settings))


# ═══════════════════════════════════════════════════════════════════════
#  Lo que de verdad habla con la API — testable con un doble del servicio
# ═══════════════════════════════════════════════════════════════════════

def _escapar_para_consulta(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("'", "\\'")


def _buscar_doc_id(servicio_drive, nombre: str) -> str | None:  # noqa: ANN001
    """Busca un Google Doc por nombre exacto. `None` si no hay ninguno."""
    consulta = (
        f"name = '{_escapar_para_consulta(nombre)}' and "
        "mimeType = 'application/vnd.google-apps.document' and trashed = false"
    )
    resultado = (
        servicio_drive.files()
        .list(q=consulta, spaces="drive", fields="files(id, name)", pageSize=1)
        .execute()
    )
    archivos = resultado.get("files", [])
    return archivos[0]["id"] if archivos else None


def _extraer_texto(documento: dict) -> str:
    partes: list[str] = []
    for elemento in documento.get("body", {}).get("content", []):
        parrafo = elemento.get("paragraph")
        if not parrafo:
            continue
        for trozo in parrafo.get("elements", []):
            texto_run = trozo.get("textRun")
            if texto_run:
                partes.append(texto_run.get("content", ""))
    return "".join(partes)


def _leer_doc_texto(servicio_docs, doc_id: str) -> str:  # noqa: ANN001
    documento = servicio_docs.documents().get(documentId=doc_id).execute()
    return _extraer_texto(documento)


def _indice_final(documento: dict) -> int:
    contenido = documento.get("body", {}).get("content", [])
    if not contenido:
        return 1
    fin = contenido[-1].get("endIndex", 1)
    # El último índice incluye el salto de línea implícito del documento;
    # insertar justo ahí lo dejaría fuera de rango para la API.
    return max(1, fin - 1)


def _anadir_texto(servicio_docs, doc_id: str, texto: str) -> None:  # noqa: ANN001
    documento = servicio_docs.documents().get(documentId=doc_id).execute()
    indice = _indice_final(documento)
    servicio_docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertText": {"location": {"index": indice}, "text": texto}}]},
    ).execute()


def _reemplazar_texto(servicio_docs, doc_id: str, buscar: str, reemplazar: str) -> int:  # noqa: ANN001
    resultado = servicio_docs.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [{
                "replaceAllText": {
                    "containsText": {"text": buscar, "matchCase": False},
                    "replaceText": reemplazar,
                }
            }]
        },
    ).execute()
    respuestas = resultado.get("replies", [{}])
    return respuestas[0].get("replaceAllText", {}).get("occurrencesChanged", 0)


def _crear_doc(servicio_docs, titulo: str, contenido: str) -> str:  # noqa: ANN001
    documento = servicio_docs.documents().create(body={"title": titulo}).execute()
    doc_id = documento["documentId"]
    if contenido:
        _anadir_texto(servicio_docs, doc_id, contenido)
    return doc_id


# ═══════════════════════════════════════════════════════════════════════
#  Orquestación: resuelve el nombre, habla con la API, redacta para voz
# ═══════════════════════════════════════════════════════════════════════

def _resolver_doc(settings: Settings, nombre: str) -> tuple[str | None, str | None]:
    """(doc_id, None) si lo encuentra; (None, mensaje_de_error) si no."""
    try:
        doc_id = _buscar_doc_id(_servicio_drive(settings), nombre)
    except _GoogleNoDisponible as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 - fallo real de red/API de Google
        return None, f"No he podido buscar «{nombre}»: {exc}"
    if doc_id is None:
        return None, f"No encuentro ningún documento llamado «{nombre}»."
    return doc_id, None


def buscar_doc(settings: Settings, nombre: str) -> str:
    """Busca un Google Doc por nombre y dice si existe."""
    _doc_id, error = _resolver_doc(settings, nombre)
    if error:
        return error
    return f"Sí, tengo un documento llamado «{nombre}»."


def leer_doc(settings: Settings, nombre: str) -> str:
    """El contenido de un Google Doc, en texto plano."""
    doc_id, error = _resolver_doc(settings, nombre)
    if error:
        return error
    try:
        texto = _leer_doc_texto(_servicio_docs(settings), doc_id)
    except Exception as exc:  # noqa: BLE001
        return f"No he podido leer «{nombre}»: {exc}"
    return texto.strip() or f"El documento «{nombre}» está vacío."


def anadir_al_doc(settings: Settings, nombre: str, texto: str) -> str:
    """Añade texto al final de un Google Doc existente."""
    doc_id, error = _resolver_doc(settings, nombre)
    if error:
        return error
    try:
        _anadir_texto(_servicio_docs(settings), doc_id, texto)
    except Exception as exc:  # noqa: BLE001
        return f"No he podido añadir texto a «{nombre}»: {exc}"
    return f"Añadido al final de «{nombre}»."


def reemplazar_en_doc(settings: Settings, nombre: str, buscar: str, reemplazar: str) -> str:
    """Busca y reemplaza un fragmento de texto en un Google Doc existente."""
    doc_id, error = _resolver_doc(settings, nombre)
    if error:
        return error
    try:
        cambios = _reemplazar_texto(_servicio_docs(settings), doc_id, buscar, reemplazar)
    except Exception as exc:  # noqa: BLE001
        return f"No he podido reemplazar texto en «{nombre}»: {exc}"
    if cambios == 0:
        return f"No he encontrado «{buscar}» en «{nombre}»: no he cambiado nada."
    return f"Reemplazado en «{nombre}» ({cambios} {'vez' if cambios == 1 else 'veces'})."


def crear_doc(settings: Settings, titulo: str, contenido: str = "") -> str:
    """Crea un Google Doc nuevo, con contenido inicial opcional."""
    try:
        _crear_doc(_servicio_docs(settings), titulo, contenido)
    except _GoogleNoDisponible as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        return f"No he podido crear «{titulo}»: {exc}"
    return f"He creado el documento «{titulo}»."


# ═══════════════════════════════════════════════════════════════════════
#  Registro en MCP
# ═══════════════════════════════════════════════════════════════════════

def herramientas_de_google_docs(settings: Settings) -> list[Any]:
    """Las herramientas de Google Docs, para registrarlas junto al resto.

    Se registran siempre, aunque falte el extra `google` o la configuración:
    fallan con un mensaje claro en el momento de usarlas, no revientan el
    arranque (mismo criterio que el resto de `jarvis/tools/`).
    """

    @tool(
        "buscar_doc",
        "Busca un Google Doc por su nombre y dice si existe. Úsalo antes de "
        "leer, añadir o reemplazar texto si no estás seguro de que el "
        "documento existe.",
        {"nombre": str},
    )
    async def buscar(args: dict[str, Any]) -> dict[str, Any]:
        texto = await en_hilo_daemon(buscar_doc, settings, str(args.get("nombre", "")))
        return {"content": [{"type": "text", "text": texto}]}

    @tool(
        "leer_doc",
        "Lee el contenido completo de un Google Doc por su nombre.",
        {"nombre": str},
    )
    async def leer(args: dict[str, Any]) -> dict[str, Any]:
        texto = await en_hilo_daemon(leer_doc, settings, str(args.get("nombre", "")))
        return {"content": [{"type": "text", "text": texto}]}

    @tool(
        "anadir_al_doc",
        "Añade texto al final de un Google Doc existente, por su nombre. "
        "Pide confirmación al usuario antes de ejecutarse.",
        {"nombre": str, "texto": str},
    )
    async def anadir(args: dict[str, Any]) -> dict[str, Any]:
        texto = await en_hilo_daemon(
            anadir_al_doc, settings, str(args.get("nombre", "")), str(args.get("texto", ""))
        )
        return {"content": [{"type": "text", "text": texto}]}

    @tool(
        "reemplazar_en_doc",
        "Busca un fragmento de texto en un Google Doc existente y lo "
        "reemplaza por otro. Pide confirmación al usuario antes de "
        "ejecutarse.",
        {"nombre": str, "buscar": str, "reemplazar": str},
    )
    async def reemplazar(args: dict[str, Any]) -> dict[str, Any]:
        texto = await en_hilo_daemon(
            reemplazar_en_doc,
            settings,
            str(args.get("nombre", "")),
            str(args.get("buscar", "")),
            str(args.get("reemplazar", "")),
        )
        return {"content": [{"type": "text", "text": texto}]}

    @tool(
        "crear_doc",
        "Crea un Google Doc nuevo, con un título y contenido inicial "
        "opcional. Pide confirmación al usuario antes de ejecutarse.",
        {
            "titulo": str,
            "contenido": {
                "type": "string",
                "description": "Texto inicial del documento. Opcional.",
            },
        },
    )
    async def crear(args: dict[str, Any]) -> dict[str, Any]:
        texto = await en_hilo_daemon(
            crear_doc, settings, str(args.get("titulo", "")), str(args.get("contenido", ""))
        )
        return {"content": [{"type": "text", "text": texto}]}

    return [buscar, leer, anadir, reemplazar, crear]
