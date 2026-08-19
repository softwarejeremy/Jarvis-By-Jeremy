"""La memoria es lo que hace que J.A.R.V.I.S. te siga conociendo mañana."""

from __future__ import annotations

from jarvis.core.memory import CATEGORIAS, Memory


class TestMemory:
    def test_arranca_vacia(self, tmp_path):
        assert Memory(tmp_path).cargar() == ""

    def test_recordar_y_recuperar(self, tmp_path):
        m = Memory(tmp_path)
        m.recordar("Se llama Jeremy", "perfil")
        m.recordar("Prefiere Python", "preferencias")

        volcado = m.cargar()
        assert "Jeremy" in volcado
        assert "Python" in volcado
        assert "Perfil" in volcado and "Preferencias" in volcado

    def test_no_duplica(self, tmp_path):
        m = Memory(tmp_path)
        m.recordar("Se llama Jeremy", "perfil")
        respuesta = m.recordar("se llama jeremy", "perfil")

        assert "ya lo tenía" in respuesta
        assert m.cargar().lower().count("jeremy") == 1

    def test_categoria_invalida_va_a_notas(self, tmp_path):
        m = Memory(tmp_path)
        m.recordar("Un dato suelto", "inventada")
        assert "Un dato suelto" in m.leer("notas")

    def test_olvidar_borra_solo_lo_que_coincide(self, tmp_path):
        m = Memory(tmp_path)
        m.recordar("Le gusta el café", "preferencias")
        m.recordar("Le gusta el té", "preferencias")

        respuesta = m.olvidar("café")
        assert "Olvidado" in respuesta
        assert "café" not in m.cargar()
        assert "té" in m.cargar()

    def test_olvidar_algo_inexistente_lo_dice(self, tmp_path):
        m = Memory(tmp_path)
        m.recordar("Algo", "notas")
        assert "No he encontrado" in m.olvidar("dinosaurios")

    def test_olvidar_sin_texto_no_borra_todo(self, tmp_path):
        # Un "olvida" mal transcrito no puede vaciar la memoria entera.
        m = Memory(tmp_path)
        m.recordar("Un dato importante", "perfil")
        m.olvidar("   ")
        assert "Un dato importante" in m.cargar()

    def test_recordar_vacio_no_escribe(self, tmp_path):
        m = Memory(tmp_path)
        m.recordar("   ", "notas")
        assert m.cargar() == ""

    def test_se_recorta_al_crecer_demasiado(self, tmp_path):
        """El prompt no puede crecer sin límite: se sueltan los más viejos."""
        m = Memory(tmp_path)
        for i in range(400):
            m.recordar(f"Dato número {i} con bastante texto de relleno", "notas")

        contenido = m.leer("notas")
        assert len(contenido) <= 8_000
        assert "Dato número 399" in contenido, "lo reciente debe conservarse"
        assert "Dato número 0 " not in contenido, "lo viejo se descarta"

    def test_persiste_entre_instancias(self, tmp_path):
        Memory(tmp_path).recordar("Trabaja en Skypie", "proyectos")
        assert "Skypie" in Memory(tmp_path).cargar()

    def test_los_archivos_son_legibles_a_mano(self, tmp_path):
        """A propósito son Markdown: tienes que poder abrirlos y corregirlos."""
        m = Memory(tmp_path)
        m.recordar("Un hecho cualquiera", "perfil")

        archivo = tmp_path / "perfil.md"
        assert archivo.is_file()
        texto = archivo.read_text(encoding="utf-8")
        assert texto.startswith("- ")
        assert "Un hecho cualquiera" in texto

    def test_todas_las_categorias_tienen_descripcion(self):
        # Las descripciones acaban en el prompt de la herramienta: si falta
        # una, Claude no sabrá dónde archivar las cosas.
        assert all(desc.strip() for desc in CATEGORIAS.values())
