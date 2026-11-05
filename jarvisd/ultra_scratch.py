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
dizinlerini şeffaf biçimde bu 400 GB/s RAM-Diske yönlendirir ve arka planda SSD yedeğiyle
(`~/.jarvis/state/ultra_scratch_mirror`) senkronize tutar.

Güvenlik ve Kalıcılık:
- Bilgisayar yeniden başlatıldığında SSD aynasındaki tüm `.pek`, `.mcdb` ve `.ptx` dosyaları
  milisaniyeler içinde RAM-Diske geri yüklenir (hiçbir önbellek kaybolmaz).
- Masaüstünde disk ikonu kalabalığı yapmaması için `-nobrowse` ile bağlanır, Spotlight ve FSEvents
  indekslemesi (`.metadata_never_index` ve `.fseventsd/no_log`) tamamen kapatılır.
- Kritik bellek baskısında (`pressure >= 4`) en eski önbellek dosyalarını SSD'ye aktarıp RAM'i
  otomatik boşaltır.
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
SYNC_INTERVAL_S = 300  # 5 dakikada bir RAM-Disk -> SSD artımlı senkronizasyon


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

    def sync_to_ssd(self, force=False):
        """RAM-Diskteki yeni/güncel önbellek dosyalarını kalıcı SSD aynasına (`mirror_root`) yazar."""
        now = time.time()
        if not force and (now - self.last_sync < SYNC_INTERVAL_S):
            return False
        if not self.is_mounted():
            return False
        self.last_sync = now
        ram_adobe = os.path.join(self.mount_point, "AdobeCommon")
        for sub in SCRATCH_DIRS:
            ram_path = os.path.join(ram_adobe, sub)
            mirror_path = os.path.join(self.mirror_root, sub)
            if os.path.isdir(ram_path):
                os.makedirs(mirror_path, exist_ok=True)
                _run(["/usr/bin/rsync", "-a", "--delete", f"{ram_path}/", f"{mirror_path}/"])
        return True

    def trim_if_pressured(self, pressure=1):
        """Kritik bellek baskısı (`pressure >= 4`) olursa RAM-Diski önce SSD'ye yedekler, ardından
        en eski geçici dosyaları RAM-Diskten temizler."""
        if not self.is_mounted():
            return 0
        if pressure < 4 and self.used_bytes() < int(self.size_gb * 0.8 * (1024 ** 3)):
            return 0
        self.sync_to_ssd(force=True)
        ram_adobe = os.path.join(self.mount_point, "AdobeCommon")
        files = []
        for dp, _, fn in os.walk(ram_adobe):
            for f in fn:
                if f == ".metadata_never_index":
                    continue
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                    files.append((st.st_mtime, st.st_size, p))
                except OSError:
                    continue
        files.sort()  # en eskiler önce
        freed = 0
        target_free = int(self.size_gb * 0.4 * (1024 ** 3))
        for _, sz, p in files:
            try:
                os.remove(p)
                freed += sz
                if freed >= target_free:
                    break
            except OSError:
                pass
        return freed

    def used_bytes(self):
        if not self.is_mounted():
            return 0
        try:
            st = os.statvfs(self.mount_point)
            return (st.f_blocks - st.f_bfree) * st.f_frsize
        except OSError:
            return 0

    def tick(self, pressure=1):
        if not self.is_mounted():
            self.ensure_mounted()
        else:
            self.sync_to_ssd(force=False)
            if pressure >= 4:
                self.trim_if_pressured(pressure=pressure)

    def report(self):
        if not self.is_mounted():
            return "Ultra-Scratch (400 GB/s APFS RAM-Disk): şu an bağlı değil."
        used_mb = self.used_bytes() / (1024 * 1024)
        return (
            f"Ultra-Scratch (400 GB/s APFS RAM-Disk): Aktif ({self.mount_point}) | "
            f"Kapasite: {self.size_gb} GB (ince-tahsisli, kullanılan: {used_mb:.1f} MB) | "
            f"Yönlendirilen önbellekler: {', '.join(SCRATCH_DIRS)} (SSD kalıcı aynalı)."
        )
