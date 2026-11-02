"""Status and fixed-command interface for the managed hosts-file ad blocker."""
import subprocess


BEGIN = "# BEGIN MEK AD BLOCK"
END = "# END MEK AD BLOCK"


def status(hosts_path="/etc/hosts"):
    try:
        with open(hosts_path, encoding="utf-8", errors="replace") as hosts_file:
            lines = hosts_file.readlines()
    except OSError as exc:
        return {"enabled": False, "count": 0, "error": str(exc)}

    inside = False
    found = False
    count = 0
    for line in lines:
        marker = line.strip()
        if marker == BEGIN:
            if inside or found:
                return {"enabled": False, "count": 0, "error": "hosts işaretleri bozuk"}
            inside = found = True
        elif marker == END:
            if not inside:
                return {"enabled": False, "count": 0, "error": "hosts işaretleri bozuk"}
            inside = False
        elif inside and line.strip() and not marker.startswith("#"):
            count += 1
    if inside:
        return {"enabled": False, "count": 0, "error": "hosts işaretleri bozuk"}
    return {"enabled": found, "count": count, "error": None}


def change(action, helper="/usr/local/libexec/mek-priv", runner=None):
    if action not in {"enable", "disable"}:
        raise ValueError("Desteklenmeyen reklam engelleme eylemi.")
    runner = runner or subprocess.run
    try:
        result = runner(["sudo", "-n", helper, f"adblock-{action}"],
                        capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": str(exc)}
    message = (result.stdout or result.stderr or "").strip()
    return {"ok": result.returncode == 0,
            "message": message or ("Hosts engelleyici güncellendi." if action == "enable" else
                                   "Hosts engelleyici kapatıldı.")}