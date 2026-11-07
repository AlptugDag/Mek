#!/bin/bash
# /Applications/Mek.app paketini derler, App Intents meta verisini üretir,
# imzalar ve LaunchServices'e kaydeder.
#
# Siri entegrasyonunun tamamı buradan gelir: Kestirmeler uygulamasına hiç
# dokunmadan, App Intents meta verisi doğrudan .app paketine yazılır ve
# macOS'un App Intents dizinleyicisi (linkd) onu okuyup Siri'ye bildirir.
#
# Tam Xcode gerekmez; Command Line Tools yeterlidir.  Xcode'un
# ``appintentsmetadataprocessor`` aracının yerini bin/appintents_metadata.py
# alır (swiftc'nin -emit-const-values çıktısını kullanır).
#
# Kullanım:  build_mek_app.sh [hedef-app-yolu]
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-/Applications/Mek.app}"
MODULE="Mek"
BUILD="$(mktemp -d "${TMPDIR:-/tmp}/mek-build.XXXXXX")"
trap 'rm -rf "$BUILD"' EXIT

command -v swiftc >/dev/null || {
  echo "hata: swiftc bulunamadı. Xcode Command Line Tools kurun:" >&2
  echo "       xcode-select --install" >&2
  exit 1
}

# swiftc'ye hangi protokollere ait derleme zamanı bilgisini toplayacağını
# söyleyen liste. Xcode da aynı mekanizmayı kullanır.
cat > "$BUILD/protocols.json" <<'JSON'
["AppIntent","AppShortcutsProvider","AppEntity","AppEnum","EntityQuery",
 "EntityStringQuery","DynamicOptionsProvider","PersistentlyIdentifiable"]
JSON

echo "==> Mek.app derleniyor (module: $MODULE)"
# -module-name sabit tutulmalı: App Intents meta verisindeki "mangled" tip
# adları modül adını içerir; değişirse Siri intent'i çözemez.
swiftc -O -wmo -parse-as-library -module-name "$MODULE" \
  -emit-const-values -emit-const-values-path "$BUILD/$MODULE.swiftconstvalues" \
  -Xfrontend -const-gather-protocols-file -Xfrontend "$BUILD/protocols.json" \
  -o "$BUILD/$MODULE" "$SRC/ui/MekMenuBar.swift"

[ -s "$BUILD/$MODULE.swiftconstvalues" ] || {
  echo "hata: swiftc App Intents meta verisini üretemedi" >&2
  echo "      ($BUILD/$MODULE.swiftconstvalues boş). Swift 5.9+ gerekir." >&2
  exit 1
}

echo "==> Paket oluşturuluyor: $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
install -m 755 "$BUILD/$MODULE" "$APP/Contents/MacOS/Mek"
install -m 644 "$SRC/ui/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
install -m 644 "$SRC/ui/Mek.sdef" "$APP/Contents/Resources/Mek.sdef"
for icon in Mek.icns AppIcon.icns; do
  install -m 644 "$SRC/ui/Mek.icns" "$APP/Contents/Resources/$icon"
done

echo "==> App Intents meta verisi üretiliyor (Siri için)"
python3 "$SRC/bin/appintents_metadata.py" \
  --constvalues "$BUILD/$MODULE.swiftconstvalues" \
  --bundle "$APP"

echo "==> İmzalanıyor"
# Yerel kullanım için ad-hoc imza yeterli. --deep kullanmıyoruz (Apple
# tarafından önerilmiyor ve iç içe paket yok).
codesign --force --sign - --identifier com.alptug.mek "$APP"

# App Intents dizinleyicisi (linkd) karantina damgalı paketleri "güvenilir
# değil" sayıp atlar; yerel derlemede damga olmamalı.
xattr -cr "$APP" 2>/dev/null || true

echo "==> LaunchServices'e kaydediliyor"
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREG" -f "$APP"

echo "Mek.app hazır: $APP"
