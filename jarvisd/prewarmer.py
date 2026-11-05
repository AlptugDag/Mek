# -*- coding: utf-8 -*-
"""M1 Max Birleşik Bellek (RAM Page-Cache) Ön-Isıtıcı (Pre-Warmer).

macOS XNU çekirdeğinin `F_RDADVISE` (asenkron NVMe -> Birleşik RAM DMA önbellekleme) mekanizmasını
kullanarak, kurgu uygulamalarının (Premiere Pro, After Effects, Media Encoder, Photoshop, DaVinci
Resolve, Topaz Video) ana ikili dosyalarını, çekirdek framework'lerini, tüm eklentileri
(Red Giant, Maxon, BorisFX, Dehancer, FxFactory, Laforge, OFX, CEP/UXP, VST3/AU) ve GPU shader
önbelleklerini (PTX) birleşik bellekte (RAM dosya önbelleği) sıcak tutar.

Sonuç:
- Uygulama açılışlarında (Cold/Warm Launch) diskten binlerce `.bundle`/`.framework` okuma gecikmesi ortadan kalkar.
- Efekt paneli açılışı, eklenti yükleme ve ilk render başlatma anında sıfır disk darboğazı yaşanır.
- Yalnızca bellek baskısı normalken (`pressure == 1`) ve en düşük arka plan önceliğinde çalışır;
  macOS birleşik bellek mimarisi bu önbelleği ihtiyaç halinde 0 ms gecikmeyle uygulamalara geri verir.
"""
import fcntl
import glob
import logging
import os
import re
import struct
import threading
import time

log = logging.getLogger("mek.prewarmer")
HOME = os.path.expanduser("~")
F_RDADVISE = 44  # macOS Darwin <sys/fcntl.h>: struct radvisory { off_t ra_offset; int ra_count; }

# Kurgu uygulamalarında açılış, zaman çizelgesi (timeline), decode ve render hızını doğrudan belirleyen framework'ler:
HOT_FRAMEWORK_PREFIXES = (
    "dvacore",
    "dvaaccelerate",
    "dvaaudiodsp",
    "dvaaudiofilterhost",
    "dvamediatypes",
    "dvanet",
    "dvascripting",
    "dvatemporalxmp",
    "dvavulcan",
    "GPUFoundation",
    "ImageRenderer",
    "VideoFrame",
    "VideoRenderer",
    "Backend",
    "Mezzanine",
    "MLFoundation",
    "PluginSupport",
    "SweetPeaSupport",
    "VideoFilterHost",
    "ImporterHost",
    "ExporterHost",
    "PlayerHost",
    "CodecSupport",
    "DynamicLinkMedia",
    "Frontend",
    "HandlerTimeline",
    "HandlerProgramMonitor",
    "HandlerSourceMonitor",
    "HandlerEffectControls",
    "HandlerEffects",
    "HandlerProject",
    "ATEWrapper",
    "TimeCarving",
)

PLUGIN_ROOTS = (
    "/Library/Application Support/Adobe/Common/Plug-ins/7.0/MediaCore",
    "/Library/OFX/Plugins",
    "/Library/Application Support/Adobe/CEP/extensions",
    os.path.expanduser("~/Library/Application Support/Adobe/CEP/extensions"),
    "/Library/Audio/Plug-Ins/VST3",
    "/Library/Audio/Plug-Ins/Components",
)

CREATIVE_APP_GLOBS = (
    "/Applications/Adobe Premiere Pro*/*.app",
    "/Applications/Adobe After Effects*/*.app",
    "/Applications/Adobe Media Encoder*/*.app",
    "/Applications/Adobe Photoshop*/*.app",
    "/Applications/Adobe Audition*/*.app",
    "/Applications/DaVinci Resolve/*.app",
    "/Applications/Topaz*.app",
)


def advise_file(path, max_bytes=32 * 1024 * 1024):
    """Darwin F_RDADVISE ile dosyayı kullanıcı alanına kopyalamadan doğrudan çekirdek RAM önbelleğine alır."""
    try:
        st = os.stat(path)
        if st.st_size <= 0:
            return 0
        count = min(int(st.st_size), int(max_bytes))
        with open(path, "rb") as f:
            try:
                fcntl.fcntl(f.fileno(), F_RDADVISE, struct.pack("qi", 0, count))
            except OSError:
                # F_RDADVISE desteklenmeyen özel dosya sistemlerinde küçük başlık okuması:
                f.read(min(count, 65536))
        return count
    except OSError:
        return 0


def _discover_creative_apps(apps_dir="/Applications"):
    apps = []
    if apps_dir == "/Applications":
        patterns = CREATIVE_APP_GLOBS
    else:
        patterns = (
            os.path.join(apps_dir, "Adobe*/*.app"),
            os.path.join(apps_dir, "Adobe*.app"),
            os.path.join(apps_dir, "DaVinci*/*.app"),
            os.path.join(apps_dir, "Topaz*.app"),
        )
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            if os.path.isdir(p):
                apps.append(p)
    return apps


def warm_app_bundle(app_path, budget_bytes=450 * 1024 * 1024):
    """Tek bir kurgu uygulamasının (.app) ana çalıştırılabilir dosyasını, kritik framework'lerini ve
    yerleşik eklentilerini RAM önbelleğine ısıtır."""
    warmed = 0
    contents = os.path.join(app_path, "Contents")
    if not os.path.isdir(contents):
        return 0

    # 1. Info.plist ve ana çalıştırılabilir ikili dosyalar (Contents/MacOS/*)
    warmed += advise_file(os.path.join(contents, "Info.plist"), max_bytes=512 * 1024)
    macos_dir = os.path.join(contents, "MacOS")
    if os.path.isdir(macos_dir):
        try:
            for entry in os.listdir(macos_dir):
                p = os.path.join(macos_dir, entry)
                if os.path.isfile(p):
                    warmed += advise_file(p, max_bytes=96 * 1024 * 1024)
                    if warmed >= budget_bytes:
                        return warmed
        except OSError:
            pass

    # 2. Sıcak çekirdek framework'ler (Contents/Frameworks/*.framework)
    fw_dir = os.path.join(contents, "Frameworks")
    if os.path.isdir(fw_dir):
        for prefix in HOT_FRAMEWORK_PREFIXES:
            fw_bin = os.path.join(fw_dir, f"{prefix}.framework", "Versions", "A", prefix)
            if not os.path.isfile(fw_bin):
                fw_bin = os.path.join(fw_dir, f"{prefix}.framework", prefix)
            if os.path.isfile(fw_bin):
                warmed += advise_file(fw_bin, max_bytes=24 * 1024 * 1024)
                if warmed >= budget_bytes:
                    return warmed

    # 3. Uygulama içi yerleşik temel eklentiler (Importer/Exporter/GPU hızlandırıcılar)
    common_plugins = os.path.join(contents, "Plug-Ins", "Common")
    if os.path.isdir(common_plugins):
        for dp, _, fn in os.walk(common_plugins):
            for f in fn:
                if f == "Info.plist" or "/Contents/MacOS" in dp:
                    warmed += advise_file(os.path.join(dp, f), max_bytes=8 * 1024 * 1024)
                    if warmed >= budget_bytes:
                        return warmed
    return warmed


def warm_plugins_and_caches(home=HOME, plugin_roots=PLUGIN_ROOTS, budget_bytes=750 * 1024 * 1024):
    """Tüm kurulu eklentileri (MediaCore, OFX, CEP, VST3/AU), GPU shader önbelleklerini (PTX)
    ve Adobe eklenti kayıt defterlerini RAM önbelleğine ısıtır."""
    warmed = 0
    files_count = 0

    # 1. GPU Shader Önbelleği (PTX) ve Eklenti Kayıt Defterleri (PluginCache / AudioPluginManager / Prefs)
    hot_user_globs = (
        os.path.join(home, "Library/Application Support/Adobe/Common/PTX/**/*"),
        os.path.join(home, "Library/Preferences/Adobe/*/*/Debug Database.txt"),
        os.path.join(home, "Library/Preferences/Adobe/*/*/AudioPluginManagerSettings*.xml"),
        os.path.join(home, "Library/Preferences/Adobe/*/*/PluginCache*"),
        os.path.join(home, "Documents/Adobe/Premiere Pro/*/Profile-*/Adobe Premiere Pro Prefs"),
        os.path.join(home, "Documents/Adobe/Adobe Media Encoder/*/Adobe Media Encoder Prefs"),
        os.path.join(home, "Library/Preferences/Blackmagic Design/DaVinci Resolve/config.*"),
    )
    for pat in hot_user_globs:
        for p in glob.glob(pat, recursive=True):
            if os.path.isfile(p):
                b = advise_file(p, max_bytes=16 * 1024 * 1024)
                if b > 0:
                    warmed += b
                    files_count += 1

    # 2. Üçüncü parti eklenti kökleri (Red Giant, Maxon, BorisFX, Dehancer, FxFactory, Laforge, CEP...)
    for root in plugin_roots:
        if not os.path.isdir(root):
            continue
        for dp, _, fn in os.walk(root):
            for f in fn:
                if f in ("Info.plist", "PkgInfo", "manifest.xml") or "/Contents/MacOS" in dp or f.endswith((".jsx", ".js", ".html")):
                    p = os.path.join(dp, f)
                    if os.path.isfile(p):
                        b = advise_file(p, max_bytes=12 * 1024 * 1024)
                        if b > 0:
                            warmed += b
                            files_count += 1
                        if warmed >= budget_bytes:
                            return warmed, files_count
    return warmed, files_count


class PreWarmer:
    """Kurgu uygulamalarını, framework'lerini ve eklentilerini birleşik RAM önbelleğinde sıcak tutar."""

    def __init__(self, home=HOME, apps_dir="/Applications", min_interval_s=1200):
        self.home = home
        self.apps_dir = apps_dir
        self.min_interval_s = min_interval_s
        self.last_full_warm = 0.0
        self.warmed_apps = {}  # app_path -> ts
        self._lock = threading.Lock()
        self._running = False

    def warm_now(self, app_filter=None):
        """Senkron ön-ısıtma çalıştırır; ısıtılan toplam bayt ve uygulama sayısını döndürür."""
        total_bytes = 0
        apps_warmed = 0
        now = time.time()

        b_plug, n_plug = warm_plugins_and_caches(home=self.home)
        total_bytes += b_plug

        for app_path in _discover_creative_apps(self.apps_dir):
            if app_filter and not re.search(app_filter, app_path, re.I):
                continue
            b_app = warm_app_bundle(app_path)
            if b_app > 0:
                total_bytes += b_app
                apps_warmed += 1
                self.warmed_apps[app_path] = now

        self.last_full_warm = now
        log.info(
            "Birleşik RAM önbellek ön-ısıtması tamamlandı: %d uygulama, %d eklenti/önbellek dosyası (%.1f MB)",
            apps_warmed,
            n_plug,
            total_bytes / (1024 * 1024),
        )
        return {"bytes": total_bytes, "apps": apps_warmed, "plugin_files": n_plug}

    def tick_async(self, pressure=1, mem_pct=0.0):
        """Bellek baskısı normalken (pressure == 1) arka plan iş parçacığında ön-ısıtmayı tetikler."""
        if pressure > 1 or mem_pct >= 85.0:
            return False
        now = time.time()
        if now - self.last_full_warm < self.min_interval_s:
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True

        def _worker():
            try:
                try:
                    os.nice(10)
                except OSError:
                    pass
                self.warm_now()
            except Exception as e:
                log.warning("Ön-ısıtma hatası: %s", e)
            finally:
                with self._lock:
                    self._running = False

        threading.Thread(target=_worker, daemon=True).start()
        return True
