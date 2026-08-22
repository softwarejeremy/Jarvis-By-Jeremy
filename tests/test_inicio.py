"""Arranque automático con Windows.

Vive en la carpeta de Inicio del usuario, no en el registro ni en el
Programador de tareas: no pide permisos de administrador, es del usuario y no
de toda la máquina, y se deshace borrando un archivo que el propio usuario
puede ver y quitar sin herramientas.
"""

from __future__ import annotations

from pathlib import Path

from jarvis import inicio


class TestCarpetaInicio:
    def test_ninguna_fuera_de_windows(self, monkeypatch):
        monkeypatch.setattr(inicio.platform, "system", lambda: "Linux")
        assert inicio.carpeta_inicio() is None

    def test_usa_appdata_en_windows(self, monkeypatch, tmp_path):
        monkeypatch.setattr(inicio.platform, "system", lambda: "Windows")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        carpeta = inicio.carpeta_inicio()
        assert carpeta is not None
        assert str(tmp_path) in str(carpeta)
        assert "Startup" in str(carpeta)

    def test_sin_appdata_no_revienta(self, monkeypatch):
        monkeypatch.setattr(inicio.platform, "system", lambda: "Windows")
        monkeypatch.delenv("APPDATA", raising=False)
        assert inicio.carpeta_inicio() is None


class TestGuionVbs:
    def test_lanza_sin_ventana(self):
        """El 0 es el modo de ventana oculta; sin él se vería una consola al
        encender el equipo todos los días."""
        guion = inicio.guion_vbs(Path("C:/Python/pythonw.exe"), Path("C:/Jarvis"), ["-m", "jarvis"])
        assert ", 0, False" in guion
        assert "CreateObject" in guion

    def test_fija_la_carpeta_del_proyecto(self):
        guion = inicio.guion_vbs(Path("py.exe"), Path("C:/Users/TU-USUARIO/Jarvis"), ["-m", "jarvis"])
        assert "C:/Users/TU-USUARIO/Jarvis" in guion

    def test_cita_argumentos_con_espacios(self):
        # Sin comillas, "mi carpeta" se leería como dos argumentos distintos.
        guion = inicio.guion_vbs(Path("py.exe"), Path("."), ["-m", "jarvis", "mi carpeta"])
        assert '""mi carpeta""' in guion

    def test_no_cita_lo_que_no_lleva_espacios(self):
        guion = inicio.guion_vbs(Path("py.exe"), Path("."), ["-m", "jarvis", "--web"])
        assert "--web" in guion
        assert '""--web""' not in guion


class TestInstalarYDesinstalar:
    def test_instala_y_deja_constancia(self, tmp_path):
        respuesta = inicio.instalar(["-m", "jarvis", "--web"], destino=tmp_path)
        assert "arrancaré solo" in respuesta.lower()
        assert (tmp_path / inicio.NOMBRE_ARCHIVO).is_file()
        assert inicio.esta_instalado(tmp_path)

    def test_reinstalar_respeta_las_banderas_nuevas(self, tmp_path):
        """Si instalaste con --web --https, eso es lo que debe arrancar
        cada mañana, no lo que se instaló la primera vez."""
        inicio.instalar(["-m", "jarvis", "--web"], destino=tmp_path)
        inicio.instalar(["-m", "jarvis", "--web", "--https"], destino=tmp_path)

        contenido = (tmp_path / inicio.NOMBRE_ARCHIVO).read_text()
        assert "--https" in contenido

    def test_desinstalar_quita_el_archivo(self, tmp_path):
        inicio.instalar(destino=tmp_path)
        assert inicio.esta_instalado(tmp_path)

        respuesta = inicio.desinstalar(tmp_path)
        assert "Quitado" in respuesta
        assert not inicio.esta_instalado(tmp_path)

    def test_desinstalar_dos_veces_no_revienta(self, tmp_path):
        inicio.desinstalar(tmp_path)
        respuesta = inicio.desinstalar(tmp_path)
        assert "No estaba" in respuesta

    def test_fuera_de_windows_lo_dice_sin_tocar_nada(self, monkeypatch):
        monkeypatch.setattr(inicio.platform, "system", lambda: "Linux")
        monkeypatch.delenv("APPDATA", raising=False)
        assert "sólo está implementado para Windows" in inicio.instalar()

    def test_crea_la_carpeta_si_no_existe(self, tmp_path):
        destino = tmp_path / "no" / "existe"
        inicio.instalar(destino=destino)
        assert (destino / inicio.NOMBRE_ARCHIVO).is_file()


class TestInterpreteSinConsola:
    def test_prefiere_pythonw_si_existe_en_el_venv(self, monkeypatch, tmp_path):
        """pythonw.exe no abre consola; python.exe sí. Sin el primero, cada
        arranque mostraría una ventana negra."""
        venv = tmp_path / "Scripts"
        venv.mkdir()
        (venv / "python.exe").touch()
        (venv / "pythonw.exe").touch()
        monkeypatch.setattr(inicio.sys, "executable", str(venv / "python.exe"))

        assert inicio._interprete_sin_consola().name == "pythonw.exe"

    def test_cae_al_interprete_actual_si_no_hay_pythonw(self, monkeypatch, tmp_path):
        venv = tmp_path / "Scripts"
        venv.mkdir()
        (venv / "python.exe").touch()
        monkeypatch.setattr(inicio.sys, "executable", str(venv / "python.exe"))

        assert inicio._interprete_sin_consola().name == "python.exe"
