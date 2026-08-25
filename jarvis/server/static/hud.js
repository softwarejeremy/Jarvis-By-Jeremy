/* ═══════════════════════════════════════════════════════════════
   HUD de J.A.R.V.I.S. — cliente del bus de eventos.

   No tiene lógica propia: recibe los mismos eventos que el HUD de
   consola y los pinta. Todo lo que decide, lo decide el núcleo.
   ═══════════════════════════════════════════════════════════════ */

const $ = (id) => document.getElementById(id);

const el = {
  reactor: $("reactor"),
  panelReactor: document.querySelector(".panel-reactor"),
  estado: $("estado-texto"),
  pista: $("estado-pista"),
  conversacion: $("conversacion"),
  actividad: $("actividad"),
  memoria: $("memoria"),
  formulario: $("formulario"),
  campo: $("campo"),
  btnEscuchar: $("btn-escuchar"),
  btnInterrumpir: $("btn-interrumpir"),
  btnPausa: $("btn-pausa"),
  btnPausaTexto: $("btn-pausa-texto"),
  conexion: $("conexion"),
  conexionTexto: $("conexion-texto"),
  subtitulo: $("subtitulo"),
  modelo: $("modelo"),
  voz: $("voz"),
  coste: $("coste"),
  permiso: $("permiso"),
  permisoTexto: $("permiso-texto"),
  btnPermisoSi: $("btn-permiso-si"),
  btnPermisoNo: $("btn-permiso-no"),
  btnMovil: $("btn-movil"),
  movilOverlay: $("movil-overlay"),
  btnMovilCerrar: $("btn-movil-cerrar"),
  movilQr: $("movil-qr"),
  movilUrl: $("movil-url"),
};

// Qué decirle al usuario en cada estado. Un HUD que sólo muestra el
// nombre del estado obliga a adivinar qué se espera de ti.
const PISTAS = {
  dormido: 'Di «Hey Jarvis» o pulsa el botón',
  escuchando: "Le escucho…",
  transcribiendo: "Entendiendo lo que ha dicho…",
  pensando: "Pensando…",
  hablando: "Hablando. Puede interrumpirle.",
  confirmando: "Espera su confirmación: diga «sí» o «no»",
  pausado: "Micrófono en pausa. No le está escuchando.",
  error: "Algo ha fallado. Revise la actividad.",
};

let socket = null;
let reintento = 1000;
let turnoJarvis = null;   // burbuja en curso, para ir añadiendo el streaming
let hayMicrofono = true;  // lo confirma /api/estado al arrancar

/* ── Conexión ──────────────────────────────────────────────── */
function conectar() {
  const protocolo = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${protocolo}//${location.host}/ws`);

  socket.onopen = () => {
    reintento = 1000;
    el.conexion.dataset.estado = "conectado";
    el.conexionTexto.textContent = "en línea";
    el.subtitulo.textContent = "a su disposición";
  };

  socket.onclose = () => {
    el.conexion.dataset.estado = "desconectado";
    el.conexionTexto.textContent = "reconectando…";
    el.subtitulo.textContent = "sin conexión con el núcleo";
    // Reintento con espera creciente: si el servidor está caído, no tiene
    // sentido machacarlo cada segundo.
    setTimeout(conectar, reintento);
    reintento = Math.min(reintento * 1.6, 15000);
  };

  socket.onerror = () => socket.close();
  socket.onmessage = (ev) => {
    try {
      manejar(JSON.parse(ev.data));
    } catch (e) {
      console.error("evento ilegible", e);
    }
  };
}

function enviar(mensaje) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(mensaje));
    return true;
  }
  return false;
}

/* ── Eventos del núcleo ────────────────────────────────────── */
function manejar({ type, data = {} }) {
  switch (type) {
    case "state_changed":
      pintarEstado(data.state);
      break;

    case "historial":
      pintarHistorial(data.turnos || []);
      break;

    case "wake_detected":
      registrar("«Hey Jarvis» detectado", "ok");
      break;

    case "final_transcript":
      if (data.kind === "confirmacion") {
        registrar(`Respuesta: «${data.text}»`, "permiso-log");
      } else if (data.text) {
        anadirTurno("usuario", data.text);
      }
      break;

    case "assistant_delta":
      escribirDelta(data.text || "");
      break;

    case "assistant_done":
      cerrarTurno();
      break;

    case "tool_use":
      registrar(`${data.name}${resumirEntrada(data.input)}`, "herramienta");
      // J.A.R.V.I.S. acaba de anotar o borrar algo: el panel se refresca
      // solo, en vez de esperar a que el usuario recargue para verlo.
      if (data.name === "mcp__jarvis__recordar" || data.name === "mcp__jarvis__olvidar") {
        cargarMemoria();
      }
      break;

    case "permission_request":
      el.permisoTexto.textContent = data.question || "";
      el.permiso.hidden = false;
      registrar(`Pide permiso: ${data.tool}`, "permiso-log");
      break;

    case "permission_result":
      el.permiso.hidden = true;
      registrar(
        data.allowed ? "Autorizado" : `Denegado${data.reason ? ": " + data.reason : ""}`,
        data.allowed ? "ok" : "error"
      );
      break;

    case "cost_update":
      el.coste.textContent = "$" + Number(data.total_usd || 0).toFixed(4);
      break;

    case "error":
      registrar(data.message || "error", "error");
      cerrarTurno();
      break;

    case "log":
      registrar(data.message || "", "");
      break;
  }
}

/* ── Pintado ───────────────────────────────────────────────── */
function pintarEstado(estado) {
  if (!estado) return;
  // En el panel, para que el color llegue también a la etiqueta de estado.
  el.panelReactor.dataset.estado = estado;
  el.reactor.dataset.estado = estado;
  el.estado.textContent = estado;
  el.pista.textContent = PISTAS[estado] || "";

  const ocupado = estado === "hablando" || estado === "pensando";
  el.btnInterrumpir.disabled = !ocupado;
  // Durante una confirmación hablada la respuesta tiene que ir por voz, así
  // que no se ofrece reiniciar la escucha desde el navegador.
  el.btnEscuchar.disabled =
    !hayMicrofono || estado === "confirmando" || estado === "pausado";

  if (estado === "pausado" || estado === "dormido") pintarPausa(estado === "pausado");
}

// El botón dice lo que va a hacer, no en qué estado está: «Pausar micrófono»
// mientras escucha y «Reanudar» mientras no.
function pintarPausa(pausado) {
  el.btnPausa.setAttribute("aria-pressed", String(pausado));
  el.btnPausa.classList.toggle("activo", pausado);
  el.btnPausaTexto.textContent = pausado ? "Reanudar micrófono" : "Pausar micrófono";
}

// Llega cada vez que se conecta un WebSocket, no sólo al arrancar: recargar
// la página o entrar desde un segundo dispositivo repone la conversación en
// vez de dejarla en blanco. Repintar entero es más simple que llevar la
// cuenta de qué turnos ya están puestos, y no duplica nada porque siempre
// parte de cero.
function pintarHistorial(turnos) {
  turnoJarvis = null;
  if (turnos.length === 0) {
    el.conversacion.innerHTML = '<p class="vacio">Todavía no habéis hablado.</p>';
    return;
  }
  el.conversacion.innerHTML = "";
  for (const turno of turnos) anadirTurno(turno.quien, turno.texto);
}

function anadirTurno(quien, texto) {
  if (el.conversacion.querySelector(".vacio")) el.conversacion.innerHTML = "";

  const turno = document.createElement("div");
  turno.className = `turno turno-${quien}`;

  const etiqueta = document.createElement("span");
  etiqueta.className = "quien";
  etiqueta.textContent = quien === "usuario" ? "usted" : "jarvis";

  const dicho = document.createElement("div");
  dicho.className = "dicho";
  dicho.textContent = texto;

  turno.append(etiqueta, dicho);
  el.conversacion.append(turno);
  bajarDelTodo();
  return turno;
}

function escribirDelta(texto) {
  if (!turnoJarvis) {
    turnoJarvis = anadirTurno("jarvis", "");
    turnoJarvis.classList.add("escribiendo");
  }
  turnoJarvis.querySelector(".dicho").textContent += texto;
  bajarDelTodo();
}

function cerrarTurno() {
  if (turnoJarvis) {
    turnoJarvis.classList.remove("escribiendo");
    turnoJarvis = null;
  }
}

function registrar(mensaje, clase) {
  if (!mensaje) return;
  if (el.actividad.querySelector(".vacio")) el.actividad.innerHTML = "";

  const fila = document.createElement("li");
  if (clase) fila.className = clase;

  const hora = document.createElement("span");
  hora.className = "hora";
  hora.textContent = new Date().toLocaleTimeString("es", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });

  const que = document.createElement("span");
  que.className = "que";
  que.textContent = mensaje;

  fila.append(hora, que);
  el.actividad.append(fila);

  // El historial completo no aporta y acabaría comiendo memoria.
  while (el.actividad.children.length > 80) el.actividad.firstChild.remove();
  el.actividad.scrollTop = el.actividad.scrollHeight;
}

/* ── Memoria ───────────────────────────────────────────────── */
async function cargarMemoria() {
  try {
    const r = await fetch("/api/memoria");
    pintarMemoria(await r.json());
  } catch (e) {
    // Sin memoria que mostrar no hay mucho más que hacer aquí: el panel
    // se queda con el mensaje de "todavía no recuerda nada" de partida.
  }
}

function pintarMemoria(porCategoria) {
  el.memoria.innerHTML = "";
  const categorias = Object.entries(porCategoria || {}).filter(([, l]) => l.length);

  if (categorias.length === 0) {
    el.memoria.innerHTML = '<p class="vacio">Todavía no recuerda nada.</p>';
    return;
  }

  for (const [categoria, entradas] of categorias) {
    const titulo = document.createElement("p");
    titulo.className = "memoria-categoria-titulo";
    titulo.textContent = categoria.charAt(0).toUpperCase() + categoria.slice(1);
    el.memoria.append(titulo);

    for (const texto of entradas) {
      const fila = document.createElement("div");
      fila.className = "memoria-entrada";

      const span = document.createElement("span");
      span.className = "texto";
      span.textContent = texto;

      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = "olvidar";
      boton.title = "Olvidar";
      boton.textContent = "✕";
      boton.addEventListener("click", () => olvidarEntrada(texto));

      fila.append(span, boton);
      el.memoria.append(fila);
    }
  }
}

async function olvidarEntrada(texto) {
  // Borrar memoria merece un clic de más: no es reversible desde el HUD.
  if (!confirm(`¿Olvidar «${texto}»?`)) return;

  try {
    const r = await fetch("/api/memoria/olvidar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto }),
    });
    const datos = await r.json();
    pintarMemoria(datos.memoria);
    registrar(datos.mensaje, "");
  } catch (e) {
    registrar("No se pudo actualizar la memoria.", "error");
  }
}

/* ── Acceso desde el móvil ─────────────────────────────────── */
// Ya en el propio móvil esto no aporta nada —está justo mirando esta
// pantalla—, pero no molesta: el botón simplemente no aparece si /api/movil
// no encuentra una IP de red local que ofrecer.
async function cargarAccesoMovil() {
  try {
    const r = await fetch("/api/movil");
    const datos = await r.json();
    if (!datos.url) return;

    el.movilUrl.textContent = datos.url;
    el.movilQr.innerHTML = datos.qr_svg || "";
    el.btnMovil.hidden = false;
  } catch (e) {
    // Sin datos que ofrecer, el botón se queda oculto: no hay nada roto
    // que contar, sólo no hay forma de llegar al móvil desde aquí.
  }
}

el.btnMovil.addEventListener("click", () => { el.movilOverlay.hidden = false; });
el.btnMovilCerrar.addEventListener("click", () => { el.movilOverlay.hidden = true; });
el.movilOverlay.addEventListener("click", (ev) => {
  if (ev.target === el.movilOverlay) el.movilOverlay.hidden = true;
});

function resumirEntrada(entrada) {
  if (!entrada) return "";
  for (const campo of ["command", "file_path", "pattern", "query", "hecho", "url"]) {
    if (entrada[campo]) {
      const texto = String(entrada[campo]).replace(/\s+/g, " ");
      return "  " + (texto.length > 60 ? texto.slice(0, 60) + "…" : texto);
    }
  }
  return "";
}

function etiquetarBoton(texto) {
  // El botón lleva un icono SVG y, detrás, su texto como nodo suelto. Se
  // reemplaza sólo ese nodo para no tocar el icono.
  for (const nodo of el.btnEscuchar.childNodes) {
    if (nodo.nodeType === Node.TEXT_NODE && nodo.textContent.trim()) {
      nodo.textContent = " " + texto;
      return;
    }
  }
  el.btnEscuchar.append(" " + texto);
}

function bajarDelTodo() {
  el.conversacion.scrollTop = el.conversacion.scrollHeight;
}

/* ═══════════════════════════════════════════════════════════════
   Micrófono del navegador: pulsar y mantener para hablar.

   Sirve sobre todo para el móvil, que es un mando de voz sin
   micrófono propio conectado al núcleo. Se manda la frase entera
   de una vez, no un flujo continuo: así no compite con el
   micrófono del equipo ni hay que sincronizar dos fuentes de
   audio, que es donde estos sistemas se rompen.

   AVISO IMPORTANTE: el navegador sólo expone el micrófono en
   contexto seguro (HTTPS o localhost). Sobre http://192.168.x.x
   `navigator.mediaDevices` ni siquiera existe — por eso el
   servidor puede arrancar con --https.
   ═══════════════════════════════════════════════════════════════ */

const TASA = 16000;          // lo que esperan Whisper y el VAD
const MAX_SEGUNDOS = 30;     // freno: más que esto no es una frase

const mic = {
  disponible: Boolean(navigator.mediaDevices?.getUserMedia),
  ctx: null,
  fuente: null,
  procesador: null,
  flujo: null,
  trozos: [],
  grabando: false,
};

function contextoSeguro() {
  return window.isSecureContext;
}

async function prepararMicrofono() {
  if (mic.ctx) return true;

  try {
    mic.flujo = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,   // no queremos oír a J.A.R.V.I.S. a sí mismo
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (e) {
    registrar(`No se pudo abrir el micrófono: ${e.message}`, "error");
    return false;
  }

  // Se pide 16 kHz directamente; si el navegador lo ignora, se remuestrea
  // luego a mano.
  mic.ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TASA });
  if (mic.ctx.state === "suspended") await mic.ctx.resume();

  mic.fuente = mic.ctx.createMediaStreamSource(mic.flujo);
  mic.procesador = mic.ctx.createScriptProcessor(4096, 1, 1);

  mic.procesador.onaudioprocess = (ev) => {
    if (!mic.grabando) return;
    mic.trozos.push(new Float32Array(ev.inputBuffer.getChannelData(0)));

    const muestras = mic.trozos.length * 4096;
    if (muestras / mic.ctx.sampleRate > MAX_SEGUNDOS) pararGrabacion();
  };

  // El procesador sólo corre si está conectado a la salida, pero conectarlo
  // directamente devolvería el micrófono por los altavoces. Un nodo de
  // ganancia a cero lo mantiene vivo y en silencio.
  const mudo = mic.ctx.createGain();
  mudo.gain.value = 0;
  mic.fuente.connect(mic.procesador);
  mic.procesador.connect(mudo);
  mudo.connect(mic.ctx.destination);

  return true;
}

async function empezarGrabacion() {
  if (mic.grabando) return;
  if (!(await prepararMicrofono())) return;

  mic.trozos = [];
  mic.grabando = true;
  el.btnEscuchar.classList.add("grabando");
  el.panelReactor.dataset.estado = "escuchando";
  el.reactor.dataset.estado = "escuchando";
  el.estado.textContent = "grabando";
  el.pista.textContent = "Suelte para enviar";
}

function pararGrabacion() {
  if (!mic.grabando) return;
  mic.grabando = false;
  el.btnEscuchar.classList.remove("grabando");

  const pcm = aInt16(unir(mic.trozos), mic.ctx.sampleRate);
  mic.trozos = [];

  if (pcm.byteLength < 4000) {
    registrar("Grabación demasiado corta.", "");
    pintarEstado("dormido");
    return;
  }

  if (!socket || socket.readyState !== WebSocket.OPEN) {
    registrar("Sin conexión: no se ha enviado el audio.", "error");
    pintarEstado("dormido");
    return;
  }

  socket.send(pcm);
  el.estado.textContent = "enviando";
  el.pista.textContent = "Enviando su voz…";
}

function unir(trozos) {
  let total = 0;
  for (const t of trozos) total += t.length;
  const salida = new Float32Array(total);
  let i = 0;
  for (const t of trozos) { salida.set(t, i); i += t.length; }
  return salida;
}

/* Remuestreo lineal + conversión a PCM de 16 bits.

   Lineal y no algo más fino a propósito: la voz ya viene limitada en banda
   por el micrófono, Whisper es muy tolerante, y hacerlo aquí evita mandar
   tres veces más bytes por la wifi. */
function aInt16(muestras, tasaOrigen) {
  let datos = muestras;

  if (tasaOrigen !== TASA) {
    const factor = tasaOrigen / TASA;
    const largo = Math.floor(muestras.length / factor);
    datos = new Float32Array(largo);
    for (let i = 0; i < largo; i++) {
      const pos = i * factor;
      const a = Math.floor(pos);
      const b = Math.min(a + 1, muestras.length - 1);
      datos[i] = muestras[a] + (muestras[b] - muestras[a]) * (pos - a);
    }
  }

  const pcm = new Int16Array(datos.length);
  for (let i = 0; i < datos.length; i++) {
    const v = Math.max(-1, Math.min(1, datos[i]));
    pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return pcm.buffer;
}

/* ── Interacción ───────────────────────────────────────────── */
el.formulario.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const texto = el.campo.value.trim();
  if (!texto) return;
  if (enviar({ type: "texto", text: texto })) {
    anadirTurno("usuario", texto);
    el.campo.value = "";
  } else {
    registrar("Sin conexión: no se ha enviado", "error");
  }
});

/* El botón hace una cosa u otra según de dónde pueda salir la voz:

   - Micrófono del navegador (móvil, o el PC por HTTPS/localhost): pulsar y
     mantener. La grabación se manda al soltar.
   - Sin micrófono en el navegador pero sí en el equipo: un clic normal, y
     escucha el micrófono del PC.
   - Ninguno de los dos: desactivado, y se dice por qué. */
function usaMicrofonoDelNavegador() {
  return mic.disponible && contextoSeguro();
}

if (usaMicrofonoDelNavegador()) {
  // `pointer*` cubre ratón y dedo con los mismos manejadores.
  el.btnEscuchar.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    // Sin captura, si el dedo se desliza fuera del botón nunca llega el
    // "pointerup" y la grabación se quedaría abierta para siempre.
    el.btnEscuchar.setPointerCapture(ev.pointerId);
    empezarGrabacion();
  });
  for (const evento of ["pointerup", "pointercancel", "lostpointercapture"]) {
    el.btnEscuchar.addEventListener(evento, () => pararGrabacion());
  }
  // Si la pestaña se va a segundo plano a media grabación, se cierra: nadie
  // quiere descubrir que su móvil llevaba diez minutos grabando.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pararGrabacion();
  });
} else {
  el.btnEscuchar.addEventListener("click", () => enviar({ type: "escuchar" }));
}

el.btnInterrumpir.addEventListener("click", () => enviar({ type: "interrumpir" }));
el.btnPausa.addEventListener("click", () => enviar({ type: "pausa" }));
el.btnPermisoSi.addEventListener("click", () => enviar({ type: "confirmar", allowed: true }));
el.btnPermisoNo.addEventListener("click", () => enviar({ type: "confirmar", allowed: false }));

// Barra espaciadora para hablar. Sólo cuando no hay nada enfocado: si el foco
// está en un botón, el espacio ya lo pulsa, y disparábamos la escucha dos
// veces —una de ellas cancelando el turno recién empezado—.
document.addEventListener("keydown", (ev) => {
  if (ev.code !== "Space" || ev.repeat) return;
  const foco = document.activeElement;
  if (foco && foco !== document.body) return;
  if (el.btnEscuchar.disabled) return;
  ev.preventDefault();
  enviar({ type: "escuchar" });
});

/* ── Arranque ──────────────────────────────────────────────── */
fetch("/api/estado")
  .then((r) => r.json())
  .then((s) => {
    el.modelo.textContent = s.modelo || "—";
    el.voz.textContent = s.voz || "—";
    el.coste.textContent = "$" + Number(s.coste_usd || 0).toFixed(4);
    hayMicrofono = Boolean(s.microfono) || usaMicrofonoDelNavegador();

    if (usaMicrofonoDelNavegador()) {
      etiquetarBoton("Mantén para hablar");
      PISTAS.dormido = "Mantén pulsado el botón y habla";
    } else if (!s.microfono) {
      // Ni micrófono en el navegador ni en el equipo: pulsar dejaría al
      // núcleo esperando un audio que nunca llega. Mejor decir por qué.
      el.btnEscuchar.disabled = true;
      el.btnEscuchar.title = mic.disponible
        ? "El navegador sólo da acceso al micrófono por HTTPS. Arranca con --https."
        : "Este navegador no permite usar el micrófono";
      PISTAS.dormido = mic.disponible
        ? "Sin HTTPS no hay micrófono aquí: escríbele abajo"
        : "Sin micrófono: escríbele abajo";
    } else if (!s.wake_word) {
      PISTAS.dormido = s.atajo
        ? `Pulsa el botón o ${s.atajo}`
        : "Pulsa el botón para hablar";
    }

    pintarPausa(Boolean(s.pausado));
    pintarEstado(s.state);
  })
  .catch(() => {});

cargarMemoria();
cargarAccesoMovil();
conectar();
