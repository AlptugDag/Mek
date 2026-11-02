#!/usr/bin/env python3
"""Remove an app bundle and its directly identifiable per-user support files."""
import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys


BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9.-]+$")
LIBRARY_LOCATIONS = (
    "Application Support",
    "Caches",
    "Containers",
    "Logs",
    "Preferences",
    "Saved Application State",
    "HTTPStorages",
    "WebKit",
    "Application Scripts",
)


def app_identity(value):
    """Return (bundle ID, display name, app path) for an app path or bundle ID."""
    value = os.path.expanduser(value)
    if not value.lower().endswith(".app"):
        if not BUNDLE_ID.fullmatch(value):
            raise ValueError("Bir .app yolu veya geçerli bundle ID belirtin.")
        return value, value, None
    path = os.path.abspath(value)
    if os.path.islink(path) or not os.path.isdir(path):
        raise ValueError("Uygulama paketi bulunamadı veya sembolik bağlantı; güvenlik için işlem yapılmadı.")
    resolved = os.path.realpath(path)
    if any(resolved == root or resolved.startswith(root + "/")
           for root in ("/System", "/Library", "/usr", "/bin", "/sbin")):
        raise ValueError("macOS sistem konumlarındaki uygulamalar kaldırılamaz.")
    info_path = os.path.join(path, "Contents", "Info.plist")
    try:
        with open(info_path, "rb") as info_file:
            info = plistlib.load(info_file)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ValueError(f"Uygulamanın Info.plist dosyası okunamadı: {exc}") from exc
    bundle_id = info.get("CFBundleIdentifier", "")
    if not isinstance(bundle_id, str) or not BUNDLE_ID.fullmatch(bundle_id):
        raise ValueError("Uygulamanın geçerli bir CFBundleIdentifier değeri yok.")
    name = info.get("CFBundleDisplayName") or info.get("CFBundleName") or os.path.basename(path)[:-4]
    return bundle_id, str(name), path


def _is_owned_launch_agent(path, bundle_id, app_path):
    try:
        with open(path, "rb") as plist_file:
            data = plistlib.load(plist_file)
    except (OSError, plistlib.InvalidFileException):
        return False
    label = data.get("Label", "")
    if isinstance(label, str) and (label == bundle_id or label.startswith(bundle_id + ".")):
        return True
    if not app_path:
        return False
    programs = [data.get("Program")]
    programs.extend(data.get("ProgramArguments", []))
    return any(isinstance(item, str) and (item == app_path or item.startswith(app_path + "/"))
               for item in programs)


def collect_artifacts(bundle_id, app_name, home, app_path=None):
    """Find exact-name per-user artifacts; shared containers and broad matches are excluded."""
    library = os.path.join(home, "Library")
    names = {bundle_id.casefold(), app_name.casefold()}
    found = set()
    if app_path:
        found.add(os.path.abspath(app_path))

    for location in LIBRARY_LOCATIONS:
        parent = os.path.join(library, location)
        if not os.path.isdir(parent):
            continue
        try:
            entries = os.scandir(parent)
        except OSError:
            continue
        with entries:
            for entry in entries:
                stem = entry.name[:-6] if location == "Preferences" and entry.name.endswith(".plist") else entry.name
                if stem.casefold() in names or (location == "Saved Application State" and
                                                 stem.casefold() == (bundle_id + ".savedstate").casefold()):
                    found.add(entry.path)

    by_host = os.path.join(library, "Preferences", "ByHost")
    if os.path.isdir(by_host):
        try:
            with os.scandir(by_host) as entries:
                for entry in entries:
                    if entry.name.startswith(bundle_id + ".") and entry.name.endswith(".plist"):
                        found.add(entry.path)
        except OSError:
            pass

    agents = os.path.join(library, "LaunchAgents")
    if os.path.isdir(agents):
        try:
            with os.scandir(agents) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False) and entry.name.endswith(".plist") and \
                            _is_owned_launch_agent(entry.path, bundle_id, app_path):
                        found.add(entry.path)
        except OSError:
            pass

    return sorted(path for path in found if os.path.lexists(path))


def path_size(path):
    try:
        if not os.path.isdir(path) or os.path.islink(path):
            return os.lstat(path).st_size
        total = 0
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))]
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def remove_artifact(path):
    """Delete an explicitly selected file/tree without following a symlink."""
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Uygulamayı ve doğrudan ilişkili kullanıcı kalıntılarını kaldırır.")
    parser.add_argument("app", help=".app paketi veya uygulama bundle ID'si")
    parser.add_argument("--dry-run", action="store_true", help="Yalnızca bulunacak öğeleri göster")
    args = parser.parse_args(argv)

    try:
        bundle_id, app_name, app_path = app_identity(args.app)
    except ValueError as exc:
        parser.error(str(exc))
    artifacts = collect_artifacts(bundle_id, app_name, os.path.expanduser("~"), app_path)
    print(f"Uygulama: {app_name} ({bundle_id})")
    if not artifacts:
        print("İlişkili öğe bulunamadı.")
        return 0
    print("\nKaldırılacak öğeler:")
    for path in artifacts:
        print(f"  {path}  ({path_size(path) / 1024 ** 2:.1f} MB)")
    print(f"\nToplam: {sum(path_size(path) for path in artifacts) / 1024 ** 2:.1f} MB")
    if args.dry_run:
        return 0

    print("\nPaylaşılan grup alanları ve yönetici yetkisi gerektiren sistem öğeleri taranmadı.")
    try:
        confirmation = input(f"Silme işlemini onaylamak için bundle ID'yi yazın ({bundle_id}): ").strip()
    except EOFError:
        confirmation = ""
    if confirmation != bundle_id:
        print("İşlem iptal edildi.")
        return 1

    failures = []
    for path in artifacts:
        try:
            if os.path.basename(path).endswith(".plist") and os.path.dirname(path) == os.path.join(
                    os.path.expanduser("~"), "Library", "LaunchAgents"):
                subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", path],
                               capture_output=True, timeout=15)
            remove_artifact(path)
            print(f"Kaldırıldı: {path}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append((path, str(exc)))
    for path, error in failures:
        print(f"Kaldırılamadı: {path}: {error}", file=sys.stderr)
    print(f"Tamamlandı: {len(artifacts) - len(failures)}/{len(artifacts)} öğe kaldırıldı.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())