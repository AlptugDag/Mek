# -*- coding: utf-8 -*-
"""Jarvis — merkezi ayarlar. Değerleri ~/.jarvis/jarvis.json ile ezebilirsiniz."""
import json
import os

HOME = os.path.expanduser("~")
JARVIS_DIR = os.path.join(HOME, ".jarvis")
STATE_DIR = os.path.join(JARVIS_DIR, "state")
QUARANTINE = os.path.join(JARVIS_DIR, "karantina")
LOG_FILE = os.path.join(JARVIS_DIR, "jarvisd.log")
SOCKET_PATH = os.path.join(JARVIS_DIR, "jarvis.sock")
AI_BIN = os.path.join(JARVIS_DIR, "ai/jarvis-ai")           # Apple Intelligence köprüsü
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
        with open(os.path.join(JARVIS_DIR, "jarvis.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(QUARANTINE, exist_ok=True)
    return cfg
