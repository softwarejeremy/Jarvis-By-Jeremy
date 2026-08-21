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
  formulario: $("formulario"),
  campo: $("campo"),
  btnEscuchar: $("btn-escuchar"),
  btnInterrumpir: $("btn-interrumpir"),
  conexion: $("conexion"),
  conexionTexto: $("conexion-texto"),
  subtitulo: $("subtitulo"),
  modelo: $("modelo"),
  voz: $("voz"),
  coste: $("coste"),
  permiso: $("permiso"),
  permisoTexto: $("permiso-texto"),
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
  el.btnEscuchar.disabled = !hayMicrofono || estado === "confirmando";
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

function bajarDelTodo() {
  el.conversacion.scrollTop = el.conversacion.scrollHeight;
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

el.btnEscuchar.addEventListener("click", () => enviar({ type: "escuchar" }));
el.btnInterrumpir.addEventListener("click", () => enviar({ type: "interrumpir" }));

// Barra espaciadora para hablar, salvo mientras se escribe.
document.addEventListener("keydown", (ev) => {
  if (ev.code === "Space" && document.activeElement !== el.campo) {
    ev.preventDefault();
    enviar({ type: "escuchar" });
  }
});

/* ── Arranque ──────────────────────────────────────────────── */
fetch("/api/estado")
  .then((r) => r.json())
  .then((s) => {
    el.modelo.textContent = s.modelo || "—";
    el.voz.textContent = s.voz || "—";
    el.coste.textContent = "$" + Number(s.coste_usd || 0).toFixed(4);
    hayMicrofono = Boolean(s.microfono);

    if (!hayMicrofono) {
      // Sin micrófono real, pulsar «Escuchar» dejaría al núcleo esperando
      // un audio que nunca llegará. Mejor decirlo que dejarlo colgado.
      el.btnEscuchar.disabled = true;
      el.btnEscuchar.title = "No hay micrófono disponible en este equipo";
      PISTAS.dormido = "Sin micrófono: escríbele abajo";
    } else if (!s.wake_word) {
      PISTAS.dormido = s.atajo
        ? `Pulsa el botón o ${s.atajo}`
        : "Pulsa el botón para hablar";
    }

    pintarEstado(s.state);
  })
  .catch(() => {});

conectar();
