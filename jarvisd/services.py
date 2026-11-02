# -*- coding: utf-8 -*-
"""Dinamik kaynak yöneticisi (eski keepawake_videoapps.sh'nin yerini alır).

Profiller:
  performance  - kurgu/render uygulaması açık: pro süreçler en yüksek QoS, arka plan
                 süreçleri E-çekirdeklere, gereksiz ajanlar durdurulur, uyku engellenir.
  normal       - her şey eski haline döner.
Ayrıcalıklı adımlar (pmset, sistem daemon'ları, purge) yalnızca install_privileged.sh
ile kurulmuş /usr/local/libexec/mek-priv yardımcısı varsa yapılır.
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


class ServiceManager:
    def __init__(self, cfg, events=None):
        self.cfg = cfg
        self.events = events
        self.profile = "normal"
        self.caff = None
        self.stopped_agents = []
        self.last_pro_seen = 0.0
        self.cleaner_stamp = os.path.join(STATE_DIR, "cleaner_last")
        priv("restore-high-power")
        _run(["/bin/launchctl", "limit", "maxfiles", "65536", "200000"])
        if os.access(MASTER_SETUP, os.X_OK):
            subprocess.Popen([MASTER_SETUP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # --- her döngüde -----------------------------------------------------------
    def guard(self):
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = p.info["name"] or ""
                if "WallpaperMacintoshExtension" in name:
                    p.kill()
                elif name == "siriactionsd":
                    _run(["/usr/sbin/taskpolicy", "-b", "-p", str(p.pid)])
                    r = subprocess.run(["ps", "-p", str(p.pid), "-o", "%cpu="], capture_output=True, text=True)
                    if float(r.stdout.strip() or 0) > 80:
                        p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
                pass
        for pid in _pids(BG_CLAMP):
            _run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)])

    def tick(self, snap):
        self.guard()
        now = time.time()
        if snap.pro_pids:
            self.last_pro_seen = now
            if self.profile != "performance":
                self.enter_performance()
            for pid in snap.pro_pids:
                _run(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", str(pid)])
            for pid in _pids(PERF_CLAMP):
                _run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)])
        elif self.profile == "performance" and now - self.last_pro_seen > 180:
            self.enter_normal()
        if self.caff and self.caff.poll() is not None:
            self.caff = None
        if self.profile == "performance" and not self.caff:
            self.caff = subprocess.Popen(["caffeinate", "-dimsu"])

    # --- profiller -------------------------------------------------------------
    def enter_performance(self):
        log.info("Profil: performance")
        if self.events:
            self.events.publish("performance.profile_changed", profile="performance")
        self.profile = "performance"
        for plist in self.cfg["deferrable_agents"]:
            if os.path.exists(plist):
                r = subprocess.run(["/bin/launchctl", "bootout", f"gui/{UID}", plist],
                                   capture_output=True)
                if r.returncode == 0:
                    self.stopped_agents.append(plist)
        priv("perf")

    def enter_normal(self):
        log.info("Profil: normal")
        if self.events:
            self.events.publish("performance.profile_changed", profile="normal")
        self.profile = "normal"
        if self.caff:
            self.caff.terminate()
            self.caff = None
        for pid in _pids(PERF_CLAMP):
            _run(["/usr/sbin/taskpolicy", "-B", "-p", str(pid)])
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
        if self.profile == "performance":
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
