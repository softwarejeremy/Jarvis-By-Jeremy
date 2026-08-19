"""El troceo en frases decide la latencia percibida: si se equivoca, o
J.A.R.V.I.S. tarda en arrancar a hablar, o suena entrecortado."""

from __future__ import annotations

from jarvis.text import SentenceChunker, limpiar_para_voz


class TestSentenceChunker:
    def test_suelta_la_frase_en_cuanto_esta_completa(self):
        c = SentenceChunker()
        assert c.feed("Los sistemas están en línea") == []
        frases = c.feed(" y operativos. Ahora")
        assert frases == ["Los sistemas están en línea y operativos."]

    def test_acumula_las_frases_demasiado_cortas(self):
        # "Sí." sola sonaría cortada; espera a tener material suficiente.
        c = SentenceChunker()
        assert c.feed("Sí.") == []
        assert c.feed(" He revisado los tres archivos.") == [
            "Sí. He revisado los tres archivos."
        ]

    def test_no_corta_en_abreviaturas(self):
        c = SentenceChunker()
        assert c.feed("Lo ha pedido el Sr. Stark esta mañana") == []
        assert c.feed(" temprano. Y") == [
            "Lo ha pedido el Sr. Stark esta mañana temprano."
        ]

    def test_no_corta_en_decimales(self):
        c = SentenceChunker()
        assert c.feed("La temperatura del reactor es de 3.14 grados") == []
        assert c.feed(" centígrados. ") == [
            "La temperatura del reactor es de 3.14 grados centígrados."
        ]

    def test_corta_por_longitud_sin_puntuacion(self):
        # Un monólogo sin puntos no puede dejar mudo al asistente para siempre.
        c = SentenceChunker(max_chars=60)
        frases = c.feed("palabra " * 20)
        assert frases, "debería haber cortado por longitud"
        assert all(not f.endswith("palab") for f in frases), "no parte palabras"

    def test_flush_devuelve_el_resto_sin_puntuacion_final(self):
        c = SentenceChunker()
        c.feed("Una respuesta sin punto final")
        assert c.flush() == ["Una respuesta sin punto final"]
        assert c.flush() == []

    def test_interrogaciones_y_exclamaciones(self):
        c = SentenceChunker()
        assert c.feed("¿Quiere que lo abra ahora mismo? Dígame") == [
            "¿Quiere que lo abra ahora mismo?"
        ]

    def test_no_pierde_texto(self):
        entrada = "Primera frase completa aquí. Segunda frase completa aquí. Cola sin punto"
        c = SentenceChunker()
        salida = []
        for ch in entrada:  # carácter a carácter, como el streaming real
            salida += c.feed(ch)
        salida += c.flush()
        assert "".join(salida).replace(" ", "") == entrada.replace(" ", "")


class TestLimpiarParaVoz:
    def test_quita_markdown(self):
        assert limpiar_para_voz("Esto es **importante** y esto `código`") == (
            "Esto es importante y esto código"
        )

    def test_sustituye_bloques_de_codigo(self):
        salida = limpiar_para_voz("Mira:\n```python\nprint('hola')\n```\nListo.")
        assert "print" not in salida
        assert "el código está en pantalla" in salida

    def test_quita_vinetas_y_encabezados(self):
        assert limpiar_para_voz("# Título\n- uno\n- dos") == "Título uno dos"

    def test_quita_emojis(self):
        assert limpiar_para_voz("Hecho 🚀✅") == "Hecho"

    def test_conserva_el_texto_de_los_enlaces(self):
        assert limpiar_para_voz("Está en [la documentación](https://x.com/y)") == (
            "Está en la documentación"
        )
