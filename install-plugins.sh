#!/usr/bin/env bash
# Integraciones y plugins de herdr para tener más información de los agentes.
#
#   ./install-plugins.sh local                 # tu herdr personal (este Mac)
#   ./install-plugins.sh remote workbox        # el herdr del trabajo, vía SSH
#
# Variables opcionales:
#   AGENTS="claude codex"        integraciones a instalar (claude codex opencode pi)
#   EXTRA=1                      añade los plugins opcionales (ver abajo)
#
# Los plugins de herdr NO están revisados ni aislados: se ejecutan con tu usuario.
# Por eso se instalan SIN --yes: herdr te enseña el manifiesto y los comandos
# antes de ejecutar nada; lee y confirma uno a uno. Cuando estés conforme,
# fija la versión revisada:  herdr plugin install owner/repo --ref <tag>
set -euo pipefail

MODE="${1:-local}"
HOST="${2:-}"
AGENTS="${AGENTS:-claude codex}"

# Núcleo: información de agentes, en ambos servidores.
CORE=(
  levi-qiao/herdr-agent-usage     # uso, contexto y caché por agente (sidebar/statusline)
  kryptamine/herdr-auto-title     # nombra pestañas/paneles por rama, actividad y sesión de Claude Code
  persiyanov/herdr-reviewr        # ver el diff del agente y devolverle comentarios por línea
  ntindle/herdr-resurrect         # snapshot/restore de workspaces y agentes tras reinicio
)
# Solo en el Mac local.
LOCAL_ONLY=(
  thanhdat77/herdr-navigator      # salto difuso entre workspaces, agentes y remotos
)
# Opcionales (EXTRA=1).
OPTIONAL=(
  furkankly/zoetrope              # grafo en vivo de sesiones Claude Code/Codex (terminal o navegador)
  nikok6/herdr-mirror             # espeja el herdr remoto en la sidebar local
  kukv/herdr-plugin-github-dash   # PRs e issues de GitHub (requiere gh y Go para compilar)
)

run() {  # ejecuta en local o en el host remoto con TTY (para la vista previa)
  if [[ "$MODE" == "remote" ]]; then
    ssh -t "$HOST" "$(printf '%q ' "$@")"
  else
    "$@"
  fi
}

if [[ "$MODE" == "remote" && -z "$HOST" ]]; then
  echo "Uso: $0 remote <host-ssh>"; exit 1
fi

echo "== herdr en ${HOST:-local}"
run herdr -V

echo "== Integraciones (estado más fiable y reanudación de sesión): $AGENTS"
for a in $AGENTS; do
  run herdr integration install "$a" || echo "  ! integración $a falló (¿agente no instalado?)"
done

PLUGINS=("${CORE[@]}")
[[ "$MODE" == "local" ]] && PLUGINS+=("${LOCAL_ONLY[@]}")
[[ "${EXTRA:-0}" == "1" ]] && PLUGINS+=("${OPTIONAL[@]}")

for p in "${PLUGINS[@]}"; do
  echo
  echo "== $p   (revisa: https://github.com/$p/blob/HEAD/herdr-plugin.toml)"
  read -r -p "¿Instalar? [s/N] " ok </dev/tty
  [[ "$ok" =~ ^[sSyY]$ ]] || { echo "  saltado"; continue; }
  run herdr plugin install "$p" || echo "  ! falló $p"
done

echo
echo "== Verificación"
run herdr plugin list
echo
echo "IDs de acciones (cópialos a 'plugin_actions' de ~/.config/herdeck/config.json):"
echo "  herdr plugin action list --plugin <plugin.id>"
run herdr server reload-config || true
