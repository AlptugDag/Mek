#!/bin/bash
# Bir kez SİZ çalıştırın:  sudo bash ~/.jarvis/mek/install_privileged.sh
# Mek'e yalnızca aşağıdaki sabit komutlar için parolasız yetki verir (başka hiçbir şey).
set -e
[ "$(id -u)" = 0 ] || { echo "sudo ile çalıştırın"; exit 1; }
USER_NAME="${SUDO_USER:?}"
install -d -o root -g wheel -m 755 /usr/local/libexec
cat > /usr/local/libexec/mek-priv <<'H'
#!/bin/bash
# Mek ayrıcalıklı yardımcı: yalnızca sabit alt komutlar
DAEMONS="/Library/LaunchDaemons/com.google.keystone.daemon.plist /Library/LaunchDaemons/com.google.GoogleUpdater.wake.system.plist /Library/LaunchDaemons/com.logi.optionsplus.updater.plist /Library/LaunchDaemons/com.redgiant.service.plist"
case "$1" in
  perf)
    /usr/bin/pmset -a powernap 0 disksleep 0 standby 0
    for d in $DAEMONS; do [ -f "$d" ] && /bin/launchctl bootout system "$d" 2>/dev/null; done; true;;
  normal)
    /usr/bin/pmset -a powernap 0 disksleep 10 standby 1
    for d in $DAEMONS; do [ -f "$d" ] && /bin/launchctl bootstrap system "$d" 2>/dev/null; done; true;;
  purge) /usr/sbin/purge;;
  *) exit 2;;
esac
H
chown root:wheel /usr/local/libexec/mek-priv; chmod 755 /usr/local/libexec/mek-priv
echo "$USER_NAME ALL=(root) NOPASSWD: /usr/local/libexec/mek-priv" > /etc/sudoers.d/mek
chmod 440 /etc/sudoers.d/mek
visudo -cf /etc/sudoers.d/mek && echo "Kuruldu."
