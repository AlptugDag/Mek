#!/bin/bash
# Mek kurulumu (legacy ~/.jarvis dizinini kullanır). Eski ayrı ajanları durdurur.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
J="$HOME/.jarvis"; BIN="$HOME/.local/bin"; LA="$HOME/Library/LaunchAgents"; U=$(id -u)
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

[ "$(uname -m)" = arm64 ] || { echo "Apple Silicon gerekli."; exit 1; }
command -v swiftc >/dev/null || {
  echo "swiftc bulunamadı. Önce Command Line Tools kurun:  xcode-select --install" >&2
  exit 1
}

# --- ClamAV (isteğe bağlı) --------------------------------------------------
# Eskiden clamav kurulu değilken $CONF.sample da bulunmadığı için sed hata
# veriyor ve `set -e` yüzünden kurulumun tamamı yarıda kalıyordu.
if command -v brew >/dev/null; then
  brew list clamav >/dev/null 2>&1 || brew install clamav || echo "ClamAV kurulamadı; virüs taraması devre dışı kalacak."
  CONF=/opt/homebrew/etc/clamav/freshclam.conf
  if [ ! -f "$CONF" ] && [ -f "$CONF.sample" ]; then
    sed 's/^Example/#Example/' "$CONF.sample" > "$CONF"
  fi
fi

# --- dosyalar ---------------------------------------------------------------
mkdir -p "$J/jarvisd" "$J/ai" "$J/state" "$J/legacy" "$BIN" "$LA"
cp "$SRC"/jarvisd/*.py "$SRC"/jarvisd/*.sh "$J/jarvisd/"
cp "$SRC/jarvis.py" "$J/"
[ -f "$SRC/assets/mek.png" ] && cp "$SRC/assets/mek.png" "$J/mek.png"
[ -f "$SRC/ui/Mek.icns" ] && cp "$SRC/ui/Mek.icns" "$J/Mek.icns"
# bin/ içindeki her şey kopyalanır ve çalıştırılabilir yapılır. Dosya adlarını
# tek tek saymak, bin/ içeriği her değiştiğinde kurulumu bozuyordu.
for f in "$SRC"/bin/*; do
  [ -f "$f" ] || continue          # __pycache__ gibi dizinleri atla
  install -m 755 "$f" "$BIN/$(basename "$f")"
done
ln -sf jarvis "$BIN/mek"

# --- Apple Intelligence köprüsü --------------------------------------------
(swiftc -O "$SRC/ai/jarvis-ai.swift" -o "$J/ai/jarvis-ai.tmp" && mv -f "$J/ai/jarvis-ai".tmp "$J/ai/jarvis-ai") \
  || echo "Apple Intelligence köprüsü derlenemedi (hazır cümlelerle çalışır)."

# --- /Applications/Mek.app (Siri App Intents dahil) -------------------------
# Siri entegrasyonunun tamamı burada: build_mek_app.sh, App Intents meta
# verisini üretip paketin içine yazar. Kestirmeler uygulaması kullanılmaz.
"$SRC/bin/build_mek_app.sh" /Applications/Mek.app
ln -sf /Applications/Mek.app/Contents/MacOS/Mek "$J/mek-menubar"

# --- python ortamı ----------------------------------------------------------
[ -x "$J/venv/bin/python" ] || "$(command -v python3.11 || command -v python3)" -m venv "$J/venv"
"$J/venv/bin/pip" install -q --upgrade pip
"$J/venv/bin/pip" install -q -r "$SRC/requirements.txt"

# --- eski ajanları temizle --------------------------------------------------
for l in jarvis.periodic jarvis.startup keepawake-videoapps smart-cleaner mek; do
  launchctl bootout "gui/$U/com.alptug.$l" 2>/dev/null || true
  [ -f "$LA/com.alptug.$l.plist" ] && mv "$LA/com.alptug.$l.plist" "$J/legacy/"
done
# Eski Kestirmeler tabanlı Siri köprüsü artık kullanılmıyor.
rm -f "$BIN/install_siri_shortcut.sh"

# --- launchd servisleri -----------------------------------------------------
sed "s|__HOME__|$HOME|g" "$SRC/launchd/com.alptug.jarvis.plist" > "$LA/com.alptug.jarvis.plist"
sed "s|__HOME__|$HOME|g" "$SRC/launchd/com.alptug.mek.ui.plist" > "$LA/com.alptug.mek.ui.plist"

for label in com.alptug.jarvis com.alptug.mek.ui; do
  launchctl bootout "gui/$U/$label" 2>/dev/null || true
done
sleep 2
launchctl bootstrap "gui/$U" "$LA/com.alptug.jarvis.plist"
launchctl bootstrap "gui/$U" "$LA/com.alptug.mek.ui.plist"

# --- Siri'nin Mek'i görmesi --------------------------------------------------
# macOS'un App Intents dizinleyicisi (linkd) yalnızca "güvenilir" paketleri
# okur; yerel derlemede bu, karantina damgasının olmaması ve uygulamanın en az
# bir kez çalışmış olması demektir. Menü çubuğu ajanı yukarıda başlatıldı;
# şimdi yeniden kaydederek dizinleyiciyi tetikliyoruz.
sleep 3
"$LSREG" -f /Applications/Mek.app

cat <<'EOF'

Mek kuruldu.

Siri (Kestirmeler uygulaması olmadan, doğrudan):
  "Hey Siri, Mek"                      -> Mek ne istediğinizi sorar
  "Hey Siri, Mek durum"
  "Hey Siri, Mek brifing"
  "Hey Siri, Mek karantinada ne var"
  "Hey Siri, Mek kurgu modu"
  Yazarak kullanmak için: Sistem Ayarları > Erişilebilirlik > Siri >
  "Siri'ye Yazın" seçeneğini açın.

Sistem alanı ve güç profilleri için bir kez:
  sudo bash ~/.jarvis/jarvisd/install_privileged.sh
EOF
