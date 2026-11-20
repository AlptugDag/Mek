"""Read-only macOS disk health & SSD longevity checks; this module never repairs or changes a disk."""
import json
import os
import plistlib
import shutil
import subprocess


def inspect_disk(runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["diskutil", "info", "-plist", "/"], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"device": None, "volume": None, "smart": "Unavailable",
                "warning": f"Disk bilgisi okunamadı: {exc}"}
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip() if isinstance(result.stderr, bytes) else str(result.stderr or "")
        return {"device": None, "volume": None, "smart": "Unavailable",
                "warning": f"diskutil başarısız oldu: {detail[:300]}"}
    try:
        info = plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, TypeError, ValueError) as exc:
        return {"device": None, "volume": None, "smart": "Unavailable",
                "warning": f"Disk bilgisi çözümlenemedi: {exc}"}

    smart = str(info.get("SMARTStatus", "Not Supported"))
    warning = "Disk SMART durumu arıza bildiriyor." if smart.casefold() in {"failing", "failed"} else None
    return {"device": info.get("DeviceIdentifier"),
            "volume": info.get("VolumeName") or info.get("MountPoint") or "/",
            "smart": smart,
            "warning": warning}


def nvme_wear_stats(runner=None):
    """smartctl kuruluysa NVMe SSD aşınma (Percentage Used, TBW, çalışma saati) bilgisini okur."""
    if not shutil.which("smartctl"):
        return None
    runner = runner or subprocess.run
    try:
        r = runner(["smartctl", "-j", "-a", "disk0"], capture_output=True, text=True, timeout=15)
        if not r.stdout:
            return None
        data = json.loads(r.stdout)
        nvme = data.get("nvme_smart_health_information_log") or {}
        if not nvme:
            return None
        units_written = nvme.get("data_units_written")
        tbw = round((units_written * 512000) / (1000 ** 4), 2) if units_written is not None else None
        return {
            "percentage_used": nvme.get("percentage_used"),
            "available_spare": nvme.get("available_spare"),
            "tbw": tbw,
            "power_on_hours": nvme.get("power_on_hours"),
            "temperature_c": nvme.get("temperature"),
        }
    except Exception:
        return None


def apfs_headroom():
    """APFS ve NVMe aşınma dengelemesi (wear-leveling) için boş alan oranını ölçer."""
    try:
        st = os.statvfs("/System/Volumes/Data")
        total = st.f_blocks * st.f_frsize
        avail = st.f_bavail * st.f_frsize
        free_pct = (avail / total * 100.0) if total > 0 else 0.0
        return {
            "total_gb": round(total / (1024 ** 3), 1),
            "free_gb": round(avail / (1024 ** 3), 1),
            "free_pct": round(free_pct, 1),
            "healthy_headroom": free_pct >= 15.0,
        }
    except OSError:
        return {"total_gb": 0.0, "free_gb": 0.0, "free_pct": 0.0, "healthy_headroom": True}


def verify_volume(runner=None):
    """Run macOS's non-repairing filesystem verification on the startup volume."""
    runner = runner or subprocess.run
    try:
        result = runner(["diskutil", "verifyVolume", "/"], capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": f"Dosya sistemi doğrulanamadı: {exc}"}
    detail = (result.stdout or result.stderr or "").strip()
    if result.returncode:
        return {"ok": False, "message": f"Dosya sistemi denetimi hata bildirdi: {detail[-800:]}"}
    return {"ok": True, "message": detail[-800:] or "Başlangıç birimi doğrulandı."}