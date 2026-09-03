# Seguridad

J.A.R.V.I.S. tiene acceso real al equipo de quien lo usa: puede leer y
escribir archivos, ejecutar comandos, y hablar con Claude en su nombre. Esto
es lo que lo hace útil, y también lo que hay que entender antes de dejarlo
correr sin vigilancia — y, sobre todo, antes de publicar este código.

Nada de lo de aquí abajo es teórico: cada punto responde a un hallazgo real
de una auditoría de seguridad hecha sobre este mismo proyecto.

## Qué protege

- **El HUD web no está abierto a cualquiera.** Por defecto escucha sólo en
  `127.0.0.1` (hace falta `--lan` para exponerlo a la red local), y cada
  arranque genera un token de sesión que hace falta para leer la
  conversación, la memoria, o **aprobar un permiso pendiente** desde el
  navegador. El WebSocket además comprueba la cabecera `Origin`, para que
  una página maliciosa abierta en el mismo navegador no pueda hablarle a
  espaldas de quien lo usa.
- **Tres barreras antes de tocar el equipo** (`jarvis/core/permissions.py`):
  las rutas fuera de lo autorizado se deniegan sin preguntar; todo lo demás
  exige un "sí" hablado, con el comando o la acción leídos **enteros**, sin
  recortar; y el silencio nunca autoriza nada.
- **`abrir` no ejecuta cualquier cosa por ruta.** Una lista negra de
  intérpretes conocidos (`cmd`, `powershell`, `bash`...) más una lista
  blanca de extensiones seguras conocidas — no cualquier `.exe` o `.bat`
  ajeno a esa lista pasa el filtro.
- **La memoria persistente se enmarca como datos, nunca como instrucciones**
  al inyectarse en el system prompt, para reducir el riesgo de que algo
  leído en una web o un Google Doc y guardado sin querer acabe
  interpretándose como una orden.
- **Las API keys no tienen por qué vivir en texto plano.**
  `python -m jarvis --guardar-clave` las mete en el almacén de credenciales
  del sistema operativo (Credential Manager en Windows); `.env` sigue
  funcionando como respaldo para quien no lo use. `--diag` nunca muestra ni
  un fragmento de la clave real, sólo su longitud.
- **Detector de secretos permanente.** `gitleaks` corre en la CI sobre el
  historial completo en cada push, y hay un hook de pre-commit local para
  cogerlo antes de que el commit exista siquiera — con una regla propia para
  rutas locales de Windows, no sólo claves de API.
- **El historial de git no lleva datos personales.** Se auditó commit a
  commit y se reescribió lo que hacía falta: correo real, usuario de
  Windows, rutas de ejemplo. Ver la sección de publicación más abajo — esto
  no es lo mismo que "puede hacerse público ahora mismo".

## Qué NO protege (léelo antes de confiar en esto)

- **Cualquiera al alcance del micrófono puede despertarlo.** No hay
  verificación de quién habla: decir «Hey Jarvis» y pedirle algo funciona
  igual para el dueño del equipo que para cualquier otra persona en la
  habitación. Tampoco hay nada que impida que un tercero conteste "sí" a una
  confirmación de permiso pendiente — quien conteste primero, gana. Esto es
  trabajo futuro (verificación de hablante), no algo ya resuelto.
- **La conversación se guarda para siempre, sin cifrar.** El historial del
  día a día y la memoria persistente viven como archivos de texto plano en
  la carpeta de datos (`~/.jarvis` por defecto). Quien tenga acceso al
  sistema de archivos —otro usuario del equipo, un backup, un disco
  robado— puede leer todo lo que se ha hablado alguna vez. No hay rotación
  ni caducidad automática.
- **`--lan` expone el HUD a toda la red local con un único token por
  arranque.** Cualquiera en esa wifi que consiga el token (lo vea por encima
  del hombro, lo intercepte sin HTTPS) tiene el mismo acceso que el dueño
  hasta el siguiente reinicio. `--https` cifra el transporte, pero el modelo
  de un solo token compartido sigue siendo el mismo.
- **`google_token.json` se protege con permisos de archivo (`chmod 600`),
  no con cifrado.** Un atacante con acceso de administrador al equipo lo lee
  igual.
- **Nada de esto es aislamiento multiusuario.** El proyecto asume un único
  dueño con acceso físico o remoto de confianza al equipo. No hay conceptos
  de roles, cuentas ni permisos por persona.

## Antes de hacer público el repositorio

Esta checklist no sustituye el juicio de quien publica; es lo mínimo que
este proyecto concluyó que hacía falta:

1. **No cambiar este repositorio a público.** El historial se reescribió
   (correo, usuario de Windows, rutas personales), pero GitHub **conserva
   los objetos viejos tras un force-push**: no aparecen en un `clone`
   normal, pero siguen siendo accesibles por su SHA desde la web y la API.
   Crear un **repositorio nuevo y vacío**, y empujar ahí el historial ya
   limpio, es la única forma de garantizar que esos objetos no viajan. Éste
   se queda privado, como repositorio de trabajo.
2. **Confirmar que no hay secretos reales en el árbol de trabajo**:
   `git ls-files | grep -iE '\.env$|config\.toml$|client_secret|token'` no
   debería devolver nada salvo los `.example`.
3. **Correr `gitleaks git . --config .gitleaks.toml`** sobre el historial
   completo del repositorio nuevo antes de darlo por bueno — es exactamente
   lo mismo que corre la CI, pero confirmarlo a mano antes de publicar no
   sobra.
4. **Decidir una licencia.** Hoy no hay ningún archivo `LICENSE`: sin uno,
   la ley por defecto es "todos los derechos reservados", que probablemente
   no es lo que se busca al abrir el código.
5. **Revisar el propio `README.md` y `SECURITY.md`** por si algo de lo
   escrito mientras el repositorio era privado asume cosas que ya no son
   ciertas en el nuevo repositorio (URLs, nombre de usuario del ejemplo).
6. Si alguna vez `.env` estuvo commiteado en algún punto de la historia
   original (no es el caso conocido de este proyecto, pero es la
   comprobación que hay que hacer en cualquier repositorio antes de
   publicarlo): rotar esa clave con el proveedor, no basta con borrarla del
   archivo.

## Reportar un problema

Este es un proyecto personal sin un canal formal de reportes. Si algo de
esto te preocupa, abre un issue describiéndolo — sin incluir la clave, el
token o el dato real de por medio, claro.
