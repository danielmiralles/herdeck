#!/usr/bin/env bash
# Instala herdeck como servicio de usuario (launchd) en macOS.
#   ./install-macos.sh            instala / actualiza y arranca
#   ./install-macos.sh uninstall  para y elimina el servicio (conserva la config)
set -euo pipefail

LABEL="dev.herdeck"
APP_DIR="$HOME/.local/share/herdeck"
CFG="$HOME/.config/herdeck/config.json"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/herdeck"
SRC="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Servicio eliminado. Config intacta en $CFG"
  exit 0
fi

PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || { echo "Falta python3 (xcode-select --install o brew install python)"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "Necesitas Python >= 3.9"; exit 1; }
command -v herdr >/dev/null || echo "Aviso: 'herdr' no está en el PATH; pon 'herdr_bin' en la config."

mkdir -p "$APP_DIR" "$LOG_DIR" "$(dirname "$PLIST")"
cp "$SRC/herdeck.py" "$SRC/deck.html" "$SRC/pin.html" "$SRC/config.example.json" "$APP_DIR/"

# Statusline de Claude Code: escribe ~/.claude/herdeck/<sesión>.json (contexto y cuota en el deck)
mkdir -p "$HOME/.claude"
cp "$SRC/statusline/herdeck-statusline.py" "$HOME/.claude/herdeck-statusline.py"
"$PY" - "$HOME/.claude/settings.json" <<'PYEOF' || echo "Aviso: no se pudo configurar el statusLine; revisa ~/.claude/settings.json"
import json, os, shutil, sys
path = sys.argv[1]
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
if "statusLine" in data:
    print("Aviso: ya tienes un statusLine en %s; no lo toco. Para el deck usa: "
          "python3 ~/.claude/herdeck-statusline.py" % path)
    sys.exit(0)
if os.path.exists(path):
    shutil.copy2(path, path + ".bak-herdeck")
data["statusLine"] = {"type": "command", "command": "python3 ~/.claude/herdeck-statusline.py"}
with open(path + ".tmp-herdeck", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(path + ".tmp-herdeck", path)
print("statusLine de herdeck añadido a %s" % path)
PYEOF

if [[ ! -f "$CFG" ]]; then
  "$PY" "$APP_DIR/herdeck.py" --config "$CFG" --print-url >/dev/null
  echo "Config creada en $CFG — revisa el host SSH de la fuente 'trabajo'."
fi

HERDR_DIR="$(dirname "$(command -v herdr 2>/dev/null || echo /opt/homebrew/bin/herdr)")"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$APP_DIR/herdeck.py</string>
    <string>--config</string><string>$CFG</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HERDR_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/herdeck.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/herdeck.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1
echo "herdeck en marcha. Logs: $LOG_DIR/herdeck.log"
echo "Abre esto en la tablet (misma red o Tailscale) y añádelo a la pantalla de inicio:"
"$PY" "$APP_DIR/herdeck.py" --config "$CFG" --print-url
