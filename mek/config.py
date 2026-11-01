# -*- coding: utf-8 -*-
"""Mek — merkezi ayarlar. Değerleri ~/.jarvis/mek.json ile ezebilirsiniz."""
import json
import os

HOME = os.path.expanduser("~")
JARVIS_DIR = os.path.join(HOME, ".jarvis")
STATE_DIR = os.path.join(JARVIS_DIR, "state")
LOG_FILE = os.path.join(JARVIS_DIR, "mek.log")
SOCKET_PATH = os.path.join(JARVIS_DIR, "mek.sock")
CLEANER = os.path.join(HOME, ".local/bin/smart_system_cleaner.sh")
MASTER_SETUP = os.path.join(HOME, ".local/bin/workstation_master_setup.sh")
PRIV_HELPER = "/usr/local/libexec/mek-priv"  # isteğe bağlı, install_privileged.sh kurar

DEFAULTS = {
    "user_title": "Alptuğ Bey",
    # Uyandırma kelimesi ve whisper'ın onu yanlış duyabileceği biçimler
    "wake_words": ["mek", "mekk", "meck", "mekh", "mehk"],
    "stt_model": "mlx-community/whisper-large-v3-turbo-q4",   # 4-bit: ~0.5 GB
    "llm_model": "mlx-community/Qwen3-4B-4bit",              # ~2.3 GB, yalnızca gerektiğinde
    # "local_first": sohbet ve komutlar yerelde (bedava); Gemini yalnızca arama/ekran için.
    # "cloud_first": her şey önce Gemini'ye (ücretli plan varsa daha akıllı).
    "brain_mode": "local_first",
    "gemini_daily_budget": 18,       # ücretsiz katman günde 20 istek veriyor
    "llm_unload_after_s": 90,
    "llm_min_free_gb": 12,           # bundan az boş bellek varsa yerel beyin yüklenmez
    "listen": True,
    "input_device_hint": "",          # boş = sistem varsayılan mikrofonu
    "quiet_start_hour": 0,            # proaktif konuşma sessiz saatleri
    "quiet_end_hour": 11,
    "proactive_min_gap_s": 600,       # iki öneri arası en az süre
    "break_after_min": 90,            # kesintisiz kurgu sonrası mola önerisi
    "away_threshold_s": 1200,         # bu kadar boşta kalınca "uzakta" sayılır
    "cleaner_min_gap_h": 12,
    "shutdown_countdown_s": 20,
    # Bu uygulamalar öndeyken mikrofon dinleme durur (seslendirme kaydı / görüşme)
    "recording_apps": ["Adobe Audition", "Farrago", "Logic Pro", "Pro Tools", "Reaper",
                       "Audio Hijack", "zoom.us", "FaceTime", "Microsoft Teams", "Discord"],
    # Kurgu modunda geçici olarak durdurulan, sonra geri açılan kullanıcı servisleri
    "deferrable_agents": [
        "/Library/LaunchAgents/com.google.keystone.agent.plist",
        "/Library/LaunchAgents/com.google.keystone.xpcservice.plist",
        "/Library/LaunchAgents/com.maxon.mxnotify.agent.plist",
        "/Library/LaunchAgents/com.maxon.mxredirect.agent.plist",
        "/Library/LaunchAgents/com.displaylink.loginscreen.plist",
    ],
    "gemini_api_key": "",             # doluysa beyin Gemini olur (en akıllı mod)
    # Sırayla denenir; biri 404/503 verirse sonrakine geçilir
    # Yeni anahtarlar yalnızca güncel modele erişebiliyor; listeye yenisi eklenebilir
    "gemini_models": ["gemini-3.8-flash"],
    "anthropic_api_key": "",          # doluysa karmaşık istekler Claude'a gider
    "anthropic_model": "claude-haiku-5-5",
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
    path = os.path.join(JARVIS_DIR, "mek.json")
    try:
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"listen": True, "gemini_api_key": "", "anthropic_api_key": ""}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    os.makedirs(STATE_DIR, exist_ok=True)
    return cfg
