"""El icono de la bandeja es lo único que J.A.R.V.I.S. enseña cuando arranca
sin consola. Si no se lee a 16 píxeles, no sirve de nada."""

from __future__ import annotations

import pytest

from jarvis.ui import icono

pil = pytest.importorskip("PIL", reason="el dibujo necesita Pillow (extra `bandeja`)")

# El tamaño real de la bandeja de Windows al 100 % de escalado. Todo lo que se
# afirme aquí tiene que cumplirse a este tamaño, no sólo a 64.
BANDEJA = 16


class TestColores:
    def test_cada_estado_conocido_tiene_su_color(self):
        from jarvis.events import State

        for estado in State:
            assert estado.value in icono.COLOR_ESTADO

    def test_coinciden_con_los_del_hud_web(self):
        # Si alguien retoca `estilo.css`, el icono de la bandeja y el reactor
        # del navegador contarían cosas distintas del mismo estado y nadie se
        # enteraría hasta que un usuario lo notase.
        del_hud = icono.colores_del_hud()
        assert del_hud, "no se ha podido leer ningún color del CSS"
        assert del_hud == {e: c.lower() for e, c in icono.COLOR_ESTADO.items()}

    def test_un_estado_desconocido_no_revienta(self):
        assert icono.color_de("algo_que_no_existe") == icono.COLOR_DESCONOCIDO


class TestTooltip:
    def test_menciona_el_estado(self):
        assert "escuchando" in icono.texto_tooltip("escuchando")
        assert "J.A.R.V.I.S." in icono.texto_tooltip("escuchando")

    def test_incluye_el_coste_cuando_lo_hay(self):
        assert "$0.0123" in icono.texto_tooltip("pensando", coste_usd=0.0123)

    def test_sin_gasto_no_habla_de_dinero(self):
        assert "$" not in icono.texto_tooltip("dormido", coste_usd=0.0)

    def test_cabe_en_el_limite_de_windows(self):
        largo = icono.texto_tooltip("x" * 200, coste_usd=1.5)
        assert len(largo) <= icono.LIMITE_TOOLTIP


class TestDibujo:
    def test_devuelve_una_imagen_cuadrada_con_alfa(self):
        img = icono.dibujar_reactor("dormido", 32)
        assert img.size == (32, 32)
        assert img.mode == "RGBA"

    def test_las_esquinas_son_transparentes(self):
        # Un icono con fondo se vería como un cuadrado sobre la barra de tareas.
        px = icono.dibujar_reactor("hablando", 64).load()
        for x, y in ((0, 0), (63, 0), (0, 63), (63, 63)):
            assert px[x, y][3] == 0

    def test_se_lee_a_dieciseis_pixeles(self):
        px = icono.dibujar_reactor("escuchando", BANDEJA).load()
        opacos = sum(
            1
            for y in range(BANDEJA)
            for x in range(BANDEJA)
            if px[x, y][3] >= 128
        )
        # Menos de un tercio del cuadro y el icono se ve como una mota.
        assert opacos >= BANDEJA * BANDEJA // 3
        fila_central = sum(1 for x in range(BANDEJA) if px[x, BANDEJA // 2][3] >= 128)
        assert fila_central >= 8, "el anillo se desvanece al reducir"

    def test_el_nucleo_toma_el_color_del_estado(self):
        centro = 32
        ambar = icono.dibujar_reactor("pensando", 64).load()[centro, centro]
        verde = icono.dibujar_reactor("hablando", 64).load()[centro, centro]
        assert ambar[0] > ambar[1] > ambar[2], "el ámbar debería tirar a rojo"
        assert verde[1] > verde[0], "el verde debería dominar sobre el rojo"

    def test_la_pausa_deja_el_nucleo_hueco(self):
        # La señal que sobrevive en escala de grises y para quien no distingue
        # colores: en pausa el centro está vacío.
        hueco = icono.dibujar_reactor("pausado", 64).load()[32, 32][3]
        lleno = icono.dibujar_reactor("dormido", 64).load()[32, 32][3]
        assert lleno == 255
        assert hueco < 128

    def test_el_error_parte_el_anillo_exterior(self):
        # A las tres en punto, sobre el anillo exterior: roto en «error»,
        # entero en cualquier otro estado.
        roto = icono.dibujar_reactor("error", 64).load()[60, 32][3]
        entero = icono.dibujar_reactor("escuchando", 64).load()[60, 32][3]
        assert roto == 0
        assert entero > 128

    def test_estados_distintos_dan_iconos_distintos(self):
        vistos = {icono.dibujar_reactor(e, 32).tobytes() for e in icono.COLOR_ESTADO}
        assert len(vistos) == len(icono.COLOR_ESTADO)

    def test_repintar_el_mismo_estado_reutiliza_la_cache(self):
        # `state_changed` se emite varias veces por turno; sin caché cada una
        # serían cuatro elipses a 256×256 más una reducción.
        icono.dibujar_reactor.cache_clear()
        primera = icono.dibujar_reactor("pensando", 32)
        segunda = icono.dibujar_reactor("pensando", 32)
        assert primera is segunda
        assert icono.dibujar_reactor.cache_info().hits == 1

    def test_un_estado_desconocido_se_dibuja_igual(self):
        # Un estado nuevo en el núcleo no puede dejar la bandeja sin icono.
        assert icono.dibujar_reactor("estado_del_futuro", 32).size == (32, 32)


class TestPillow:
    def test_dice_que_puede_dibujar(self):
        assert icono.hay_pillow() is True
