# herdeck: deck táctil para tus dos herdr

Convierte una tablet o un móvil en un panel tipo Stream Deck que muestra **en tiempo real** todos los agentes de tu herdr personal (este Mac) y del herdr del trabajo (remoto), y te deja actuar desde él.

```
 Tablet/móvil ──HTTP+SSE──▶ herdeck (Mac, launchd)
                              ├─ socket local  ~/.config/herdr/herdr.sock          → herdr personal
                              └─ túnel SSH de socket Unix ──▶ workbox:~/.config/herdr/herdr.sock → herdr trabajo
```

- **Estado por push**: `session.snapshot` + `events.subscribe` (`pane.agent_status_changed` por panel y eventos del ciclo de vida). Además hace un refresco de seguridad cada 20 s. Si un servidor cae, se reconecta solo.
- **Teselas** ordenadas por urgencia: *Necesita input* → *Terminado* → *Trabajando* → *En espera*, con el tiempo que lleva cada una en ese estado, la insignia de origen (Personal/Trabajo), el workspace, la pestaña y la rama.
- **Estados de terminal**: spinner braille mientras trabaja, cursor `▌` parpadeando si necesita input, `✓` al terminar y `zZ` en espera. Respetan *reducir movimiento*.
- **Claude Code**: icono del agente, chip del modelo (`opus`, `sonnet`…) y un anillo con el % de contexto usado junto al tiempo (azul < 60 %, ámbar 60–85 %, rojo > 85 %). En la cabecera, cada fuente muestra su icono (casa/maletín, o `icon` en la config) y los anillos de cuota del plan de 5 h y 7 d cuando existen.
- **Alertas**: si un agente pasa a `blocked`, la tablet vibra y suena, y el título muestra el contador.
- **Tocar una tesela**: enfoca el agente y lo abre en tu consola (`terminal_app`, p. ej. `"Ghostty"`). Si ya hay un cliente herdr de esa fuente abierto en el Mac, solo trae la terminal al frente; si no, abre una ventana nueva con `herdr` o `herdr --remote <ssh>`.
- **Detalle** (mantener pulsada una tesela): las últimas líneas de salida del agente y el botón *Abrir en la consola*. Si activas `allow_input`, también respuestas rápidas (Enter/Esc/texto) y un campo para escribirle.
- **Filtros por espacio**: chips en la cabecera para quedarte solo con los agentes de uno o varios workspaces (espacios), por fuente.
- **Acciones de plugins**: botones que ejecutan `herdr plugin action invoke …` en el servidor que elijas.
- Sin dependencias: Python ≥ 3.9 y la stdlib, nada más.

## Instalación (Mac)

1. Comprueba que el SSH al servidor del trabajo funciona **sin pedir contraseña** (clave + agente), porque herdeck usa `BatchMode=yes`:
   ```bash
   ssh -o BatchMode=yes workbox 'herdr -V'
   ```
2. Instala y arranca el servicio:
   ```bash
   ./install-macos.sh
   ```
   Al terminar, el script imprime la URL con el token, por ejemplo `http://192.168.1.20:7777/?t=…`.
3. Edita `~/.config/herdeck/config.json`:
   - `sources[1].ssh`: el host de `~/.ssh/config` que ya usas con `herdr --remote` (por defecto `workbox`).
   - `remote_session`: si en el remoto usas una sesión con nombre (lo que pasaste en `--remote-session`).
   - `remote_socket`: solo si el socket remoto no está en `~/.config/herdr/`.
   - Después: `launchctl kickstart -k gui/$(id -u)/dev.herdeck`
4. El instalador copia `statusline/herdeck-statusline.py` a `~/.claude/` y lo pone como `statusLine` de Claude Code **solo si no tienes ya uno** (guarda copia en `settings.json.bak-herdeck`). Ese script guarda el JSON del statusline en `~/.claude/herdeck/<sesión>.json`, de donde herdeck lee contexto, modelo y cuota (en el remoto, por SSH cada ~10 s). En el servidor del trabajo, copia el script a `~/.claude/` y añade a su `~/.claude/settings.json`: `"statusLine": {"type": "command", "command": "python3 ~/.claude/herdeck-statusline.py"}`. Si ya tienes un statusline propio, haz que también escriba ese fichero.
5. En la tablet, abre la URL y usa **Añadir a pantalla de inicio**: se abre a pantalla completa y mantiene la pantalla encendida.

Fuera de casa, usa **Tailscale** y pon la IP o el nombre MagicDNS en `public_host`. No abras el puerto 7777 a Internet: el token protege la API, pero el tráfico va en HTTP plano.

## Plugins e integraciones (más información de los agentes)

```bash
./install-plugins.sh local                 # Mac
./install-plugins.sh remote workbox        # servidor del trabajo
EXTRA=1 ./install-plugins.sh local         # + zoetrope, herdr-mirror, github-dash
```

| Qué | Para qué |
|---|---|
| `herdr integration install claude/codex/…` | Estado más fiable que leer la pantalla y reanudación de sesión tras reiniciar el servidor. Es lo primero que conviene instalar. |
| `levi-qiao/herdr-agent-usage` | Uso, contexto y caché por agente en la sidebar o la statusline |
| `kryptamine/herdr-auto-title` | Nombres de pestaña y panel a partir de la rama, la actividad y la sesión. Así las teselas del deck tienen nombres útiles. |
| `persiyanov/herdr-reviewr` | Revisar el diff del agente y devolverle comentarios por línea |
| `ntindle/herdr-resurrect` | Restaurar workspaces y agentes tras un reinicio o un crash |
| `thanhdat77/herdr-navigator` (local) | Salto difuso entre workspaces, agentes y remotos |
| Opcionales | `furkankly/zoetrope` (grafo de sesiones), `nikok6/herdr-mirror` (espeja el remoto en la sidebar local), `kukv/herdr-plugin-github-dash` |

El script instala **sin `--yes`**: herdr enseña el manifiesto antes de ejecutar nada. Los plugins no están revisados ni aislados y corren con tu usuario. En el servidor del trabajo, instala solo lo que permita la política de seguridad de la empresa.

Después, lista los IDs de acción con `herdr plugin action list --plugin <id>` y añádelos a `plugin_actions` de la config para tener botones en el deck.

`herdr-config.snippet.toml` activa las notificaciones y añade la máquina y `$summary` a la sidebar.

## PIN

El token solo no basta: la primera vez que abres el deck la tablet pide un **PIN** de 4 a 6 dígitos. Se guarda en `pin` de `~/.config/herdeck/config.json` y, si falta, herdeck genera uno de 6 dígitos al arrancar. `python3 herdeck.py --print-url` lo imprime en la última línea (`PIN: …`), después de las URLs. Los intentos fallidos se bloquean con espera exponencial (por IP y global, hasta 1 h), lo que frena la fuerza bruta en la red local, pero no abras el puerto a Internet.

## herdr-app (segundo deck)

Si usas **herdr-app** (terminales gestionadas por `hc`/`herdr-app`), añade un bloque `herdr_app` a la config (hay un ejemplo en `config.example.json`). herdeck levanta entonces un segundo deck en su propio puerto (`7778` por defecto) con el mismo token y PIN. Es **otra PWA**: ábrela en la tablet y usa *Añadir a pantalla de inicio* aparte; aparecerá como "herdeck app" y la de `:7777` sigue funcionando sin reinstalar nada.

- `sources`: una entrada por máquina. `machine: null` es este Mac; con `machine: "workbox"` y `ssh: "workbox"` se lee y enfoca el remoto por SSH.
- Opciones con valor por defecto: `port` (7778), `title`, `accent`, `hc_socket`, `herdr_app_bin` (`herdr-app`), `app_name` (`Herdr`), `poll_seconds` (20) y `enabled: false` para apagarlo.
- Contexto, modelo y cuota de Claude requieren el statusline de herdeck en cada máquina (ver instalación, paso 4) y `ssh` sin contraseña para las remotas.
- En este deck solo se admite **tocar para enfocar** la terminal; no hay lectura de salida ni envío de teclas.
- `--print-url` imprime ambas URLs y el PIN.

## Responder desde la tablet (opcional)

Por defecto el deck **solo lee y enfoca**. Con `"allow_input": true` puedes enviar teclas (`agent.send_keys`) o texto (`agent.prompt`) al agente. Es cómodo para aprobar un paso, pero cualquiera que tenga el token puede dirigir tus agentes, también los del trabajo. Si lo activas, que sea solo dentro de Tailscale.

## Diagnóstico

- `~/Library/Logs/herdeck/herdeck.log`: conexiones, túnel y errores.
- Si la conexión falla, la cabecera del deck muestra un punto rojo y el motivo.
- `http://…:7777/api/raw` devuelve el snapshot crudo de cada servidor. Si una versión de herdr cambia los nombres de los campos y alguna tesela sale vacía, compáralo con `normalize_snapshot()` en `herdeck.py`.
- Las acciones *leer salida* y *plugins* llaman a tu `herdr` local con `HERDR_SOCKET_PATH` apuntando al socket (también al remoto tunelizado). Mantén **la misma versión de herdr** en el Mac y en el servidor para que el protocolo coincida.
