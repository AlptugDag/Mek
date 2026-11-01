#!/bin/bash
# Mek kurulumu: ~/.jarvis altına kurar, eski Jarvis ajanlarını yedekleyip devre dışı bırakır.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
J="$HOME/.jarvis"; BIN="$HOME/.local/bin"; LA="$HOME/Library/LaunchAgents"; U=$(id -u)

command -v ffmpeg >/dev/null || { echo "ffmpeg gerekli:  brew install ffmpeg"; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "Apple Silicon (M serisi) gerekli."; exit 1; }

mkdir -p "$J/mek" "$J/legacy" "$J/state" "$BIN" "$LA"
cp "$SRC"/mek/*.py "$SRC/mek/install_privileged.sh" "$J/mek/"
[ -f "$J/jarvis.py" ] && cp "$J/jarvis.py" "$J/legacy/jarvis.py.bak"
cp "$SRC/jarvis.py" "$J/jarvis.py"
cp "$SRC"/bin/* "$BIN/"; chmod +x "$BIN"/mek "$BIN"/mek-anahtar "$BIN"/*.sh

if [ ! -x "$J/venv/bin/python" ]; then
  PY=$(command -v python3.11 || command -v python3)
  "$PY" -m venv "$J/venv"
fi
"$J/venv/bin/pip" install -q --upgrade pip
"$J/venv/bin/pip" install -q -r "$SRC/requirements.txt"

# Eski ayrı ajanları kapat (yedekleri legacy/ altında)
for l in jarvis.periodic jarvis.startup keepawake-videoapps smart-cleaner; do
  launchctl bootout "gui/$U/com.alptug.$l" 2>/dev/null || true
  [ -f "$LA/com.alptug.$l.plist" ] && mv "$LA/com.alptug.$l.plist" "$J/legacy/"
done

for p in mek mek.ui; do
  sed "s|__HOME__|$HOME|g" "$SRC/launchd/com.alptug.$p.plist" > "$LA/com.alptug.$p.plist"
  launchctl bootout "gui/$U/com.alptug.$p" 2>/dev/null || true
  launchctl bootstrap "gui/$U" "$LA/com.alptug.$p.plist"
done

echo "Mek kuruldu. Sonraki adımlar (isteğe bağlı):"
echo "  mek-anahtar                                   # Gemini anahtarı (arama / ekrana bakma)"
echo "  ~/.jarvis/venv/bin/python ~/.jarvis/mek/wakeword.py ekle kayit.wav   # 'Mek' ses şablonu"
echo "  sudo bash ~/.jarvis/mek/install_privileged.sh # güç profili ve sistem servisleri için"
