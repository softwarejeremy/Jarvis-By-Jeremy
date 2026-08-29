"""Las opciones con las que se lanza el CLI de Claude Code.

`_build_options` es puro cableado, y por eso mismo se rompe en silencio: si
un ajuste no llega al SDK, aquí no falla nada —falla en mitad de una
conversación, o no falla y simplemente no hace lo que se pedía—. El caso que
motivó estos tests es el `stderr`: sin él, un CLI que arranca pero no
contesta no deja una sola línea que leer.
"""

from __future__ import annotations

from jarvis.core.agent import Agent


class TestOpcionesDelCli:
    def test_lleva_el_callback_de_stderr(self, settings):
        recogidas: list[str] = []

        opciones = Agent(settings, stderr=recogidas.append)._build_options()

        assert opciones.stderr is not None
        opciones.stderr("Error: unknown option")
        assert recogidas == ["Error: unknown option"]

    def test_sin_stderr_no_pone_nada(self, settings):
        assert Agent(settings)._build_options().stderr is None

    def test_lleva_el_modelo_y_el_tope_de_gasto(self, settings):
        settings.agent.model = "claude-sonnet-5"
        settings.agent.max_budget_usd = 3.5

        opciones = Agent(settings)._build_options()

        assert opciones.model == "claude-sonnet-5"
        assert opciones.max_budget_usd == 3.5

    def test_pasa_la_clave_por_entorno(self, settings):
        # Al subproceso se le da por entorno, no por línea de órdenes: así no
        # aparece en la lista de procesos del equipo.
        settings.anthropic_api_key = "sk-ant-de-prueba"

        opciones = Agent(settings)._build_options()

        assert opciones.env["ANTHROPIC_API_KEY"] == "sk-ant-de-prueba"

    def test_sin_clave_no_inventa_una_vacia(self, settings):
        # Una cadena vacía taparía las credenciales que ya tenga la máquina.
        settings.anthropic_api_key = ""

        opciones = Agent(settings)._build_options()

        assert "ANTHROPIC_API_KEY" not in opciones.env

    def test_no_hereda_la_configuracion_del_equipo(self, settings):
        # J.A.R.V.I.S. debe comportarse igual en cualquier máquina.
        assert Agent(settings)._build_options().setting_sources == []

    def test_pide_los_mensajes_parciales(self, settings):
        # Sin esto no hay texto token a token, y no se puede empezar a hablar
        # antes de que Claude termine de escribir.
        assert Agent(settings)._build_options().include_partial_messages is True
