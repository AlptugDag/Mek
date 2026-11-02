"""Read-only macOS disk health checks; this module never repairs or changes a disk."""
import plistlib
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