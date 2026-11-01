#!/bin/bash
# ==============================================================================
# macOS M1 Max Pro Workstation — Self-Contained Master Performance Enforcer
# Location: ~/.local/bin/workstation_master_setup.sh
# Completely independent of Antigravity. Automatically maintains all macOS,
# DaVinci Resolve, Adobe Premiere Pro, Media Encoder, and Topaz Video settings.
# ==============================================================================

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# 1. Raise File Descriptor Limits
/bin/launchctl limit maxfiles 65536 200000 2>/dev/null

# 2. Enforce DaVinci Resolve Studio Settings (only when Resolve is not running)
RESOLVE_PREF_DIR="$HOME/Library/Preferences/Blackmagic Design/DaVinci Resolve"
if ! pgrep -f "Contents/MacOS/Resolve" > /dev/null 2>&1; then
    if [ -f "$RESOLVE_PREF_DIR/config.user.xml" ]; then
        # Ensure AutoSave, Live Save, and Timeline Backups remain strictly OFF
        sed -i '' \
            -e 's|<AutoSaveMode>[0-9]*</AutoSaveMode>|<AutoSaveMode>0</AutoSaveMode>|g' \
            -e 's|<IsLiveSaveEnabled>true</IsLiveSaveEnabled>|<IsLiveSaveEnabled>false</IsLiveSaveEnabled>|g' \
            -e 's|<IsProjectLiveSaveEnabled>true</IsProjectLiveSaveEnabled>|<IsProjectLiveSaveEnabled>false</IsProjectLiveSaveEnabled>|g' \
            -e 's|<IsTimelineBackupsEnabled>true</IsTimelineBackupsEnabled>|<IsTimelineBackupsEnabled>false</IsTimelineBackupsEnabled>|g' \
            -e 's|<IsStopRenderOnError>true</IsStopRenderOnError>|<IsStopRenderOnError>false</IsStopRenderOnError>|g' \
            -e 's|<FusionMemoryCacheMode>FUSION_MEMORY_CACHE_MODE_OFF</FusionMemoryCacheMode>|<FusionMemoryCacheMode>FUSION_MEMORY_CACHE_MODE_AUTO</FusionMemoryCacheMode>|g' \
            "$RESOLVE_PREF_DIR/config.user.xml" 2>/dev/null
    fi
    if [ -f "$RESOLVE_PREF_DIR/config.dat" ]; then
        sed -i '' \
            -e 's|^Local.Resource.Buffers.1 = .*|Local.Resource.Buffers.1 = 49152|g' \
            -e 's|^Bg.Tasks.EnableBgTranscription = .*|Bg.Tasks.EnableBgTranscription = 0|g' \
            "$RESOLVE_PREF_DIR/config.dat" 2>/dev/null
    fi
    if [ -f "$RESOLVE_PREF_DIR/.config.data" ]; then
        sed -i '' \
            -e 's|^Memory = .*|Memory = 49152|g' \
            -e 's|^Bg.Tasks.EnableBgTranscription = .*|Bg.Tasks.EnableBgTranscription = 0|g' \
            "$RESOLVE_PREF_DIR/.config.data" 2>/dev/null
    fi
fi

# 3. Enforce Topaz Video AI Peak Performance Settings (when Topaz is not running)
if ! pgrep -f "Contents/MacOS/Topaz Video" > /dev/null 2>&1; then
    defaults write "com.topazlabs.Topaz Video" maxMemoryUsage -int 100 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" gpuDeviceID -int 0 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" hardwareDecodingEnabled -int 1 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" powerSaverMode -int 0 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" liveExportEnabled -int 0 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" exportWithLivePreviewEnabled -int 0 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" fileLoggingEnabled -int 0 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" allowAnonymousDataCollection -int 0 2>/dev/null
    defaults write "com.topazlabs.Topaz Video" NSAppSleepDisabled -bool YES 2>/dev/null
    defaults write "com.topazlabs.Topaz-Video" NSAppSleepDisabled -bool YES 2>/dev/null
fi

# 4. Enforce App Nap Disabled Across All Pro Creative Apps
for bid in \
    "com.blackmagic-design.DaVinciResolve" \
    "com.adobe.PremierePro.26" \
    "com.adobe.ame.application.26" \
    "com.adobe.AfterEffects.application" \
    "com.adobe.Audition" \
    "com.adobe.Photoshop" \
    "com.topazlabs.Topaz-Video" \
    "ai.colourlab.editor" \
    "com.obsproject.obs-studio" \
    "UVR" \
    "com.rogueamoeba.farrago" \
    "com.prosoundeffects.soundq"
do
    defaults write "$bid" NSAppSleepDisabled -bool YES 2>/dev/null
done

# 5. Enforce Spotlight Exclusions (.metadata_never_index) on All Heavy Cache/Temp Folders
for dir in \
    "$HOME/Movies/CacheClip" \
    "$HOME/Movies/.gallery" \
    "$HOME/Movies/Resolve Project Backups" \
    "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.BGRender" \
    "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.BGTranscribe" \
    "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.LUT" \
    "$HOME/Library/Application Support/Adobe/Common/Media Cache Files" \
    "$HOME/Library/Application Support/Adobe/Common/Media Cache" \
    "$HOME/Library/Application Support/Adobe/Common/Peak Files" \
    "$HOME/Library/Application Support/Adobe/Common/PTX" \
    "$HOME/Documents/Topaz Video Projects" \
    "$HOME/Documents/Topaz Video Projects/Default/previews" \
    "$HOME/Documents/Topaz Video Projects/Default/thumbnails" \
    "$HOME/Documents/Topaz Video Projects/Default/cpe" \
    "$HOME/Library/Application Support/Topaz Labs LLC/Topaz Video" \
    "$HOME/Library/Application Support/Topaz Labs LLC/Topaz Video/models" \
    "$HOME/Library/Application Support/Topaz Labs LLC/Topaz Video/logs" \
    "$HOME/.cache" \
    "$HOME/.npm" \
    "$HOME/Library/Logs" \
    "$HOME/Library/Caches"
do
    if [ -d "$dir" ]; then
        touch "$dir/.metadata_never_index" 2>/dev/null
    fi
done

exit 0
