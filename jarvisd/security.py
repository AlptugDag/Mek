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
# Üçüncü taraf / resmi olmayan kurulum paketlerine (DMG/PKG/command) gizlenen macOS bilgi hırsızları
# (AMOS / Atomic Stealer, Poseidon, Banshee, Cuckoo, XCSSET): sahte parola penceresi, keychain dökümü,
# şifreli base64/curl kabuk boru hattı.
STEALER_CMD_RE = re.compile(
    r"osascript\b.*display\s+dialog\b.*with\s+hidden\s+answer|"
    r"security\s+(dump-keychain|unlock-keychain|find-generic-password\s+-w)|"
    r"(curl|wget)\s+[^|;\n]*\|\s*(sudo\s+)?(ba|z)?sh\b|"
    r"base64\s+(-D|-d|--decode)[^|;\n]*\|\s*(sudo\s+)?(ba|z)?sh\b|"
    r"\b(AtomicStealer|AMOS_stealer|PoseidonStealer|BansheeStealer|CuckooStealer|XCSSET)\b",
    re.I | re.S,
)
SHELL_INJECTION_RE = re.compile(
    r"(curl|wget)\s+[^#\n]*\|\s*(sudo\s+)?(ba|z)?sh\b|"
    r"base64\s+(-D|-d|--decode)[^#\n]*\|\s*(sudo\s+)?(ba|z)?sh\b|"
    r"osascript\b[^\n]*with\s+hidden\s+answer|"
    r"security\s+dump-keychain|"
    r"nohup\s+(/tmp/|/private/tmp/|/var/tmp/|/Users/Shared/\.)[^\s#]+",
    re.I,
)
INTERPRETERS = re.compile(r"^(python[\d.]*|Python|node|bash|zsh|sh|perl|ruby|java|osascript|deno|bun)$")
# Kurgu uygulamaları ve üçüncü taraf eklenti dizinleri (adhoc imzalı meşru eklentiler dahil)
# hiçbir koşulda yanlış pozitif olarak karantinaya taşınmaz:
PROTECTED = (
    "/System",
    "/usr/",
    "/bin",
    "/sbin",
    "/Applications",
    "/Library/Apple",
    "/Library/Application Support",
    "/Library/OFX/Plugins",
    "/Library/Audio/Plug-Ins",
    "/Library/Frameworks",
    "/opt/homebrew",
    os.path.expanduser("~/.jarvis"),
    os.path.expanduser("~/Library/Application Support/Adobe"),
)
POOL_PORTS = {3333, 4444, 5555, 6666, 7777, 8888, 9999, 14433, 14444, 45560, 45700, 20535, 10128}
SUSPICIOUS_DIRS = re.compile(r"^(/tmp|/private/tmp|/private/var/tmp|/Users/Shared|/var/folders)|/\.[^/]+/")
PERSIST_GLOBS = [os.path.join(HOME, "Library/LaunchAgents/*.plist"), "/Library/LaunchAgents/*.plist",
                 "/Library/LaunchDaemons/*.plist"]
SHELL_PROFILES = [
    os.path.join(HOME, ".zshrc"),
    os.path.join(HOME, ".zprofile"),
    os.path.join(HOME, ".bash_profile"),
    os.path.join(HOME, ".bashrc"),
]
BASELINE = os.path.join(STATE_DIR, "persist_baseline.json")
SHELL_BASELINE = os.path.join(STATE_DIR, "shell_baseline.json")
SCAN_STATE = os.path.join(STATE_DIR, "av_scan.json")
DOWNLOAD_GATE_STATE = os.path.join(STATE_DIR, "download_gate.json")
TRUSTED = os.path.join(STATE_DIR, "trusted_items.json")  # karantinadan geri alınan, bir daha dokunulmayacak öğeler


def is_own_item(label, prog):
    """Mek'in kendi ajanları (daemon, menü çubuğu, /Applications/Mek.app) kendi kendini karantinaya almasın."""
    return bool(prog) and label.startswith(("com.alptug.jarvis", "com.alptug.mek")) and \
        os.path.realpath(prog).startswith((MEK_HOME + os.sep, os.path.join(HOME, ".local/bin") + os.sep, "/Applications/Mek.app" + os.sep))


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
    label, prog, _ = _plist_details(plist_path)
    return label, prog


def _plist_details(plist_path):
    try:
        with open(plist_path, "rb") as f:
            d = plistlib.load(f)
    except Exception:
        return None, None, ""
    args = d.get("ProgramArguments") or []
    prog = d.get("Program") or (args[0] if args else None)
    full_args = " ".join(str(a) for a in args) if args else (str(prog) if prog else "")
    return d.get("Label"), prog, full_args


def inspect_script_text(text):
    """Bir kabuk betiği / .command dosyası veya başlangıç satırında zararlı enjeksiyon var mı?"""
    if not text:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if STEALER_CMD_RE.search(line):
            return "bilgi hırsızı (stealer / sahte parola penceresi / keychain dökümü)"
        if SHELL_INJECTION_RE.search(line):
            return "şüpheli kabuk enjeksiyonu (gizli boru hattı / geçici dizin çalıştırması)"
        if MINER_ARGS.search(line) or MINER_NAMES.search(line):
            return "kripto madenci komutu"
    return None


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

    # ------------------------------------------------------------------ madenci & bilgi hırsızı (stealer)
    def check_miners(self, hot):
        """hot: bağlam taramasından gelen (pid, ad, cpu, komut) — ek maliyet yok."""
        for pid, name, cpu, cmd in hot:
            try:
                full = " ".join(psutil.Process(pid).cmdline())
            except Exception:
                full = cmd
            if MINER_NAMES.search(name) or MINER_NAMES.search(full) or MINER_ARGS.search(full):
                self.neutralize(pid, name, full, "kripto madenci imzası")
            elif STEALER_CMD_RE.search(full):
                self.neutralize(pid, name, full, "bilgi hırsızı (stealer / sahte parola penceresi)")

    def check_stealers(self, procs):
        """Tüm aktif süreç tablosunda (s.procs) sahte parola diyaloğu, keychain dökümü veya
        gizli kabuk boru hattı (AMOS / Poseidon / Banshee stealer) tarar — sıfır ek süreç tarama maliyeti."""
        my_pid = os.getpid()
        for item in procs or ():
            pid = item[0]
            name = item[1] if len(item) > 1 else ""
            cmd = item[2] if len(item) > 2 else ""
            if pid == my_pid or (cmd and cmd.startswith(MEK_HOME)):
                continue
            target = cmd or name or ""
            if STEALER_CMD_RE.search(target):
                try:
                    full = " ".join(psutil.Process(pid).cmdline())
                except Exception:
                    full = target
                if STEALER_CMD_RE.search(full):
                    self.neutralize(pid, name or "stealer", full, "bilgi hırsızı (stealer / sahte parola penceresi)")

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
                    if name.lower() in ("adb", "scrcpy"):
                        continue  # Android geliştirici araçları (port 5555) olağan geliştirme trafiğidir
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

    def quarantine_clear(self):
        """Karantinadaki tüm dosyaları ve kayıt kütüğünü güvenle temizler."""
        count = 0
        try:
            if os.path.exists(QUARANTINE):
                for item in os.listdir(QUARANTINE):
                    item_path = os.path.join(QUARANTINE, item)
                    try:
                        if os.path.isfile(item_path) or os.path.islink(item_path):
                            os.chmod(item_path, 0o600)
                            os.unlink(item_path)
                            count += 1
                        elif os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                            count += 1
                    except Exception:
                        pass
            return f"Karantina temizlendi ({count} öğe silindi)."
        except Exception as e:
            return f"Karantina temizlenirken hata: {e}"

    # ------------------------------------------------------------------ kalıcılık
    def audit_persistence(self):
        """Yeni başlangıç öğelerini, kabuk profillerini ve crontab'ı taban çizgisiyle karşılaştırır."""
        self.audit_shell_and_cron()
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
            label, prog, full_args = _plist_details(p)
            if is_own_item(label, prog):
                continue
            sig = signature(prog) if prog else "missing"
            script_issue = inspect_script_text(full_args)
            suspicious = bool(script_issue) or (sig in ("unsigned", "adhoc") and prog and (
                SUSPICIOUS_DIRS.search(prog) or MINER_NAMES.search(prog or "")))
            what = "yeni" if p not in base else "değişmiş"
            if suspicious and p.startswith(HOME):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", p], capture_output=True)
                self.quarantine(p)
                detail = f"({script_issue})" if script_issue else "imzasız ve şüpheli bir konumdan çalışıyordu"
                self._notify(f"Güvenlik: {what} bir başlangıç öğesi ({label}) {detail}. "
                             "Devre dışı bırakıp karantinaya aldım.", urgent=True)
            elif sig in ("unsigned", "adhoc") and not p.startswith(os.path.join(HOME, "Library/LaunchAgents/com.alptug")):
                # Meşru kurgu/eklenti klasörlerindeki (/Applications, /Library/Application Support) adhoc öğeleri için
                # yalnızca sakin bilgilendirme yapılır, asla karantinaya alınmaz.
                self._notify(f"Bilginiz olsun: {what} bir başlangıç öğesi eklendi, {label}. Programı özel/imzasız. "
                             "Siz kurduysanız sorun yok.")
            else:
                log.info("Başlangıç öğesi %s: %s (%s)", what, label, sig)
        _save(BASELINE, current)

    def audit_shell_and_cron(self):
        """Kabuk başlangıç dosyaları (~/.zshrc, ~/.zprofile vb.) ve crontab içinde truva/stealer enjeksiyonu denetler."""
        base = _load(SHELL_BASELINE, {})
        current = {}
        findings = []
        for path in SHELL_PROFILES:
            if not os.path.isfile(path):
                continue
            try:
                raw = open(path, "rb").read()
                h = hashlib.sha1(raw).hexdigest()
                current[path] = h
                if base.get(path) != h:
                    issue = inspect_script_text(raw.decode("utf-8", "ignore"))
                    if issue:
                        findings.append(f"{os.path.basename(path)}: {issue}")
            except OSError:
                pass
        try:
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                h = hashlib.sha1(r.stdout.encode("utf-8", "ignore")).hexdigest()
                current["crontab"] = h
                if base.get("crontab") != h:
                    issue = inspect_script_text(r.stdout)
                    if issue:
                        findings.append(f"crontab: {issue}")
        except Exception:
            pass
        _save(SHELL_BASELINE, current)
        if findings:
            msg = "; ".join(findings)
            log.warning("KABUK/CRON ENJEKSİYONU: %s", msg)
            self._notify(f"Güvenlik uyarısı: Kabuk başlangıç dosyalarında şüpheli komut tespit edildi ({msg}). "
                         "Kontrol etmenizi öneririm.", urgent=True)
        return findings

    def scan_recent_downloads(self, target_dirs=None):
        """İndirilenler (~/Downloads) ve Masaüstü (~/Desktop) klasörlerine yeni düşen
        .command, .sh, .tool, .pkg, .dmg kurulum dosyalarını daha çalıştırılmadan önce
        truva/stealer betiklerine ve zararlı imzalarına karşı denetler."""
        if target_dirs is None:
            target_dirs = [
                os.path.join(HOME, "Downloads"),
                os.path.join(HOME, "Desktop"),
            ]
        valid_dirs = [d for d in target_dirs if d and os.path.isdir(d)]
        if not valid_dirs:
            return {"checked": 0, "quarantined": []}
        st = _load(DOWNLOAD_GATE_STATE, {"t": time.time() - 3600})
        since = st.get("t", 0)
        now = time.time()
        quarantined = []
        installer_bins = []
        checked = 0
        for root_dir in valid_dirs:
            for dp, dn, fn in os.walk(root_dir):
                dn[:] = [d for d in dn if not d.startswith(".") and not d.endswith((".photoslibrary", ".fcpbundle", ".app"))]
                for f in fn:
                    p = os.path.join(dp, f)
                    try:
                        s = os.stat(p)
                    except OSError:
                        continue
                    if s.st_mtime <= since:
                        continue
                    low = f.lower()
                    if low.endswith((".command", ".sh", ".tool")) and s.st_size < 2 * 1024 * 1024:
                        checked += 1
                        try:
                            text = open(p, encoding="utf-8", errors="ignore").read()
                        except OSError:
                            continue
                        issue = inspect_script_text(text)
                        if issue:
                            dest = self.quarantine(p)
                            quarantined.append(f)
                            log.warning("İNDİRİLEN ZARARLI BETİK: %s (%s) -> %s", p, issue, dest)
                            self._notify(f"Güvenlik: {f} kurulum betiğinde {issue} tespit ettim. "
                                         "Sisteme bulaşmadan karantinaya aldım.", urgent=True)
                    elif low.endswith((".dmg", ".pkg")) and s.st_size < 500 * 1024 ** 2:
                        installer_bins.append(p)
        st["t"] = now
        _save(DOWNLOAD_GATE_STATE, st)
        clam = shutil.which("clamscan") or "/opt/homebrew/bin/clamscan"
        if installer_bins and os.path.exists(clam):
            checked += len(installer_bins[:10])
            db = "/opt/homebrew/var/lib/clamav"
            cmd = ["nice", "-n", "20", clam, "--no-summary", "--infected", f"--database={db}"] + installer_bins[:10]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                subprocess.run(["/usr/sbin/taskpolicy", "-b", "-p", str(proc.pid)], capture_output=True)
                out, _ = proc.communicate(timeout=300)
                for line in out.splitlines():
                    m = re.match(r"(.+): (.+) FOUND$", line)
                    if m:
                        bname = os.path.basename(m.group(1))
                        self.quarantine(m.group(1))
                        quarantined.append(bname)
                        self._notify(f"Güvenlik: Yeni indirilen {bname} paketinde zararlı yazılım ({m.group(2)}) "
                                     "bulundu. Kurulmadan önce karantinaya aldım.", urgent=True)
            except Exception as e:
                log.warning("İndirilen paket taraması tamamlanamadı: %s", e)
        return {"checked": checked, "quarantined": quarantined}

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
