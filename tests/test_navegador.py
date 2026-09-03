"""Arrancando con el sistema no hay consola donde leer la URL del HUD. Abrirlo
solo es la diferencia entre «funciona» y «no ha pasado nada»."""

from __future__ import annotations

import asyncio
import socket

from jarvis.ui import navegador


def _puerto_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestEsperarPuerto:
    async def test_detecta_a_quien_ya_escucha(self):
        # El callback cierra la conexión que acepta: en Python 3.12+
        # `Server.wait_closed()` espera también a que se cierren las
        # conexiones ya aceptadas, no sólo a que el socket deje de escuchar.
        # Con un callback que no cierra nada (p. ej. `lambda *_: None`) esa
        # conexión queda abierta para siempre y el `wait_closed()` del
        # `finally` se cuelga.
        servidor = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
        puerto = servidor.sockets[0].getsockname()[1]
        try:
            assert await navegador.esperar_puerto(puerto, timeout=2.0) is True
        finally:
            servidor.close()
            await servidor.wait_closed()

    async def test_espera_a_que_el_servidor_levante(self):
        # El caso real: el sondeo corre en paralelo con `servir()`, que tarda
        # un momento en abrir el socket.
        puerto = _puerto_libre()
        servidor = None

        async def levantar_tarde():
            nonlocal servidor
            await asyncio.sleep(0.3)
            servidor = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", puerto)

        tarde = asyncio.create_task(levantar_tarde())
        try:
            assert await navegador.esperar_puerto(puerto, timeout=5.0) is True
        finally:
            await tarde
            if servidor is not None:
                servidor.close()
                await servidor.wait_closed()

    async def test_se_rinde_si_nadie_contesta(self):
        assert await navegador.esperar_puerto(_puerto_libre(), timeout=0.3) is False


class TestAbrirCuandoEscuche:
    async def test_abre_la_url_una_vez_hay_servidor(self, monkeypatch):
        abiertas = []
        monkeypatch.setattr(navegador, "abrir", lambda url, **_k: abiertas.append(url) or True)

        servidor = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
        puerto = servidor.sockets[0].getsockname()[1]
        try:
            ok = await navegador.abrir_cuando_escuche(f"http://localhost:{puerto}", puerto)
        finally:
            servidor.close()
            await servidor.wait_closed()

        assert ok is True
        assert abiertas == [f"http://localhost:{puerto}"]

    async def test_no_abre_nada_si_el_servidor_no_llega(self, monkeypatch):
        # Abrir el navegador contra un puerto muerto sólo enseña un error de
        # conexión, y eso se lee como «J.A.R.V.I.S. no funciona».
        abiertas = []
        monkeypatch.setattr(navegador, "abrir", lambda url, **_k: abiertas.append(url) or True)

        puerto = _puerto_libre()
        ok = await navegador.abrir_cuando_escuche("http://x", puerto, timeout=0.3)

        assert ok is False
        assert abiertas == []

    async def test_lo_abre_fuera_del_hilo_del_loop(self, monkeypatch):
        # `webbrowser.open` tarda segundos en Windows; en el loop serían
        # segundos sin leer el micrófono.
        import threading

        hilos = []
        monkeypatch.setattr(
            navegador, "abrir", lambda url, **_k: hilos.append(threading.get_ident()) or True
        )

        servidor = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
        puerto = servidor.sockets[0].getsockname()[1]
        try:
            await navegador.abrir_cuando_escuche("http://x", puerto)
        finally:
            servidor.close()
            await servidor.wait_closed()

        assert hilos and hilos[0] != threading.get_ident()

    async def test_un_navegador_que_falla_no_revienta(self, monkeypatch):
        # navegador="sistema": nos saltamos la búsqueda de Chrome, que ya
        # tiene sus propios tests más abajo.
        def explota(_url: str) -> bool:
            raise RuntimeError("no hay navegador")

        monkeypatch.setattr("webbrowser.open", explota)
        assert navegador.abrir("http://x", navegador="sistema") is False


class TestAbrir:
    """`abrir()` intenta Chrome primero (por defecto) y cae al navegador del
    sistema si no lo encuentra o si Chrome falla al abrir."""

    def test_navegador_sistema_va_directo_a_webbrowser_open(self, monkeypatch):
        llamadas = []
        monkeypatch.setattr("webbrowser.open", lambda url, **_k: llamadas.append(url) or True)
        # Si intentara Chrome, esto reventaría el test: nadie debería llamarlo.
        monkeypatch.setattr(navegador, "_chrome", lambda: (_ for _ in ()).throw(
            AssertionError("no debería buscar Chrome con navegador='sistema'")
        ))

        assert navegador.abrir("http://x", navegador="sistema") is True
        assert llamadas == ["http://x"]

    def test_chrome_encontrado_lo_abre_sin_tocar_el_del_sistema(self, monkeypatch):
        abiertas = []

        class ChromeFalso:
            def open(self, url, new=0):  # noqa: ANN001, ARG002
                abiertas.append(url)
                return True

        monkeypatch.setattr(navegador, "_chrome", lambda: ChromeFalso())
        monkeypatch.setattr(
            "webbrowser.open",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("no debería caer al navegador del sistema")
            ),
        )

        assert navegador.abrir("http://x") is True
        assert abiertas == ["http://x"]

    def test_sin_chrome_cae_al_navegador_del_sistema(self, monkeypatch):
        monkeypatch.setattr(navegador, "_chrome", lambda: None)
        llamadas = []
        monkeypatch.setattr("webbrowser.open", lambda url, **_k: llamadas.append(url) or True)

        assert navegador.abrir("http://x") is True
        assert llamadas == ["http://x"]

    def test_chrome_que_falla_al_abrir_cae_al_navegador_del_sistema(self, monkeypatch):
        class ChromeQueFalla:
            def open(self, url, new=0):  # noqa: ANN001, ARG002
                raise RuntimeError("Chrome no arrancó")

        monkeypatch.setattr(navegador, "_chrome", lambda: ChromeQueFalla())
        llamadas = []
        monkeypatch.setattr("webbrowser.open", lambda url, **_k: llamadas.append(url) or True)

        assert navegador.abrir("http://x") is True
        assert llamadas == ["http://x"]


class TestChrome:
    """`_chrome()` busca Chrome; en Windows no hay registro automático, así
    que además de `webbrowser.get` se prueban rutas típicas de instalación."""

    def test_lo_encuentra_si_webbrowser_ya_lo_conoce(self, monkeypatch):
        import webbrowser

        centinela = object()
        monkeypatch.setattr(webbrowser, "get", lambda _nombre: centinela)

        assert navegador._chrome() is centinela

    def test_sin_registro_busca_en_las_rutas_de_windows(self, monkeypatch, tmp_path):
        import webbrowser

        def sin_registro(_nombre):
            raise webbrowser.Error("no encontrado")

        monkeypatch.setattr(webbrowser, "get", sin_registro)
        monkeypatch.delenv("PROGRAMFILES", raising=False)
        monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

        ruta = tmp_path / "Google" / "Chrome" / "Application"
        ruta.mkdir(parents=True)
        (ruta / "chrome.exe").write_bytes(b"")

        controlador = navegador._chrome()
        assert controlador is not None
        assert controlador.name == str(ruta / "chrome.exe")

    def test_sin_registro_ni_rutas_no_encuentra_nada(self, monkeypatch, tmp_path):
        import webbrowser

        def sin_registro(_nombre):
            raise webbrowser.Error("no encontrado")

        monkeypatch.setattr(webbrowser, "get", sin_registro)
        monkeypatch.delenv("PROGRAMFILES", raising=False)
        monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # vacío: no hay chrome.exe

        assert navegador._chrome() is None
