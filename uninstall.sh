#!/bin/bash
# Mek'i durdurur ve eski Jarvis ajanlarını geri yükler. Dosyalarınızı ve ~/.jarvis/state'i silmez.
U=$(id -u); LA="$HOME/Library/LaunchAgents"; J="$HOME/.jarvis"
for p in mek mek.ui; do launchctl bootout "gui/$U/com.alptug.$p" 2>/dev/null; mv "$LA/com.alptug.$p.plist" "$J/legacy/" 2>/dev/null; done
for f in "$J"/legacy/com.alptug.{jarvis.periodic,jarvis.startup,keepawake-videoapps,smart-cleaner}.plist; do
  [ -f "$f" ] && cp "$f" "$LA/" && launchctl bootstrap "gui/$U" "$LA/$(basename "$f")" 2>/dev/null
done
echo "Mek durduruldu, eski ajanlar geri yüklendi."
