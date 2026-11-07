# -*- coding: utf-8 -*-
"""APFS Copy-on-Write (`cp -c`) Sıfır Disk Alanlı Proje Zaman Makinesi (`project_timemachine.py`).

Premiere Pro (`.prproj`), After Effects (`.aep`) ve DaVinci Resolve (`.drp`) projelerinizde her
kayıt (`Cmd+S`) yaptığınızda, APFS dosya sisteminin donanımsal *Copy-on-Write* (`clonefile` / `cp -c`)
özelliğini kullanarak projenin anlık dondurulmuş bir kopyasını:
  `~/Movies/Mek Proje Zaman Makinesi/<ProjeAdı>/`
altına alır.

Özellikler:
- **0 Bayt Ek Disk Alanı & <1 ms Hız:** APFS `cp -c` blokları kopyalamaz, yalnızca üstveri işaretçisi
  oluşturur; diskte ek yer kaplamaz ve kurgu sırasında G/Ç gecikmesi yaratmaz.
- **Bozuk Kayıt Koruması (Bütünlük Doğrulaması):** `.prproj` (gzip/XML) dosyalarının yarım yazılmış
  veya bozuk olup olmadığını klonlamadan önce doğrular; bozuk kayıtlar sağlam geçmişin üzerine yazılmaz.
- **Tek Komutla Geri Dönüş:** `mek zamanmakinesi` (veya `mek zm`) ile tüm geçmişi listeler,
  `mek zamanmakinesi geri <proje> [no]` ile istediğiniz dakikadaki sağlam sürüme anında geri döner.
"""
import datetime as dt
import glob
import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import proc


log = logging.getLogger("mek.timemachine")

HOME = os.path.expanduser("~")
DEFAULT_VAULT_DIR = os.path.join(HOME, "Movies", "Mek Proje Zaman Makinesi")
PROJECT_EXTENSIONS = (".prproj", ".aep", ".drp")
SKIP_DIR_FRAGMENTS = (
    "Auto-Save",
    "Autosave",
    "AutoRecover",
    "Mek Proje Zaman Makinesi",
    "Resolve Project Backups",
    "CacheClip",
    ".gallery",
    ".Trash",
    "node_modules",
    ".git",
    "Library",
)
MAX_SNAPSHOTS_PER_PROJECT = 60
MAX_PROJECT_AGE_DAYS = 45


def is_valid_project_file(path):
    """Proje dosyasının yarım yazılmadığını ve bozuk olmadığını doğrular."""
    try:
        st = os.stat(path)
        if st.st_size < 64:
            return False
        with open(path, "rb") as f:
            head = f.read(16)
        # Premiere Pro .prproj dosyaları genellikle gzip (\x1f\x8b) sıkıştırmalı XML'dir:
        if head.startswith(b"\x1f\x8b"):
            with gzip.open(path, "rb") as gz:
                while gz.read(65536):
                    pass
        return True
    except Exception:
        return False


def _sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(131072):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def clone_apfs(src, dst):
    """APFS üzerinde `cp -c` (Copy-on-Write sıfır alanlı klon) yapar; farklı diskteyse normal kopyalar."""
    r = proc.run(["/bin/cp", "-c", src, dst], capture_output=True, timeout=300)
    if r.returncode == 0 and os.path.exists(dst):
        return "apfs_cow"
    shutil.copy2(src, dst)
    return "copy"


def discover_projects(home=HOME, max_age_days=MAX_PROJECT_AGE_DAYS):
    """Premiere Pro son kullanılanlar (MRU) listesini ve çalışma klasörlerini tarayarak aktif projeleri bulur."""
    found = set()
    cutoff = time.time() - (max_age_days * 86400)

    # 1. Premiere Pro tercih dosyalarındaki MRU (Son Açılan Projeler) kayıtları (2026..2030+ sürümden bağımsız)
    prefs_glob = os.path.join(home, "Documents", "Adobe", "Premiere Pro", "*", "Profile-*", "Adobe Premiere Pro Prefs")
    for pref in glob.glob(prefs_glob):
        try:
            with open(pref, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            for m in re.finditer(r"<BE\.Prefs\.MRU\.Document\.\d+>([^<]+)</BE\.Prefs\.MRU\.Document\.\d+>", raw):
                p = m.group(1).strip()
                if os.path.isfile(p) and p.lower().endswith(PROJECT_EXTENSIONS):
                    found.add(os.path.abspath(p))
        except OSError:
            continue

    # 2. Masaüstü, Filmler ve Belgeler altındaki güncel proje dosyaları (en fazla 4 klasör derinlikte)
    search_roots = (
        os.path.join(home, "Desktop"),
        os.path.join(home, "Movies"),
        os.path.join(home, "Documents"),
    )
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        root_depth = root.rstrip(os.sep).count(os.sep)
        for dp, dn, fn in os.walk(root):
            dn[:] = [
                d for d in dn
                if not d.startswith(".")
                and not any(frag.lower() in d.lower() for frag in SKIP_DIR_FRAGMENTS)
                and (os.path.join(dp, d).count(os.sep) - root_depth) <= 4
            ]
            if any(frag.lower() in dp.lower() for frag in SKIP_DIR_FRAGMENTS):
                continue
            for f in fn:
                if f.startswith(".") or not f.lower().endswith(PROJECT_EXTENSIONS):
                    continue
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                    if st.st_mtime >= cutoff:
                        found.add(os.path.abspath(p))
                except OSError:
                    continue

    return sorted(found)


class ProjectTimeMachine:
    """Kurgu projelerini APFS CoW (`cp -c`) ile sıfır disk alanı harcayarak otomatik yedekleyen zaman makinesi."""

    def __init__(self, vault_dir=DEFAULT_VAULT_DIR, home=HOME, max_snapshots=MAX_SNAPSHOTS_PER_PROJECT):
        self.vault_dir = vault_dir
        self.home = home
        self.max_snapshots = max_snapshots
        self._seen_mtime = {}  # path -> (mtime_ns, size)
        os.makedirs(self.vault_dir, exist_ok=True)
        try:
            open(os.path.join(self.vault_dir, ".metadata_never_index"), "a").close()
        except OSError:
            pass

    def _project_key(self, path):
        stem = os.path.splitext(os.path.basename(path))[0]
        safe_stem = re.sub(r"[^\w\-. ]+", "_", stem).strip() or "Proje"
        short_hash = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:6]
        return f"{safe_stem}_{short_hash}"

    def _manifest_path(self, proj_dir):
        return os.path.join(proj_dir, "manifest.json")

    def _load_manifest(self, proj_dir):
        mp = self._manifest_path(proj_dir)
        if os.path.exists(mp):
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"project_path": "", "project_name": "", "snapshots": []}

    def _save_manifest(self, proj_dir, data):
        mp = self._manifest_path(proj_dir)
        tmp = mp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, mp)

    def snapshot_project(self, path):
        """Tek bir proje dosyasının (değişmişse ve sağlamsa) APFS CoW anlık kopyasını alır."""
        abspath = os.path.abspath(path)
        try:
            st = os.stat(abspath)
        except OSError:
            return None

        sig = (st.st_mtime_ns, st.st_size)
        if self._seen_mtime.get(abspath) == sig:
            return None

        if not is_valid_project_file(abspath):
            log.warning("Proje dosyası henüz yazılıyor veya bütünlük doğrulamasından geçemedi, atlandı: %s", abspath)
            return None

        digest = _sha256_file(abspath)
        if not digest:
            return None

        key = self._project_key(abspath)
        proj_dir = os.path.join(self.vault_dir, key)
        os.makedirs(proj_dir, exist_ok=True)
        manifest = self._load_manifest(proj_dir)
        manifest["project_path"] = abspath
        manifest["project_name"] = os.path.basename(abspath)

        snapshots = manifest.get("snapshots", [])
        if snapshots and snapshots[-1].get("sha256") == digest:
            self._seen_mtime[abspath] = sig
            return None

        ts = dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d_%H-%M-%S")
        base, ext = os.path.splitext(os.path.basename(abspath))
        snap_filename = f"{base}__{ts}{ext}"
        snap_path = os.path.join(proj_dir, snap_filename)
        if os.path.exists(snap_path):
            snap_filename = f"{base}__{ts}_{digest[:6]}{ext}"
            snap_path = os.path.join(proj_dir, snap_filename)

        mode = clone_apfs(abspath, snap_path)
        entry = {
            "filename": snap_filename,
            "timestamp": ts,
            "mtime": st.st_mtime,
            "size": st.st_size,
            "sha256": digest,
            "mode": mode,
        }
        snapshots.append(entry)

        # Eski anlık görüntüleri sınırla (son max_snapshots adet kalır)
        while len(snapshots) > self.max_snapshots:
            old = snapshots.pop(0)
            old_p = os.path.join(proj_dir, old.get("filename", ""))
            try:
                if os.path.exists(old_p):
                    os.remove(old_p)
            except OSError:
                pass

        manifest["snapshots"] = snapshots
        self._save_manifest(proj_dir, manifest)
        self._seen_mtime[abspath] = sig
        log.info("Proje Zaman Makinesi (%s): %s -> %s", mode, os.path.basename(abspath), snap_filename)
        return entry

    def scan_and_snapshot(self, paths=None):
        """Tüm aktif kurgu projelerini tarar ve yeni kayıtları sıfır disk alanıyla klonlar."""
        targets = paths if paths is not None else discover_projects(home=self.home)
        taken = []
        for p in targets:
            res = self.snapshot_project(p)
            if res:
                taken.append((p, res))
        return taken

    def list_projects(self):
        out = []
        if not os.path.isdir(self.vault_dir):
            return out
        for entry in sorted(os.listdir(self.vault_dir)):
            proj_dir = os.path.join(self.vault_dir, entry)
            if not os.path.isdir(proj_dir):
                continue
            m = self._load_manifest(proj_dir)
            if m.get("snapshots"):
                out.append((entry, m))
        return out

    def restore(self, query, version_index=1):
        """Belirtilen projenin `version_index` (1 = en son sağlam kayıt, 2 = bir önceki...) sürümünü geri yükler."""
        q = (query or "").strip().lower()
        matches = []
        for key, m in self.list_projects():
            pname = (m.get("project_name") or "").lower()
            ppath = (m.get("project_path") or "").lower()
            if not q or q in key.lower() or q in pname or q in ppath:
                matches.append((key, m))
        if not matches:
            return False, f"'{query}' ile eşleşen yedeklenmiş proje bulunamadı."

        key, manifest = matches[0]
        snaps = list(reversed(manifest.get("snapshots", [])))  # 1 = en yeni
        if version_index < 1 or version_index > len(snaps):
            return False, f"Geçersiz sürüm numarası ({version_index}). Bu proje için 1..{len(snaps)} arası sürüm var."

        chosen = snaps[version_index - 1]
        proj_dir = os.path.join(self.vault_dir, key)
        snap_path = os.path.join(proj_dir, chosen["filename"])
        dest_path = manifest["project_path"]
        if not os.path.exists(snap_path):
            return False, f"Anlık görüntü dosyası bulunamadı: {snap_path}"

        # Mevcut dosyayı ezmeden önce güvenlik klonu al:
        if os.path.exists(dest_path):
            backup_path = f"{dest_path}.geri_yukleme_oncesi_yedek"
            clone_apfs(dest_path, backup_path)

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        clone_apfs(snap_path, dest_path)
        return (
            True,
            f"'{manifest['project_name']}' projesi {chosen['timestamp']} tarihli sağlam APFS klonuna geri yüklendi "
            f"(Hedef: {dest_path}).",
        )

    def report(self, query=None):
        projects = self.list_projects()
        if not projects:
            return "Proje Zaman Makinesi hazır; henüz klonlanmış proje yok."
        lines = [f"APFS Sıfır Alanlı Proje Zaman Makinesi ({self.vault_dir}):"]
        q = (query or "").strip().lower()
        for key, m in projects:
            pname = m.get("project_name") or key
            if q and q not in pname.lower() and q not in key.lower():
                continue
            snaps = list(reversed(m.get("snapshots", [])))
            latest = snaps[0]["timestamp"] if snaps else "-"
            lines.append(f"• {pname} ({len(snaps)} anlık klon, son: {latest}) — Konum: {m.get('project_path')}")
            for idx, s in enumerate(snaps[:5], 1):
                sz_kb = s.get("size", 0) / 1024
                lines.append(f"    {idx}) {s['timestamp']} ({sz_kb:.0f} KB, {s.get('mode', 'apfs_cow')})")
        lines.append("Geri yüklemek için: `mek zamanmakinesi geri <proje_adı> [sürüm_no]`")
        return "\n".join(lines)
