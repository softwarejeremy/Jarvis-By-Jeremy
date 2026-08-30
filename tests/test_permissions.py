"""Los permisos son lo único que separa a J.A.R.V.I.S. de poder destruir
archivos por un malentendido de voz. Estos tests son los más importantes del
proyecto."""

from __future__ import annotations

import pytest

from jarvis.core.permissions import (
    PermissionGuard,
    describir_para_voz,
    interpretar_respuesta,
)
from jarvis.events import EventBus


class TestInterpretarRespuesta:
    @pytest.mark.parametrize(
        "frase",
        ["sí", "si", "Sí.", "claro", "dale", "adelante", "vale", "ok",
         "sí, hazlo", "por supuesto", "está bien", "confirmo"],
    )
    def test_afirmaciones(self, frase):
        assert interpretar_respuesta(frase) is True

    @pytest.mark.parametrize(
        "frase",
        ["no", "No.", "negativo", "cancela", "mejor no", "no lo hagas",
         "para", "déjalo", "ni se te ocurra", "no, espera"],
    )
    def test_negaciones(self, frase):
        assert interpretar_respuesta(frase) is False

    @pytest.mark.parametrize("frase", ["", "no sé", "qué hora es", "mmm", "a ver"])
    def test_ambiguas_devuelven_none(self, frase):
        # Crítico: ante la duda hay que repreguntar, nunca adivinar.
        assert interpretar_respuesta(frase) is None

    @pytest.mark.parametrize(
        "frase",
        ["dame la mano", "eso lo conocemos", "somos nosotros", "el piano"],
    )
    def test_no_confunde_no_dentro_de_otra_palabra(self, frase):
        # El fallo clásico: `"no" in "mano"` es True y convertiría esto en
        # una negación. Estas frases no contestan nada.
        assert interpretar_respuesta(frase) is not False

    def test_la_primera_palabra_manda(self):
        assert interpretar_respuesta("no, claro que no") is False
        assert interpretar_respuesta("sí, dale") is True

    def test_negacion_gana_a_afirmacion_en_frase_hecha(self):
        assert interpretar_respuesta("bueno, mejor no") is False


class TestDescribirParaVoz:
    def test_bash_se_lee_literal(self):
        # El comando es justo el detalle que hay que oír para poder decidir.
        d = describir_para_voz("Bash", {"command": "rm -rf /tmp/cosas"})
        assert "rm -rf /tmp/cosas" in d

    def test_bash_largo_se_acorta(self):
        d = describir_para_voz("Bash", {"command": "echo " + "x" * 500})
        assert len(d) < 300
        assert "omitido por longitud" in d

    def test_write_dice_solo_el_nombre_del_archivo(self):
        d = describir_para_voz("Write", {"file_path": "C:/Users/TU-USUARIO/notas.txt"})
        assert "notas.txt" in d
        assert "Users" not in d, "no debe leer la ruta completa en voz alta"

    def test_edit_usa_el_verbo_correcto(self):
        assert "modificar" in describir_para_voz("Edit", {"file_path": "/a/b.py"})
        assert "sobrescribir" in describir_para_voz("Write", {"file_path": "/a/b.py"})


class TestPermissionGuard:
    @pytest.fixture
    def bus(self):
        return EventBus()

    async def test_las_herramientas_de_lectura_no_preguntan(self, settings, bus):
        async def nunca(_p: str) -> bool:
            raise AssertionError("no debería haber preguntado")

        guard = PermissionGuard(settings, nunca, bus)
        r = await guard("Read", {"file_path": str(settings.workspace / "x.txt")}, None)
        assert r.behavior == "allow"

    async def test_las_herramientas_propias_no_preguntan(self, settings, bus):
        async def nunca(_p: str) -> bool:
            raise AssertionError("no debería haber preguntado")

        guard = PermissionGuard(settings, nunca, bus)
        r = await guard("mcp__jarvis__recordar", {"hecho": "le gusta el café"}, None)
        assert r.behavior == "allow"

    async def test_escribir_fuera_del_workspace_se_deniega_sin_preguntar(
        self, settings, bus, tmp_path
    ):
        preguntas = []

        async def registrar(p: str) -> bool:
            preguntas.append(p)
            return True

        guard = PermissionGuard(settings, registrar, bus)
        fuera = tmp_path / "otro_sitio" / "victima.txt"
        r = await guard("Write", {"file_path": str(fuera)}, None)

        assert r.behavior == "deny"
        assert preguntas == [], "la barrera de rutas actúa antes de preguntar"
        assert "fuera de las carpetas autorizadas" in r.message

    async def test_escribir_dentro_del_workspace_pide_confirmacion(self, settings, bus):
        preguntas = []

        async def aceptar(p: str) -> bool:
            preguntas.append(p)
            return True

        guard = PermissionGuard(settings, aceptar, bus)
        dentro = settings.workspace / "notas.txt"
        r = await guard("Write", {"file_path": str(dentro)}, None)

        assert r.behavior == "allow"
        assert len(preguntas) == 1
        assert "notas.txt" in preguntas[0]

    async def test_un_no_deniega(self, settings, bus):
        async def rechazar(_p: str) -> bool:
            return False

        guard = PermissionGuard(settings, rechazar, bus)
        r = await guard("Bash", {"command": "rm -rf ."}, None)
        assert r.behavior == "deny"

    async def test_bash_siempre_pide_confirmacion(self, settings, bus):
        # Bash no lleva ruta, así que la barrera de rutas no aplica: la única
        # defensa es la confirmación hablada. No puede saltarse nunca.
        preguntas = []

        async def aceptar(p: str) -> bool:
            preguntas.append(p)
            return True

        guard = PermissionGuard(settings, aceptar, bus)
        r = await guard("Bash", {"command": "dir"}, None)
        assert r.behavior == "allow"
        assert len(preguntas) == 1

    async def test_rutas_extra_autorizadas_se_respetan(self, settings, bus, tmp_path):
        permitida = tmp_path / "documentos"
        permitida.mkdir()
        settings.permissions.writable_paths = [permitida]

        async def aceptar(_p: str) -> bool:
            return True

        guard = PermissionGuard(settings, aceptar, bus)
        r = await guard("Write", {"file_path": str(permitida / "a.txt")}, None)
        assert r.behavior == "allow"

    async def test_no_se_escapa_por_dos_puntos(self, settings, bus):
        # "workspace/../../etc/passwd" resuelve fuera: hay que detectarlo.
        async def aceptar(_p: str) -> bool:
            return True

        guard = PermissionGuard(settings, aceptar, bus)
        escape = settings.workspace / ".." / ".." / "passwd"
        r = await guard("Write", {"file_path": str(escape)}, None)
        assert r.behavior == "deny"

    async def test_emite_eventos_al_bus(self, settings, bus):
        eventos = []
        bus.on(eventos.append)

        async def aceptar(_p: str) -> bool:
            return True

        guard = PermissionGuard(settings, aceptar, bus)
        await guard("Bash", {"command": "ls"}, None)

        tipos = [e.type.value for e in eventos]
        assert "permission_request" in tipos
        assert "permission_result" in tipos

    async def test_herramienta_desconocida_pide_confirmacion(self, settings, bus):
        # Ante algo que no está en ninguna lista, lo seguro es preguntar.
        preguntas = []

        async def aceptar(p: str) -> bool:
            preguntas.append(p)
            return True

        guard = PermissionGuard(settings, aceptar, bus)
        r = await guard("HerramientaNuevaQueNoConozco", {}, None)
        assert r.behavior == "allow"
        assert len(preguntas) == 1


class TestDescribirHerramientasPropias:
    """Una pregunta que no se entiende no es una confirmación, es un trámite."""

    def test_abrir_dice_qué_va_a_abrir(self):
        d = describir_para_voz("mcp__jarvis__abrir", {"objetivo": "la calculadora"})
        assert "abrir la calculadora" in d
        assert "mcp__jarvis__" not in d, "el nombre técnico no se lee en voz alta"

    def test_abrir_sin_objetivo_no_queda_raro(self):
        assert "algo" in describir_para_voz("mcp__jarvis__abrir", {})

    def test_bloquear_se_explica_solo(self):
        d = describir_para_voz("mcp__jarvis__bloquear_pantalla", {})
        assert "bloquear la pantalla" in d
        assert "mcp__jarvis__" not in d

    def test_anadir_al_doc_dice_qué_documento(self):
        d = describir_para_voz("mcp__jarvis__anadir_al_doc", {"nombre": "Notas"})
        assert "Notas" in d
        assert "mcp__jarvis__" not in d

    def test_reemplazar_en_doc_dice_qué_documento(self):
        d = describir_para_voz("mcp__jarvis__reemplazar_en_doc", {"nombre": "Notas"})
        assert "Notas" in d
        assert "mcp__jarvis__" not in d

    def test_crear_doc_dice_qué_título(self):
        d = describir_para_voz("mcp__jarvis__crear_doc", {"titulo": "Plan de viaje"})
        assert "Plan de viaje" in d
        assert "mcp__jarvis__" not in d

    def test_crear_doc_sin_titulo_no_queda_raro(self):
        assert "uno nuevo" in describir_para_voz("mcp__jarvis__crear_doc", {})
