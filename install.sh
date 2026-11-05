#!/bin/bash
# Mek kurulumu (legacy ~/.jarvis dizinini kullanır). Eski ayrı ajanları durdurur.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
J="$HOME/.jarvis"; BIN="$HOME/.local/bin"; LA="$HOME/Library/LaunchAgents"; U=$(id -u)
[ "$(uname -m)" = arm64 ] || { echo "Apple Silicon gerekli."; exit 1; }
command -v brew >/dev/null && { brew list clamav >/dev/null 2>&1 || brew install clamav; }
CONF=/opt/homebrew/etc/clamav/freshclam.conf
[ -f "$CONF" ] || sed 's/^Example/#Example/' "$CONF.sample" > "$CONF"
mkdir -p "$J/jarvisd" "$J/ai" "$J/state" "$J/legacy" "$BIN" "$LA"
cp "$SRC"/jarvisd/*.py "$SRC"/jarvisd/*.sh "$J/jarvisd/"; cp "$SRC/jarvis.py" "$J/"
[ -f "$SRC/assets/mek.png" ] && cp "$SRC/assets/mek.png" "$J/mek.png"
[ -f "$SRC/ui/Mek.icns" ] && cp "$SRC/ui/Mek.icns" "$J/Mek.icns"
cp "$SRC"/bin/* "$BIN/"; chmod +x "$BIN/jarvis" "$BIN/workstation_master_setup.sh" "$BIN/install_siri_shortcut.sh"
ln -sf jarvis "$BIN/mek"
(swiftc -O "$SRC/ai/jarvis-ai.swift" -o "$J/ai/jarvis-ai.tmp" && mv -f "$J/ai/jarvis-ai.tmp" "$J/ai/jarvis-ai") || echo "Apple Intelligence köprüsü derlenemedi (hazır cümlelerle çalışır)."
# Native /Applications/Mek.app (AppIntents, AppleScript sdef ve Siri Beta doğrudan entegrasyonu)
mkdir -p /Applications/Mek.app/Contents/MacOS /Applications/Mek.app/Contents/Resources
swiftc -O -parse-as-library "$SRC/ui/MekMenuBar.swift" -o /Applications/Mek.app/Contents/MacOS/Mek
cp "$SRC/ui/Info.plist" /Applications/Mek.app/Contents/Info.plist
echo -n "APPL????" > /Applications/Mek.app/Contents/PkgInfo
cp "$SRC/ui/Mek.icns" /Applications/Mek.app/Contents/Resources/Mek.icns
cp "$SRC/ui/Mek.icns" /Applications/Mek.app/Contents/Resources/AppIcon.icns
cp "$SRC/ui/Mek.sdef" /Applications/Mek.app/Contents/Resources/Mek.sdef
codesign --force --deep --sign - /Applications/Mek.app
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f /Applications/Mek.app
ln -sf /Applications/Mek.app/Contents/MacOS/Mek "$J/mek-menubar"
[ -x "$J/venv/bin/python" ] || "$(command -v python3.11 || command -v python3)" -m venv "$J/venv"
"$J/venv/bin/pip" install -q -r "$SRC/requirements.txt"
for l in jarvis.periodic jarvis.startup keepawake-videoapps smart-cleaner mek; do
  launchctl bootout "gui/$U/com.alptug.$l" 2>/dev/null || true
  [ -f "$LA/com.alptug.$l.plist" ] && mv "$LA/com.alptug.$l.plist" "$J/legacy/"
done
sed "s|__HOME__|$HOME|g" "$SRC/launchd/com.alptug.jarvis.plist" > "$LA/com.alptug.jarvis.plist"
sed "s|__HOME__|$HOME|g" "$SRC/launchd/com.alptug.mek.ui.plist" > "$LA/com.alptug.mek.ui.plist"
launchctl bootout "gui/$U/com.alptug.jarvis" 2>/dev/null || true
sleep 2
launchctl bootstrap "gui/$U" "$LA/com.alptug.jarvis.plist"
launchctl bootout "gui/$U/com.alptug.mek.ui" 2>/dev/null || true
sleep 2
launchctl bootstrap "gui/$U" "$LA/com.alptug.mek.ui.plist"
echo "Mek kuruldu. Sistem alanı ve güç profilleri için bir kez:  sudo bash ~/.jarvis/jarvisd/install_privileged.sh"
