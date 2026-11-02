# -*- coding: utf-8 -*-
"""Mek merkezi ayarları. Eski kurulumlarla uyum için kullanıcı dizini ~/.jarvis olarak kalır."""
import json
import os
import tempfile

HOME = os.path.expanduser("~")
MEK_HOME = os.path.join(HOME, ".jarvis")
STATE_DIR = os.path.join(MEK_HOME, "state")
QUARANTINE = os.path.join(MEK_HOME, "karantina")
LOG_FILE = os.path.join(MEK_HOME, "jarvisd.log")
SOCKET_PATH = os.path.join(MEK_HOME, "jarvis.sock")
AI_BIN = os.path.join(MEK_HOME, "ai/jarvis-ai")             # Apple Intelligence legacy binary
CLEANER = os.path.join(HOME, ".local/bin/smart_system_cleaner.sh")
MASTER_SETUP = os.path.join(HOME, ".local/bin/workstation_master_setup.sh")
PRIV_HELPER = "/usr/local/libexec/mek-priv"  # isteğe bağlı: sudo bash ~/.jarvis/jarvisd/install_privileged.sh

DEFAULTS = {
    "user_title": "Alptuğ Bey",
    "quiet_start_hour": 0,            # proaktif konuşma sessiz saatleri (acil güvenlik uyarıları hariç)
    "quiet_end_hour": 11,
    "proactive_min_gap_s": 600,       # iki sesli bildirim arası en az süre
    "break_after_min": 90,
    "away_threshold_s": 1200,
    "cleaner_min_gap_h": 12,
    "ups_auto_shutdown_enabled": False,
    "ups_shutdown_percent": 15,
    "ups_shutdown_minutes": 5,
    "event_alert_min": 10,
    "market_watchlist": [],           # ör. ["THYAO.IS", "AAPL"]
    "market_alert_pct": {"XU100.IS": 1.5, "TRY=X": 0.8, "GAU": 1.5, "BTC-USD": 4.0, "default": 3.0},
    "deferrable_agents": [
        "/Library/LaunchAgents/com.google.keystone.agent.plist",
        "/Library/LaunchAgents/com.google.keystone.xpcservice.plist",
        "/Library/LaunchAgents/com.maxon.mxnotify.agent.plist",
        "/Library/LaunchAgents/com.maxon.mxredirect.agent.plist",
        "/Library/LaunchAgents/com.displaylink.loginscreen.plist",
    ],
}

PRO_APP_PATTERN = (r"Contents/MacOS/(Resolve|Adobe Premiere Pro|Adobe Media Encoder|PProHeadless|"
                   r"After Effects|aerender|Adobe Audition|Topaz Video|ffmpeg|neuroserver|Colourlab Ai|OBS)"
                   r"|DaVinci Resolve Helper")
RENDER_PROCS = {
    "Topaz": r"Topaz Video.*/(ffmpeg|neuroserver)",
    "Media Encoder": r"Adobe Media Encoder",
    "After Effects": r"aerender|AEGPUSniffer",
    "Premiere (arka plan)": r"PProHeadless",
}


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(MEK_HOME, "jarvis.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(QUARANTINE, exist_ok=True)
    return cfg


def save(cfg):
    """Persist user settings atomically so a partial write cannot corrupt the config."""
    os.makedirs(MEK_HOME, exist_ok=True)
    path = os.path.join(MEK_HOME, "jarvis.json")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=MEK_HOME,
                                        prefix=".jarvis.", delete=False) as f:
            temp_path = f.name
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
