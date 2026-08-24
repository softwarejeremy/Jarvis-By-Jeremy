# CLAUDE.md

Memoria e instrucciones de proyecto para trabajar en J.A.R.V.I.S. (Cositas-Skypie).
La arquitectura y el uso están en `README.md`; este archivo es para lo que el
código por sí solo no cuenta.

## Contexto del proyecto

- Asistente de voz personal para Jeremy (softwarejeremy), estilo J.A.R.V.I.S.,
  construido como extensión de Claude sobre el Claude Agent SDK — no un chatbot
  con micrófono, sino Claude con oídos, voz y permisos sobre el equipo.
- Rama de desarrollo: `claude/jarvis-voice-assistant-qclzfi`. El PR #1 en
  `softwarejeremy/Cositas-Skypie` ya existe desde el principio del proyecto y
  se actualiza solo con cada push a esa rama — **nunca crear un PR nuevo**.
- Jeremy prueba en su Windows real; esta sesión no tiene acceso a Windows,
  audio real, ni pantalla. Todo lo que reporta lo hace pegando literalmente
  la salida de PowerShell o adjuntando una captura de pantalla.

## Limitaciones del entorno de desarrollo (sandbox)

- **Sin PortAudio**: `sounddevice` falla al importar audio real. `MicStream` y
  `Player` reales no se pueden ejercitar aquí; para todo lo que no dependa de
  hardware se usan `--texto`, `--muda`, `FakeMicStream`, `ControlledMic`
  (`tests/conftest.py`).
- **Sin `$DISPLAY`**: `pynput` falla ya al *importarse* (no es sólo "no está
  instalado": lanza una excepción real). Por eso los tests del atajo global
  fuerzan la ausencia vía `sys.modules` en lugar de depender de esta
  casualidad del sandbox (`tests/test_hotkey.py`).
- **Sin Windows**: `pywin32`/SAPI no se pueden ejercitar de verdad.
  `crear_motor()` cae solo a `edge-tts`; esa caída sí está probada, forzada de
  la misma manera.
- **Chromium/Playwright preinstalados** (ver notas del entorno): sirven para
  levantar el HUD web de verdad y hacerle capturas cuando hace falta verificar
  visualmente un cambio de interfaz, en vez de fiarse sólo del código.
- `python -m jarvis --diag` es la herramienta que usa Jeremy para diagnosticar
  su propio equipo; cada sección va envuelta en `_seguro()` (`jarvis/diag.py`)
  para que un fallo no tumbe el resto del diagnóstico.

## Estado pendiente (no se ve leyendo el código)

- Jeremy aún no ha probado la voz completa de punta a punta en su equipo real
  (wake word, atajo, barge-in) con una `ANTHROPIC_API_KEY` configurada.
- La bandeja del sistema (icono, menú, pausa, arranque automático, instancia
  única) está implementada y probada con dobles, pero **nunca verificada
  visualmente en un Windows real** — depende de que Jeremy la pruebe y lo
  reporte.
- Puede haber una revisión periódica del PR #1 corriendo en segundo plano
  (rutina programada de esta sesión) que comprueba CI y comentarios cada
  hora aproximadamente y se reprograma sola y en silencio si no hay cambios.
  Es normal ver notificaciones de ese tipo sin que el usuario haya escrito nada.

## Reglas globales

- **Nunca crear un PR nuevo** para este proyecto: ya existe el PR #1.
- **Idioma**: todo en español — código, docstrings, comentarios, tests,
  mensajes de commit, y las respuestas al usuario.
- **Comentarios**: sólo explican el *porqué* (una decisión no obvia, una
  limitación real, un bug que motivó el código), nunca el *qué*. Por defecto,
  sin comentarios.
- **Tests nuevos**: antes de darlos por buenos, mutar a propósito el código
  que deberían cubrir y confirmar que fallan donde toca — luego restaurar el
  original. `pytest -q` + `ruff check jarvis tests` deben quedar en verde
  antes de cada commit.
- **Dependencias opcionales** (`pynput`, `pywin32`, `av`, `PIL`/`pystray`):
  import perezoso dentro de la función que las usa, nunca a nivel de módulo;
  degradan con `except Exception` a un `Null*` o al motor gratuito, nunca
  reventando el arranque.
- **Nunca depender de una casualidad del entorno** en un test (p. ej. "esta
  máquina no es Windows"): forzar la condición explícitamente vía
  `monkeypatch`/`sys.modules`, para que el test sea igual de fiable aquí, en
  la CI y en el Windows real de Jeremy.
- **Verificación visual**: cuando un cambio toca el HUD web o el icono de la
  bandeja, no basta con los tests — generar una imagen real (Playwright para
  el HUD, una hoja de contacto con Pillow para el icono) y mirarla.

## Comandos útiles

- `pytest -q` — suite completa (rápida, ronda los 20 s).
- `ruff check jarvis tests` — estilo y errores estáticos. No se corre
  `ruff format`: el proyecto alinea tablas y comentarios a mano a propósito.
- `python -m jarvis --diag` — pensado para correr en el Windows real de Jeremy.
