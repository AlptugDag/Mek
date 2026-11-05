# -*- coding: utf-8 -*-
"""Dinamik kaynak yöneticisi (eski keepawake_videoapps.sh'nin yerini alır).

Kural: kaynak, gerçekten iş yapan uygulamaya. Bir kurgu uygulamasının yalnızca AÇIK olması sistemi
"performans" kipine sokmaz; önde kullanılıyor ya da render/dışa aktarma yapıyor olması gerekir.

Profiller:
  performance  - kurgu/fotoğraf uygulaması önde ya da render sürüyor: o uygulama arka plan kısıtından
                 çıkarılır (8 P-çekirdeğin tamamı ona verilir), arka planda boşta bekleyen diğer
                 ağır uygulamalar ve tüm eklenti/eşitleme süreçleri (CEPHtmlEngine vb.) E-çekirdeklere
                 park edilir. Render sürüyorsa sistem uykuya geçmez (ekran uyuyabilir).
  ready        - kurgu uygulaması açık ama arka planda boşta: boşta bekleyen ağır uygulamalar E-çekirdekte
                 ve App Nap altında sessizce bekler; sistem kaynaklarını sömürmez.
  normal       - kurgu uygulaması yok: her şey eski haline döner, yetim yardımcı süreçler temizlenir.
"""
import logging
import os
import re
import signal
import subprocess
import threading
import time

import psutil

from config import (
    CLEANER,
    CREATIVE_BUNDLE_PATTERN,
    MASTER_SETUP,
    PLUGIN_SERVICE_AGENTS,
    PRIV_HELPER,
    PRO_APP_PATTERN,
    STATE_DIR,
)
import app_tuner
import context as ctx
from prewarmer import PreWarmer
from project_timemachine import ProjectTimeMachine
from thermal_fan import ThermalFanController
from ultra_scratch import UltraScratchManager

log = logging.getLogger("mek.services")
UID = os.getuid()

# Adobe'nin pazarlama/reklam/telemetri alt süreçleri (%98 CPU yakabilen GrowthSDKHelper vb.):
# Bunlar Creative Cloud klasörü altında çalışsa bile ASLA kurgu yardımcısı sayılmaz!
ADOBE_MARKETING_BLOAT_RE = re.compile(
    r"GrowthSDKHelper|Adobe Notification Client|Adobe Crash Processor|CRLogTransport|LogTransport2|"
    r"crashpad_handler|CCXProcess.*--type=renderer",
    re.I,
)

# Kurgu uygulamaları, eklentiler (Red Giant, Maxon, BorisFX, Dehancer, FxFactory, Laforge, CEP/UXP)
# ve bunların bağlı olduğu IPC/lisans/köprü servisleri:
# Hangi sürümde (2026, 2030 vb.) olursa olsun ASLA taskpolicy -b (DARWIN_BG) altına alınmaz!
CREATIVE_IPC_RE = re.compile(
    r"CEPHtmlEngine|UXP|AdobeIPCBroker|dynamiclinkmanager|AdobeQTServer|TeamProjectsLocalHub|"
    r"PProHeadless|aerender|AEGPUSniffer|Adobe Desktop Service|CCLibrary|Core Sync|"
    r"ACCFinderSync|Red Giant|Maxon|mxnotify|mxredirect|Magic Bullet|Trapcode|"
    r"BorisFX|bfxdhelper|rlmserver|Sapphire|Continuum|Mocha|FxFactory|Dehancer|Laforge|"
    r"Captioneer|AtomX|BeatEdit|PSEPremierePanel|FilmConvert|fusionscript|DPDecoder|"
    r"Fairlight|neuroserver|veai|logioptionsplus|Camera Hub",
    re.I,
)
CREATIVE_PROTECT_RE = re.compile(
    f"({CREATIVE_BUNDLE_PATTERN})|({PRO_APP_PATTERN})|({CREATIVE_IPC_RE.pattern})",
    re.I,
)

# Her zaman E-çekirdeklere (arka plan QoS) sabitlenecek sistem indeksleme süreçleri.
# Not: WallpaperMacintoshExtension koşulsuz öldürülürse macOS 27'de WallpaperAgent %110+ CPU döngüsüne girer;
# bu yüzden öldürmek yerine E-çekirdeklere sabitlenir, yalnızca CPU kaçarsa yeniden başlatılır.
BG_CLAMP = re.compile(
    r"spotlightknowledged|mds_stores|photoanalysisd|mediaanalysisd|"
    r"IntelligencePlatformComputeService|SetStoreUpdateService|"
    r"WallpaperAgent|WallpaperMacintoshExtension|WallpaperVideoExtension"
)

# Yalnızca kurgu ve eklenti ekosisteminin DIŞINDA kalan bulut/güncelleyici/telemetri süreçleri:
BLOAT_CLAMP = re.compile(
    r"GrowthSDKHelper|Adobe Notification Client|Adobe Crash Processor|CRLogTransport|LogTransport2|crashpad_handler|"
    r"GoogleUpdater|Dropbox|OneDrive|Google Drive|bird|cloudd|photolibraryd|cloudphotod"
)

PERF_CLAMP = BLOAT_CLAMP

# Ana Adobe/kurgu uygulamaları kapandıktan sonra bellekte asılı kalabilen yetim süreçler (yıldan bağımsız)
ADOBE_MAIN_RE = re.compile(
    r"Contents/MacOS/(Adobe Premiere[^/]*|After Effects[^/]*|Adobe After Effects[^/]*|"
    r"Adobe Media Encoder[^/]*|Adobe Photoshop[^/]*|Photoshop[^/]*|Adobe Audition[^/]*|"
    r"Adobe Illustrator[^/]*|Adobe Lightroom[^/]*)"
)
ORPHAN_HELPERS_RE = re.compile(
    r"CEPHtmlEngine|dynamiclinkmanager|AdobeQTServer|AI MACHINE Server|Adobe Crash Processor|CRLogTransport|LogTransport2"
)

# Arka planda gerçek render/dışa aktarma motorları (yıldan/sürümden bağımsız)
RENDER_ENGINE_RE = re.compile(
    r"PProHeadless|Adobe Media Encoder|aerender|AEGPUSniffer|Topaz[^/]*.*/(ffmpeg|neuroserver)|Compressor"
)

_PLUGIN_DIRS = (
    "/Library/OFX/Plugins",
    "/Library/Application Support/Adobe/Common/Plug-ins/7.0/MediaCore",
    "/Library/Application Support/Adobe/CEP/extensions",
    os.path.expanduser("~/Library/Application Support/Adobe/CEP/extensions"),
    "/Library/Audio/Plug-Ins/VST3",
    "/Library/Audio/Plug-Ins/Components",
)


def _discover_installed_plugin_rx():
    """Sistemdeki eklenti klasörlerini tarayarak yeni kurulan veya güncellenen eklentilerin
    isimlerini otomatik öğrenir (5 dakikada bir önbellekli)."""
    tokens = set()
    for d in _PLUGIN_DIRS:
        try:
            for entry in os.listdir(d):
                if entry.startswith("."):
                    continue
                base = re.sub(r"\.(plugin|bundle|ofx\.bundle|aex|vst3|component)$", "", entry, flags=re.I)
                base = re.sub(r"[-_v]+\d[\d._]*$", "", base).strip()
                if len(base) >= 4:
                    tokens.add(re.escape(base))
        except OSError:
            continue
    if not tokens:
        return None
    return re.compile("|".join(sorted(tokens)), re.I)


def is_creative_or_plugin(target):
    """Verilen süreç adı/yolu bir kurgu uygulamasına, eklentiye veya eklenti servisine mi ait?"""
    if not target:
        return False
    if ADOBE_MARKETING_BLOAT_RE.search(target):
        return False
    if CREATIVE_PROTECT_RE.search(target):
        return True
    dyn_rx = ctx._cached("dyn_plugin_rx", 300, _discover_installed_plugin_rx)
    return bool(dyn_rx and dyn_rx.search(target))


def _lineage_pids(root_pids, ppids):
    """Aktif kurgu uygulamalarının başlattığı tüm alt/yardımcı/eklenti süreçlerini (ppid ağacı) bulur."""
    if not root_pids or not ppids:
        return set()
    tree = set(root_pids)
    changed = True
    depth = 0
    while changed and depth < 6:
        changed = False
        depth += 1
        for pid, ppid in ppids.items():
            if ppid in tree and pid not in tree:
                tree.add(pid)
                changed = True
    return tree - set(root_pids)

RENDER_CPU = 280.0     # 10 çekirdekli M1 Max'te çok çekirdekli gerçek render eşiği (boşta CEP spin'i ~150%'dir)
IDLE_GRACE_S = 45      # önden çekildikten sonra performans kipinde kalma süresi
BG_PARK_DELAY_S = 12   # arka plana düşen boşta kurgu uygulamasını E-çekirdeğe park etme gecikmesi
PURGE_COOLDOWN_S = 180 # iki otomatik RAM önbellek temizliği (purge) arası en az süre


def _run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, **kw)


def _iter_procs(snap=None):
    """Varsa snapshot içindeki tek geçişli süreç listesini kullanır; yoksa psutil ile tarar."""
    if snap is not None and getattr(snap, "procs", None):
        return snap.procs
    out = []
    for p in psutil.process_iter(["pid", "name", "exe", "cpu_percent"]):
        try:
            info = p.info
            out.append((info["pid"], info.get("name") or "", info.get("exe") or "", info.get("cpu_percent") or 0.0, 0))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return out


def _pids(rx, snap=None):
    out = []
    for pid, name, cmd, _, _ in _iter_procs(snap):
        if rx.search(cmd or name or ""):
            out.append(pid)
    return out


def priv(*args):
    """Ayrıcalıklı yardımcıyı parolasız çalıştırır (kuruluysa)."""
    if not os.path.exists(PRIV_HELPER):
        return False
    r = subprocess.run(["sudo", "-n", PRIV_HELPER, *args], capture_output=True, text=True)
    if r.returncode != 0:
        log.warning("mek-priv %s başarısız: %s", args, r.stderr.strip())
    return r.returncode == 0


def app_of(exe):
    """'/Applications/X/Y.app/Contents/MacOS/Y' -> 'Y' (paket adı)."""
    m = re.search(r"/([^/]+)\.app/Contents/MacOS/", exe or "")
    return m.group(1) if m else os.path.basename(exe or "")


def _renice_safe(pid, nice_val):
    try:
        os.setpriority(os.PRIO_PROCESS, pid, nice_val)
    except OSError:
        pass


class ServiceManager:
    def __init__(self, cfg, events=None):
        self.cfg = cfg
        self.events = events
        self.profile = "normal"
        self.caff = None
        self.stopped_agents = []
        self.last_active = 0.0
        self.boosted = set()          # Tam öncelikteki (Tier-0 I/O ve gecikme) aktif kurgu süreçleri
        self.parked_pro = set()       # Arka planda boşta bekleyen ikincil kurgu süreçleri (DARWIN_BG'ye sokulmaz)
        self.pro_last_active = {}     # pid -> son aktif (önde/render) zamanı
        self.unthrottled_ipc = set()  # DARWIN_BG'den çıkarıldığı doğrulanmış kurgu/eklenti/IPC yardımcıları
        self.clamped = set()          # E-çekirdeğe alınan bulut/eşitleme süreçleri
        self.bg_clamped = set()       # E-çekirdeğe alınan sistem indeksleme süreçleri
        self.next_guard = 0.0
        self.next_tune = 0.0
        self.next_tm_scan = 0.0
        self.last_adobe_seen = time.time()
        self.last_purge = 0.0
        self.last_front_pro = None
        self.cleaner_stamp = os.path.join(STATE_DIR, "cleaner_last")
        self.prewarmer = PreWarmer()
        self.ultra_scratch = UltraScratchManager()
        self.timemachine = ProjectTimeMachine()
        self.thermal_fan = ThermalFanController()
        priv("restore-high-power")
        self._ensure_plugin_services()
        _run(["/bin/launchctl", "limit", "maxfiles", "65536", "200000"])
        if os.access(MASTER_SETUP, os.X_OK):
            subprocess.Popen([MASTER_SETUP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._tune_and_prewarm_bg, daemon=True).start()

    def _tune_and_prewarm_bg(self):
        try:
            self.ultra_scratch.ensure_mounted()
            self.timemachine.scan_and_snapshot()
            app_tuner.tune_all()
            self.prewarmer.tick_async(pressure=1, mem_pct=50.0)
        except Exception as e:
            log.warning("Başlangıç kurgu motoru optimizasyonu hatası: %s", e)

    def _ensure_plugin_services(self):
        """Red Giant, Maxon, BorisFX, Adobe ve Topaz eklenti servislerinin her zaman çalışır olmasını sağlar."""
        for plist in PLUGIN_SERVICE_AGENTS:
            if os.path.exists(plist):
                _run(["/bin/launchctl", "bootstrap", f"gui/{UID}", plist])

    def _safe_priv_perf(self):
        """Eski kurulu mek-priv betiğinde com.redgiant.service varsa onu durdurmamak için normal çağrılır."""
        try:
            if os.path.exists(PRIV_HELPER):
                with open(PRIV_HELPER, encoding="utf-8", errors="ignore") as f:
                    if "redgiant" in f.read():
                        priv("normal")
                        return
        except OSError:
            pass
        priv("perf")

    # --- yardımcı QoS işlemleri ---------------------------------------------------
    def _unpark_pro(self, pid, snap=None):
        """Kurgu sürecini ve alt süreç ağacını arka plan kısıtından çıkarıp tam P-çekirdek/Tier-0 önceliğine alır."""
        if pid in self.parked_pro or pid not in self.boosted:
            _run(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", str(pid)])
            _renice_safe(pid, 0)
            self.parked_pro.discard(pid)
            self.boosted.add(pid)
            log.info("Kurgu süreci tam performansa alındı (P-çekirdek, Tier-0 I/O): pid=%d", pid)
        if snap is not None and getattr(snap, "ppids", None):
            for child_pid in _lineage_pids({pid}, snap.ppids):
                if child_pid not in self.unthrottled_ipc:
                    _run(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", str(child_pid)])
                    _renice_safe(child_pid, 0)
                    self.unthrottled_ipc.add(child_pid)

    def _park_pro(self, pid, name="", secondary=False):
        """Arka planda boşta duran kurgu sürecini 'hazır/beklemede' olarak işaretler.
        Başka bir kurgu uygulaması aktif olarak önde/render'daysa (secondary=True), geri alınabilir
        Tier-3 throughput/latency kademesine (-B -t 3 -l 3) çeker; tek kurgu uygulaması açıksa Tier-0'da tutar.
        ÖNEMLİ: Kurgu süreçlerine ASLA taskpolicy -b (PRIO_DARWIN_BG) veya pozitif nice uygulanmaz."""
        if pid not in self.parked_pro:
            tier = "3" if secondary else "0"
            _run(["/usr/sbin/taskpolicy", "-B", "-t", tier, "-l", tier, "-p", str(pid)])
            self.boosted.discard(pid)
            self.parked_pro.add(pid)
            log.info("Arka planda boşta bekleyen kurgu süreci bekleme kipine (Tier-%s) alındı: %s (pid=%d)",
                     tier, name or "pro", pid)

    def smart_purge(self, reason="kritik bellek baskısı"):
        """Yalnızca kritik çekirdek bellek baskısında (pressure >= 4) çalıştırılır.
        Normal kullanımda dosya önbelleği (Unified Buffer Cache) ASLA sıfırlanmaz."""
        now = time.time()
        if now - self.last_purge < PURGE_COOLDOWN_S:
            return False
        self.last_purge = now

        def _do():
            if priv("purge"):
                log.info("Akıllı RAM önbellek temizliği (purge) tamamlandı (%s).", reason)

        threading.Thread(target=_do, daemon=True).start()
        return True

    def cleanup_orphans(self, snap=None, force=False):
        """Ana Adobe uygulamaları kapalıyken bellekte asılı kalan yetim yardımcı süreçleri temizler.
        Herhangi bir Adobe ana uygulaması açıksa (force=True olsa dahi) yardımcı süreçlere dokunulmaz!"""
        procs = _iter_procs(snap)
        now = time.time()
        adobe_open = any(ADOBE_MAIN_RE.search(cmd or name or "") for _, name, cmd, _, _ in procs)
        if adobe_open:
            self.last_adobe_seen = now
            return 0
        if not force and (now - self.last_adobe_seen < 30):
            return 0
        killed = 0
        for pid, name, cmd, _, _ in procs:
            target = cmd or name or ""
            if ORPHAN_HELPERS_RE.search(target) and not ADOBE_MAIN_RE.search(target):
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                except OSError:
                    pass
        if killed:
            log.info("Kapalı uygulamalardan kalan %d yetim yardımcı süreç temizlendi.", killed)
        return killed

    # --- anlık uygulama geçişi (MekMenuBar NSWorkspace bildirimiyle <20ms) -------
    def on_focus_change(self, pid, app_name, snap=None):
        """Kullanıcı Cmd+Tab ile uygulama değiştirdiğinde anında çağrılır."""
        now = time.time()
        front_lower = (app_name or "").lower()
        ctx.set_front_override(pid, app_name)
        if snap is not None:
            snap.front = app_name
            snap.front_pid = pid

        pro_procs = getattr(snap, "pro_procs", ()) if snap is not None else ()
        focused_is_pro = any(
            (ppid == pid) or (front_lower and (front_lower in app_of(exe).lower() or app_of(exe).lower() in front_lower))
            for ppid, exe, _, _ in pro_procs
        )
        for ppid, exe, cpu, _ in pro_procs:
            pname = app_of(exe).lower()
            is_this_front = (ppid == pid) or (front_lower and (front_lower in pname or pname in front_lower))
            if is_this_front:
                self.pro_last_active[ppid] = now
                self._unpark_pro(ppid, snap)
            else:
                is_rendering = (cpu >= RENDER_CPU) or bool(RENDER_ENGINE_RE.search(exe) and cpu >= 50.0)
                if not is_rendering:
                    # Yalnızca başka bir kurgu uygulaması öndeyse (focused_is_pro=True) ikincil Tier-3'e al
                    self._park_pro(ppid, app_of(exe), secondary=focused_is_pro)

        if focused_is_pro:
            if self.last_front_pro and self.last_front_pro != front_lower and snap is not None:
                # Yalnızca kırmızı/kritik bellek baskısı (pressure >= 4) varsa purge çalıştır;
                # normal geçişlerde birleşik RAM dosya önbelleği korunur!
                if getattr(snap, "pressure", 1) >= 4:
                    self.smart_purge(f"{self.last_front_pro} -> {front_lower} kritik bellek geçişi")
            self.last_front_pro = front_lower

    # --- 30 saniyede bir ---------------------------------------------------------
    def guard(self, snap=None):
        alive_bg = set()
        alive_bloat = set()
        alive_ipc = set()
        lineage = _lineage_pids(getattr(snap, "pro_pids", ()), getattr(snap, "ppids", None)) if snap else set()
        for pid, name, cmd, cpu, _ in _iter_procs(snap):
            try:
                target = cmd or name or ""
                if "WallpaperMacintoshExtension" in name and cpu > 80.0:
                    os.kill(pid, signal.SIGKILL)
                    continue
                if re.search(r"GrowthSDKHelper|Adobe Crash Processor|CRLogTransport|LogTransport2", target) and cpu > 25.0:
                    os.kill(pid, signal.SIGKILL)
                    log.info("Adobe telemetri/çökme raporlama süreci yüksek CPU nedeniyle sonlandırıldı: %s pid=%d (%%%.0f)",
                             name or "helper", pid, cpu)
                    continue
                # Kurgu paketleri, alt süreç ağaçları, eklentiler ve bağlı servisler (Red Giant, Maxon,
                # BorisFX, Dehancer, FxFactory, CEPHtmlEngine, dynamiclinkmanager vb.) kesinlikle kısıtlanmaz;
                # aksine tam performansa (Tier-0, P-çekirdek) açılır!
                if (pid in lineage or is_creative_or_plugin(target)) and not ADOBE_MARKETING_BLOAT_RE.search(target):
                    if pid in lineage or CREATIVE_IPC_RE.search(target) or is_creative_or_plugin(target):
                        alive_ipc.add(pid)
                        if pid not in self.unthrottled_ipc:
                            _run(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", str(pid)])
                            _renice_safe(pid, 0)
                    continue
                if BG_CLAMP.search(target):
                    alive_bg.add(pid)
                    if pid not in self.bg_clamped:
                        _run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)])
                if BLOAT_CLAMP.search(target):
                    alive_bloat.add(pid)
                    if pid not in self.clamped:
                        _run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)])
            except (OSError, ValueError):
                pass
        self.bg_clamped = alive_bg
        self.clamped = alive_bloat
        self.unthrottled_ipc = alive_ipc
        self.cleanup_orphans(snap)

    # --- her döngüde -------------------------------------------------------------
    @staticmethod
    def working_pids(snap):
        """Gerçekten iş yapan kurgu süreçleri: öndeki uygulamaya ait olanlar ya da aktif render yapanlar."""
        front = (snap.front or "").lower()
        front_pid = getattr(snap, "front_pid", None)
        out = []
        for pid, exe, cpu, _ in getattr(snap, "pro_procs", ()):
            name = app_of(exe).lower()
            is_front = (pid == front_pid) or bool(front and (front in name or name in front))
            is_render_engine = bool(RENDER_ENGINE_RE.search(exe)) and cpu >= 50.0
            if is_front or is_render_engine or cpu >= RENDER_CPU:
                out.append(pid)
        return out

    def tick(self, snap, rendering=False):
        now = time.time()
        if now >= self.next_guard:
            self.next_guard = now + 30
            self.guard(snap)
        if now >= self.next_tm_scan:
            self.next_tm_scan = now + 30
            threading.Thread(
                target=self._periodic_scratch_and_tm,
                args=(getattr(snap, "pressure", 1),),
                daemon=True,
            ).start()
        if now >= self.next_tune:
            self.next_tune = now + 900
            self.prewarmer.tick_async(
                pressure=getattr(snap, "pressure", 1),
                mem_pct=getattr(snap, "mem_pct", 0.0),
            )
        self.thermal_fan.tick(rendering=rendering, thermal_state=getattr(snap, "thermal", 0))

        working = self.working_pids(snap)
        working_set = set(working)
        for pid in working_set:
            self.pro_last_active[pid] = now

        if working or rendering:
            self.last_active = now
        if working or rendering or (self.profile == "performance" and now - self.last_active < IDLE_GRACE_S):
            target = "performance"
        elif snap.pro_pids:
            target = "ready"
        else:
            target = "normal"

        if target != self.profile:
            {"performance": self.enter_performance, "ready": self.enter_ready,
             "normal": self.enter_normal}[target]()

        # Aktif (öndeki veya render yapan) kurgu süreçlerini ve alt eklenti ağacını Tier-0'a aç,
        # arka planda boşta bekleyen ikincil kurgu süreçlerini ise (başka bir kurgu aktifse) Tier-3'e al!
        for pid, exe, cpu, _ in getattr(snap, "pro_procs", ()):
            if pid in working_set:
                self._unpark_pro(pid, snap)
            else:
                last_act = self.pro_last_active.get(pid, 0.0)
                if working_set or (now - last_act >= BG_PARK_DELAY_S):
                    self._park_pro(pid, app_of(exe), secondary=bool(working_set))

        # Yalnızca kırmızı/kritik çekirdek bellek baskısı (pressure >= 4) varsa purge çalıştır
        if snap.pro_pids and getattr(snap, "pressure", 1) >= 4:
            self.smart_purge("kritik bellek baskısı")

        live = set(snap.pro_pids)
        self.boosted &= live
        self.parked_pro &= live
        for dead in set(self.pro_last_active) - live:
            self.pro_last_active.pop(dead, None)

        # Uyku engeli yalnızca render sürerken (ekran uyuyabilir; siz başındayken zaten uyumaz)
        if self.caff and self.caff.poll() is not None:
            self.caff = None
        if rendering and not self.caff:
            self.caff = subprocess.Popen(["caffeinate", "-ims"])
        elif not rendering and self.caff:
            self.caff.terminate()
            self.caff = None

    # --- profiller -------------------------------------------------------------
    def _publish(self, profile):
        log.info("Profil: %s", profile)
        if self.events:
            self.events.publish("performance.profile_changed", profile=profile)

    def _stop_agents(self):
        if self.stopped_agents:
            return
        protected = set(PLUGIN_SERVICE_AGENTS)
        for plist in self.cfg["deferrable_agents"]:
            if plist in protected:
                continue
            if os.path.exists(plist):
                r = subprocess.run(["/bin/launchctl", "bootout", f"gui/{UID}", plist], capture_output=True)
                if r.returncode == 0:
                    self.stopped_agents.append(plist)

    def enter_performance(self):
        previous, self.profile = self.profile, "performance"
        self._publish("performance")
        self._ensure_plugin_services()
        self._stop_agents()
        if previous == "normal":
            self._safe_priv_perf()

    def enter_ready(self):
        previous, self.profile = self.profile, "ready"
        self._publish("ready")
        self._ensure_plugin_services()
        self._stop_agents()
        if previous == "normal":
            self._safe_priv_perf()

    def _release_clamps(self):
        for pid in list(self.clamped):
            _run(["/usr/sbin/taskpolicy", "-B", "-p", str(pid)])
        self.clamped = set()

    def enter_normal(self):
        self.profile = "normal"
        self._publish("normal")
        if self.caff:
            self.caff.terminate()
            self.caff = None
        self._release_clamps()
        self.boosted = set()
        self.parked_pro = set()
        self.last_front_pro = None
        for plist in self.stopped_agents:
            _run(["/bin/launchctl", "bootstrap", f"gui/{UID}", plist])
        self.stopped_agents = []
        # Premiere/AE/Photoshop kapandıysa yetim yardımcı süreçleri temizle ve iç motor ayarlarını yenile
        self.cleanup_orphans(force=True)
        priv("normal")
        if os.access(MASTER_SETUP, os.X_OK):
            subprocess.Popen([MASTER_SETUP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._tune_and_prewarm_bg, daemon=True).start()

    def _periodic_scratch_and_tm(self, pressure=1):
        try:
            self.timemachine.scan_and_snapshot()
            self.ultra_scratch.tick(pressure=pressure)
        except Exception as e:
            log.warning("Ultra-Scratch / Proje Zaman Makinesi döngü hatası: %s", e)

    def shutdown(self):
        try:
            self.ultra_scratch.sync_to_ssd(force=True)
        except Exception:
            pass
        try:
            self.thermal_fan.shutdown()
        except Exception:
            pass
        if self.profile != "normal":
            self.enter_normal()

    # --- bakım -----------------------------------------------------------------
    def cleaner_due(self):
        try:
            last = os.path.getmtime(self.cleaner_stamp)
        except OSError:
            return True
        return time.time() - last > self.cfg["cleaner_min_gap_h"] * 3600

    def run_cleaner(self, busy=False):
        """busy: kurgu/render sürüyorsa temizlik yapılmaz."""
        if busy or not os.access(CLEANER, os.X_OK):
            return False
        open(self.cleaner_stamp, "w").close()
        proc = subprocess.Popen([CLEANER, "--auto"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc
