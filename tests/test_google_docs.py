"""Google Docs: leer, añadir, reemplazar y crear, sin tocar la red ni la API
de Google de verdad.

`_buscar_doc_id`, `_leer_doc_texto`, `_anadir_texto`, `_reemplazar_texto` y
`_crear_doc` reciben un objeto "servicio" en vez de construirlo ellas mismas
(igual que `sistema.py` separa "qué se pide" de "cómo se hace"), así que se
prueban con dobles que imitan la cadena `.files().list(...).execute()` de
`googleapiclient` — sin necesitar esa librería instalada ni credenciales.
"""

from __future__ import annotations

import threading

import pytest

from jarvis.tools import google_docs


class _PeticionFalsa:
    def __init__(self, resultado):  # noqa: ANN001
        self._resultado = resultado

    def execute(self):
        return self._resultado


class _DriveFalso:
    """Sustituye al servicio `drive` de `googleapiclient.discovery.build`."""

    def __init__(self, archivos: list[dict] | None = None) -> None:
        self._archivos = archivos if archivos is not None else []
        self.consultas: list[str] = []

    def files(self):  # noqa: ANN201
        return self

    def list(self, *, q, spaces, fields, pageSize):  # noqa: ANN001, N803, A002
        del spaces, fields, pageSize
        self.consultas.append(q)
        return _PeticionFalsa({"files": self._archivos})


class _DocsFalso:
    """Sustituye al servicio `docs` de `googleapiclient.discovery.build`."""

    def __init__(self, documento: dict | None = None, *, ocurrencias: int = 1) -> None:
        self._documento = documento if documento is not None else {"body": {"content": []}}
        self._ocurrencias = ocurrencias
        self.batch_updates: list[dict] = []
        self.creados: list[dict] = []
        self.id_creado = "doc-nuevo"

    def documents(self):  # noqa: ANN201
        return self

    def get(self, *, documentId):  # noqa: ANN001, N803
        del documentId
        return _PeticionFalsa(self._documento)

    def batchUpdate(self, *, documentId, body):  # noqa: ANN001, N802, N803
        self.batch_updates.append({"documentId": documentId, "body": body})
        peticion = body["requests"][0]
        if "replaceAllText" in peticion:
            return _PeticionFalsa(
                {"replies": [{"replaceAllText": {"occurrencesChanged": self._ocurrencias}}]}
            )
        return _PeticionFalsa({"replies": [{}]})

    def create(self, *, body):  # noqa: ANN001
        self.creados.append(body)
        return _PeticionFalsa({"documentId": self.id_creado, "title": body.get("title")})


def _un_documento_con_texto(texto: str) -> dict:
    return {
        "body": {
            "content": [
                {
                    "paragraph": {"elements": [{"textRun": {"content": texto}}]},
                    "endIndex": len(texto) + 1,
                }
            ]
        }
    }


class TestBuscarDocId:
    def test_encuentra_por_nombre(self):
        drive = _DriveFalso([{"id": "abc123", "name": "Notas"}])
        assert google_docs._buscar_doc_id(drive, "Notas") == "abc123"

    def test_no_encuentra_nada(self):
        drive = _DriveFalso([])
        assert google_docs._buscar_doc_id(drive, "Notas") is None

    def test_escapa_comillas_en_la_consulta(self):
        # Sin escapar, un nombre con comilla simple rompería la consulta de
        # Drive (o, peor, cambiaría lo que busca).
        drive = _DriveFalso([])
        google_docs._buscar_doc_id(drive, "Notas de Jeremy's")
        assert "Jeremy\\'s" in drive.consultas[0]


class TestExtraerTexto:
    def test_concatena_los_textruns(self):
        documento = _un_documento_con_texto("Hola, señor.")
        assert google_docs._extraer_texto(documento) == "Hola, señor."

    def test_documento_vacio_da_texto_vacio(self):
        assert google_docs._extraer_texto({"body": {"content": []}}) == ""

    def test_ignora_elementos_sin_parrafo(self):
        documento = {"body": {"content": [{"sectionBreak": {}}]}}
        assert google_docs._extraer_texto(documento) == ""


class TestIndiceFinal:
    def test_documento_vacio_usa_indice_uno(self):
        assert google_docs._indice_final({"body": {"content": []}}) == 1

    def test_resta_uno_al_salto_de_linea_final(self):
        documento = _un_documento_con_texto("hola")
        # endIndex = len("hola") + 1 = 5; el índice de inserción es 5 - 1 = 4.
        assert google_docs._indice_final(documento) == 4

    def test_nunca_baja_de_uno(self):
        documento = {"body": {"content": [{"endIndex": 0}]}}
        assert google_docs._indice_final(documento) == 1


class TestAnadirTexto:
    def test_inserta_en_el_indice_correcto(self):
        docs = _DocsFalso(_un_documento_con_texto("hola"))
        google_docs._anadir_texto(docs, "doc1", " mundo")

        peticion = docs.batch_updates[0]
        assert peticion["documentId"] == "doc1"
        insertar = peticion["body"]["requests"][0]["insertText"]
        assert insertar["text"] == " mundo"
        assert insertar["location"]["index"] == 4


class TestReemplazarTexto:
    def test_devuelve_cuantas_veces_cambio(self):
        docs = _DocsFalso(ocurrencias=3)
        cambios = google_docs._reemplazar_texto(docs, "doc1", "viejo", "nuevo")
        assert cambios == 3

    def test_sin_coincidencias_devuelve_cero(self):
        docs = _DocsFalso(ocurrencias=0)
        assert google_docs._reemplazar_texto(docs, "doc1", "x", "y") == 0


class TestCrearDoc:
    def test_crea_con_titulo(self):
        docs = _DocsFalso()
        doc_id = google_docs._crear_doc(docs, "Mi doc", "")
        assert doc_id == "doc-nuevo"
        assert docs.creados == [{"title": "Mi doc"}]

    def test_con_contenido_tambien_anade_texto(self):
        docs = _DocsFalso()
        google_docs._crear_doc(docs, "Mi doc", "contenido inicial")
        # create() + batchUpdate() para el contenido: dos llamadas a la API.
        assert len(docs.batch_updates) == 1
        assert docs.batch_updates[0]["body"]["requests"][0]["insertText"]["text"] == (
            "contenido inicial"
        )


class TestOrquestacionSinGoogleConfigurado:
    """Sin el extra `google` instalado o sin client_secret_path, cada función
    pública devuelve un mensaje claro en vez de reventar."""

    def test_buscar_doc_sin_extra_instalado(self, settings, monkeypatch):
        # Basta con forzar la ausencia del paquete `google`: es el primer
        # import del bloque, y los tres van en el mismo try/except. Se fuerza
        # en vez de confiar en que este sandbox no lo tenga instalado (regla
        # de CLAUDE.md) — aunque hoy tampoco lo tiene.
        import sys

        monkeypatch.setitem(sys.modules, "google", None)
        settings.google.client_secret_path = "algo.json"

        texto = google_docs.buscar_doc(settings, "Notas")

        assert "extra `google`" in texto

    def test_leer_doc_sin_client_secret_configurado(self, settings, monkeypatch):
        # Con los paquetes de Google instalados de verdad pero sin
        # client_secret_path configurado. Se fuerza que `_importar_google`
        # "funcione" para llegar a esa comprobación sin depender de tener
        # las librerías reales instaladas en el sandbox.
        monkeypatch.setattr(
            google_docs, "_importar_google", lambda: (object(), object(), object(), object())
        )
        settings.google.client_secret_path = ""

        texto = google_docs.leer_doc(settings, "Notas")

        assert "client_secret_path" in texto


class TestOrquestacionConServicioFalso:
    """`_resolver_doc` y las funciones públicas, con `_servicio_drive` y
    `_servicio_docs` sustituidos: la parte de autenticación real no se
    ejercita aquí, sólo la lógica de qué hacer con la respuesta de la API."""

    def _forzar_servicios(self, monkeypatch, *, drive=None, docs=None):  # noqa: ANN001
        monkeypatch.setattr(google_docs, "_servicio_drive", lambda _s: drive)
        monkeypatch.setattr(google_docs, "_servicio_docs", lambda _s: docs)

    def test_buscar_doc_que_existe(self, settings, monkeypatch):
        self._forzar_servicios(
            monkeypatch, drive=_DriveFalso([{"id": "abc", "name": "Notas"}])
        )
        texto = google_docs.buscar_doc(settings, "Notas")
        assert texto == "Sí, tengo un documento llamado «Notas»."

    def test_buscar_doc_que_no_existe(self, settings, monkeypatch):
        self._forzar_servicios(monkeypatch, drive=_DriveFalso([]))
        texto = google_docs.buscar_doc(settings, "Notas")
        assert "No encuentro" in texto

    def test_leer_doc_devuelve_el_contenido(self, settings, monkeypatch):
        self._forzar_servicios(
            monkeypatch,
            drive=_DriveFalso([{"id": "abc", "name": "Notas"}]),
            docs=_DocsFalso(_un_documento_con_texto("Contenido real.")),
        )
        assert google_docs.leer_doc(settings, "Notas") == "Contenido real."

    def test_leer_doc_vacio_lo_dice(self, settings, monkeypatch):
        self._forzar_servicios(
            monkeypatch,
            drive=_DriveFalso([{"id": "abc", "name": "Notas"}]),
            docs=_DocsFalso({"body": {"content": []}}),
        )
        texto = google_docs.leer_doc(settings, "Notas")
        assert "vacío" in texto

    def test_leer_doc_inexistente_no_llama_a_docs(self, settings, monkeypatch):
        docs = _DocsFalso()
        self._forzar_servicios(monkeypatch, drive=_DriveFalso([]), docs=docs)

        google_docs.leer_doc(settings, "Fantasma")

        assert docs.batch_updates == []

    def test_anadir_al_doc(self, settings, monkeypatch):
        docs = _DocsFalso(_un_documento_con_texto("hola"))
        self._forzar_servicios(
            monkeypatch, drive=_DriveFalso([{"id": "abc", "name": "Notas"}]), docs=docs
        )

        texto = google_docs.anadir_al_doc(settings, "Notas", " mundo")

        assert "Añadido" in texto
        assert docs.batch_updates[0]["body"]["requests"][0]["insertText"]["text"] == " mundo"

    def test_reemplazar_en_doc_con_cambios(self, settings, monkeypatch):
        self._forzar_servicios(
            monkeypatch,
            drive=_DriveFalso([{"id": "abc", "name": "Notas"}]),
            docs=_DocsFalso(ocurrencias=2),
        )

        texto = google_docs.reemplazar_en_doc(settings, "Notas", "viejo", "nuevo")

        assert "2 veces" in texto

    def test_reemplazar_en_doc_sin_coincidencias(self, settings, monkeypatch):
        self._forzar_servicios(
            monkeypatch,
            drive=_DriveFalso([{"id": "abc", "name": "Notas"}]),
            docs=_DocsFalso(ocurrencias=0),
        )

        texto = google_docs.reemplazar_en_doc(settings, "Notas", "viejo", "nuevo")

        assert "No he encontrado" in texto

    def test_crear_doc(self, settings, monkeypatch):
        docs = _DocsFalso()
        self._forzar_servicios(monkeypatch, docs=docs)

        texto = google_docs.crear_doc(settings, "Doc nuevo", "contenido")

        assert "He creado" in texto
        assert docs.creados == [{"title": "Doc nuevo"}]

    def test_un_fallo_de_red_no_revienta(self, settings, monkeypatch):
        class _DriveQueFalla:
            def files(self):  # noqa: ANN201
                raise RuntimeError("sin conexión")

        self._forzar_servicios(monkeypatch, drive=_DriveQueFalla())

        texto = google_docs.buscar_doc(settings, "Notas")

        assert "No he podido buscar" in texto


class TestEnHiloDaemon:
    """Reportado en vivo: un consentimiento OAuth atascado (URI mal
    configurada en Google Cloud Console, en el caso real) dejaba el proceso
    congelado tras Ctrl+C. `asyncio.to_thread` corre en el executor por
    defecto, cuyos hilos no son daemon; `_en_hilo_daemon` usa uno propio
    para que un Ctrl+C no se quede esperando a que Google conteste."""

    async def test_usa_un_hilo_daemon(self, monkeypatch):
        creados: list[bool | None] = []
        hilo_real = threading.Thread

        class HiloEspia(hilo_real):
            def __init__(self, *a, **kw):  # noqa: ANN002, ANN003
                creados.append(kw.get("daemon"))
                super().__init__(*a, **kw)

        monkeypatch.setattr(threading, "Thread", HiloEspia)

        resultado = await google_docs._en_hilo_daemon(lambda x: x * 2, 21)

        assert resultado == 42
        assert creados == [True]

    async def test_propaga_la_excepcion_tal_cual(self):
        def explota() -> None:
            raise ValueError("fallo de verdad")

        with pytest.raises(ValueError, match="fallo de verdad"):
            await google_docs._en_hilo_daemon(explota)


class TestRegistro:
    def test_las_herramientas_quedan_registradas(self, settings):
        nombres = {t.name for t in google_docs.herramientas_de_google_docs(settings)}
        assert nombres == {
            "buscar_doc", "leer_doc", "anadir_al_doc", "reemplazar_en_doc", "crear_doc",
        }
