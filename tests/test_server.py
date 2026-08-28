"""Servidor web.

El HUD no tiene lógica propia: sólo traduce eventos del bus a pantalla y
clics a órdenes. Lo que hay que fijar aquí es ese contrato, más el fallo real
que costó encontrar: el WebSocket devolvía 403 porque, con
`from __future__ import annotations`, FastAPI no podía resolver el tipo
`WebSocket` importado dentro de una función.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date

import numpy as np
import pytest

from jarvis.audio.capture import FakeMicStream, MicStream
from jarvis.audio.player import NullPlayer
from jarvis.audio.stt import FakeTranscriber
from jarvis.audio.wakeword import NullWakeWord
from jarvis.core.core import JarvisCore
from jarvis.core.historial import Historial
from jarvis.core.memory import CATEGORIAS, Memory
from jarvis.events import EventType
from jarvis.server.app import HISTORIAL_MAXLEN, _atender, _atender_audio, crear_app

from .conftest import FakeAgent, FakeTTS

fastapi_testclient = pytest.importorskip("fastapi.testclient")


def construir_core(settings, *, mic=None):
    return JarvisCore(
        settings,
        agent=FakeAgent(),
        tts=FakeTTS(),
        player=NullPlayer(),
        mic=mic if mic is not None else FakeMicStream(np.zeros(0, dtype=np.float32)),
        transcriber=FakeTranscriber(),
        wakeword=NullWakeWord(),
    )


@pytest.fixture
def cliente(settings):
    core = construir_core(settings)
    # En producción lo escucha main.py, no el servidor (Fase C: el registro
    # vive en disco y sobrevive al reinicio). Aquí se conecta a mano, igual
    # que main.py, para poder probar el servidor sin arrancar todo el resto.
    Historial(settings.data_dir / "conversaciones").escuchar(core.bus)
    with fastapi_testclient.TestClient(crear_app(core)) as c:
        c.core = core
        yield c


class TestPaginas:
    def test_sirve_el_hud(self, cliente):
        r = cliente.get("/")
        assert r.status_code == 200
        assert "J.A.R.V.I.S." in r.text

    def test_sirve_los_estaticos(self, cliente):
        for ruta in ("/estaticos/estilo.css", "/estaticos/hud.js", "/estaticos/icono.svg"):
            assert cliente.get(ruta).status_code == 200, ruta

    def test_sirve_el_manifest_de_la_pwa(self, cliente):
        r = cliente.get("/estaticos/manifest.json")
        assert r.status_code == 200
        datos = r.json()
        assert datos["start_url"] == "/"
        assert datos["display"] == "standalone"


class TestApiEstado:
    def test_devuelve_lo_que_el_hud_necesita(self, cliente):
        datos = cliente.get("/api/estado").json()
        for clave in ("state", "coste_usd", "modelo", "voz", "wake_word", "microfono"):
            assert clave in datos, f"falta {clave}"
        assert datos["state"] == "dormido"

    def test_avisa_de_que_no_hay_microfono(self, cliente):
        # Con un micrófono falso, el HUD desactiva el botón de escuchar; si
        # mintiéramos aquí, el usuario dejaría el núcleo colgado esperando voz.
        assert cliente.get("/api/estado").json()["microfono"] is False

    def test_reconoce_un_microfono_real(self, settings):
        core = construir_core(settings, mic=MicStream())
        with fastapi_testclient.TestClient(crear_app(core)) as c:
            assert c.get("/api/estado").json()["microfono"] is True


class TestWebSocket:
    def test_conecta_y_manda_el_estado_inicial(self, cliente):
        """El fallo del 403 vivía justo aquí."""
        with cliente.websocket_connect("/ws") as ws:
            primero = ws.receive_json()
        assert primero["type"] == "state_changed"
        assert primero["data"]["state"] == "dormido"

    def test_reenvia_los_eventos_del_bus(self, cliente):
        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()  # el estado inicial
            ws.receive_json()  # el historial (vacío)
            cliente.core.bus.emit(EventType.LOG, message="hola")
            evento = ws.receive_json()
        assert evento["type"] == "log"
        assert evento["data"]["message"] == "hola"


class TestHistorial:
    """Reponer la conversación al conectar: recargar, o entrar desde otro
    dispositivo, no debe dejar el HUD en blanco a media charla."""

    def test_no_mezcla_turnos_de_otro_dia(self, cliente, settings):
        # Sin separador visual de por medio, mezclar "ayer" con "en directo"
        # confundiría más de lo que ayuda; para eso está el selector de días.
        from datetime import date, timedelta

        ayer = (date.today() - timedelta(days=1)).isoformat()
        archivo = settings.data_dir / "conversaciones" / f"{ayer}.jsonl"
        archivo.write_text(
            '{"hora": "09:00:00", "quien": "usuario", "texto": "de ayer"}\n',
            encoding="utf-8",
        )
        cliente.core.bus.emit(EventType.FINAL_TRANSCRIPT, text="de hoy")

        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()
            historial = ws.receive_json()

        assert [t["texto"] for t in historial["data"]["turnos"]] == ["de hoy"]

    def test_arranca_vacio(self, cliente):
        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()  # el estado inicial
            historial = ws.receive_json()
        assert historial["type"] == "historial"
        assert historial["data"]["turnos"] == []

    def test_repone_los_turnos_ya_ocurridos(self, cliente):
        cliente.core.bus.emit(EventType.FINAL_TRANSCRIPT, text="hola")
        cliente.core.bus.emit(EventType.ASSISTANT_DONE, text="¿En qué le ayudo?")

        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()  # el estado inicial
            historial = ws.receive_json()

        # El registro en disco añade la hora (Fase C); aquí sólo importa quién
        # dijo qué, y en qué orden.
        turnos = [{"quien": t["quien"], "texto": t["texto"]} for t in historial["data"]["turnos"]]
        assert turnos == [
            {"quien": "usuario", "texto": "hola"},
            {"quien": "jarvis", "texto": "¿En qué le ayudo?"},
        ]

    def test_un_turno_tecleado_se_repone_tambien(self, cliente):
        # A diferencia de los otros tests de esta clase, aquí no se emite el
        # evento a mano: se manda por el WebSocket, el mismo camino real
        # (_atender) que usa el HUD. Es donde vivía el hueco — core.responder()
        # no emite nada por sí solo, así que un turno tecleado se perdía al
        # reconectar aunque la respuesta de J.A.R.V.I.S. sí sobreviviera.
        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()  # el estado inicial
            ws.receive_json()  # el historial (vacío)
            ws.send_json({"type": "texto", "text": "qué hora es"})
            eco = ws.receive_json()  # el propio turno, reenviado por el bus

        assert eco["type"] == "final_transcript"
        assert eco["data"]["text"] == "qué hora es"

        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()
            historial = ws.receive_json()

        turnos = [{"quien": t["quien"], "texto": t["texto"]} for t in historial["data"]["turnos"]]
        assert {"quien": "usuario", "texto": "qué hora es"} in turnos

    def test_las_confirmaciones_no_entran_en_el_historial(self, cliente):
        # Es la respuesta a un "¿lo autoriza?", no parte de la charla; ya
        # tiene su propio hueco en el panel de Actividad.
        cliente.core.bus.emit(EventType.FINAL_TRANSCRIPT, text="sí", kind="confirmacion")

        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()
            historial = ws.receive_json()
        assert historial["data"]["turnos"] == []

    def test_las_respuestas_vacias_no_entran(self, cliente):
        cliente.core.bus.emit(EventType.ASSISTANT_DONE, text="")

        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()
            historial = ws.receive_json()
        assert historial["data"]["turnos"] == []

    def test_respeta_el_tope(self, cliente):
        for i in range(HISTORIAL_MAXLEN + 10):
            cliente.core.bus.emit(EventType.FINAL_TRANSCRIPT, text=f"mensaje {i}")

        with cliente.websocket_connect("/ws") as ws:
            ws.receive_json()
            historial = ws.receive_json()

        turnos = historial["data"]["turnos"]
        assert len(turnos) == HISTORIAL_MAXLEN
        # Se descartan los más antiguos primero, no los más recientes.
        assert turnos[0]["texto"] == "mensaje 10"
        assert turnos[-1]["texto"] == f"mensaje {HISTORIAL_MAXLEN + 9}"


class TestApiConversaciones:
    """Días anteriores: el registro en disco (Fase C) se puede navegar desde
    el HUD, no sólo reponerse al conectar."""

    def test_sin_conversacion_no_hay_dias(self, cliente):
        assert cliente.get("/api/conversaciones").json() == {"dias": []}

    def test_lista_los_dias_con_conversacion(self, cliente, settings):
        Historial(settings.data_dir / "conversaciones").registrar("usuario", "hola")

        dias = cliente.get("/api/conversaciones").json()["dias"]

        assert len(dias) == 1
        assert dias[0] == date.today().isoformat()

    def test_devuelve_los_turnos_de_un_dia(self, cliente, settings):
        h = Historial(settings.data_dir / "conversaciones")
        h.registrar("usuario", "hola")
        h.registrar("jarvis", "¿En qué le ayudo?")
        hoy = h.dias()[0]

        turnos = cliente.get(f"/api/conversaciones/{hoy}").json()["turnos"]

        assert [t["texto"] for t in turnos] == ["hola", "¿En qué le ayudo?"]

    def test_un_dia_sin_registro_da_vacio(self, cliente):
        assert cliente.get("/api/conversaciones/2020-01-01").json() == {"turnos": []}

    def test_un_dia_con_formato_invalido_no_revienta(self, cliente):
        assert cliente.get("/api/conversaciones/no-es-una-fecha").json() == {"turnos": []}

    def test_la_validacion_del_dia_rechaza_recorridos_de_directorio(self):
        # El "día" se usa tal cual para construir una ruta de archivo
        # (Historial._archivo): sin esto, "../../etc/passwd" se colaría como
        # recorrido de directorios en vez de rechazarse como formato inválido.
        # Se prueba el patrón directamente: qué le llega exactamente a la ruta
        # tras el enrutado de FastAPI/Starlette no es parte de este contrato.
        from jarvis.server.app import _FORMATO_DIA_VALIDO

        for peligroso in ("../../etc/passwd", "..%2F..%2Fetc", "2020-01-01/../../etc"):
            assert not _FORMATO_DIA_VALIDO.fullmatch(peligroso), peligroso


class TestApiMemoria:
    """Consultar y borrar la memoria de largo plazo desde el HUD."""

    def test_vacia_al_principio(self, cliente):
        datos = cliente.get("/api/memoria").json()
        assert set(datos) == set(CATEGORIAS)
        assert all(entradas == [] for entradas in datos.values())

    def test_devuelve_lo_recordado(self, settings, cliente):
        Memory(settings.memory_dir).recordar("le gusta el café solo", "preferencias")

        datos = cliente.get("/api/memoria").json()

        assert len(datos["preferencias"]) == 1
        assert "le gusta el café solo" in datos["preferencias"][0]
        # Sin el guión de Markdown ni el resto de categorías contaminadas.
        assert not datos["preferencias"][0].startswith("-")
        assert datos["notas"] == []

    def test_olvidar_borra_y_devuelve_el_listado_fresco(self, settings, cliente):
        Memory(settings.memory_dir).recordar("trabaja en un cohete", "proyectos")

        r = cliente.post("/api/memoria/olvidar", json={"texto": "cohete"})
        datos = r.json()

        assert r.status_code == 200
        assert "olvidado" in datos["mensaje"].lower()
        assert datos["memoria"]["proyectos"] == []

    def test_olvidar_sin_coincidencias_no_revienta(self, cliente):
        r = cliente.post("/api/memoria/olvidar", json={"texto": "esto no existe"})
        assert r.status_code == 200
        assert "no he encontrado" in r.json()["mensaje"].lower()

    def test_olvidar_no_toca_lo_que_no_coincide(self, settings, cliente):
        Memory(settings.memory_dir).recordar("le gusta el senderismo", "preferencias")

        cliente.post("/api/memoria/olvidar", json={"texto": "algo que no está"})
        datos = cliente.get("/api/memoria").json()

        assert len(datos["preferencias"]) == 1

    def test_no_muestra_la_marca_de_fecha_en_markdown_crudo(self, settings, cliente):
        # `Memory.recordar` anota "_(anotado el AAAA-MM-DD)_" al final de cada
        # línea; sin renderizar Markdown en el HUD eso se vería como guiones
        # bajos sueltos en vez de cursiva.
        Memory(settings.memory_dir).recordar("tiene un gato llamado Pixel", "notas")

        entrada = cliente.get("/api/memoria").json()["notas"][0]

        assert entrada == "tiene un gato llamado Pixel"


class TestApiMovil:
    """Abrir el HUD desde el móvil sin teclear la IP."""

    # `request.url.port` en el TestClient de Starlette sólo se rellena si el
    # `Host` de la petición lo incluye —su `base_url` por defecto no lleva
    # puerto—, así que se fija a mano aquí para que el endpoint tenga algo
    # que devolver, tal como pasaría en una petición real del navegador.
    CABECERAS = {"host": "192.168.1.37:8765"}

    def test_sin_ip_local_no_hay_nada_que_ofrecer(self, cliente, monkeypatch):
        import jarvis.server.app as app_modulo

        monkeypatch.setattr(app_modulo, "ip_local", lambda: None)

        datos = cliente.get("/api/movil", headers=self.CABECERAS).json()

        assert datos == {"url": None, "qr_svg": None}

    def test_devuelve_la_url_y_el_qr(self, cliente, monkeypatch):
        pytest.importorskip("qrcode", reason="el QR necesita el paquete qrcode (extra `web`)")
        import jarvis.server.app as app_modulo

        monkeypatch.setattr(app_modulo, "ip_local", lambda: "192.168.1.37")

        datos = cliente.get("/api/movil", headers=self.CABECERAS).json()

        assert datos["url"] == "http://192.168.1.37:8765"
        assert datos["qr_svg"] and "<svg" in datos["qr_svg"]

    def test_sin_qrcode_instalado_sigue_dando_la_url(self, cliente, monkeypatch):
        # Degradación como cualquier otra dependencia opcional: sin `qrcode`
        # no hay QR, pero la URL en texto sigue siendo útil.
        import jarvis.server.app as app_modulo

        monkeypatch.setattr(app_modulo, "ip_local", lambda: "192.168.1.37")
        monkeypatch.setitem(sys.modules, "qrcode", None)

        datos = cliente.get("/api/movil", headers=self.CABECERAS).json()

        assert datos["url"] == "http://192.168.1.37:8765"
        assert datos["qr_svg"] is None


class TestIconoApple:
    """El PNG para `apple-touch-icon`: iOS no acepta el SVG del favicon."""

    def test_responde_un_png(self, cliente):
        pil = pytest.importorskip("PIL", reason="el icono necesita Pillow (extra `bandeja`)")
        del pil

        r = cliente.get("/estaticos-generados/icono-180.png")

        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    def test_sin_pillow_da_404(self, cliente, monkeypatch):
        # Forzado, no confiado en que este sandbox carezca de Pillow: así el
        # test es igual de fiable aquí, en la CI y en el Windows de Jeremy.
        monkeypatch.setitem(sys.modules, "PIL", None)

        r = cliente.get("/estaticos-generados/icono-180.png")

        assert r.status_code == 404


class TestOrdenes:
    """Lo que el navegador puede pedirle al núcleo."""

    async def test_texto_lanza_un_turno(self, settings):
        core = construir_core(settings)
        await core.start()
        try:
            await _atender(core, {"type": "texto", "text": "hola"})
            await asyncio.sleep(0.25)
        finally:
            await core.stop()
        assert core.agent.preguntas == ["hola"]

    async def test_texto_emite_final_transcript_para_el_historial(self, settings):
        # Sin esto, un turno tecleado en el HUD desaparecería al reconectar:
        # `core.responder()` no emite nada por sí solo, a diferencia del
        # camino por voz (`_entender_y_responder`), que sí lo hace.
        core = construir_core(settings)
        eventos = []
        core.bus.on(eventos.append)
        await core.start()
        try:
            await _atender(core, {"type": "texto", "text": "qué hora es"})
            await asyncio.sleep(0.1)
        finally:
            await core.stop()

        transcritos = [e for e in eventos if e.type is EventType.FINAL_TRANSCRIPT]
        assert len(transcritos) == 1
        assert transcritos[0].data["text"] == "qué hora es"
        assert "kind" not in transcritos[0].data

    async def test_el_texto_vacio_se_ignora(self, settings):
        core = construir_core(settings)
        await core.start()
        try:
            await _atender(core, {"type": "texto", "text": "   "})
            await asyncio.sleep(0.1)
        finally:
            await core.stop()
        assert core.agent.preguntas == []

    async def test_interrumpir_corta_el_audio(self, settings):
        core = construir_core(settings)
        cortes = {"n": 0}
        core.player.interrumpir = lambda: cortes.__setitem__("n", cortes["n"] + 1)

        await _atender(core, {"type": "interrumpir"})
        assert cortes["n"] == 1

    async def test_una_orden_desconocida_no_revienta(self, settings):
        # El navegador es código que no controlamos del todo; un mensaje
        # inesperado no puede tumbar la conexión.
        core = construir_core(settings)
        await _atender(core, {"type": "algo_que_no_existe"})
        await _atender(core, {})


class TestAudioDelNavegador:
    """Pulsar-para-hablar desde el móvil.

    El navegador manda la frase entera como PCM binario por el mismo
    WebSocket. Lo que hay que fijar aquí es qué se acepta y qué se rechaza:
    el otro extremo es código que no controlamos del todo.
    """

    @staticmethod
    def pcm(segundos: float, tasa: int = 16_000) -> bytes:
        """Audio de prueba: PCM 16 bits, mono."""
        n = int(segundos * tasa)
        return (np.zeros(n, dtype=np.int16) + 1000).tobytes()

    async def test_una_grabacion_valida_llega_al_nucleo(self, settings):
        core = construir_core(settings)
        recibido = {}

        async def capturar(audio):  # noqa: ANN001, ANN202
            recibido["audio"] = audio

        core.escuchar_audio = capturar
        await _atender_audio(core, self.pcm(1.5))

        audio = recibido["audio"]
        assert audio.dtype == np.float32, "el núcleo trabaja en float32"
        assert len(audio) == 24_000, "1,5 s a 16 kHz"
        assert -1.0 <= audio.min() and audio.max() <= 1.0, "normalizado"

    async def test_una_grabacion_muy_corta_se_descarta(self, settings):
        """Casi siempre es el botón rozado sin querer."""
        core = construir_core(settings)
        llamadas = []

        async def capturar(audio):  # noqa: ANN001, ANN202
            llamadas.append(audio)

        core.escuchar_audio = capturar
        await _atender_audio(core, self.pcm(0.05))
        assert llamadas == []

    async def test_una_grabacion_enorme_se_rechaza(self, settings):
        core = construir_core(settings)
        eventos = []
        core.bus.on(eventos.append)
        llamadas = []

        async def capturar(audio):  # noqa: ANN001, ANN202
            llamadas.append(audio)

        core.escuchar_audio = capturar
        await _atender_audio(core, b"\x00" * 3_000_000)

        assert llamadas == []
        assert any(e.type.value == "error" for e in eventos)

    async def test_un_numero_impar_de_bytes_no_revienta(self, settings):
        """PCM de 16 bits siempre ocupa un número par: si llega impar, vino
        cortado. Se recorta en vez de fallar al interpretarlo."""
        core = construir_core(settings)
        recibido = {}

        async def capturar(audio):  # noqa: ANN001, ANN202
            recibido["audio"] = audio

        core.escuchar_audio = capturar
        await _atender_audio(core, self.pcm(1.0) + b"\x7f")

        assert len(recibido["audio"]) == 16_000


class TestConfirmacionDesdeElHUD:
    """Responder un permiso sin voz: desde el móvil, o sin micrófono."""

    @staticmethod
    async def _esperar_pendiente(core, timeout: float = 3.0) -> None:
        limite = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < limite:
            if core.confirmacion_pendiente:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("la confirmación nunca quedó pendiente")

    async def test_la_orden_confirmar_autoriza(self, settings):
        core = construir_core(settings)
        await core.start()
        try:
            tarea = asyncio.create_task(core.confirmar_por_voz("¿Lo hago?"))
            await self._esperar_pendiente(core)
            await _atender(core, {"type": "confirmar", "allowed": True})
            resultado = await asyncio.wait_for(tarea, timeout=3)
        finally:
            await core.stop()
        assert resultado is True

    async def test_la_orden_confirmar_deniega(self, settings):
        core = construir_core(settings)
        await core.start()
        try:
            tarea = asyncio.create_task(core.confirmar_por_voz("¿Lo hago?"))
            await self._esperar_pendiente(core)
            await _atender(core, {"type": "confirmar", "allowed": False})
            resultado = await asyncio.wait_for(tarea, timeout=3)
        finally:
            await core.stop()
        assert resultado is False

    async def test_texto_durante_confirmacion_responde_en_vez_de_lanzar_un_turno(self, settings):
        core = construir_core(settings)
        await core.start()
        try:
            tarea = asyncio.create_task(core.confirmar_por_voz("¿Lo hago?"))
            await self._esperar_pendiente(core)
            await _atender(core, {"type": "texto", "text": "sí, adelante"})
            resultado = await asyncio.wait_for(tarea, timeout=3)
        finally:
            await core.stop()
        assert resultado is True
        assert core.agent.preguntas == [], "no debía disparar un turno de Claude"

    async def test_texto_ambiguo_durante_confirmacion_no_decide_nada(self, settings):
        core = construir_core(settings)
        eventos = []
        core.bus.on(eventos.append)
        await core.start()
        try:
            tarea = asyncio.create_task(core.confirmar_por_voz("¿Lo hago?"))
            await self._esperar_pendiente(core)
            await _atender(core, {"type": "texto", "text": "mmm no sé"})
            await asyncio.sleep(0.05)
            assert core.confirmacion_pendiente, "un «no sé» no debe decidir nada"
            await _atender(core, {"type": "confirmar", "allowed": True})
            resultado = await asyncio.wait_for(tarea, timeout=3)
        finally:
            await core.stop()
        assert resultado is True
        assert any("¿sí o no?" in e.data.get("message", "").lower() for e in eventos)


class TestEscucharAudioEnElNucleo:
    async def test_transcribe_y_responde(self, settings):
        core = construir_core(settings)
        core.transcriber = FakeTranscriber(["enciende la luz"])

        await core.start()
        try:
            await core.escuchar_audio(np.zeros(16_000, dtype=np.float32))
            for _ in range(200):
                if core.agent.preguntas:
                    break
                await asyncio.sleep(0.01)
        finally:
            await core.stop()

        assert core.agent.preguntas == ["enciende la luz"]
