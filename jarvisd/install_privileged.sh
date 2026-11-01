#!/bin/bash
# Bir kez SİZ çalıştırın:  sudo bash ~/.jarvis/jarvisd/install_privileged.sh
# Jarvis'e yalnızca aşağıdaki sabit komutlar için parolasız yetki verir (başka hiçbir şey).
set -e
[ "$(id -u)" = 0 ] || { echo "sudo ile çalıştırın"; exit 1; }
USER_NAME="${SUDO_USER:?}"
install -d -o root -g wheel -m 755 /usr/local/libexec
cat > /usr/local/libexec/mek-priv <<'H'
#!/bin/bash
# Jarvis ayrıcalıklı yardımcı: yalnızca sabit alt komutlar, dışarıdan yol/argüman almaz
DAEMONS="/Library/LaunchDaemons/com.google.keystone.daemon.plist /Library/LaunchDaemons/com.google.GoogleUpdater.wake.system.plist /Library/LaunchDaemons/com.logi.optionsplus.updater.plist /Library/LaunchDaemons/com.redgiant.service.plist"
case "$1" in
  perf)
    /usr/bin/pmset -a powernap 0 disksleep 0 standby 0
    for d in $DAEMONS; do [ -f "$d" ] && /bin/launchctl bootout system "$d" 2>/dev/null; done; true;;
  normal)
    /usr/bin/pmset -a powernap 0 disksleep 10 standby 1
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
  *) exit 2;;
esac
H
chown root:wheel /usr/local/libexec/mek-priv; chmod 755 /usr/local/libexec/mek-priv
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/local/libexec/mek-priv" > /etc/sudoers.d/mek
chmod 440 /etc/sudoers.d/mek
visudo -cf /etc/sudoers.d/mek && echo "Kuruldu."
