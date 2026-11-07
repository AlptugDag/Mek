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
    "morning_greeting_from_hour": 6,  # günün ilk selamı: siz başındaysanız bu saatten sonra sessiz saatte de söylenir
    "speech_volume": 0.30,            # tüm seslendirmeler bu düzeye dijital olarak kısılır
    # Mek daima dahili hoparlörden konuşur; konuşma boyunca hoparlörün donanım
    # sesi speech_volume düzeyine sabitlenir ve hemen ardından sizin
    # bıraktığınız değere geri alınır. Böylece klavyedeki ses tuşları Mek'in
    # sesini etkileyemez. "system" yazılırsa sistemin varsayılan çıkışı kullanılır.
    "speech_device": "mac studio",
    "crash_alerts": "repeated",       # "off": hiç | "repeated": aynı uygulama 24 saatte 3+ kez çökerse tek sessiz bildirim
    "memory_reserve_gb": 10,          # macOS + tarayıcı vb. için ağır uygulamalara hiç verilmeyen bellek
    "memory_overcommit": 1.2,         # aynı anda açık uygulamalar nadiren aynı anda tepe yapar
    "memory_relief": True,            # bellek sıkışınca boştaki ağır uygulama için soru sor
    "trusted_processes": ["adb", "scrcpy"],  # havuz portu uyarısı verilmeyecek programlar
    "market_watchlist": [],           # ör. ["THYAO.IS", "AAPL"]
    "market_alert_pct": {"XU100.IS": 1.5, "TRY=X": 0.8, "GAU": 1.5, "BTC-USD": 4.0, "default": 3.0},
    "deferrable_agents": [
        "/Library/LaunchAgents/com.google.keystone.agent.plist",
        "/Library/LaunchAgents/com.google.keystone.xpcservice.plist",
        "/Library/LaunchAgents/com.displaylink.loginscreen.plist",
    ],
}

# Kurgu eklentilerinin (Red Giant, Maxon, BorisFX, Adobe, Topaz) bağlı olduğu ve her zaman çalışır tutulacak servisler
PLUGIN_SERVICE_AGENTS = [
    "/Library/LaunchAgents/com.maxon.mxnotify.agent.plist",
    "/Library/LaunchAgents/com.maxon.mxredirect.agent.plist",
    "/Library/LaunchAgents/com.borisfx.bfxdhelper.plist",
    "/Library/LaunchAgents/com.adobe.AdobeDesktopService.plist",
    "/Library/LaunchAgents/com.adobe.AdobeCreativeCloud.plist",
    os.path.join(HOME, "Library/LaunchAgents/com.topazlabs.veai.nukepath.plist"),
]

# Yıl veya sürüm numarasından (2026, 2030 vb.) tamamen bağımsız ana kurgu/yaratıcı uygulama örüntüsü
PRO_APP_PATTERN = (
    r"Contents/MacOS/("
    r"Resolve|DaVinci Resolve[^/]*|fusionscript|"
    r"Adobe Premiere[^/]*|Adobe Media Encoder[^/]*|PProHeadless|After Effects[^/]*|Adobe After Effects[^/]*|aerender|"
    r"Adobe Audition[^/]*|Adobe Photoshop[^/]*|Photoshop[^/]*|Adobe Illustrator[^/]*|Adobe Lightroom[^/]*|"
    r"Adobe Character Animator[^/]*|Adobe Bridge[^/]*|Adobe InDesign[^/]*|"
    r"Topaz Video[^/]*|Topaz Photo[^/]*|Topaz Gigapixel[^/]*|Gigapixel[^/]*|ffmpeg|neuroserver|"
    r"Final Cut Pro[^/]*|Compressor[^/]*|Motion[^/]*|Logic Pro[^/]*|"
    r"Cinema 4D[^/]*|Blender[^/]*|Colourlab[^/]*|OBS[^/]*|HandBrake[^/]*"
    r")|DaVinci Resolve Helper"
)

# Kullanıcının yaratıcı uygulamaları, eklentileri ve eklenti servislerinin paket/dizinleri:
# Hangi yılda/sürümde olursa olsun (2026, 2030 vb.) veya yeni eklenti eklendiğinde bunların hiçbir süreci yavaşlatılmaz
CREATIVE_BUNDLE_PATTERN = (
    r"^(/Applications/(Adobe [^/]+|Topaz[^/]*|DaVinci Resolve[^/]*|Blackmagic[^/]*|Final Cut[^/]*|"
    r"Logic Pro[^/]*|Compressor[^/]*|Motion[^/]*|Colourlab[^/]*|OBS[^/]*|Maxon[^/]*|Red Giant[^/]*|"
    r"Boris[^/]*|FxFactory[^/]*|Dehancer[^/]*|Cinema 4D[^/]*|Blender[^/]*)"
    r"|/Library/Application Support/(Adobe|Blackmagic Design|Red Giant|Maxon|BorisFX|FxFactory|"
    r"Dehancer|Topaz[^/]*|Pro Sound Effects|Laforge[^/]*|FilmConvert[^/]*)"
    r"|/Library/OFX/Plugins"
    r"|/Library/Audio/Plug-Ins"
    r"|/Users/[^/]+/Library/Application Support/(Adobe|Blackmagic Design|Red Giant|Maxon|BorisFX|"
    r"FxFactory|Dehancer|Topaz[^/]*))(/|$)"
)
RENDER_PROCS = {
    "Topaz": r"Topaz[^/]*.*/(ffmpeg|neuroserver)",
    "Media Encoder": r"Adobe Media Encoder",
    "After Effects": r"aerender|AEGPUSniffer",
    "Premiere (arka plan)": r"PProHeadless",
    "Compressor": r"Contents/MacOS/Compressor",
    "DaVinci Resolve": r"Contents/MacOS/Resolve",
}


CONFIG_FILE = os.path.join(MEK_HOME, "jarvis.json")
# Eski sürümler bazı ayarları (ör. gemini_api_key) config.json'a yazıyordu.
# Okurken ikisi de görülür, jarvis.json üstün gelir; yazarken yalnızca
# jarvis.json kullanılır. Böylece "anahtarı kaydettim ama motor bulamıyor"
# durumu ortadan kalkar.
LEGACY_CONFIG_FILE = os.path.join(MEK_HOME, "config.json")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def load():
    cfg = dict(DEFAULTS)
    cfg.update(_read_json(LEGACY_CONFIG_FILE))
    cfg.update(_read_json(CONFIG_FILE))
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(QUARANTINE, exist_ok=True)
    return cfg


def secret(name, env_var=None):
    """Bir gizli ayarı sırayla ortam değişkeni, jarvis.json, config.json'dan okur."""
    if env_var is None:
        env_var = name.upper()
    val = os.environ.get(env_var)
    if val and val.strip():
        return val.strip()
    for path in (CONFIG_FILE, LEGACY_CONFIG_FILE):
        val = _read_json(path).get(name)
        if val and str(val).strip():
            return str(val).strip()
    return None


def set_values(**values):
    """Verilen ayarları jarvis.json'a atomik olarak işler ve yeni ayarları döner."""
    cfg = _read_json(CONFIG_FILE)
    cfg.update(values)
    save(cfg)
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
