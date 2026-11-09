#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mek depolama temizliği — kapsamlı, katmanlı, kendi kendine çalışan.

Katmanlar
  gunluk    yaşa göre önbellek/kayıt/geçici dosya temizliği (kullanıcı alanı)
  haftalik  günlük + paket yöneticileri, geliştirici araçları, sistem alanı (yönetici yardımcısıyla)
  acil      disk dolmak üzereyken: yaş eşikleri düşer, Time Machine yerel anlık görüntüleri inceltilir

Kural: yalnızca YENİDEN OLUŞTURULABİLEN veriler silinir (önbellek, kayıt, geçici, çökme raporu,
indirilebilir güncelleme). Projeler, medya, renk stilleri (.gallery), proje yedekleri, otomatik
kayıtların son 30 günü, yapay zekâ modelleri, Fotoğraflar/Mail/Mesajlar verisi ASLA silinmez.
Kişisel dosyalar (eski kurulum dosyaları, aylardır açılmamış büyük dosyalar) yalnızca raporlanır.

Kullanım:  cleaner.py --tier gunluk|haftalik|acil [--dry-run]   (sonuç JSON olarak stdout'a)
"""
import argparse
import glob
import json
import os
import plistlib
import re
import shutil
import subprocess
import time
import proc


HOME = os.path.expanduser("~")
H = lambda p: os.path.join(HOME, p)  # noqa: E731
DAY = 86400
PRIV = "/usr/local/libexec/mek-priv"

# Asla dokunulmayacak yollar (alt yolları dahil)
PROTECT = [H("Movies/.gallery"), H("Movies/Resolve Project Backups"), H("Movies/Mek Proje Zaman Makinesi"), H(".jarvis"),
           H(".cache/huggingface"), H(".cache/torch"), H(".cache/alptugdub-vc"), H(".cache/whisper"),
           H(".ollama"), H(".lmstudio"), H("Library/Application Support/Topaz Labs LLC/Topaz Video/models"),
           H("Library/Application Support/Adobe/Common/PTX"),
           H("Library/Application Support/Adobe/Common/.mek_ssd_backing"),
           H("Pictures"), H("Library/Mail"), H("Library/Messages"), H("Library/Mobile Documents"),
           H("Library/Application Support/MobileSync"), H("Library/Keychains"),
           H("Library/Application Support/Blackmagic Design/DaVinci Resolve/Resolve Disk Database")]
# Sistemin ya da açık indirmelerin ihtiyaç duyduğu önbellekler
SKIP_CACHE = re.compile(r"^(com\.apple\.(nsurlsessiond|bird|akd|CloudKit|cloudd|HomeKit|appstore|"
                        r"SoftwareUpdate|containermanagerd|ap\.adprivacyd|Spotlight)|CloudKit|"
                        r"com\.apple\.icloud|Homebrew/downloads/.*incomplete)")


# Kalıntı taramasında daima korunacak sistem/üretici dizin adları
SYSTEM_WHITELIST = {
    "apple", "system", "preferences", "crashreporter", "dock", "dvd player",
    "icdd", "syncservices", "cloudkit", "addressbook", "fileprovider", "knowledge",
    "quick look", "callhistorydb", "callhistorytransactions", "coreparsec",
    "mobilemediaplayer", "containermanagerd", "diskimages", "cefac", "cef",
    "antigravity", "jarvis", "mek", "steam", "google", "microsoft", "discord",
    "spotify", "obs-studio", "jdownloader 2", "pro sound effects", "farrago 2",
    "alptuguide", "alptug", "claude", "whisper", "huggingface", "torch", "ollama",
    "lmstudio", "native instruments", "gse saves", "elgato", "izotope",
    "borisfx", "adobe", "blackmagic design", "maxon", "red giant", "redgiant",
    "dehancer", "topaz labs llc", "soundq", "ultimate vocal remover", "daisydisk",
    "animoji", "clouddocs", "differentialprivacy", "familycircled",
    "networkserviceproxy", "askpermissiond", "creative cloud", "csxs", "airobin",
    "whatsapp", "net.whatsapp", "homebrew", "keychains", "mobilesync", "tipsnext",
    "diagnostics", "locationaccessstored", "homeenergyd", "appplaceholdersyncd",
    "tipsd", "identityservicesd", "privatecloudcomputed", "contactsd", "stickersd",
    "intelligenceflow", "summary-events", "node", "swift-frontend",
    "displaylink", "nikon", "parallels", "epicgames", "aescripts", "productioncrate",
    "crumplepop", "unomi", "pixel sorter", "omata", "logi", "logitech", "rode",
    "røde", "zoom", "baskl", "bskl", "playwright", "clickup", "fxfactory",
    "python", "pip", "brew", "cask"
}

APPLE_SYSTEM_DIRS = {
    "app store", "automator", "facetime", "music", "motion", "controlcenter",
    "icloud", "askpermission", "categories", "ilifemediabrowser", "logs",
    "addressbook", "callhistorydb", "callhistorytransactions", "clouddocs",
    "differentialprivacy", "diskimages", "dock", "fileprovider", "knowledge",
    "syncservices", "byhost", "no_backup", "databases", "webpush", "quick look",
    "coreparsec", "mobilemediaplayer", "containermanagerd", "identityservicesd",
    "locationaccessstored", "homeenergyd", "appplaceholdersyncd", "tipsd",
    "privatecloudcomputed", "contactsd", "stickersd", "intelligenceflow",
    "summary-events", "icloudmailagent", "storedownloadd", "crashreporter",
    "default.store", "default.store-shm", "default.store-wal"
}


def protected(p):
    return any(p == x or p.startswith(x + "/") for x in PROTECT)


def running_bundles():
    try:
        out = subprocess.run(["lsappinfo", "list"], capture_output=True, text=True).stdout
        return set(re.findall(r'bundleID="([^"]+)"', out)) | {
            n.lower() for n in re.findall(r'^\s*\d+\) "([^"]+)"', out, re.M)}
    except Exception:
        return set()


class Cleaner:
    def __init__(self, dry=False, scale=1.0, tier="gunluk"):
        self.dry, self.scale, self.tier = dry, scale, tier
        self.is_deep = (tier in ("haftalik", "acil"))
        self.freed = {}                        # kategori -> bayt
        self.running = running_bundles()
        self.errors = 0

    def _add(self, cat, n):
        self.freed[cat] = self.freed.get(cat, 0) + n

    def _rm_file(self, p, cat):
        try:
            st = os.lstat(p)
            if not self.dry:
                os.remove(p)
            self._add(cat, st.st_blocks * 512)
        except OSError:
            self.errors += 1

    def _rm_tree(self, p, cat):
        """Bir klasör ağacındaki tüm dosyaları ölçüp siler; ardından klasörü kaldırır."""
        try:
            for dp, dn, fn in os.walk(p, topdown=False):
                if protected(dp):
                    continue
                for f in fn:
                    self._rm_file(os.path.join(dp, f), cat)
                if not self.dry:
                    try:
                        os.rmdir(dp)
                    except OSError:
                        pass
            if not self.dry and os.path.exists(p) and not protected(p):
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            self.errors += 1

    def age(self, cat, path, days, keep=(".metadata_never_index",)):
        """path (glob olabilir) altında days günden eski dosyaları siler, boş klasörleri toplar."""
        limit = time.time() if days <= 0 else (time.time() - max(0.5, days * self.scale) * DAY)
        for root in glob.glob(path, recursive=True):
            if protected(root) or not os.path.isdir(root):
                continue
            for dp, dn, fn in os.walk(root, topdown=False):
                if protected(dp):
                    continue
                for f in fn:
                    if f in keep:
                        continue
                    p = os.path.join(dp, f)
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if max(st.st_mtime, st.st_ctime) < limit:
                        self._rm_file(p, cat)
                if dp != root and not self.dry:
                    try:
                        os.rmdir(dp)  # yalnızca boşsa
                    except OSError:
                        pass

    def wipe(self, cat, path, unless_running=()):
        """Klasörün içeriğini tamamen boşaltır (klasörün kendisi kalır). Uygulama açıksa atlar."""
        if any(r.lower() in self.running for r in unless_running):
            return
        for root in glob.glob(path):
            if protected(root) or not os.path.isdir(root):
                continue
            for e in os.listdir(root):
                if e == ".metadata_never_index":
                    continue
                p = os.path.join(root, e)
                if os.path.isdir(p) and not os.path.islink(p):
                    self.age(cat, p, 0)
                    if not self.dry:
                        shutil.rmtree(p, ignore_errors=True)
                else:
                    self._rm_file(p, cat)

    def cmd(self, cat, args, measure=None):
        """Bir aracın kendi temizlik komutu; measure verilirse önce/sonra boyut farkı ölçülür."""
        if not shutil.which(args[0]) and not os.path.exists(args[0]):
            return
        before = du(measure) if measure else 0
        if not self.dry:
            subprocess.run(args, capture_output=True, timeout=1800)
        if measure:
            self._add(cat, max(0, before - du(measure)))

    # ------------------------------------------------------------------ kullanıcı alanı
    def kurgu(self):
        A = H("Library/Application Support/Adobe/Common")
        # Aktif ve yakın tarihli projelerin ses dalga formu (Peak Files) ve indeks (Media Cache)
        # önbellekleri 60 gün korunur; PTX (derlenmiş GPU shader önbelleği) ASLA silinmez!
        for sub in ("Media Cache Files", "Media Cache", "Peak Files", "Analyzer Cache Files"):
            self.age("Adobe önbellekleri", os.path.join(A, sub), 60)
        for sub in ("Cache", "Team Projects Cache", "Team Projects Local Hub"):
            self.age("Adobe önbellekleri", os.path.join(A, sub), 14)
        self.age("Adobe önbellekleri", H("Documents/Adobe/Premiere Pro/*/Profile-*/Media Cache*"), 60)
        self.age("Adobe önbellekleri", H("Library/Application Support/Adobe/**/Disk Cache"), 30)
        self.age("Adobe önbellekleri", H("Library/Caches/Adobe"), 7)
        self.age("Adobe önbellekleri", H("Library/Caches/Adobe Camera Raw"), 14)
        self.age("Adobe önbellekleri", H("Library/Application Support/Adobe/CameraRaw/Cache"), 14)
        self.age("Adobe önbellekleri", H("Library/Caches/com.adobe.*"), 7)
        self.age("Adobe önbellekleri", H("Library/Application Support/Adobe Substance*/cache"), 14)
        self.age("Adobe önbellekleri", H("Library/Application Support/com.adobe.dunamis/cache"), 3)
        self.age("Adobe önbellekleri", H("Library/Application Support/Adobe/UPI/*/cache"), 7)
        self.age("Adobe kayıtları", H("Library/Logs/Adobe"), 7)
        self.age("Adobe kayıtları", H("Library/Application Support/Adobe/OOBE/Logs"), 7)
        self.age("Adobe kayıtları", H("Library/Application Support/Adobe/**/CrashReports"), 7)
        # Otomatik kayıtlar: yalnızca 30 günden eskiler
        for d in glob.glob(H("Library/Application Support/Adobe/**/Auto*"), recursive=True):
            if os.path.basename(d) in ("AutoRecover", "Autosave", "Auto-Save"):
                self.age("Eski otomatik kayıtlar", d, 30)
        self.age("Eski otomatik kayıtlar", H("Documents/Adobe/Premiere Pro/*/Adobe Premiere Pro Auto-Save"), 30)
        self.age("Lightroom önbelleği", H("Library/Caches/com.adobe.LightroomClassicCC7"), 14)
        R = H("Library/Application Support/Blackmagic Design/DaVinci Resolve")
        davinci_running = any("davinci" in x or "resolve" in x for x in self.running)
        if not davinci_running:
            self.age("DaVinci önbellekleri", H("Movies/CacheClip"), 14 if not self.is_deep else 7)
            self.age("DaVinci önbellekleri", os.path.join(R, ".BGRender"), 3 if not self.is_deep else 1)
        self.age("DaVinci önbellekleri", os.path.join(R, ".BGTranscribe"), 7)
        self.age("DaVinci önbellekleri", os.path.join(R, "Cache"), 7)
        self.age("DaVinci kayıtları", os.path.join(R, "logs"), 7 if not self.is_deep else 2)
        T = H("Documents/Topaz Video Projects/Default")
        self.age("Topaz önbellekleri", os.path.join(T, "previews"), 7)
        self.age("Topaz önbellekleri", os.path.join(T, "thumbnails"), 14)
        self.age("Topaz önbellekleri", os.path.join(T, "cpe"), 7)
        self.age("Topaz kayıtları", H("Library/Application Support/Topaz Labs LLC/Topaz Video/logs"), 7)
        self.age("Capture One önbelleği", H("Library/Caches/com.captureone*"), 14)
        self.age("Maxon/Red Giant önbelleği", H("Library/Caches/net.maxon*"), 14)
        self.age("Maxon/Red Giant önbelleği", H("Library/Application Support/Maxon/*/cache"), 14)

    def uygulamalar(self):
        # Genel kullanıcı önbellekleri: her uygulama klasörü ayrı; açık uygulamalar ve sistemin ihtiyaç duyduğu
        # klasörler korunur. Derin temizlikte (haftalık/acil) kapalı uygulamaların önbellekleri tamamen silinir (0 gün).
        for d in glob.glob(H("Library/Caches/*")):
            name = os.path.basename(d)
            if SKIP_CACHE.match(name):
                continue
            is_running = name in self.running or name.lower() in self.running
            days = (7 if self.is_deep else 14) if is_running else (0 if self.is_deep else 7)
            self.age("Uygulama önbellekleri", d, days)
        # Sandbox'lı uygulamaların önbellekleri (eski betiklerin kaçırdığı büyük alan)
        for d in glob.glob(H("Library/Containers/*/Data/Library/Caches")):
            bid = d.split("/Containers/")[1].split("/")[0]
            if SKIP_CACHE.match(bid):
                continue
            is_running = bid in self.running or bid.lower() in self.running
            days = (7 if self.is_deep else 14) if is_running else (0 if self.is_deep else 7)
            self.age("Uygulama önbellekleri", d, days)
        for d in glob.glob(H("Library/Group Containers/*/Library/Caches")):
            self.age("Uygulama önbellekleri", d, 7 if self.is_deep else 14)
        # Electron/Chromium tabanlı tüm uygulamalar (Discord, Slack, Spotify, VS Code, Figma, Notion...)
        electron_days = 0 if self.is_deep else 7
        for pat in ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
                    "Service Worker/CacheStorage", "Service Worker/ScriptCache", "Crashpad/completed",
                    "Crashpad/pending", "logs", "CachedData", "CachedExtensionVSIXs", "ShaderCache"):
            self.age("Uygulama önbellekleri", H(f"Library/Application Support/*/{pat}"), electron_days)
            self.age("Uygulama önbellekleri", H(f"Library/Application Support/*/*/{pat}"), electron_days)
        self.age("Mesajlaşma önbellekleri", H("Library/Group Containers/*.ru.keepcoder.Telegram/account-*/postbox/media"), 14 if self.is_deep else 30)
        self.age("Mesajlaşma önbellekleri", H("Library/Application Support/Spotify/PersistentCache"), 3 if self.is_deep else 14)
        self.age("Mesajlaşma önbellekleri", H("Library/Application Support/zoom.us/Logs"), 1 if self.is_deep else 3)
        self.age("Mesajlaşma önbellekleri", H("Library/Application Support/Microsoft/Teams/blob_storage"), 3 if self.is_deep else 14)
        # Tarayıcılar (tüm profiller)
        browser_days = 0 if self.is_deep else 7
        self.age("Tarayıcı önbellekleri", H("Library/Caches/com.apple.Safari"), browser_days)
        self.age("Tarayıcı önbellekleri", H("Library/Containers/com.apple.Safari/Data/Library/Caches"), browser_days)
        for b in ("Google/Chrome", "Microsoft Edge", "BraveSoftware/Brave-Browser", "Company/Arc", "Vivaldi", "Opera"):
            self.age("Tarayıcı önbellekleri", H(f"Library/Caches/{b}/*/Cache"), browser_days)
            self.age("Tarayıcı önbellekleri", H(f"Library/Caches/{b}/*/Code Cache"), browser_days)
            self.age("Tarayıcı önbellekleri", H(f"Library/Application Support/{b}/*/Service Worker/CacheStorage"), 3 if self.is_deep else 14)
            self.age("Tarayıcı önbellekleri", H(f"Library/Application Support/{b}/*/GPUCache"), browser_days)
        self.age("Tarayıcı önbellekleri", H("Library/Caches/Firefox/Profiles/*/cache2"), browser_days)
        # Kayıtlar, çökme raporları, geçici dosyalar, uygulama durumları
        self.age("Kayıt ve çökme raporları", H("Library/Logs"), 1 if self.is_deep else 7)
        self.age("Kayıt ve çökme raporları", H("Library/Logs/DiagnosticReports"), 1 if self.is_deep else 7)
        self.age("Eski uygulama durumları", H("Library/Saved Application State"), 7 if self.is_deep else 30)
        for var, days in (("DARWIN_USER_TEMP_DIR", 0 if self.is_deep else 3), ("DARWIN_USER_CACHE_DIR", 0 if self.is_deep else 7)):
            d = subprocess.run(["getconf", var], capture_output=True, text=True).stdout.strip()
            if d:
                self.age("Geçici dosyalar", d.rstrip("/"), days)
        self.age("Mail ekleri önbelleği", H("Library/Containers/com.apple.mail/Data/Library/Mail Downloads"), 14 if self.is_deep else 30)
        self.wipe("Eski iOS güncelleme dosyaları", H("Library/iTunes/*Software Updates"))
        self.age("Çöp Sepeti", H(".Trash"), 7 if self.is_deep else 30)
        # ~/.cache: model önbellekleri korunur, gerisi derin temizlikte 7 gün
        for d in glob.glob(H(".cache/*")):
            self.age("Araç önbellekleri", d, 7 if self.is_deep else 30)
        if not self.dry:
            subprocess.run(["qlmanage", "-r", "cache"], capture_output=True)
            if self.is_deep:
                subprocess.run(["dscacheutil", "-flushcache"], capture_output=True)
                subprocess.run(["killall", "-HUP", "mDNSResponder"], capture_output=True)

    def _installed_apps(self):
        names = set()
        bids = set()
        app_patterns = (
            "/Applications/*.app", "/Applications/*/*.app",
            "/System/Applications/*.app", "/System/Applications/*/*.app",
            H("Applications/*.app"), H("Applications/*/*.app")
        )
        for pattern in app_patterns:
            for app_path in glob.glob(pattern):
                base = os.path.basename(app_path)[:-4].casefold()
                names.add(base)
                p = os.path.join(app_path, "Contents", "Info.plist")
                if os.path.exists(p):
                    try:
                        with open(p, "rb") as f:
                            pl = plistlib.load(f)
                            bid = pl.get("CFBundleIdentifier")
                            if bid and isinstance(bid, str):
                                bids.add(bid.casefold())
                            for k in ("CFBundleName", "CFBundleDisplayName"):
                                v = pl.get(k)
                                if v and isinstance(v, str):
                                    names.add(v.casefold())
                    except Exception:
                        pass
                for ext in glob.glob(os.path.join(app_path, "Contents", "PlugIns", "*.appex")):
                    ext_p = os.path.join(ext, "Contents", "Info.plist")
                    if os.path.exists(ext_p):
                        try:
                            with open(ext_p, "rb") as f:
                                pl = plistlib.load(f)
                                bid = pl.get("CFBundleIdentifier")
                                if bid and isinstance(bid, str):
                                    bids.add(bid.casefold())
                        except Exception:
                            pass
        for pdir in ("/Library/Audio/Plug-Ins/VST3", "/Library/Audio/Plug-Ins/Components",
                     "/Library/Audio/Plug-Ins/VST", "/Library/OFX/Plugins",
                     "/Library/Plug-ins/FxPlug", "/Library/Application Support/Adobe/Common/Plug-ins"):
            if os.path.exists(pdir):
                for dp, _, fn in os.walk(pdir):
                    for item in fn:
                        base = re.sub(r"\.(vst3|component|vst|ofx\.bundle|bundle|fxplug|plugin)$", "", item, flags=re.I).casefold()
                        names.add(base)
        return names, bids

    def kalintilar(self):
        """Kaldırılmış uygulamaların sistemde bıraktığı artık klasör ve dosyaları temizler."""
        installed_names, installed_bids = self._installed_apps()

        system_vendors = set()
        for sroot in ("/Library/Application Support", "/System/Library/Application Support"):
            if os.path.isdir(sroot):
                try:
                    for e in os.scandir(sroot):
                        if e.is_dir() and not e.name.startswith("."):
                            system_vendors.add(e.name.casefold())
                            system_vendors.add(e.name.casefold().replace(" ", ""))
                except OSError:
                    pass

        def is_kept(name):
            low = name.casefold()
            clean = re.sub(r"\.(savedstate|plist|binarycookies)$", "", low)
            if clean in APPLE_SYSTEM_DIRS or low in APPLE_SYSTEM_DIRS:
                return True
            if any(k in clean for k in ("alptug", "apple", "google", "adobe", "blackmagic", "microsoft")):
                return True
            for w in SYSTEM_WHITELIST:
                w_clean = w.replace(" ", "")
                if w in clean or w_clean in clean or clean.startswith(w) or clean.startswith("com." + w_clean):
                    return True
            if clean in system_vendors or clean.replace(" ", "") in system_vendors:
                return True
            if clean.startswith("com.apple.") or clean.startswith("group.com.apple."):
                return True
            if clean in installed_names or clean in installed_bids or low in installed_names or low in installed_bids:
                return True
            if any(clean == bid or clean.startswith(bid + ".") for bid in installed_bids):
                return True
            return False

        targets = [
            H("Library/Application Support"),
            H("Library/Containers"),
            H("Library/Saved Application State"),
            H("Library/HTTPStorages"),
            H("Library/WebKit"),
        ]
        cat = "Kaldırılan uygulama kalıntıları"
        for root in targets:
            if not os.path.isdir(root):
                continue
            try:
                entries = list(os.scandir(root))
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if name.startswith(".") or re.match(r"^[0-9A-Fa-f-]{36}$", name):
                    continue
                if is_kept(name):
                    continue
                p = entry.path
                if protected(p):
                    continue
                if entry.is_dir() and not entry.is_symlink():
                    self._rm_tree(p, cat)
                else:
                    self._rm_file(p, cat)

    def gelistirici(self):
        derived_days = 0 if self.is_deep else 7
        self.age("Geliştirici önbellekleri", H("Library/Developer/Xcode/DerivedData"), derived_days)
        self.wipe("Geliştirici önbellekleri", H("Library/Developer/CoreSimulator/Caches"))
        self.age("Geliştirici önbellekleri", H("Library/Developer/Xcode/*DeviceSupport"), 90)
        self.age("Geliştirici önbellekleri", H("Library/Developer/Xcode/DocumentationCache"), 30)
        if not self.dry:
            subprocess.run(["xcrun", "simctl", "delete", "unavailable"], capture_output=True)
        brew_prune = "--prune=all" if self.is_deep else "--prune=7"
        self.cmd("Paket yöneticisi önbellekleri", ["brew", "cleanup", "-s", brew_prune],
                 measure=subprocess.run(["brew", "--cache"], capture_output=True, text=True).stdout.strip()
                 if shutil.which("brew") else None)
        self.cmd("Paket yöneticisi önbellekleri", ["npm", "cache", "clean", "--force"], measure=H(".npm/_cacache"))
        self.cmd("Paket yöneticisi önbellekleri", ["pip3", "cache", "purge"], measure=H("Library/Caches/pip"))
        self.cmd("Paket yöneticisi önbellekleri", ["uv", "cache", "prune"], measure=H(".cache/uv"))
        self.cmd("Paket yöneticisi önbellekleri", ["yarn", "cache", "clean"], measure=H("Library/Caches/Yarn"))
        self.cmd("Paket yöneticisi önbellekleri", ["pnpm", "store", "prune"], measure=H("Library/pnpm/store"))
        for p, d in ((".gradle/caches", 30), (".cargo/registry/cache", 30), ("Library/Caches/go-build", 30),
                     ("Library/Caches/CocoaPods", 30), ("Library/Caches/org.swift.swiftpm", 30),
                     ("Library/Caches/ms-playwright", 60), ("Library/Caches/Cypress", 60), (".android/cache", 30)):
            self.age("Paket yöneticisi önbellekleri", H(p), d)
        if proc.have("docker") and proc.ok(["docker", "info"], timeout=15):
            self.cmd("Docker", ["docker", "builder", "prune", "-f", "--filter", "until=720h"])
            self.cmd("Docker", ["docker", "image", "prune", "-f", "--filter", "until=720h"])

    # ------------------------------------------------------------------ sistem alanı (yönetici)
    def sistem(self, emergency=False):
        if not os.path.exists(PRIV):
            return
        before = free_bytes()
        args = ["sudo", "-n", PRIV, "clean-system"] + (["--snapshots"] if emergency else [])
        if not self.dry:
            subprocess.run(args, capture_output=True, timeout=1800)
            self._add("Sistem önbellekleri ve kayıtları", max(0, free_bytes() - before))

    # ------------------------------------------------------------------ yalnızca rapor
    def rapor(self):
        """Silinmez; size söylenir: eski kurulum dosyaları ve uzun süredir açılmamış büyük dosyalar."""
        out = {}
        inst = [p for p in glob.glob(H("Downloads/**/*"), recursive=True)
                if p.lower().endswith((".dmg", ".pkg", ".iso", ".xip", ".ipsw"))
                and time.time() - os.path.getmtime(p) > 30 * DAY]
        out["eski_kurulum_dosyalari"] = {"adet": len(inst), "bayt": sum(os.path.getsize(p) for p in inst)}
        big, total = [], 0
        for root in (H("Downloads"), H("Desktop"), H("Movies")):
            for dp, dn, fn in os.walk(root):
                dn[:] = [d for d in dn if not d.startswith(".") and not protected(os.path.join(dp, d))]
                for f in fn:
                    p = os.path.join(dp, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if st.st_size > 2 * 1024 ** 3 and time.time() - max(st.st_atime, st.st_mtime) > 180 * DAY:
                        big.append(p)
                        total += st.st_size
        out["acilmamis_buyuk_dosyalar"] = {"adet": len(big), "bayt": total, "ornek": big[:5]}
        return out


def du(path):
    if not path or not os.path.exists(path):
        return 0
    r = proc.run(["du", "-sk", path], capture_output=True, text=True, timeout=60)
    try:
        return int(r.stdout.split()[0]) * 1024
    except (ValueError, IndexError):
        return 0


def free_bytes():
    st = os.statvfs("/System/Volumes/Data")
    return st.f_bavail * st.f_frsize


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="gunluk", choices=["gunluk", "haftalik", "acil"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    start_free = free_bytes()
    c = Cleaner(dry=a.dry_run, scale=0.35 if a.tier == "acil" else 1.0, tier=a.tier)
    c.kurgu()
    c.uygulamalar()
    if a.tier in ("haftalik", "acil"):
        c.gelistirici()
        c.kalintilar()
        c.sistem(emergency=(a.tier == "acil"))
    result = {"tier": a.tier, "dry": a.dry_run, "kategoriler": c.freed,
              "toplam": sum(c.freed.values()),
              "gercek_bosalan": max(0, free_bytes() - start_free) if not a.dry_run else None,
              "rapor": c.rapor() if a.tier != "gunluk" else None}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
