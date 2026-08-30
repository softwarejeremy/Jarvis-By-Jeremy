# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

J.A.R.V.I.S. (Cositas-Skypie): asistente de voz personal construido sobre el
Claude Agent SDK. El uso de cara al usuario está en `README.md`; este archivo
es para lo que hace falta para desarrollar aquí — comandos, arquitectura, y lo
que el código por sí solo no cuenta.

## Comandos

```bash
pip install -e ".[voice,dev,web,bandeja]"   # entorno de desarrollo completo
pytest -q                                    # suite completa
pytest tests/test_core.py -v                 # un archivo
pytest tests/test_core.py::TestNoSeQuedaColgado::test_el_vigilante_rescata_un_estado_atascado  # un test suelto
ruff check jarvis tests                      # único lint (sin ruff format, ver Reglas globales)
python -m jarvis --diag                      # diagnóstico (pensado para Windows real)
python -m jarvis --texto --demo              # probar el flujo sin clave ni audio
python -m jarvis --web --sin-navegador       # HUD web sin abrir pestaña
```

La CI (`.github/workflows/ci.yml`) instala sólo
`pip install -e ".[dev,web]" numpy faster-whisper onnxruntime` — no `voice`
completo, ni `elevenlabs`, ni `windows` — y corre `ruff check` + `pytest -q`
en Python 3.10, 3.11 y 3.12. Cualquier cambio tiene que pasar en verde con ese
subconjunto de extras, aunque en local se use `voice` entero.

## Arquitectura

El núcleo (`jarvis/core/core.py`, una máquina de estados) sólo publica eventos
a través de `EventBus` (`jarvis/events.py`); no sabe si lo está mirando una
terminal, el HUD web o un test. Por eso se pudo añadir la interfaz web y la
bandeja del sistema sin tocar la lógica central, y por eso los tests recorren
el ciclo completo sin micrófono real (dobles en `tests/conftest.py`).

Piezas que sólo se entienden leyendo más de un archivo a la vez:

- **Permisos** (`jarvis/core/permissions.py`): tres barreras en cascada —
  rutas permitidas (`writable_paths`) deniegan sin preguntar → confirmación
  hablada → el silencio deniega por timeout.
- **TTS** (`jarvis/audio/tts/{base,edge,sapi,elevenlabs}.py`): `crear_motor()`
  decide el motor por `settings.tts.engine`, con caída silenciosa a
  `edge-tts` si falta la key de ElevenLabs o `pywin32` en Windows.
- **Pausa** (`jarvis/core/core.py`): `PAUSADO` se guarda como bool aparte
  (`self._pausado`), no sólo como `State` — cualquier "vuelta a reposo" pasa
  por `_a_reposo()` en vez de fijar `State.DORMIDO` a pelo, para no
  des-pausar el micrófono sin querer desde otro camino del código.
- **Instancia única** (`jarvis/instancia.py`): cerrojo por socket TCP
  (127.0.0.1:8764), no PID file — en Windows no hay forma segura de
  comprobar si un PID sigue vivo sin arriesgarse a matarlo.
- **Bandeja** (`jarvis/ui/bandeja.py`): tres hilos separados a propósito
  (loop de asyncio / bucle de mensajes de pystray / executor para repintar),
  cruzados con `call_soon_threadsafe` + `create_task`, nunca bloqueando con
  `.result()` — así ni el frame de audio de 32 ms ni el bucle de pystray se
  quedan esperando al otro.
- **Arranque y cierre** (`jarvis/main.py`): `_correr_hasta_el_final()`
  sustituye a `asyncio.run()` porque la limpieza final de tareas del propio
  `asyncio.run()` no está acotada en tiempo — hace falta un límite en dos
  capas distintas (el `finally` de `_arrancar_todo` y el cierre del loop),
  no sólo una, o un Ctrl+C puede dejar la terminal colgada.
- **El truco del `contenedor`** (`jarvis/main.py:_construir()`): el guardián
  de permisos y las herramientas propias necesitan poder hablar con el
  núcleo (`confirmar`, `avisar`) antes de que `JarvisCore` exista. Un
  `contenedor: dict[str, JarvisCore] = {}` se rellena con
  `contenedor["core"] = core` sólo después de construirlo, y los closures
  leen `contenedor.get("core")` en el momento de la llamada, no de la
  construcción — así se rompe la dependencia circular. Cualquier nueva
  herramienta que necesite hablar sola más tarde (como los temporizadores)
  reutiliza este mismo patrón en vez de inventar uno nuevo.
- **Servidor MCP único** (`jarvis/tools/memory_tool.py:construir_servidor_jarvis`):
  memoria, sistema y temporizadores se registran todas en un solo
  `create_sdk_mcp_server(name="jarvis", ...)` — dos servidores con el mismo
  nombre se pisarían, y nombres distintos obligarían a mantener dos listas
  de permisos para nada. Toda herramienta propia nueva se añade a la lista
  `tools` de esa misma función, y su nombre a `PROPIAS_AUTOMATICAS`
  (`jarvis/core/permissions.py`) si no necesita confirmación hablada.
- **Temporizadores** (`jarvis/tools/temporizadores.py`): a diferencia del
  resto de herramientas (que devuelven texto y ya), `poner_temporizador`
  lanza un `asyncio.create_task` que espera y luego llama a `avisar()` — no
  bloquea el turno. Decisión deliberada: no sobreviven un reinicio (viven
  sólo como tarea en memoria) y no hay "cancelar temporizador" — ninguna de
  las dos se pidió y ambas añadirían estado a rastrear sin necesidad real
  todavía.

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
- **"Capacidades reales" (completo, CI verde)**: las tres piezas del plan
  aprobado están implementadas — estado del equipo (`jarvis/tools/sistema.py:
  estado_del_equipo`, usa `psutil`), control de medios (`sistema.py:
  controlar_medios`, mismo patrón por SO que el volumen) y temporizadores/
  alarmas (`jarvis/tools/temporizadores.py`, ver Arquitectura). Cada una con
  su commit separado, mutación de tests confirmada y README actualizado.
- **Lo que sigue, sin empezar todavía** (orden de prioridad de Jeremy, a la
  espera de que él confirme pasar a la siguiente fase — no arrancar solo):
  1. **Memoria que se llena sola**: hoy `recordar`/`olvidar` dependen de que
     Claude decida guardar algo explícitamente; falta que el propio núcleo
     proponga o detecte datos duraderos sin que el usuario lo pida.
  2. **Verificar la bandeja en Windows**: confirmar visualmente con Jeremy
     que el icono, el menú y la pausa funcionan de verdad en su equipo —
     esto no se puede probar desde el sandbox (ver limitaciones abajo).

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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
