# -*- coding: utf-8 -*-
"""Güvenlik bekçisi: kripto madenci, kalıcılık (başlangıç öğeleri), zararlı dosya, güvenlik ayarları.

Müdahale ilkesi
  - Kesin madenci imzası (xmrig, stratum havuzu vb.) -> süreç durdurulur, onu başlatan kullanıcı
    ajanı devre dışı bırakılıp karantinaya taşınır, sesli ve yazılı uyarı verilir.
  - Şüpheli ama belirsiz durumlar -> yalnızca uyarı (yanlış alarmla meşru yazılımı bozmamak için).
  - Hiçbir dosya silinmez: zararlılar ~/.jarvis/karantina/ altına taşınır; karar sizindir.
  - Sistem güvenlik ayarları (SIP, Gatekeeper, FileVault, güvenlik duvarı) değiştirilmez, kapalıysa söylenir.
Maliyet: hızlı denetimler mevcut süreç taramasını kullanır; ağır işler (ClamAV) yalnızca boştayken,
en düşük öncelikte çalışır.
"""
import glob
import hashlib
import json
import logging
import os
import plistlib
import re
import shutil
import signal
import subprocess
import time

import psutil

from config import HOME, MEK_HOME, QUARANTINE, STATE_DIR

log = logging.getLogger("mek.security")

MINER_NAMES = re.compile(r"xmrig|xmr-stak|minerd|cpuminer|cgminer|bfgminer|ethminer|nbminer|t-rex|"
                         r"lolminer|phoenixminer|gminer|teamredminer|nanominer|srbminer|xmrminer|"
                         r"kdevtmpfsi|kinsing|\bc3pool\b|moneroocean", re.I)
MINER_ARGS = re.compile(r"stratum\+(tcp|ssl)://|--donate-level|--coin[= ](monero|xmr)|-a (rx/0|cryptonight)|"
                        r"pool\.(minexmr|supportxmr|hashvault)|nicehash|2miners|nanopool|f2pool", re.I)
INTERPRETERS = re.compile(r"^(python[\d.]*|Python|node|bash|zsh|sh|perl|ruby|java|osascript|deno|bun)$")
PROTECTED = ("/System", "/usr/", "/bin", "/sbin", "/Applications", "/Library/Apple", "/opt/homebrew",
             os.path.expanduser("~/.jarvis"))
POOL_PORTS = {3333, 4444, 5555, 6666, 7777, 8888, 9999, 14433, 14444, 45560, 45700, 20535, 10128}
SUSPICIOUS_DIRS = re.compile(r"^(/tmp|/private/tmp|/private/var/tmp|/Users/Shared|/var/folders)|/\.[^/]+/")
PERSIST_GLOBS = [os.path.join(HOME, "Library/LaunchAgents/*.plist"), "/Library/LaunchAgents/*.plist",
                 "/Library/LaunchDaemons/*.plist"]
BASELINE = os.path.join(STATE_DIR, "persist_baseline.json")
SCAN_STATE = os.path.join(STATE_DIR, "av_scan.json")
TRUSTED = os.path.join(STATE_DIR, "trusted_items.json")  # karantinadan geri alınan, bir daha dokunulmayacak öğeler


def is_own_item(label, prog):
    """Mek'in kendi ajanları (daemon, menü çubuğu) kendi kendini karantinaya almasın."""
    return bool(prog) and label.startswith(("com.alptug.jarvis", "com.alptug.mek")) and \
        os.path.realpath(prog).startswith((MEK_HOME + os.sep, os.path.join(HOME, ".local/bin") + os.sep))


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    with open(path + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(path + ".tmp", path)


def signature(path):
    """'apple' | 'developer:<ekip>' | 'adhoc' | 'unsigned' | 'missing'."""
    if not path or not os.path.exists(path):
        return "missing"
    r = subprocess.run(["codesign", "-dv", "--verbose=2", path], capture_output=True, text=True, timeout=15)
    out = r.stderr
    if r.returncode != 0:
        return "unsigned"
    if "Signature=adhoc" in out:
        return "adhoc"
    m = re.search(r"TeamIdentifier=(\S+)", out)
    if "Authority=Software Signing" in out or (m and m.group(1) == "not set" and "Apple" in out):
        return "apple"
    return f"developer:{m.group(1)}" if m else "unknown"


def _program_of(plist_path):
    try:
        with open(plist_path, "rb") as f:
            d = plistlib.load(f)
    except Exception:
        return None, None
    prog = d.get("Program") or (d.get("ProgramArguments") or [None])[0]
    return d.get("Label"), prog


class Security:
    def __init__(self, notify, events=None):
        self.notify = notify          # notify(text, urgent=False)
        self.events = events
        self.last = {}
        self.reported = set()

    def _notify(self, text, urgent=False):
        if self.events:
            self.events.publish("security.alert", text=text, urgent=urgent)
        self.notify(text, urgent=urgent)

    def _due(self, key, every):
        now = time.time()
        if now - self.last.get(key, 0) >= every:
            self.last[key] = now
            return True
        return False

    # ------------------------------------------------------------------ madenci
    def check_miners(self, hot):
        """hot: bağlam taramasından gelen (pid, ad, cpu, komut) — ek maliyet yok."""
        for pid, name, cpu, cmd in hot:
            try:
                full = " ".join(psutil.Process(pid).cmdline())
            except Exception:
                full = cmd
            if MINER_NAMES.search(name) or MINER_NAMES.search(full) or MINER_ARGS.search(full):
                self.neutralize(pid, name, full, "kripto madenci imzası")

    def check_pool_connections(self):
        """Madencilik havuzu portlarına bağlanan süreçler (2 dakikada bir, ~0.3 sn)."""
        r = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED", "-F", "pcn"],
                           capture_output=True, text=True, timeout=20)
        pid, name = None, ""
        for line in r.stdout.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("c"):
                name = line[1:]
            elif line.startswith("n") and "->" in line:
                port = line.rsplit(":", 1)[-1]
                if port.isdigit() and int(port) in POOL_PORTS:
                    try:
                        full = " ".join(psutil.Process(pid).cmdline())
                        cpu = psutil.Process(pid).cpu_percent(interval=0.5)
                    except Exception:
                        continue
                    # Havuz portu + yüksek işlemci ya da madenci argümanı = kesin; aksi hâlde yalnızca uyarı
                    if cpu > 80 or MINER_ARGS.search(full) or MINER_NAMES.search(full):
                        self.neutralize(pid, name, full, f"madencilik havuzu bağlantısı (port {port})")
                    elif ("pool", pid) not in self.reported:
                        self.reported.add(("pool", pid))
                        self._notify(f"{name} adlı uygulama madencilik havuzlarının sık kullandığı {port} "
                                     "portuna bağlı. Şimdilik dokunmadım, tanımıyorsanız haber verin.")

    def neutralize(self, pid, name, cmd, reason):
        key = ("kill", name)
        try:
            p = psutil.Process(pid)
            exe = p.exe()
            p.send_signal(signal.SIGSTOP)  # önce dondur (anında işlemciyi bırakır)
            p.kill()
        except Exception:
            exe = ""
        agent = self._agent_for(exe or cmd)
        moved = None
        target = exe
        if exe and INTERPRETERS.search(os.path.basename(exe)):
            # Yorumlayıcı (python, node, bash...) asla taşınmaz; çalıştırdığı betik dosyası taşınır
            target = next((a for a in cmd.split()[1:] if os.path.isfile(a) and not a.startswith("-")), None)
        if target and not target.startswith(PROTECTED) and signature(target) in ("unsigned", "adhoc", "missing"):
            moved = self.quarantine(target)
        log.warning("ETKİSİZ: %s (%s) %s ajan=%s karantina=%s", name, reason, cmd[:200], agent, moved)
        if key not in self.reported:
            self.reported.add(key)
            extra = f" Onu başlatan {agent} ajanını devre dışı bıraktım." if agent else ""
            self._notify(f"Güvenlik uyarısı: {name} süreci {reason} taşıyordu, durdurdum.{extra} "
                         "Dosyası karantina klasöründe, silmedim.", urgent=True)

    def _agent_for(self, needle):
        """Zararlıyı başlatan kullanıcı ajanını bulup devre dışı bırakır (yalnızca kullanıcı alanı)."""
        if not needle:
            return None
        for plist in glob.glob(os.path.join(HOME, "Library/LaunchAgents/*.plist")):
            label, prog = _program_of(plist)
            try:
                text = open(plist, "rb").read().decode("utf-8", "ignore")
            except Exception:
                continue
            if needle in text or (prog and prog in needle):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", plist], capture_output=True)
                self.quarantine(plist)
                return label or os.path.basename(plist)
        return None

    def quarantine(self, path):
        """Dosyayı silmeden karantinaya taşır, çalıştırılamaz yapar ve kaydını tutar."""
        try:
            os.makedirs(QUARANTINE, exist_ok=True)
            dest = os.path.join(QUARANTINE, f"{int(time.time())}_{os.path.basename(path)}")
            shutil.move(path, dest)
            os.chmod(dest, 0o400)
            with open(os.path.join(QUARANTINE, "kayit.txt"), "a") as f:
                f.write(f"{time.strftime('%F %T')}\t{path}\t->\t{dest}\n")
            return dest
        except Exception as e:
            log.warning("Karantinaya alınamadı %s: %s", path, e)
            return None

    def quarantine_list(self):
        """Karantina kayıtları: [(no, zaman, özgün yol, karantina yolu, hâlâ orada mı)]."""
        rows = []
        try:
            lines = open(os.path.join(QUARANTINE, "kayit.txt"), encoding="utf-8").read().splitlines()
        except OSError:
            return rows
        for i, line in enumerate(lines, 1):
            parts = line.split("\t")
            if len(parts) >= 4:
                rows.append((i, parts[0], parts[1], parts[3], os.path.exists(parts[3])))
        return rows

    def quarantine_restore(self, no):
        """Öğeyi yerine koyar ve bir daha şüpheli saymaz. Süreci başlatmaz; yeniden başlatma sizin kararınız."""
        for i, _, orig, dest, there in self.quarantine_list():
            if i != no:
                continue
            if not there:
                return "Bu öğe karantinada artık yok."
            if os.path.exists(orig):
                return f"Özgün konumda zaten bir dosya var: {orig}"
            os.chmod(dest, 0o644)
            shutil.move(dest, orig)
            _save(TRUSTED, sorted(set(_load(TRUSTED, [])) | {orig}))
            return f"Geri alındı: {orig}. Güvenilir listesine eklendi; yeniden başlatmak sizin kararınız."
        return "Böyle bir karantina kaydı yok."

    # ------------------------------------------------------------------ kalıcılık
    def audit_persistence(self):
        """Yeni başlangıç öğelerini taban çizgisiyle karşılaştırır (15 dakikada bir)."""
        base = _load(BASELINE, None)
        current = {}
        for g in PERSIST_GLOBS:
            for p in glob.glob(g):
                try:
                    current[p] = hashlib.sha1(open(p, "rb").read()).hexdigest()
                except Exception:
                    pass
        if base is None:  # ilk çalıştırma: mevcut durumu kabul et
            _save(BASELINE, current)
            return
        trusted = _load(TRUSTED, [])
        for p, h in current.items():
            if base.get(p) == h or p in trusted:
                continue
            label, prog = _program_of(p)
            if is_own_item(label, prog):
                continue
            sig = signature(prog) if prog else "missing"
            suspicious = sig in ("unsigned", "adhoc") and prog and (
                SUSPICIOUS_DIRS.search(prog) or MINER_NAMES.search(prog or ""))
            what = "yeni" if p not in base else "değişmiş"
            if suspicious and p.startswith(HOME):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", p], capture_output=True)
                self.quarantine(p)
                self._notify(f"Güvenlik: {what} bir başlangıç öğesi ({label}) imzasız ve şüpheli bir konumdan "
                             "çalışıyordu. Devre dışı bırakıp karantinaya aldım.", urgent=True)
            elif sig in ("unsigned", "adhoc") and not p.startswith(os.path.join(HOME, "Library/LaunchAgents/com.alptug")):
                self._notify(f"Bilginiz olsun: {what} bir başlangıç öğesi eklendi, {label}. Programı imzasız. "
                             "Siz kurduysanız sorun yok.")
            else:
                log.info("Başlangıç öğesi %s: %s (%s)", what, label, sig)
        _save(BASELINE, current)

    # ------------------------------------------------------------------ güvenlik ayarları
    def posture(self):
        """SIP, Gatekeeper, FileVault, güvenlik duvarı, XProtect yaşı. Değiştirmez, yalnızca bildirir."""
        issues = []

        def run(cmd):
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.lower()
        if "enabled" not in run(["csrutil", "status"]):
            issues.append("Sistem Bütünlüğü Koruması kapalı")
        if "enabled" not in run(["spctl", "--status"]):
            issues.append("Gatekeeper kapalı")
        if "on" not in run(["fdesetup", "status"]):
            issues.append("FileVault disk şifrelemesi kapalı")
        if "enabled" not in run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"]):
            issues.append("güvenlik duvarı kapalı")
        xp = "/Library/Apple/System/Library/CoreServices/XProtect.bundle"
        try:
            age_days = (time.time() - os.path.getmtime(xp)) / 86400
            if age_days > 30:
                issues.append(f"XProtect zararlı yazılım veritabanı {int(age_days)} gündür güncellenmemiş")
        except OSError:
            pass
        return issues

    # ------------------------------------------------------------------ dosya taraması (boştayken)
    def av_scan(self, max_files=400):
        """ClamAV ile son taramadan beri gelen indirmeleri tarar; en düşük öncelikte."""
        clam = shutil.which("clamscan") or "/opt/homebrew/bin/clamscan"
        if not os.path.exists(clam):
            return None
        st = _load(SCAN_STATE, {"t": 0})
        since = st["t"]
        roots = [os.path.join(HOME, d) for d in ("Downloads", "Desktop", "Documents")]
        files = []
        for r in roots:
            for dp, dn, fn in os.walk(r):
                dn[:] = [d for d in dn if not d.startswith(".") and not d.endswith((".app", ".photoslibrary",
                                                                                       ".fcpbundle", ".prproj"))]
                for f in fn:
                    p = os.path.join(dp, f)
                    try:
                        s = os.stat(p)
                    except OSError:
                        continue
                    # medya dosyaları atlanır (zararlı taşıma riski düşük, taraması pahalı)
                    if s.st_mtime > since and s.st_size < 200 * 1024 ** 2 and not f.lower().endswith(
                            (".mov", ".mp4", ".mxf", ".braw", ".r3d", ".wav", ".aif", ".aiff", ".prproj", ".drp")):
                        files.append(p)
        files = files[:max_files]
        st["t"] = time.time()
        if not files:
            _save(SCAN_STATE, st)
            return 0
        db = "/opt/homebrew/var/lib/clamav"
        cmd = ["nice", "-n", "20", clam, "--no-summary", "--infected", f"--database={db}"] + files
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        subprocess.run(["/usr/sbin/taskpolicy", "-b", "-p", str(proc.pid)], capture_output=True)  # E-çekirdekler
        out, _ = proc.communicate(timeout=1800)
        found = 0
        for line in out.splitlines():
            m = re.match(r"(.+): (.+) FOUND$", line)
            if m:
                found += 1
                dest = self.quarantine(m.group(1))
                self._notify(f"Zararlı dosya bulundu: {os.path.basename(m.group(1))}, {m.group(2)}. "
                             "Karantinaya aldım.", urgent=True)
                log.warning("ZARARLI: %s %s -> %s", m.group(1), m.group(2), dest)
        _save(SCAN_STATE, st)
        return found

    def update_av(self):
        fc = shutil.which("freshclam") or "/opt/homebrew/bin/freshclam"
        if os.path.exists(fc):
            subprocess.run(["nice", "-n", "20", fc, "--quiet"], capture_output=True, timeout=900)
