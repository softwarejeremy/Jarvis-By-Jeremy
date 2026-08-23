# J.A.R.V.I.S.

[![CI](https://github.com/softwarejeremy/Cositas-Skypie/actions/workflows/ci.yml/badge.svg)](https://github.com/softwarejeremy/Cositas-Skypie/actions/workflows/ci.yml)

Un asistente personal por voz construido sobre el **Claude Agent SDK**. No es un
chatbot con micrófono: es el mismo motor agéntico que usa Claude Code —con acceso a
tus archivos, a la terminal y a la web— envuelto en una capa de voz, personalidad y
permisos.

Le dices *"Hey Jarvis"*, le hablas, y te contesta. Si le pides algo, lo hace. Y antes
de tocar nada importante, te pregunta en voz alta.

```
     ██  █████  ██████  ██    ██ ██ ███████
     ██ ██   ██ ██   ██ ██    ██ ██ ██
     ██ ███████ ██████  ██    ██ ██ ███████
██   ██ ██   ██ ██   ██  ██  ██  ██      ██
 █████  ██   ██ ██   ██   ████   ██ ███████
```

---

## Qué sabe hacer

| | |
|---|---|
| **Conversar** | Charla natural en español, con memoria del hilo y personalidad propia |
| **Controlar tu PC** | Leer y escribir archivos, ejecutar comandos, buscar en tus carpetas |
| **Buscar en la web** | Información actual, no sólo lo que sabe de memoria |
| **Recordar** | Sabe quién eres y en qué andas, también mañana |
| **Pedir permiso** | Antes de escribir, borrar o ejecutar, te lo dice y espera tu "sí" |

---

## Instalación en Windows

### 1. Requisitos previos

**Python 3.11** — [python.org/downloads](https://www.python.org/downloads/).
Marca **"Add Python to PATH"** durante la instalación.

**Node.js y el CLI de Claude Code.** El Agent SDK arranca el CLI de Claude Code por
debajo; sin él no hay cerebro. Instala [Node.js](https://nodejs.org/) y luego:

```powershell
npm install -g @anthropic-ai/claude-code
```

### 2. Permitir scripts en PowerShell

Windows viene con la ejecución de scripts desactivada, así que activar el entorno virtual
falla con *"No se puede cargar el archivo ... Activate.ps1 porque la ejecución de scripts
está deshabilitada en este sistema"*. Le pasa a todo el mundo la primera vez. Ejecuta una
sola vez:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Permite ejecutar los scripts que creas en tu equipo, pero sigue exigiendo firma digital a
los descargados de internet. `-Scope CurrentUser` significa que sólo afecta a tu usuario y
**no hace falta ser administrador**.

Si prefieres no cambiar la configuración, sáltate la activación llamando al Python del
entorno por su ruta: `.venv\Scripts\python.exe -m pip install ...`, y lo mismo para
ejecutarlo. No uses `activate.bat` desde PowerShell: no da error, pero la activación se
pierde y acabarías instalando en el Python global sin darte cuenta.

### 3. El proyecto

```powershell
git clone https://github.com/softwarejeremy/Cositas-Skypie.git
cd Cositas-Skypie

python -m venv .venv
.venv\Scripts\activate

pip install -e ".[voice,windows,web,bandeja]"
```

La primera vez se descargarán el modelo de transcripción (~500 MB para `small`) y el
del wake word (~2 MB). Sólo ocurre una vez.

### 4. Tu clave de Anthropic

```powershell
copy .env.example .env
notepad .env
```

Pega tu clave en `ANTHROPIC_API_KEY`. Se saca en
[console.anthropic.com](https://console.anthropic.com) → *Settings* → *API Keys*, y hay
que cargar saldo en *Billing* (desde 5 USD).

`.env` está en `.gitignore`: tu clave nunca se sube a GitHub.

### 5. Comprueba que todo está en su sitio

```powershell
python -m jarvis --diag
```

Revisa cada eslabón por separado —Python, el CLI, las dependencias, los dispositivos de
audio, la voz, la transcripción y el micrófono— y te dice exactamente cuál falla.
**Arregla cualquier ✗ antes de seguir.**

### 6. En marcha

```powershell
python -m jarvis
```

Di **"Hey Jarvis"** y habla. O pulsa **Ctrl+Alt+J**.

---

## Modos de ejecución

```powershell
python -m jarvis                  # voz completa: wake word + atajo
python -m jarvis --texto          # escribes tú, él contesta con voz
python -m jarvis --demo           # sin clave ni gasto: respuestas simuladas
python -m jarvis --muda           # sin audio de salida, sólo texto
python -m jarvis --sim audio.wav  # inyecta un WAV en vez del micrófono
python -m jarvis --web            # además, el HUD en el navegador (se abre solo)
python -m jarvis --web --https    # y poder hablarle desde el móvil
python -m jarvis --diag           # diagnóstico
```

Otras banderas útiles: `--sin-navegador` (con `--web`, no abrir la pestaña solo) y
`--sin-bandeja` (no poner el icono en la bandeja del sistema).

**Empieza por `--demo --texto`.** Funciona sin clave y sin gastar nada, y te deja ver
el flujo completo antes de configurar la API.

---

## El HUD en el navegador

```powershell
python -m jarvis --web
```

Se abre solo en `http://localhost:8765` en cuanto el servidor está listo (si prefieres
que no, añade `--sin-navegador`). Verás el reactor cambiando de color según lo que esté
haciendo, la conversación apareciendo palabra a palabra, las herramientas que usa y el
gasto acumulado.

Es el **mismo núcleo**: lo que digas por voz sale en la pantalla, y lo que escribas
en la pantalla lo contesta por voz. No son dos programas.

**Desde el móvil**: al arrancar con `--web`, J.A.R.V.I.S. te imprime las dos
direcciones, ya calculadas:

```
HUD aquí:       http://localhost:8765
desde el móvil: http://192.168.1.37:8765  (misma red wifi)
```

Escribe esa segunda en el navegador del móvil, con el PC encendido y ambos en la
misma wifi. La primera vez Windows preguntará si permites la conexión: hay que
decir que sí **para la red privada**. Si no aparece la segunda línea, es que el
equipo no está en ninguna red local.

### Hablarle desde el móvil

```powershell
python -m jarvis --web --https
```

Con `--https`, el botón se convierte en **mantén pulsado para hablar**: grabas con
el micrófono del propio teléfono y J.A.R.V.I.S. te contesta.

El `--https` no es un capricho. Los navegadores sólo dan acceso al micrófono en
*contexto seguro* —HTTPS o `localhost`—, y sobre `http://192.168.1.x`
`navigator.mediaDevices` sencillamente no existe. Sin TLS no hay micrófono, y punto.

El certificado se genera solo la primera vez y se guarda; **es autofirmado**, así que
el navegador avisará de que el sitio no es de confianza. Acéptalo: es tu propio
equipo, en tu propia red. Sólo hay que hacerlo una vez por dispositivo.

Si cambias de wifi y el equipo recibe otra IP, el certificado se rehace solo para
incluirla.

Si no hay micrófono por ningún lado, el botón aparece desactivado explicando por qué,
y siempre queda la entrada por texto. Con `--web --texto` el teclado del navegador es
la única entrada, sin tocar ningún micrófono.

### Pausar el micrófono

El botón **Pausar micrófono** (también disponible desde la bandeja del sistema, ver
más abajo) hace que J.A.R.V.I.S. deje de atender al «Hey Jarvis» sin cerrar el
programa: útil si tienes visita y no quieres que te escuche. En pausa, ni el atajo de
teclado ni el botón de escuchar hacen nada —avisan de que está en pausa en vez de
callarse sin más—, y sigue así hasta que le des a **Reanudar micrófono**.

---

## Cuánto cuesta

Se paga por uso, no por mensualidad. Estas cifras están **medidas en este proyecto**,
no estimadas:

| | Coste |
|---|---|
| Primer turno de una sesión (caché fría) | ~$0.055 |
| Cada turno siguiente | **~$0.017** |
| Un día de uso intenso (~100 turnos) | **~$1.70** |

Hay un tope de gasto por sesión (`max_budget_usd`, por defecto $2) que corta
automáticamente, y el coste acumulado se muestra en pantalla en cada turno.

Si quieres gastar menos, `model = "claude-sonnet-5"` en `config.toml` cuesta poco más de
la mitad y para conversar va sobrado.

---

## Configuración

```powershell
copy config.example.toml config.toml
```

Todo tiene valores por defecto razonables. Los ajustes que más se tocan:

| Si te pasa esto | Cambia esto |
|---|---|
| Tarda en contestar cuando terminas de hablar | `vad.silence_ms` más bajo (p. ej. 500) |
| Te corta a media frase | `vad.silence_ms` más alto (p. ej. 900) |
| Se despierta solo | `wakeword.threshold` a 0.7 |
| No te oye al decir "Hey Jarvis" | `wakeword.threshold` a 0.4 |
| Va lento transcribiendo | `stt.model_size = "tiny"` |
| Se interrumpe a sí mismo | `audio.barge_in = false` |
| Quieres otra voz | `tts.voice` (hay una lista en el archivo) |

---

## Qué puede hacer con tu equipo

Además de leer y escribir archivos y buscar en la web, tiene herramientas propias:

| Le dices | Hace | ¿Pregunta? |
|---|---|---|
| «sube el volumen», «silencio» | Volumen del sistema | No |
| «qué hora es» | Fecha y hora | No |
| «abre Spotify», «abre mi carpeta de proyectos» | Lanza programas, archivos o webs | **Sí** |
| «bloquea la pantalla» | Bloquea la sesión | **Sí** |
| «recuerda que…», «olvida lo de…» | Memoria de largo plazo | No |

El criterio de qué pregunta y qué no es simple: se ejecuta solo lo que no puede
romper nada y cuyo efecto es evidente e inmediatamente reversible — oír el volumen
subir es su propia confirmación. Lanzar programas no cumple eso, así que pregunta.

**No abre intérpretes de comandos** (`cmd`, `powershell`, `bash`…) aunque se lo
autorices. Todo comando de shell se lee en voz alta antes de ejecutarse; si pudiera
«abrir» una consola, bastaría eso para ejecutar cualquier cosa sin que nadie la
enunciara, y el sistema de permisos entero quedaría sin efecto.

---

## Permisos: cómo evita romperte cosas

El reconocimiento de voz se equivoca, y un *"borra eso"* mal entendido no tiene deshacer.
Hay tres barreras, de más fuerte a más débil:

1. **Rutas.** Escribir fuera de las carpetas autorizadas se deniega sin preguntar
   siquiera. Se configuran en `permissions.writable_paths`.
2. **Confirmación hablada.** Escribir, editar o ejecutar exige un "sí" tuyo. Los
   comandos de shell se leen **enteros y literales** antes de pedirte permiso.
3. **El silencio deniega.** Si no contestas en 12 segundos, la respuesta es no. Si tu
   respuesta es ambigua ("no sé"), vuelve a preguntar; a la segunda, deniega.

Leer archivos, buscar en la web y consultar su memoria no piden permiso: no pueden
romper nada.

---

## El icono de la bandeja

Sin ventana de consola, la cara de J.A.R.V.I.S. es un pequeño reactor en el área de
notificación (junto al reloj), que cambia de color con el estado — el mismo color que
el HUD del navegador. Al arrancar suelta un aviso ("J.A.R.V.I.S. en línea") con la URL
del HUD, y el clic derecho abre un menú con:

- **Abrir el HUD** (sólo si arrancó con `--web`)
- **Escuchar ahora** / **Silenciar**
- **Micrófono en pausa** — el mismo interruptor que el botón del HUD
- **Arrancar con Windows** — instala o quita el arranque automático sin tocar la terminal
- **Salir**

Si por lo que sea no aparece (falta el extra `bandeja`, o el sistema no tiene bandeja de
notificación), J.A.R.V.I.S. sigue funcionando igual: se pierde el icono, no el asistente.
Se puede desactivar a propósito con `--sin-bandeja`.

---

## Arrancar solo con Windows

```powershell
python -m jarvis --arrancar-con-windows --web --https
```

Deja un pequeño script en tu carpeta de Inicio (`shell:startup`) que lanza
J.A.R.V.I.S. al iniciar sesión, **sin ventana de consola** — usa `pythonw.exe`,
que no la abre, y lo lanza en modo oculto. Las banderas que pongas después de
`--arrancar-con-windows` son las que arrancarán cada mañana: si aquí pones
`--web --https`, eso es lo que se inicia solo. El icono de la bandeja se
encarga de avisar de que está vivo; por eso, al guardar la orden para cada
mañana se añade `--sin-navegador` en automático — abrir una pestaña sola cada
inicio de sesión sería intrusivo.

No toca el registro ni el Programador de tareas — es sólo un archivo en tu
carpeta de usuario, sin permisos de administrador, y se desactiva borrándolo a
mano, desde el menú del icono de la bandeja, o con:

```powershell
python -m jarvis --quitar-del-inicio
```

Bórralo desde `shell:startup` si prefieres hacerlo sin la terminal.

**Si ya había uno arrancado** (por ejemplo, el que puso en marcha el sistema) y lo
vuelves a lanzar a mano, J.A.R.V.I.S. lo detecta, te abre el HUD del que ya está
vivo y se cierra él solo — no compiten dos instancias por el micrófono ni por el
puerto del HUD.

---

## Cómo está construido

```
jarvis/
├── config.py         Configuración: .env (secretos) + config.toml
├── events.py         Bus de eventos y estados
├── text.py           Troceo en frases para el TTS incremental
├── core/
│   ├── core.py       La máquina de estados
│   ├── agent.py      Envoltura del Claude Agent SDK
│   ├── personality.py El carácter de J.A.R.V.I.S.  ← edítalo a tu gusto
│   ├── permissions.py Las tres barreras
│   └── memory.py     Memoria persistente en Markdown
├── audio/
│   ├── capture.py    Micrófono siempre abierto, con búfer de contexto
│   ├── wakeword.py   "Hey Jarvis" (openWakeWord, local)
│   ├── vad.py        Silero: cuándo empiezas y acabas de hablar
│   ├── stt.py        faster-whisper, en local
│   ├── player.py     Reproducción interrumpible
│   └── tts/          edge · sapi · elevenlabs
├── hotkey.py         Push-to-talk global
├── instancia.py      Un solo J.A.R.V.I.S. por equipo (cerrojo por socket)
├── inicio.py         Arranque automático con Windows
├── tools/            Memoria y control del equipo, expuestos a Claude vía MCP
├── server/           HUD web: FastAPI + WebSocket, micrófono del navegador y TLS
└── ui/
    ├── console.py    HUD de terminal
    ├── bandeja.py    Icono en el área de notificación
    ├── icono.py      El reactor en miniatura, dibujado por código
    └── navegador.py  Abre el HUD cuando el servidor ya escucha
```

**El principio de diseño:** el núcleo no sabe si lo está mirando una terminal, un
navegador o un test. Sólo publica eventos. Por eso se puede añadir una interfaz web sin
tocar una línea de la lógica, y por eso los tests pueden recorrer el ciclo completo sin
micrófono.

**La decisión que más se nota:** J.A.R.V.I.S. empieza a hablar en cuanto Claude termina
la **primera frase**, no la respuesta entera. Eso recorta cerca de un segundo de silencio
incómodo en cada respuesta.

### La personalidad

Está toda en `jarvis/core/personality.py`, en castellano y sin código de por medio. Si
lo quieres más seco, más simpático o que te trate de tú, es el único archivo que hay
que tocar.

---

## Desarrollo

```bash
pip install -e ".[voice,dev,web,bandeja]"
pytest              # 313 tests, sin necesidad de micrófono
ruff check jarvis tests
```

En cada push, GitHub Actions corre lo mismo sobre Python 3.10, 3.11 y 3.12.

Los tests cubren la máquina de estados completa, las tres barreras de permisos, el
troceo en frases, el VAD, la memoria, la selección de dispositivo de transcripción
y el servidor web. Todo lo que toca hardware tiene un doble en
`tests/conftest.py`.

---

## Problemas frecuentes

**"No se puede cargar el archivo Activate.ps1 ... la ejecución de scripts está
deshabilitada"** → Es la política de PowerShell, no un fallo del proyecto. Mira el paso 2
de la instalación: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.

**"No encuentro el CLI de Claude Code"** → `npm install -g @anthropic-ai/claude-code`

**No se oye nada** → `python -m jarvis --diag` lista los dispositivos; pon el número
correcto en `audio.output_device`.

**No me oye** → El diagnóstico incluye una prueba de micrófono con medidor de nivel. Si
el pico se queda por debajo de 0.01, el micrófono está silenciado o Windows está usando
otro.

**El diagnóstico dice "Saturación"** → Tu micrófono graba demasiado alto y la señal
recorta, lo que empeora bastante la transcripción. Ajustes de sonido de Windows → tu
micrófono → baja el **volumen de entrada** a ~70 y desactiva el **refuerzo de micrófono**.
Repite `--diag` hasta que el pico quede entre 0.3 y 0.8.

**"Requested float16 compute type, but the target device..."** → El modelo intentó usar la
GPU y no pudo. Desde la versión actual esto se detecta solo y cae a CPU, avisándote. Si
aun así aparece, fuerza CPU poniendo `device = "cpu"` en la sección `[stt]` de
`config.toml`.

**Tengo GPU NVIDIA pero va en CPU** → Falta cuDNN 9 para CUDA 12, que faster-whisper
necesita y no se instala con pip. Funciona igual en CPU, sólo más despacio; instalándolo
ganarías velocidad y podrías subir a `stt.model_size = "medium"`, más preciso en español.

**Se queda en «transcribiendo» y no sale** → Casi siempre es la primera vez que
transcribe y está **descargando el modelo** (unos 500 MB), que se ve igual que un
cuelgue. Ejecuta `python -m jarvis --diag` una vez para bajarlo con calma. Desde la
versión actual hay un tope de tiempo (`stt.timeout_s`, 45 s) y un vigilante que
devuelve a J.A.R.V.I.S. a reposo si algún estado se atasca, avisando de lo ocurrido.

**Se interrumpe solo mientras habla** → Está oyendo su propia voz por los altavoces.
Usa auriculares, o pon `audio.barge_in = false`.

**El atajo de teclado no funciona** → En Windows debería ir sin más. Si otra aplicación
ya usa Ctrl+Alt+J, cambia `hotkey.combo`.

---

## Estado del proyecto

Implementado y probado:

- [x] Conversación con Claude, con personalidad y streaming
- [x] Voz (edge-tts, SAPI, ElevenLabs) con reproducción interrumpible
- [x] Escucha: captura, VAD, transcripción local
- [x] Wake word "Hey Jarvis" y push-to-talk
- [x] Barge-in: puedes interrumpirle hablando
- [x] Permisos con confirmación por voz
- [x] Memoria de largo plazo
- [x] Diagnóstico y modos demo/simulación
- [x] HUD web, accesible desde el móvil, con micrófono del navegador
- [x] Herramientas de sistema: volumen, abrir programas, bloquear
- [x] Integración continua
- [x] Arranque automático con Windows
- [x] Icono en la bandeja del sistema, pausa del micrófono e instancia única

Pendiente:


> **Nota sobre las pruebas.** El código de audio se ha desarrollado y verificado con
> tests y en modo simulación sobre Linux, sin hardware de sonido. Los tests cubren toda
> la lógica, pero **la primera ejecución real en Windows es la tuya**: empieza por
> `python -m jarvis --diag`, que está hecho justo para eso.
