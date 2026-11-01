#!/bin/bash
# ==============================================================================
# Smart System & Video Production Cache Cleaner (macOS M1 Max Edition)
# Runs automatically via launchd:
#   1. Monthly Deep Clean (every 30 days)
#   2. On-Demand ("Gerektikçe") Smart Clean when:
#      - Total video/system cache exceeds 35 GB, OR
#      - Free disk space on / drops below 100 GB
#   3. Defers automatically if DaVinci Resolve, Premiere Pro, Media Encoder,
#      After Effects, or Topaz Video AI is actively running/rendering.
# ==============================================================================

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"

MODE="${1:---auto}"
STATE_DIR="$HOME/.local/state"
LOG_DIR="$HOME/.local/logs"
LOG_FILE="$LOG_DIR/smart_cleaner.log"
LAST_MONTHLY_FILE="$STATE_DIR/smart_cleaner_last_monthly"
LOCK_FILE="/tmp/com.alptug.smart_cleaner.lock"

# Thresholds for "Gerektikçe" (On-Demand) trigger
MIN_FREE_GB=100
MAX_CACHE_GB=35
MONTHLY_INTERVAL_SEC=$((30 * 86400))

mkdir -p "$STATE_DIR" "$LOG_DIR"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

# Prevent concurrent executions
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT INT TERM HUP

# ------------------------------------------------------------------------------
# 1. Safety Check: Never clean while video/creative apps are running or rendering
# ------------------------------------------------------------------------------
if pgrep -f "Contents/MacOS/(Resolve|Adobe Premiere Pro|Adobe Media Encoder|PProHeadless|After Effects|Adobe Photoshop|Adobe Audition|Topaz Video|ffmpeg|neuroserver)|DaVinci Resolve Helper" > /dev/null 2>&1; then
    if [ "$MODE" = "--force" ]; then
        osascript -e 'display notification "DaVinci Resolve veya Adobe uygulamaları açıkken güvenlik için temizlik ertelendi." with title "Akıllı Temizleyici ⏸️"' 2>/dev/null
    fi
    log "SKIP: Active video/creative application detected. Deferring cleanup."
    exit 0
fi

# ------------------------------------------------------------------------------
# 2. Measure Disk Space & Cache Footprint
# ------------------------------------------------------------------------------
free_kb() {
    df -k / | awk 'NR==2 {print $4}'
}

dir_kb() {
    local total=0 kb p
    for p in "$@"; do
        [ -e "$p" ] || continue
        kb=$(du -sk "$p" 2>/dev/null | awk '{print $1}')
        total=$((total + ${kb:-0}))
    done
    printf '%s' "$total"
}

human_kb() {
    awk -v kb="$1" 'BEGIN {
        if (kb < 0) kb = 0;
        if (kb >= 1048576) printf "%.2f GB", kb / 1048576;
        else if (kb >= 1024) printf "%.1f MB", kb / 1024;
        else printf "%d KB", kb;
    }'
}

NOW_TS=$(date +%s)
LAST_MONTHLY_TS=0
if [ -f "$LAST_MONTHLY_FILE" ]; then
    LAST_MONTHLY_TS=$(cat "$LAST_MONTHLY_FILE" 2>/dev/null || echo 0)
fi
AGE_SINCE_MONTHLY=$((NOW_TS - LAST_MONTHLY_TS))

FREE_BEFORE_KB=$(free_kb)
FREE_BEFORE_GB=$((FREE_BEFORE_KB / 1048576))

CACHE_TOTAL_KB=$(dir_kb \
    "$HOME/Movies/CacheClip" \
    "$HOME/Library/Application Support/Adobe/Common/Media Cache Files" \
    "$HOME/Library/Application Support/Adobe/Common/Media Cache" \
    "$HOME/Library/Application Support/Adobe/Common/Peak Files" \
    "$HOME/Library/Caches/Adobe" \
    "$HOME/Library/Logs" \
    "$HOME/Library/Developer/Xcode/DerivedData")
CACHE_TOTAL_GB=$((CACHE_TOTAL_KB / 1048576))

REASON=""
CACHE_AGE_DAYS=14

if [ "$MODE" = "--force" ]; then
    REASON="Manuel / İlk Kurulum Temizliği"
    CACHE_AGE_DAYS=7
elif [ "$AGE_SINCE_MONTHLY" -ge "$MONTHLY_INTERVAL_SEC" ]; then
    REASON="Aylık Otomatik Bakım"
    CACHE_AGE_DAYS=7
elif [ "$FREE_BEFORE_GB" -lt "$MIN_FREE_GB" ]; then
    REASON="Boş Disk Alanı Eşiği (<${MIN_FREE_GB} GB)"
    CACHE_AGE_DAYS=7
elif [ "$CACHE_TOTAL_GB" -ge "$MAX_CACHE_GB" ]; then
    REASON="Önbellek Boyutu Eşiği (${CACHE_TOTAL_GB} GB >= ${MAX_CACHE_GB} GB)"
    CACHE_AGE_DAYS=10
else
    # No cleanup needed right now
    exit 0
fi

log "START: Running Smart System Cleaner (Reason: $REASON, Free: ${FREE_BEFORE_GB}GB, Cache: ${CACHE_TOTAL_GB}GB)"

# Run at background I/O & CPU priority so the Mac never stutters
/usr/sbin/taskpolicy -b -p $$ 2>/dev/null
renice 15 -p $$ >/dev/null 2>&1

# Helper: safely delete files older than N days inside a directory while preserving .metadata_never_index
clean_dir_older_than() {
    local target_dir="$1"
    local days="$2"
    [ -d "$target_dir" ] || return 0
    find "$target_dir" -mindepth 1 -not -name ".metadata_never_index" -type f -mtime +"$days" -delete 2>/dev/null
    find "$target_dir" -mindepth 1 -not -name ".metadata_never_index" -type d -empty -delete 2>/dev/null
}

# Helper: safely empty a pure cache directory while preserving .metadata_never_index
empty_cache_dir() {
    local target_dir="$1"
    [ -d "$target_dir" ] || return 0
    find "$target_dir" -mindepth 1 -not -name ".metadata_never_index" -delete 2>/dev/null
}

# ------------------------------------------------------------------------------
# 3. DaVinci Resolve — Safe Cache Cleanup (Protects .gallery Stills & Backups!)
# ------------------------------------------------------------------------------
# Clean render cache clips older than CACHE_AGE_DAYS so active projects stay cached
clean_dir_older_than "$HOME/Movies/CacheClip" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.BGRender" 3
clean_dir_older_than "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.BGTranscribe" 7
clean_dir_older_than "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/logs/LogArchive" 14
# NOTE: ~/Movies/.gallery (Color Stills/PowerGrades) and ~/Movies/Resolve Project Backups are NEVER deleted!

# ------------------------------------------------------------------------------
# 4. Adobe Suite (Premiere Pro, Media Encoder, After Effects, Photoshop, Audition)
# ------------------------------------------------------------------------------
# Clean Media Cache & Peak Files older than CACHE_AGE_DAYS (keeps active projects fast!)
clean_dir_older_than "$HOME/Library/Application Support/Adobe/Common/Media Cache Files" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Library/Application Support/Adobe/Common/Media Cache" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Library/Application Support/Adobe/Common/Peak Files" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Library/Application Support/Adobe/Common/Analyzer Cache Files" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Library/Application Support/Adobe/Common/PTX" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Library/Application Support/Adobe/Common/Cache" "$CACHE_AGE_DAYS"

# Profile-level Premiere caches
for d in "$HOME/Documents/Adobe/Premiere Pro"/*/Profile-*/"Media Cache Files" \
         "$HOME/Documents/Adobe/Premiere Pro"/*/Profile-*/"Media Cache"; do
    clean_dir_older_than "$d" "$CACHE_AGE_DAYS"
done

# After Effects Disk Cache & Temp (older than CACHE_AGE_DAYS)
find "$HOME/Library/Application Support/Adobe" -type d \( -name "Disk Cache" -o -name "temp" \) 2>/dev/null | while IFS= read -r d; do
    clean_dir_older_than "$d" "$CACHE_AGE_DAYS"
done
clean_dir_older_than "$HOME/Library/Caches/Adobe/After Effects" "$CACHE_AGE_DAYS"

# Photoshop / Audition / InDesign: NEVER delete recent AutoRecover/Autosave! Only >30 days old
find "$HOME/Library/Application Support/Adobe" -type d \( -name "AutoRecover" -o -name "Autosave" -o -name "InDesign Recovery" \) 2>/dev/null | while IFS= read -r d; do
    clean_dir_older_than "$d" 30
done

# Camera Raw, Bridge, Acrobat, Dunamis Telemetry & Logs
empty_cache_dir "$HOME/Library/Caches/Adobe Camera Raw"
empty_cache_dir "$HOME/Library/Application Support/Adobe/CameraRaw/Cache"
empty_cache_dir "$HOME/Library/Caches/Adobe/Bridge CC"
empty_cache_dir "$HOME/Library/Caches/com.adobe.bridgeCC"
empty_cache_dir "$HOME/Library/Caches/Adobe/Acrobat"
empty_cache_dir "$HOME/Library/Caches/com.adobe.Reader"
empty_cache_dir "$HOME/Library/Caches/com.adobe.Acrobat.Pro"
empty_cache_dir "$HOME/Library/Application Support/com.adobe.dunamis/cache"
empty_cache_dir "$HOME/Library/Logs/Adobe"
empty_cache_dir "$HOME/Library/Application Support/Adobe/OOBE/Logs"

# Topaz Video AI — Clean old logs and old preview/thumbnail caches (NEVER touch models!)
clean_dir_older_than "$HOME/Library/Application Support/Topaz Labs LLC/Topaz Video/logs" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Documents/Topaz Video Projects/Default/previews" "$CACHE_AGE_DAYS"
clean_dir_older_than "$HOME/Documents/Topaz Video Projects/Default/thumbnails" "$CACHE_AGE_DAYS"

# ------------------------------------------------------------------------------
# 5. Restore Spotlight Protection (.metadata_never_index) on All Video Caches
# ------------------------------------------------------------------------------
for dir in \
    "$HOME/Movies/CacheClip" \
    "$HOME/Movies/.gallery" \
    "$HOME/Movies/Resolve Project Backups" \
    "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.BGRender" \
    "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.BGTranscribe" \
    "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/.LUT" \
    "$HOME/Library/Application Support/Adobe/Common/Media Cache" \
    "$HOME/Library/Application Support/Adobe/Common/Media Cache Files" \
    "$HOME/Library/Application Support/Adobe/Common/Peak Files" \
    "$HOME/Library/Application Support/Adobe/Common/Analyzer Cache Files" \
    "$HOME/Library/Application Support/Adobe/Common/Metadata Cache" \
    "$HOME/Library/Application Support/Adobe/Common/Motion Graphics Templates" \
    "$HOME/Documents/Topaz Video Projects" \
    "$HOME/Documents/Topaz Video Projects/Default/previews" \
    "$HOME/Documents/Topaz Video Projects/Default/thumbnails" \
    "$HOME/Library/Application Support/Topaz Labs LLC/Topaz Video" \
    "$HOME/Library/Application Support/Topaz Labs LLC/Topaz Video/models" \
    "$HOME/Library/Application Support/Topaz Labs LLC/Topaz Video/logs"; do
    [ -d "$dir" ] && touch "$dir/.metadata_never_index" 2>/dev/null
done

# ------------------------------------------------------------------------------
# 6. Developer, Package Managers & Simulator Cleanup
# ------------------------------------------------------------------------------
clean_dir_older_than "$HOME/Library/Developer/Xcode/DerivedData" 14
empty_cache_dir "$HOME/Library/Developer/CoreSimulator/Caches"
command -v xcrun >/dev/null 2>&1 && xcrun simctl delete unavailable >/dev/null 2>&1

if [ "$AGE_SINCE_MONTHLY" -ge "$MONTHLY_INTERVAL_SEC" ] || [ "$MODE" = "--force" ]; then
    command -v brew >/dev/null 2>&1 && brew cleanup -s >/dev/null 2>&1
    command -v npm >/dev/null 2>&1 && npm cache clean --force >/dev/null 2>&1
    command -v pip3 >/dev/null 2>&1 && pip3 cache purge >/dev/null 2>&1
    command -v uv >/dev/null 2>&1 && uv cache clean >/dev/null 2>&1
    command -v pnpm >/dev/null 2>&1 && pnpm store prune >/dev/null 2>&1
    command -v yarn >/dev/null 2>&1 && yarn cache clean >/dev/null 2>&1
    clean_dir_older_than "$HOME/.gradle/caches" 30
    clean_dir_older_than "$HOME/.cargo/registry/cache" 30
    clean_dir_older_than "$HOME/Library/Caches/CocoaPods" 30
    clean_dir_older_than "$HOME/Library/Caches/org.swift.swiftpm" 30
    clean_dir_older_than "$HOME/Library/Caches/go-build" 30
fi

# ------------------------------------------------------------------------------
# 7. Messaging, Social, Browser & Editor Caches (Safe Age-Based Cleanup)
# ------------------------------------------------------------------------------
# WhatsApp, Telegram, Discord, Slack, Spotify, Zoom, Teams
clean_dir_older_than "$HOME/Library/Containers/net.whatsapp.WhatsApp/Data/Library/Caches" 14
clean_dir_older_than "$HOME/Library/Containers/desktop.WhatsApp/Data/Library/Caches" 14
clean_dir_older_than "$HOME/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/Library/Caches" 14

find "$HOME/Library/Group Containers" -maxdepth 1 -type d -name "*.ru.keepcoder.Telegram" 2>/dev/null | while IFS= read -r d; do
    clean_dir_older_than "$d/Library/Caches" 14
    for media_dir in "$d"/account-*/postbox/media; do
        clean_dir_older_than "$media_dir" 30
    done
done

empty_cache_dir "$HOME/Library/Application Support/discord/Cache"
empty_cache_dir "$HOME/Library/Application Support/discord/Code Cache"
empty_cache_dir "$HOME/Library/Application Support/discord/Crashpad/completed"
empty_cache_dir "$HOME/Library/Application Support/Slack/Cache"
empty_cache_dir "$HOME/Library/Application Support/Slack/Service Worker/CacheStorage"
clean_dir_older_than "$HOME/Library/Application Support/Spotify/PersistentCache" 14
empty_cache_dir "$HOME/Library/Application Support/zoom.us/Logs"
empty_cache_dir "$HOME/Library/Application Support/Microsoft/Teams/Cache"

# VS Code / Cursor logs & old cached data
clean_dir_older_than "$HOME/Library/Application Support/Code/logs" 7
clean_dir_older_than "$HOME/Library/Application Support/Code/CachedData" 30
clean_dir_older_than "$HOME/Library/Application Support/Cursor/logs" 7
clean_dir_older_than "$HOME/Library/Application Support/Cursor/CachedData" 30

# Browser Caches (only files older than 14 days so active browsing stays fast)
clean_dir_older_than "$HOME/Library/Caches/com.apple.Safari" 14
clean_dir_older_than "$HOME/Library/Caches/Google/Chrome/Default/Cache" 14
clean_dir_older_than "$HOME/Library/Caches/Google/Chrome/Default/Media Cache" 14
clean_dir_older_than "$HOME/Library/Caches/Microsoft Edge/Default/Cache" 14

# ------------------------------------------------------------------------------
# 8. System Logs, Crash Reports, Temp Files & Smart Trash (>30 Days)
# ------------------------------------------------------------------------------
clean_dir_older_than "$HOME/Library/Logs" 14
clean_dir_older_than "$HOME/Library/Logs/DiagnosticReports" 14

USER_TEMP=$(getconf DARWIN_USER_TEMP_DIR 2>/dev/null)
USER_CACHE_D=$(getconf DARWIN_USER_CACHE_DIR 2>/dev/null)
[ -n "$USER_TEMP" ] && clean_dir_older_than "$USER_TEMP" 3
[ -n "$USER_CACHE_D" ] && clean_dir_older_than "$USER_CACHE_D" 7

# Smart Trash Cleanup: Only delete Trash items older than 30 days (protects recent accidental deletions!)
if [ -d "$HOME/.Trash" ]; then
    find "$HOME/.Trash" -mindepth 1 -mtime +30 -exec rm -rf {} + 2>/dev/null
fi

# Thin APFS local snapshots gently if needed (non-sudo user command)
tmutil thinlocalsnapshots / 10000000000 4 >/dev/null 2>&1

# Record monthly timestamp if this was a monthly or forced run
if [ "$AGE_SINCE_MONTHLY" -ge "$MONTHLY_INTERVAL_SEC" ] || [ "$MODE" = "--force" ]; then
    echo "$NOW_TS" > "$LAST_MONTHLY_FILE"
fi

FREE_AFTER_KB=$(free_kb)
RECLAIMED_KB=$((FREE_AFTER_KB - FREE_BEFORE_KB))
if [ "$RECLAIMED_KB" -lt 0 ]; then
    RECLAIMED_KB=0
fi
RECLAIMED_HUMAN=$(human_kb "$RECLAIMED_KB")
FREE_AFTER_HUMAN=$(human_kb "$FREE_AFTER_KB")

log "DONE: Smart System Cleaner finished ($REASON). Reclaimed: $RECLAIMED_HUMAN, Free Space: $FREE_AFTER_HUMAN"

osascript -e "display notification \"${REASON} tamamlandı. Açılan alan: ${RECLAIMED_HUMAN} (Boş Alan: ${FREE_AFTER_HUMAN})\" with title \"Akıllı Sistem Bakımı ✅\"" 2>/dev/null
exit 0
