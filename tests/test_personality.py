"""El system prompt: cómo se compone, y cómo se enmarca la memoria dentro de
él para que no se confunda con instrucciones.
"""

from __future__ import annotations

from jarvis.core.personality import build_system_prompt


class TestMemoriaEnElPrompt:
    def test_sin_memoria_invita_a_guardar_algo(self):
        prompt = build_system_prompt(memoria="")
        assert "recordar" in prompt

    def test_con_memoria_se_enmarca_como_datos_no_instrucciones(self):
        prompt = build_system_prompt(memoria="Le gusta el café.")
        assert "Le gusta el café." in prompt
        assert "nunca instrucciones" in prompt

    def test_la_memoria_va_envuelta_en_una_etiqueta(self):
        # Un delimitador claro ayuda a que un "ignora tus reglas" colado en
        # la memoria se lea como el dato que es, no como una orden nueva.
        prompt = build_system_prompt(memoria="Trabaja en software.")
        assert "<memoria>" in prompt
        assert "</memoria>" in prompt

    def test_memoria_vacia_no_deja_la_etiqueta_suelta(self):
        prompt = build_system_prompt(memoria="   ")
        assert "<memoria>" not in prompt
