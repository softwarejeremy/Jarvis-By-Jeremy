"""Configuración de J.A.R.V.I.S.

Dos fuentes, por orden de prioridad (gana la primera):

1. Variables de entorno / `.env`  — para secretos y overrides puntuales.
   Se anidan con doble guion bajo:  ``JARVIS_TTS__ENGINE=sapi``
2. ``config.toml`` en la raíz del proyecto — para el resto de ajustes.

Los secretos (API keys) viven **sólo** en `.env`, que está en `.gitignore`.
"""

from __future__ import annotations

import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - sólo en Python 3.10
    import tomli as tomllib

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Carpeta de datos del usuario: memoria, sesiones, contador de gasto.
# Se puede mover con JARVIS_DATA_DIR.
DEFAULT_DATA_DIR = Path.home() / ".jarvis"

# El contenido de config.toml para la construcción de Settings en curso.
# pydantic-settings trata los kwargs del constructor como la fuente de
# MÁS prioridad — si `config.toml` se pasara así (`Settings(**toml)`), le
# ganaría al entorno, al revés de lo que promete este módulo. Colgarlo aquí
# y leerlo desde una fuente de prioridad más baja (ver `_FuenteToml`) es lo
# que hace que `JARVIS_AGENT__MODEL` gane de verdad sobre el archivo.
_toml_en_curso: ContextVar[dict[str, Any] | None] = ContextVar("_toml_en_curso", default=None)


class AgentSettings(BaseModel):
    """Cómo piensa J.A.R.V.I.S."""

    model: str = "claude-opus-5"
    # Para conversar por voz la latencia manda: "low" responde en ~1 s.
    # Súbelo a "high" sólo si le vas a pedir tareas de programación serias.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # Tope de gasto por sesión en USD. El propio SDK corta al alcanzarlo.
    max_budget_usd: float | None = 2.0
    # Cuánto se espera al PRIMER trozo de respuesta. Sólo al primero: una vez
    # que Claude arranca, un turno con herramientas puede durar minutos con
    # toda la razón. Lo que no puede es no empezar nunca. 0 lo desactiva.
    first_token_timeout_s: float = 60.0
    # Nombre con el que J.A.R.V.I.S. se dirige a ti.
    user_name: str = "señor"
    # Carpetas que puede leer además del directorio de trabajo.
    extra_dirs: list[Path] = Field(default_factory=list)


class TTSSettings(BaseModel):
    """Cómo suena J.A.R.V.I.S."""

    engine: Literal["edge", "sapi", "elevenlabs"] = "edge"
    # Voces recomendadas para edge-tts:
    #   es-MX-JorgeNeural   (masculina, mexicana, grave)   <- por defecto
    #   es-ES-AlvaroNeural  (masculina, España)
    #   es-MX-DaliaNeural   (femenina, mexicana)
    voice: str = "es-MX-JorgeNeural"
    # Ajustes de prosodia de edge-tts. "+0%" es la velocidad natural;
    # J.A.R.V.I.S. suena mejor un pelín acelerado y algo más grave.
    rate: str = "+8%"
    pitch: str = "-4Hz"
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"


class UiSettings(BaseModel):
    """El HUD web y cómo se abre."""

    # "chrome" lo intenta primero y cae al navegador por defecto si no está;
    # "sistema" va directo al de por defecto (Edge, normalmente en Windows).
    navegador: Literal["chrome", "sistema"] = "chrome"


class STTSettings(BaseModel):
    """Cómo escucha J.A.R.V.I.S."""

    # tiny < base < small < medium < large-v3   (a más grande, mejor y más lento)
    model_size: str = "small"
    device: Literal["cpu", "cuda", "auto"] = "auto"
    # int8 en CPU, float16 en GPU. "auto" lo decide según `device`.
    compute_type: str = "auto"
    language: str = "es"
    # Sesga el reconocimiento hacia el vocabulario que de verdad vas a usar.
    initial_prompt: str = "Conversación en español con el asistente Jarvis."
    # Tope para transcribir una frase. Generoso: en un equipo lento con el
    # modelo `medium` una frase larga puede pasar de diez segundos. Si se
    # agota, J.A.R.V.I.S. lo dice y vuelve a estar listo, en vez de colgarse.
    timeout_s: float = 45.0


class VADSettings(BaseModel):
    """Detección de voz: cuándo empiezas y cuándo terminas de hablar."""

    threshold: float = 0.5
    # Cuánto silencio esperar antes de dar por terminada tu frase.
    # Bájalo si sientes que tarda; súbelo si te corta a media frase.
    silence_ms: int = 700
    # Ignora ruidos sueltos más cortos que esto.
    min_speech_ms: int = 250
    # Corta la escucha si te extiendes demasiado (evita colgarse).
    max_utterance_s: float = 30.0
    # Margen de audio que se conserva antes del inicio detectado, para no
    # comerse la primera sílaba.
    pre_roll_ms: int = 300


class WakeWordSettings(BaseModel):
    """La palabra mágica."""

    enabled: bool = True
    # Modelo incluido en openWakeWord. Se activa diciendo "Hey Jarvis".
    model_name: str = "hey_jarvis"
    # Sube a 0.7 si se activa solo; baja a 0.4 si te cuesta despertarlo.
    threshold: float = 0.5
    # Segundos de bloqueo tras una activación, para no dispararse dos veces.
    refractory_s: float = 2.0


class HotkeySettings(BaseModel):
    """Push-to-talk: hablar sin decir la palabra clave."""

    enabled: bool = True
    # Sintaxis de pynput. Ejemplos: "<ctrl>+<alt>+j", "<f9>", "<cmd>+<shift>+j"
    combo: str = "<ctrl>+<alt>+j"


class AudioSettings(BaseModel):
    """Parámetros del hardware de sonido."""

    sample_rate: int = 16_000  # requerido por Whisper, Silero y openWakeWord
    block_ms: int = 32  # 512 muestras a 16 kHz = 1 frame de Silero
    input_device: int | str | None = None  # None = dispositivo por defecto
    output_device: int | str | None = None
    # Permite interrumpir a J.A.R.V.I.S. hablándole encima.
    barge_in: bool = True
    # Ningún estado debería durar minutos. Si uno se atasca, el vigilante lo
    # devuelve a reposo: es preferible perder un turno a quedarse muerto sin
    # que el usuario sepa por qué. 0 lo desactiva.
    watchdog_s: float = 180.0


class PermissionSettings(BaseModel):
    """Qué puede hacer J.A.R.V.I.S. en tu PC sin preguntarte."""

    # Herramientas que se ejecutan sin molestarte (sólo lectura y consulta).
    auto_allow: list[str] = Field(
        default_factory=lambda: [
            "Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite", "Task",
        ]
    )
    # Herramientas que exigen un "sí" hablado antes de ejecutarse.
    confirm: list[str] = Field(
        default_factory=lambda: ["Write", "Edit", "NotebookEdit", "Bash", "KillShell"]
    )
    # Rutas que puede tocar. Vacío = sólo el directorio de trabajo.
    # En Windows escríbelas así:  ["C:/Users/TU-USUARIO/Documents"]
    writable_paths: list[Path] = Field(default_factory=list)
    # Segundos que espera tu confirmación hablada antes de denegar por defecto.
    confirm_timeout_s: float = 12.0


class _FuenteToml(PydanticBaseSettingsSource):
    """El contenido de `config.toml`, con menos prioridad que el entorno."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return _toml_en_curso.get() or {}


class Settings(BaseSettings):
    """Configuración completa. Se construye con :func:`load_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="JARVIS_",
        env_nested_delimiter="__",
        # Ruta absoluta, no ".env" a secas: relativo se resuelve contra el
        # directorio actual, así que arrancar desde otra carpeta dejaba a
        # J.A.R.V.I.S. sin ANTHROPIC_API_KEY —y por tanto en modo
        # demostración, contestando frases de ejemplo— sin decir por qué.
        env_file=(PROJECT_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Orden de mayor a menor prioridad: los kwargs explícitos del
        # constructor (los que usan los tests) siguen mandando; luego el
        # entorno y `.env`; `config.toml` va DESPUÉS para que nunca les gane.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _FuenteToml(settings_cls),
            file_secret_settings,
        )

    # Secretos: se leen del entorno con su nombre estándar, sin prefijo.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")

    data_dir: Path = DEFAULT_DATA_DIR
    workspace: Path = PROJECT_ROOT

    agent: AgentSettings = Field(default_factory=AgentSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)
    stt: STTSettings = Field(default_factory=STTSettings)
    vad: VADSettings = Field(default_factory=VADSettings)
    wakeword: WakeWordSettings = Field(default_factory=WakeWordSettings)
    hotkey: HotkeySettings = Field(default_factory=HotkeySettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    permissions: PermissionSettings = Field(default_factory=PermissionSettings)

    @property
    def memory_dir(self) -> Path:
        return self.data_dir / "memory"

    @property
    def has_api_key(self) -> bool:
        return bool(self.anthropic_api_key.strip())

    def ensure_dirs(self) -> None:
        """Crea las carpetas de datos si no existen."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_settings(config_path: Path | None = None) -> Settings:
    """Carga la configuración mezclando ``config.toml`` con el entorno.

    El entorno siempre gana, para que puedas sobrescribir cualquier ajuste
    sin editar archivos.
    """
    path = config_path or (PROJECT_ROOT / "config.toml")
    token = _toml_en_curso.set(_read_toml(path))
    try:
        settings = Settings()
    finally:
        _toml_en_curso.reset(token)
    settings.ensure_dirs()
    return settings
