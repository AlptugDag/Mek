#!/bin/bash
# Mek'i durdurur. ~/.jarvis/state ve karantina klasörüne dokunmaz.
U=$(id -u); LA="$HOME/Library/LaunchAgents"
launchctl bootout "gui/$U/com.alptug.jarvis" 2>/dev/null
launchctl bootout "gui/$U/com.alptug.mek.ui" 2>/dev/null
mv "$LA/com.alptug.jarvis.plist" "$HOME/.jarvis/legacy/" 2>/dev/null
mv "$LA/com.alptug.mek.ui.plist" "$HOME/.jarvis/legacy/" 2>/dev/null
echo "Mek durduruldu."
