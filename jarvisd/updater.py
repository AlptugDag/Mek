"""Conservative macOS software and firmware update management."""
import json
import os
import re
import shutil
import subprocess


LABEL = re.compile(r"^\s*\*\s*Label:\s*(.+?)\s*$", re.M)
VERSION = re.compile(r"\bVersion:\s*([0-9]+(?:\.[0-9]+)+)", re.I)
PACKAGE_TOKEN = re.compile(r"^[A-Za-z0-9@+_.-]+$")
MAS_UPDATE = re.compile(r"^\s*(\d{4,})\s+(.+?)\s+\(([^()]*)\)\s*$")
# Kullanıcının özel kurduğu kurgu/yaratıcı uygulamalar ve eklenti yöneticileri otomatik cask güncellemesiyle
# ASLA üzerine yazılmaz (mevcut sürüm/eklenti/özel yapılandırma bozulmasın diye).
CREATIVE_OR_CUSTOM_CASK_RE = re.compile(
    r"\b(adobe|premiere|after[- ]?effects|photoshop|lightroom|audition|media[- ]?encoder|"
    r"davinci|resolve|topaz|final[- ]?cut|logic[- ]?pro|compressor|motion|cinema[- ]?4d|"
    r"maxon|red[- ]?giant|boris|fxfactory|dehancer|colourlab|filmconvert)\b",
    re.I,
)


def find_tool(name, which=None):
    which = which or shutil.which
    found = which(name)
    if found:
        return found
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/usr/sbin"):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def parse_available_updates(output, current_version):
    current = re.match(r"([0-9]+)", current_version or "")
    if not current:
        return []
    blocks = re.split(r"(?=^\s*\*\s*Label:)", output, flags=re.M)
    updates = []
    for block in blocks:
        label_match = LABEL.search(block)
        if not label_match or not re.search(r"\bRecommended:\s*YES\b", block, re.I):
            continue
        label = label_match.group(1).strip()
        if not label:
            continue
        title_match = re.search(r"^\s*Title:\s*(.+)$", block, re.M)
        title = title_match.group(1).strip() if title_match else label
        if re.search(r"\bmacOS\b", label + " " + title, re.I):
            version_match = VERSION.search(block)
            if not version_match:
                continue
            update_major = version_match.group(1).split(".", 1)[0]
            if update_major != current.group(1):
                continue
        updates.append({"label": label, "title": title,
                        "restart_required": bool(re.search(r"\bAction:\s*restart\b", block, re.I))})
    return updates


def safe_install_window(idle, busy, on_ac_power, disk_free_gb, thermal_state, network_state):
    return (idle and not busy and on_ac_power and disk_free_gb >= 30 and thermal_state < 2 and
            network_state not in ("offline", "degraded", "busy"))


def is_on_ac_power(runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "ac power" in result.stdout.casefold()


def check_updates(current_version=None, runner=None):
    runner = runner or subprocess.run
    if current_version is None:
        try:
            version = runner(["/usr/bin/sw_vers", "-productVersion"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "updates": [], "message": str(exc)}
        if version.returncode:
            return {"ok": False, "updates": [], "message": version.stderr.strip()}
        current_version = version.stdout.strip()
    try:
        result = runner(["/usr/sbin/softwareupdate", "--list"], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "updates": [], "message": str(exc)}
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if result.returncode:
        return {"ok": False, "updates": [], "message": output.strip()[-600:]}
    return {"ok": True, "updates": parse_available_updates(output, current_version), "message": ""}


def install_update(label, helper="/usr/local/libexec/mek-priv", runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["sudo", "-n", helper, "software-update-install", label],
                        capture_output=True, text=True, timeout=7200)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": str(exc)}
    message = (result.stdout or result.stderr or "").strip()
    return {"ok": result.returncode == 0, "message": message}


def parse_brew_outdated(output):
    try:
        data = json.loads(output)
    except (TypeError, ValueError):
        return []
    updates = []
    for kind, group in (("formula", "formulae"), ("cask", "casks")):
        for item in data.get(group, []):
            token = item.get("name") if kind == "formula" else item.get("token")
            if not isinstance(token, str) or not PACKAGE_TOKEN.fullmatch(token) or item.get("pinned"):
                continue
            names = item.get("name", []) if kind == "cask" else []
            if isinstance(names, str):
                names = [names]
            updates.append({"kind": kind, "token": token,
                            "name": names[0] if names else token,
                            "app_names": names if kind == "cask" else []})
    return updates


def parse_mas_outdated(output):
    updates = []
    for line in output.splitlines():
        match = MAS_UPDATE.match(line)
        if match:
            updates.append({"id": match.group(1), "name": match.group(2).strip(),
                            "versions": match.group(3).strip()})
    return updates


def parse_brew_services(output):
    try:
        services = json.loads(output)
    except (TypeError, ValueError):
        return None
    if not isinstance(services, list):
        return None
    return {str(item.get("name", "")).casefold() for item in services
            if str(item.get("status", "")).casefold() == "started" and item.get("name")}


def parse_attached_devices(output):
    try:
        profile = json.loads(output)
    except (TypeError, ValueError):
        return []
    devices = []

    def walk(node, transport):
        if isinstance(node, dict):
            name = node.get("_name") or node.get("device_name")
            vendor = node.get("manufacturer") or node.get("vendor_name")
            identifier = node.get("vendor_id") or node.get("device_uid")
            if name and (vendor or identifier):
                devices.append({"name": str(name), "vendor": str(vendor or "Bilinmiyor"),
                                "firmware": str(node.get("firmware_version") or "Bilinmiyor"),
                                "transport": transport})
            for value in node.values():
                walk(value, transport)
        elif isinstance(node, list):
            for value in node:
                walk(value, transport)

    for section, transport in (("SPUSBDataType", "USB"), ("SPThunderboltDataType", "Thunderbolt")):
        walk(profile.get(section, []), transport)
    unique = {(device["transport"], device["vendor"], device["name"]): device for device in devices}
    return list(unique.values())


def app_is_running(names, running_apps):
    normalize = lambda name: str(name).casefold().removesuffix(".app")
    active = {normalize(name) for name in running_apps}
    return any(normalize(name) in active for name in names if name)


def _default_app_signature(app_path):
    if not app_path or not os.path.exists(app_path):
        return "missing"
    try:
        r = subprocess.run(["codesign", "-dv", "--verbose=2", app_path],
                           capture_output=True, text=True, timeout=15)
        out = r.stderr or ""
        if r.returncode != 0:
            return "unsigned"
        if "Signature=adhoc" in out:
            return "adhoc"
        return "signed"
    except Exception:
        return "unknown"


def is_custom_or_protected_app(names, token="", apps_dir="/Applications", sig_fn=None):
    """Kurgu/yaratıcı uygulamalar veya /Applications altında özel/adhoc imzayla kurulmuş paketler
    arka planda otomatik güncellenerek üzerine yazılmaz."""
    sig_fn = sig_fn or _default_app_signature
    candidates = [str(n) for n in (names or ()) if n]
    if token:
        candidates.append(str(token))
    for text in candidates:
        if CREATIVE_OR_CUSTOM_CASK_RE.search(text):
            return True
    for name in (names or ()):
        if not name:
            continue
        bundle = str(name) if str(name).endswith(".app") else f"{name}.app"
        app_path = os.path.join(apps_dir, bundle)
        if os.path.exists(app_path) and sig_fn(app_path) in ("adhoc", "unsigned"):
            return True
    return False


def update_third_party(running_apps=(), install=False, refresh=True, allow_install=None, runner=None, which=None,
                       apps_dir="/Applications", sig_fn=None):
    runner = runner or subprocess.run
    which = which or shutil.which
    allow_install = allow_install or (lambda: True)
    result = {"checked": [], "installed": [], "deferred": [], "protected_custom": [], "errors": []}

    brew = find_tool("brew", which)
    if brew:
        brew_ready = True
        if refresh:
            update = runner([brew, "update"], capture_output=True, text=True, timeout=900)
            brew_ready = update.returncode == 0
            if not brew_ready:
                result["errors"].append("Homebrew metadata: " + (update.stderr or "update başarısız"))
        services = runner([brew, "services", "list", "--json"], capture_output=True,
                          text=True, timeout=60)
        running_services = parse_brew_services(services.stdout) if services.returncode == 0 else None
        outdated = runner([brew, "outdated", "--json=v2"], capture_output=True, text=True, timeout=300)
        if outdated.returncode:
            result["errors"].append("Homebrew kontrolü: " + (outdated.stderr or "outdated başarısız"))
        else:
            for item in parse_brew_outdated(outdated.stdout):
                result["checked"].append(f"Homebrew {item['kind']}: {item['name']}")
                if item["kind"] == "cask" and is_custom_or_protected_app(
                        item["app_names"], item["token"], apps_dir=apps_dir, sig_fn=sig_fn):
                    result["deferred"].append(item["name"])
                    result["protected_custom"].append(item["name"])
                    continue
                if item["kind"] == "cask" and (not item["app_names"] or
                                                 app_is_running(item["app_names"], running_apps)):
                    result["deferred"].append(item["name"])
                    continue
                if item["kind"] == "formula" and (running_services is None or
                                                    item["token"].casefold() in running_services):
                    result["deferred"].append(item["name"])
                    continue
                if install and brew_ready and allow_install():
                    args = [brew, "upgrade", "--formula" if item["kind"] == "formula" else "--cask",
                            item["token"]]
                    upgraded = runner(args, capture_output=True, text=True, timeout=3600)
                    if upgraded.returncode:
                        result["errors"].append(f"{item['name']}: {upgraded.stderr or 'güncelleme başarısız'}")
                    else:
                        result["installed"].append(item["name"])
                elif install:
                    result["deferred"].append(item["name"])

    mas = find_tool("mas", which)
    if mas:
        outdated = runner([mas, "outdated"], capture_output=True, text=True, timeout=300)
        if outdated.returncode:
            result["errors"].append("Mac App Store kontrolü: " + (outdated.stderr or "mas outdated başarısız"))
        else:
            for item in parse_mas_outdated(outdated.stdout):
                result["checked"].append(f"App Store: {item['name']}")
                if is_custom_or_protected_app([item["name"]], apps_dir=apps_dir, sig_fn=sig_fn):
                    result["deferred"].append(item["name"])
                    result["protected_custom"].append(item["name"])
                    continue
                if app_is_running([item["name"]], running_apps):
                    result["deferred"].append(item["name"])
                    continue
                if install and allow_install():
                    upgraded = runner([mas, "upgrade", item["id"]], capture_output=True,
                                      text=True, timeout=3600)
                    if upgraded.returncode:
                        result["errors"].append(f"{item['name']}: {upgraded.stderr or 'güncelleme başarısız'}")
                    else:
                        result["installed"].append(item["name"])
                elif install:
                    result["deferred"].append(item["name"])
    return result


def attached_devices(runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["/usr/sbin/system_profiler", "-json", "SPUSBDataType", "SPThunderboltDataType"],
                        capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode:
        return []
    return parse_attached_devices(result.stdout)