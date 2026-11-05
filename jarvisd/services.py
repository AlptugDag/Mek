# -*- coding: utf-8 -*-
"""Dinamik kaynak yöneticisi (eski keepawake_videoapps.sh'nin yerini alır).

Kural: kaynak, gerçekten iş yapan uygulamaya. Bir kurgu uygulamasının yalnızca AÇIK olması sistemi
"performans" kipine sokmaz; önde kullanılıyor ya da render/dışa aktarma yapıyor olması gerekir.

Profiller:
  performance  - kurgu/fotoğraf uygulaması önde ya da render sürüyor: o uygulama arka plan kısıtından
                 çıkarılır, eşitleme/güncelleme ajanları E-çekirdeklere alınır ya da durdurulur.
                 Render sürüyorsa sistem uykuya geçmez (ekran uyuyabilir).
  ready        - kurgu uygulaması açık ama arka planda boşta: hiçbir şey zorlanmaz, macOS (App Nap,
                 bellek sıkıştırma) kendi işini yapar; ajanlar durdurulmuş kalır.
  normal       - kurgu uygulaması yok: her şey eski haline döner.
Mek her süreç için taskpolicy'yi yalnızca bir kez çağırır (eskiden her 10-15 saniyede onlarca alt süreç
açılıyordu). Ayrıcalıklı adımlar (pmset, sistem daemon'ları) yalnızca install_privileged.sh ile kurulmuş
/usr/local/libexec/mek-priv yardımcısı varsa yapılır.
"""
import logging
import os
import re
import subprocess
import time

import psutil

from config import CLEANER, MASTER_SETUP, PRIV_HELPER, STATE_DIR

log = logging.getLogger("mek.services")
UID = os.getuid()

BG_CLAMP = re.compile(r"spotlightknowledged|mds_stores|photoanalysisd|mediaanalysisd|"
                      r"IntelligencePlatformComputeService|SetStoreUpdateService")
PERF_CLAMP = re.compile(r"Camera Hub|ACCFinderSync|Core Sync|CCLibrary|Creative Cloud Helper|"
                        r"GoogleUpdater|Dropbox|OneDrive|Google Drive|bird|cloudd")
RENDER_CPU = 150.0     # bir kurgu süreci bu kadar CPU harcıyorsa "iş yapıyor" sayılır
IDLE_GRACE_S = 180     # önden çekildikten sonra performans kipinde kalma süresi


def _run(cmd, **kw):
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, **kw)


def _pids(rx):
    out = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            if rx.search(p.info["exe"] or p.info["name"] or ""):
                out.append(p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
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


class ServiceManager:
    def __init__(self, cfg, events=None):
        self.cfg = cfg
        self.events = events
        self.profile = "normal"
        self.caff = None
        self.stopped_agents = []
        self.last_active = 0.0
        self.boosted = set()      # arka plan kısıtından çıkarılan kurgu süreçleri
        self.clamped = set()      # E-çekirdeğe alınan eşitleme/güncelleme süreçleri
        self.bg_clamped = set()
        self.next_guard = 0.0
        self.cleaner_stamp = os.path.join(STATE_DIR, "cleaner_last")
        priv("restore-high-power")
        _run(["/bin/launchctl", "limit", "maxfiles", "65536", "200000"])
        if os.access(MASTER_SETUP, os.X_OK):
            subprocess.Popen([MASTER_SETUP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # --- dakikada bir ------------------------------------------------------------
    def guard(self):
        alive = set()
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = p.info["name"] or ""
                if "WallpaperMacintoshExtension" in name:
                    p.kill()
                elif name == "siriactionsd" and p.cpu_percent(interval=None) > 80:
                    p.kill()
                elif BG_CLAMP.search(p.info["exe"] or name):
                    alive.add(p.pid)
                    if p.pid not in self.bg_clamped:
                        _run(["/usr/sbin/taskpolicy", "-b", "-p", str(p.pid)])
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                pass
        self.bg_clamped = alive

    # --- her döngüde -------------------------------------------------------------
    @staticmethod
    def working_pids(snap):
        """Gerçekten iş yapan kurgu süreçleri: öndeki uygulamaya ait olanlar ya da yüksek CPU harcayanlar."""
        front = (snap.front or "").lower()
        out = []
        for pid, exe, cpu, _ in getattr(snap, "pro_procs", ()):
            name = app_of(exe).lower()
            if cpu >= RENDER_CPU or pid == getattr(snap, "front_pid", None) or (front and front in name):
                out.append(pid)
        return out

    def tick(self, snap, rendering=False):
        now = time.time()
        if now >= self.next_guard:
            self.next_guard = now + 60
            self.guard()
        working = self.working_pids(snap)
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
        if target == "performance":
            for pid in working:
                if pid not in self.boosted:
                    _run(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", str(pid)])
                    self.boosted.add(pid)
            if now >= getattr(self, "_next_clamp", 0):
                self._next_clamp = now + 60
                for pid in _pids(PERF_CLAMP):
                    if pid not in self.clamped:
                        _run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)])
                        self.clamped.add(pid)
        live = set(snap.pro_pids)
        self.boosted &= live
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
        for plist in self.cfg["deferrable_agents"]:
            if os.path.exists(plist):
                r = subprocess.run(["/bin/launchctl", "bootout", f"gui/{UID}", plist], capture_output=True)
                if r.returncode == 0:
                    self.stopped_agents.append(plist)

    def enter_performance(self):
        previous, self.profile = self.profile, "performance"
        self._publish("performance")
        self._stop_agents()
        if previous == "normal":
            priv("perf")

    def enter_ready(self):
        previous, self.profile = self.profile, "ready"
        self._publish("ready")
        self._release_clamps()
        self._stop_agents()
        if previous == "normal":
            priv("perf")

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
        for plist in self.stopped_agents:
            _run(["/bin/launchctl", "bootstrap", f"gui/{UID}", plist])
        self.stopped_agents = []
        # Premiere/AE kapandıysa artık sunucuları temizle
        for pid in _pids(re.compile(r"AI MACHINE Server")):
            try:
                psutil.Process(pid).kill()
            except Exception:
                pass
        priv("normal")
        if os.access(MASTER_SETUP, os.X_OK):
            subprocess.Popen([MASTER_SETUP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def shutdown(self):
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
        """busy: kurgu/render sürüyorsa purge gibi ağır adımlar yapılmaz."""
        if busy or not os.access(CLEANER, os.X_OK):
            return False
        open(self.cleaner_stamp, "w").close()
        proc = subprocess.Popen([CLEANER, "--auto"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        priv("purge")
        return proc
