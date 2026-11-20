# -*- coding: utf-8 -*-
"""M1 Max 400 GB/s Birleşik Bellek APFS RAM-Disk Motoru ("Ultra-Scratch" — Sıfır Gecikmeli Zaman Çizelgesi).

M1 Max'in 64 GB (400 GB/s) birleşik belleğinde ince-tahsisli (thin-provisioned, boşken yalnızca ~15 MB RAM
tutan, doldukça en fazla 12 GB'a kadar büyüyen) bir APFS RAM-Disk (`/Volumes/MekUltraScratch`) oluşturur.

Premiere Pro, After Effects ve Media Encoder'ın zaman çizelgesinde (timeline) anlık kare/ses araması
(scrubbing) sırasında sürekli okuduğu/yazdığı:
  - `Peak Files` (.pek ses dalga formları)
  - `Analyzer Cache Files` (optik akış / analiz önbelleği)
  - `Media Cache` (.mcdb medya indeks veritabanları)
  - `PTX` (derlenmiş Metal/GPU shader önbelleği)
dizinlerini şeffaf biçimde bu 400 GB/s RAM-Diske yönlendirir ve sistem sakinleştiğinde SSD aynasıyla
(`~/Library/Application Support/Adobe/Common/.mek_ssd_backing`) artımlı senkronize eder.

Güvenlik ve Kalıcılık:
- Aktif kurgu/render sırasında gereksiz rsync G/Ç çakışması yaratmaz; senkronizasyon yalnızca sistem
  sakinleştiğinde, kapanışta veya bellek baskısı tahliyesinden hemen önce yapılır.
- Bellek baskısı tahliyesinde SSD aynası `--delete` olmadan korunur; son 10 dakikada değiştirilen
  aktif zaman çizelgesi önbellek dosyaları RAM-Disk kritik doluluğa (%90) ulaşmadıkça silinmez.
- Masaüstünde disk ikonu kalabalığı yapmaması için `-nobrowse` ile bağlanır, Spotlight ve FSEvents
  indekslemesi (`.metadata_never_index` ve `.fseventsd/no_log`) tamamen kapatılır.
"""
import logging
import os
import plistlib
import shutil
import subprocess
import threading
import time

log = logging.getLogger("mek.ultra_scratch")

HOME = os.path.expanduser("~")
VOL_NAME = "MekUltraScratch"
MOUNT_POINT = f"/Volumes/{VOL_NAME}"
ADOBE_COMMON = os.path.join(HOME, "Library", "Application Support", "Adobe", "Common")
SSD_MIRROR_ROOT = os.path.join(ADOBE_COMMON, ".mek_ssd_backing")

# 400 GB/s RAM-Diske taşınacak gecikme-kritik Adobe önbellek alt klasörleri:
SCRATCH_DIRS = (
    "Peak Files",
    "Analyzer Cache Files",
    "Media Cache",
    "PTX",
)

DEFAULT_SIZE_GB = 12
SYNC_INTERVAL_S = 600        # 10 dakikada bir sakin anlarda artımlı senkronizasyon
HOT_FILE_PROTECT_S = 600     # Son 10 dakikada kullanılan aktif önbelleği koru
MIRROR_MAX_AGE_DAYS = 45     # SSD aynasında 45 günden eski kullanılmayan önbelleği buda


def _run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)


class UltraScratchManager:
    """400 GB/s APFS RAM-Disk ve kalıcı SSD ayna yöneticisi."""

    def __init__(self, size_gb=DEFAULT_SIZE_GB, mount_point=MOUNT_POINT, adobe_common=ADOBE_COMMON, mirror_root=SSD_MIRROR_ROOT):
        self.size_gb = size_gb
        self.mount_point = mount_point
        self.adobe_common = adobe_common
        self.mirror_root = mirror_root
        self.last_sync = 0.0
        self.last_mirror_prune = 0.0
        self._lock = threading.Lock()

    def is_mounted(self):
        return os.path.ismount(self.mount_point)

    def ensure_mounted(self):
        """`/Volumes/MekUltraScratch` APFS RAM-Diskini oluşturur, bağlar ve SSD aynasından ısıtır."""
        with self._lock:
            if not self.is_mounted():
                if not self._create_ram_apfs():
                    return False
            self._configure_volume_flags()
            self._setup_symlinks_and_hydrate()
            return True

    def _create_ram_apfs(self):
        # 1 GB = 2,097,152 adet 512-bayt sektör
        sectors = int(self.size_gb * 2097152)
        r = _run(["/usr/bin/hdiutil", "attach", "-nomount", f"ram://{sectors}"])
        if r.returncode != 0 or not r.stdout.strip():
            log.warning("RAM disk aygıtı oluşturulamadı: %s", r.stderr.strip())
            return False
        raw_dev = r.stdout.strip().split()[0]

        # APFS konteyneri oluştur (henüz bağlama, ardından nobrowse ile bağla)
        fmt = _run(["/usr/sbin/diskutil", "apfs", "create", raw_dev, VOL_NAME])
        if fmt.returncode != 0:
            log.warning("APFS RAM disk biçimlendirilemedi (%s): %s", raw_dev, fmt.stderr.strip())
            _run(["/usr/bin/hdiutil", "detach", raw_dev])
            return False

        # Masaüstünde çıkarılabilir disk simgesi görünmemesi için nobrowse ile yeniden bağla:
        vol_dev = self._find_volume_device(VOL_NAME)
        if vol_dev:
            _run(["/usr/sbin/diskutil", "unmount", self.mount_point])
            _run(["/usr/sbin/diskutil", "mount", "nobrowse", vol_dev])

        if self.is_mounted():
            log.info("400 GB/s APFS Ultra-Scratch RAM-Disk aktif: %s (%d GB ince-tahsisli)", self.mount_point, self.size_gb)
            return True
        return False

    @staticmethod
    def _find_volume_device(vol_name):
        r = _run(["/usr/sbin/diskutil", "info", "-plist", f"/Volumes/{vol_name}"])
        if r.returncode != 0:
            return None
        try:
            data = plistlib.loads(r.stdout.encode("utf-8"))
            return data.get("DeviceIdentifier")
        except Exception:
            return None

    def _configure_volume_flags(self):
        """Spotlight ve FSEvents'in RAM-Disk üzerinde çalışmasını engeller."""
        try:
            open(os.path.join(self.mount_point, ".metadata_never_index"), "a").close()
            fsev = os.path.join(self.mount_point, ".fseventsd")
            os.makedirs(fsev, exist_ok=True)
            open(os.path.join(fsev, "no_log"), "a").close()
            os.makedirs(os.path.join(self.mount_point, "AdobeCommon"), exist_ok=True)
            os.makedirs(os.path.join(self.mount_point, "ResolveScratch"), exist_ok=True)
        except OSError:
            pass

    def _setup_symlinks_and_hydrate(self):
        """Adobe önbellek klasörlerini SSD aynasına yedekler, RAM-Diske yükler ve sembolik bağ kurar."""
        os.makedirs(self.adobe_common, exist_ok=True)
        os.makedirs(self.mirror_root, exist_ok=True)
        ram_adobe = os.path.join(self.mount_point, "AdobeCommon")
        os.makedirs(ram_adobe, exist_ok=True)

        for sub in SCRATCH_DIRS:
            orig_path = os.path.join(self.adobe_common, sub)
            mirror_path = os.path.join(self.mirror_root, sub)
            ram_path = os.path.join(ram_adobe, sub)
            os.makedirs(mirror_path, exist_ok=True)
            os.makedirs(ram_path, exist_ok=True)

            # 1. Orijinal klasör gerçek bir dizinse (henüz symlink değilse), içeriğini SSD aynasına taşı:
            if os.path.isdir(orig_path) and not os.path.islink(orig_path):
                _run(["/usr/bin/rsync", "-a", f"{orig_path}/", f"{mirror_path}/"])
                shutil.rmtree(orig_path, ignore_errors=True)

            # 2. SSD aynasındaki veriyi 400 GB/s RAM-Diske ısıt (yalnızca eksik/güncel olanları):
            _run(["/usr/bin/rsync", "-a", "-u", f"{mirror_path}/", f"{ram_path}/"])
            try:
                open(os.path.join(ram_path, ".metadata_never_index"), "a").close()
            except OSError:
                pass

            # 3. Sembolik bağı oluştur/doğrula:
            if os.path.islink(orig_path):
                if os.readlink(orig_path) != ram_path:
                    try:
                        os.unlink(orig_path)
                        os.symlink(ram_path, orig_path)
                    except OSError:
                        pass
            elif not os.path.exists(orig_path):
                try:
                    os.symlink(ram_path, orig_path)
                except OSError:
                    pass

    def symlinks_healthy(self):
        """Tüm gecikme-kritik Adobe önbellek sembolik bağlarının RAM-Diske baktığını doğrular."""
        ram_adobe = os.path.join(self.mount_point, "AdobeCommon")
        for sub in SCRATCH_DIRS:
            orig_path = os.path.join(self.adobe_common, sub)
            ram_path = os.path.join(ram_adobe, sub)
            if not os.path.islink(orig_path) or os.readlink(orig_path) != ram_path:
                return False
        return True

    def sync_to_ssd(self, force=False, mirror_delete=False):
        """RAM-Diskteki yeni/güncel önbellek dosyalarını kalıcı SSD aynasına (`mirror_root`) yazar.

        Not: Varsayılan olarak `--delete` kullanılmaz; böylece bellek baskısı nedeniyle RAM-Diskten
        tahliye edilen eski önbellekler SSD aynasından yanlışlıkla silinmez.
        """
        now = time.time()
        if not force and (now - self.last_sync < SYNC_INTERVAL_S):
            return False
        if not self.is_mounted():
            return False
        with self._lock:
            self.last_sync = now
            ram_adobe = os.path.join(self.mount_point, "AdobeCommon")
            flags = ["-a", "-u"] + (["--delete"] if mirror_delete else [])
            for sub in SCRATCH_DIRS:
                ram_path = os.path.join(ram_adobe, sub)
                mirror_path = os.path.join(self.mirror_root, sub)
                if os.path.isdir(ram_path):
                    os.makedirs(mirror_path, exist_ok=True)
                    _run(["/usr/bin/rsync", *flags, f"{ram_path}/", f"{mirror_path}/"])
            if now - self.last_mirror_prune >= 86400:
                self.last_mirror_prune = now
                self._prune_old_mirror_files()
        return True

    def _prune_old_mirror_files(self):
        """SSD aynasında 45 günden uzun süredir dokunulmayan eski önbellekleri temizler (PTX hariç)."""
        cutoff = time.time() - MIRROR_MAX_AGE_DAYS * 86400
        for sub in SCRATCH_DIRS:
            if sub == "PTX":
                continue
            mirror_path = os.path.join(self.mirror_root, sub)
            if not os.path.isdir(mirror_path):
                continue
            for dp, _, fn in os.walk(mirror_path, topdown=False):
                for f in fn:
                    if f == ".metadata_never_index":
                        continue
                    p = os.path.join(dp, f)
                    try:
                        if os.stat(p).st_mtime < cutoff:
                            os.remove(p)
                    except OSError:
                        pass

    def trim_if_pressured(self, pressure=1, mem_pct=0.0):
        """Bellek baskısı (`pressure >= 2` veya yüksek RAM) ya da doluluk %80'i aşarsa RAM-Diskten
        en eski dosyaları SSD aynasına güvenle aktardıktan sonra RAM-Diskten temizler."""
        if not self.is_mounted():
            return 0
        used = self.used_bytes()
        cap_bytes = int(self.size_gb * (1024 ** 3))
        over_cap = used >= int(cap_bytes * 0.80)
        critical_cap = used >= int(cap_bytes * 0.90)
        pressured_trim = (pressure >= 2 or mem_pct >= 80.0) and used >= 256 * 1024 * 1024
        if pressure < 4 and not over_cap and not pressured_trim:
            return 0

        # Önce RAM-Diskteki güncel veriyi SSD aynasına (--delete OLMADAN) kaydet:
        self.sync_to_ssd(force=True, mirror_delete=False)
        ram_adobe = os.path.join(self.mount_point, "AdobeCommon")
        now = time.time()
        files = []
        for dp, _, fn in os.walk(ram_adobe):
            for f in fn:
                if f == ".metadata_never_index":
                    continue
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                    # Kritik doluluk veya pressure >= 4 değilse son 10 dakikada dokunulan aktif önbelleği koru:
                    if not critical_cap and pressure < 4 and (now - st.st_mtime) < HOT_FILE_PROTECT_S:
                        continue
                    files.append((st.st_mtime, st.st_size, p))
                except OSError:
                    continue
        files.sort()  # en eskiler önce
        freed = 0
        target_free = int(cap_bytes * 0.40)
        for _, sz, p in files:
            try:
                os.remove(p)
                freed += sz
                if freed >= target_free:
                    break
            except OSError:
                pass
        if freed > 0:
            log.info("Ultra-Scratch baskı tahliyesi: %.1f MB RAM serbest bırakıldı (SSD aynası korundu).", freed / (1024 * 1024))
        return freed

    def used_bytes(self):
        if not self.is_mounted():
            return 0
        try:
            st = os.statvfs(self.mount_point)
            return (st.f_blocks - st.f_bfree) * st.f_frsize
        except OSError:
            return 0

    def tick(self, pressure=1, mem_pct=0.0, active_editing=False, rendering=False):
        if not self.is_mounted():
            self.ensure_mounted()
            return
        if not self.symlinks_healthy() and not rendering:
            self.ensure_mounted()
        used = self.used_bytes()
        over_cap = used >= int(self.size_gb * 0.80 * (1024 ** 3))
        # Aktif render veya kurgu oynatması sırasında (doluluk %80'i geçmedikçe) rsync G/Ç çakışması yaratma:
        if not rendering and not active_editing:
            self.sync_to_ssd(force=False, mirror_delete=False)
        elif over_cap:
            self.sync_to_ssd(force=True, mirror_delete=False)
        if pressure >= 2 or mem_pct >= 80.0 or over_cap:
            self.trim_if_pressured(pressure=pressure, mem_pct=mem_pct)

    def status_dict(self):
        mounted = self.is_mounted()
        used_mb = round(self.used_bytes() / (1024 * 1024), 1) if mounted else 0.0
        pct = round((used_mb / (self.size_gb * 1024.0)) * 100.0, 1) if mounted and self.size_gb else 0.0
        return {
            "mounted": mounted,
            "mount_point": self.mount_point,
            "size_gb": self.size_gb,
            "used_mb": used_mb,
            "used_pct": pct,
            "symlinks_ok": self.symlinks_healthy() if mounted else False,
        }

    def report(self):
        st = self.status_dict()
        if not st["mounted"]:
            return "Ultra-Scratch (400 GB/s APFS RAM-Disk): şu an bağlı değil."
        link_txt = "tüm bağlar sağlam" if st["symlinks_ok"] else "bağlar onarılıyor"
        return (
            f"Ultra-Scratch (400 GB/s APFS RAM-Disk): Aktif ({self.mount_point}) | "
            f"Kapasite: {self.size_gb} GB (ince-tahsisli, kullanılan: {st['used_mb']:.1f} MB / %{st['used_pct']:.1f}) | "
            f"Yönlendirilen önbellekler: {', '.join(SCRATCH_DIRS)} ({link_txt}, SSD kalıcı aynalı)."
        )
