#!/bin/bash
# Bir kez SİZ çalıştırın:  sudo bash ~/.jarvis/jarvisd/install_privileged.sh
# Mek'e yalnızca aşağıdaki sabit komutlar için parolasız yetki verir (başka hiçbir şey).
set -e
[ "$(id -u)" = 0 ] || { echo "sudo ile çalıştırın"; exit 1; }
USER_NAME="${SUDO_USER:?}"
install -d -o root -g wheel -m 755 /usr/local/libexec
cat > /usr/local/libexec/mek-priv <<'H'
#!/bin/bash
# Mek ayrıcalıklı yardımcı: yalnızca sabit alt komutlar, dışarıdan yol/argüman almaz
DAEMONS="/Library/LaunchDaemons/com.google.keystone.daemon.plist /Library/LaunchDaemons/com.google.GoogleUpdater.wake.system.plist /Library/LaunchDaemons/com.logi.optionsplus.updater.plist"
HOSTS=/etc/hosts
ADBLOCK_URL=https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts
HPM_STATE=/var/db/mek-highpowermode
UPS_SHUTDOWN_STATE=/var/db/mek-ups-shutdown

enable_high_power() {
  ac_mode=$(/usr/bin/pmset -g custom | /usr/bin/awk '/^AC Power:/ { ac=1; next } /^Battery Power:/ { ac=0 }
    ac && $1 == "highpowermode" { print $2; exit }')
  case "$ac_mode" in
    0|1)
      if [ ! -f "$HPM_STATE" ]; then
        printf '%s\n' "$ac_mode" > "$HPM_STATE"
        /bin/chmod 600 "$HPM_STATE"
      fi
      /usr/bin/pmset -c highpowermode 1 || { /bin/rm -f "$HPM_STATE"; return 0; };;
  esac
}

restore_high_power() {
  if [ -f "$HPM_STATE" ]; then
    saved=$(/bin/cat "$HPM_STATE")
    case "$saved" in
      0|1) /usr/bin/pmset -c highpowermode "$saved" || return 1;;
    esac
    /bin/rm -f "$HPM_STATE"
  fi
}

flush_dns() {
  /usr/bin/dscacheutil -flushcache 2>/dev/null || true
  /usr/bin/killall -HUP mDNSResponder 2>/dev/null || true
}

strip_adblock() {
  /usr/bin/awk '
    $0 == "# BEGIN MEK AD BLOCK" { if (inside || seen) { invalid=1; exit }; inside=1; seen=1; next }
    $0 == "# END MEK AD BLOCK" { if (!inside) { invalid=1; exit }; inside=0; next }
    !inside { print }
    END { if (inside || invalid) exit 2 }
  ' "$HOSTS"
}

enable_adblock() {
  [ -f "$HOSTS" ] && [ ! -L "$HOSTS" ] || { echo "hosts dosyası normal dosya değil." >&2; exit 1; }
  download=$(/usr/bin/mktemp /tmp/mek-adblock-download.XXXXXX) || exit 1
  blocks=$(/usr/bin/mktemp /tmp/mek-adblock-blocks.XXXXXX) || exit 1
  base=$(/usr/bin/mktemp /etc/hosts.mek-base.XXXXXX) || exit 1
  combined=$(/usr/bin/mktemp /etc/hosts.mek-new.XXXXXX) || exit 1
  trap 'rm -f "$download" "$blocks" "$base" "$combined"' EXIT
  /usr/bin/curl --fail --location --silent --show-error --max-time 120 --max-filesize 30000000 \
    --output "$download" "$ADBLOCK_URL" || { echo "Blok listesi indirilemedi; hosts değiştirilmedi." >&2; exit 1; }
  /usr/bin/awk '
    /^[[:space:]]*#/ { next }
    NF >= 2 && ($1 == "0.0.0.0" || $1 == "127.0.0.1") {
      for (i=2; i<=NF; i++) {
        host=tolower($i)
        if (host ~ /^[a-z0-9][a-z0-9.-]*[a-z0-9]$/ && host ~ /\./ && host !~ /\.\./ &&
            host !~ /^[0-9.]+$/ && host !~ /(^|\.)localhost$/ && host !~ /(^|\.)local$/)
          print "0.0.0.0 " host
      }
    }
  ' "$download" | /usr/bin/sort -u > "$blocks"
  [ -s "$blocks" ] || { echo "İndirilen listede geçerli alan adı yok; hosts değiştirilmedi." >&2; exit 1; }
  if ! strip_adblock > "$base"; then
    echo "hosts içindeki Mek işaretleri bozuk; dosya değiştirilmedi." >&2
    exit 1
  fi
  /bin/cat "$base" > "$combined" || exit 1
  printf '\n# BEGIN MEK AD BLOCK\n' >> "$combined" || exit 1
  /bin/cat "$blocks" >> "$combined" || exit 1
  printf '# END MEK AD BLOCK\n' >> "$combined" || exit 1
  /usr/sbin/chown root:wheel "$combined" || exit 1
  /bin/chmod 644 "$combined" || exit 1
  /bin/mv "$combined" "$HOSTS" || exit 1
  flush_dns
  echo "Hosts engelleyici etkin. Engellenen alan adı: $(/usr/bin/wc -l < "$blocks" | /usr/bin/tr -d ' ')."
}

disable_adblock() {
  [ -f "$HOSTS" ] && [ ! -L "$HOSTS" ] || { echo "hosts dosyası normal dosya değil." >&2; exit 1; }
  if ! /usr/bin/grep -Fq "# BEGIN MEK AD BLOCK" "$HOSTS"; then
    echo "Hosts engelleyici zaten kapalı."
    return 0
  fi
  clean=$(/usr/bin/mktemp /etc/hosts.mek-clean.XXXXXX) || exit 1
  trap 'rm -f "$clean"' EXIT
  if ! strip_adblock > "$clean"; then
    echo "hosts içindeki Mek işaretleri bozuk; dosya değiştirilmedi." >&2
    exit 1
  fi
  /usr/sbin/chown root:wheel "$clean" || exit 1
  /bin/chmod 644 "$clean" || exit 1
  /bin/mv "$clean" "$HOSTS" || exit 1
  flush_dns
  echo "Hosts engelleyici kapatıldı; Mek dışındaki hosts kayıtları korundu."
}

software_update_install() {
  [ "$#" = 2 ] || { echo "Bir update etiketi gerekli." >&2; exit 2; }
  label="$2"
  case "$label" in
    ""|*[!A-Za-z0-9._\ \(\)+:-]*) echo "Geçersiz update etiketi." >&2; exit 2;;
  esac
  available=$(/usr/sbin/softwareupdate --list 2>&1) || { echo "Update listesi okunamadı." >&2; exit 1; }
    printf '%s\n' "$available" | /usr/bin/awk -v label="$label" '
      /^[[:space:]]*\*[[:space:]]*Label:/ {
        item=$0
        sub(/^[[:space:]]*\*[[:space:]]*Label:[[:space:]]*/, "", item)
        active=(item == label)
        if (active && /Recommended:[[:space:]]*YES/) found=1
        next
      }
      active && /Recommended:[[:space:]]*YES/ { found=1 }
      END { exit !found }
    ' || { echo "Önerilen update etiketi bulunamadı; kurulum yapılmadı." >&2; exit 1; }
  case "$label" in
    *macOS*)
      current_major=$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)
      target_major=$(printf '%s\n' "$label" | /usr/bin/sed -E 's/.*[^0-9]([0-9]+)\.[0-9]+.*/\1/')
      [ "$target_major" = "$current_major" ] || {
        echo "Büyük macOS sürüm yükseltmeleri otomatik kurulmaz." >&2; exit 2;
      };;
  esac
  /usr/sbin/softwareupdate --install "$label"
}

case "$1" in
  perf)
    /usr/bin/pmset -a powernap 0 disksleep 0 standby 0
    enable_high_power
    for d in $DAEMONS; do [ -f "$d" ] && /bin/launchctl bootout system "$d" 2>/dev/null; done; true;;
  normal)
    /usr/bin/pmset -a powernap 0 disksleep 10 standby 1
    restore_high_power
    for d in $DAEMONS; do [ -f "$d" ] && /bin/launchctl bootstrap system "$d" 2>/dev/null; done; true;;
  purge) /usr/sbin/purge;;
  clean-system)
    # Yalnızca yeniden oluşturulabilen sistem verisi; yaşa göre
    old() { [ -d "$1" ] && /usr/bin/find "$1" -mindepth 1 -xdev -type f -mtime +"$2" -delete 2>/dev/null
            [ -d "$1" ] && /usr/bin/find "$1" -mindepth 1 -xdev -type d -empty -delete 2>/dev/null; true; }
    old /Library/Caches 7
    old /Library/Logs 7
    old /Library/Logs/DiagnosticReports 7
    old /private/var/log 7
    old /private/var/db/diagnostics/Persist 14
    old /private/var/tmp 3
    for t in /private/var/folders/*/*/T; do old "$t" 3; done
    old /Library/Updates 30
    /usr/sbin/periodic daily weekly monthly >/dev/null 2>&1
    # Disk kritik dolulukta: Time Machine yerel anlık görüntülerini incelt (yedekler diskte kalır)
    [ "$2" = "--snapshots" ] && /usr/bin/tmutil thinlocalsnapshots / 999999999999 4 >/dev/null 2>&1
    true;;
  adblock-enable) enable_adblock;;
  adblock-disable) disable_adblock;;
  restore-high-power) restore_high_power;;
  software-update-install) software_update_install "$@";;
  ups-shutdown)
    [ "$#" = 1 ] || { echo "Argüman kabul edilmiyor." >&2; exit 2; }
    battery=$(/usr/bin/pmset -g batt 2>/dev/null) || exit 1
    ups_line=$(printf '%s\n' "$battery" | /usr/bin/awk 'tolower($0) ~ /ups/ && /[0-9]+%/ { print; exit }')
    printf '%s\n' "$ups_line" | /usr/bin/grep -qi discharging || {
      echo "Deşarj olan UPS doğrulanamadı; kapatma yapılmadı." >&2; exit 1;
    }
    percent=$(printf '%s\n' "$ups_line" | /usr/bin/grep -Eo '[0-9]{1,3}%' | /usr/bin/head -1 | /usr/bin/tr -d '%')
    remaining=$(printf '%s\n' "$ups_line" | /usr/bin/grep -Eo '[0-9]+:[0-9]+ remaining' | /usr/bin/head -1 | /usr/bin/awk -F '[: ]' '{ print $1 * 60 + $2 }')
    critical=false
    [ -n "$percent" ] && [ "$percent" -le 15 ] && critical=true
    [ -n "$remaining" ] && [ "$remaining" -le 5 ] && critical=true
    [ "$critical" = true ] || { echo "UPS kritik eşikte değil; kapatma yapılmadı." >&2; exit 1; }
    /sbin/shutdown -h +1 "Mek: UPS battery critical; graceful shutdown in one minute." || exit 1
    /usr/bin/touch "$UPS_SHUTDOWN_STATE" || { /sbin/shutdown -c; exit 1; }
    /usr/sbin/chown root:wheel "$UPS_SHUTDOWN_STATE" || {
      /sbin/shutdown -c; /bin/rm -f "$UPS_SHUTDOWN_STATE"; exit 1;
    }
    /bin/chmod 600 "$UPS_SHUTDOWN_STATE" || {
      /sbin/shutdown -c; /bin/rm -f "$UPS_SHUTDOWN_STATE"; exit 1;
    };;
  ups-cancel)
    [ "$#" = 1 ] || { echo "Argüman kabul edilmiyor." >&2; exit 2; }
    if [ -f "$UPS_SHUTDOWN_STATE" ]; then
      /sbin/shutdown -c && /bin/rm -f "$UPS_SHUTDOWN_STATE"
    fi;;
  *) exit 2;;
esac
H
chown root:wheel /usr/local/libexec/mek-priv; chmod 755 /usr/local/libexec/mek-priv
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/local/libexec/mek-priv" > /etc/sudoers.d/mek
chmod 440 /etc/sudoers.d/mek
visudo -cf /etc/sudoers.d/mek && echo "Kuruldu."
