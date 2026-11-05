# -*- coding: utf-8 -*-
"""Dinamik bellek bütçesi: ağır yaratıcı uygulamaların "en fazla şu kadar RAM kullan" sınırlarını
o an açık olan diğer ağır uygulamalara göre ayarlar.

Neden gerekli: Photoshop, Premiere/After Effects, Resolve ve Topaz bellek sınırlarını AÇILIŞTA okur ve
o sınıra kadar önbellek biriktirip kolay kolay geri vermez. Her biri tek başına çalışacakmış gibi
ayarlanırsa (ör. Topaz %100, Resolve 48 GB, Photoshop %70, Adobe 58 GB) birkaçı birlikte açıldığında
64 GB'lık makinede toplam 200 GB'ı aşan bir söz verilmiş olur: bellek sıkışır, takas başlar, her şey kasar.

Kural: bir uygulama kapalıyken sınırı, açık olan ağır uygulama sayısına göre yeniden yazılır; böylece
bir sonraki açılışında adil payını alır. Açık bir uygulamanın ayarına asla dokunulmaz (uygulama kapanırken
kendi ayarını geri yazar ve zaten yeni değeri ancak yeniden açılınca okur).
"""
import glob
import logging
import os
import re
import subprocess

import psutil

log = logging.getLogger("mek.memory")
GIB = 1024 ** 3
HOME = os.path.expanduser("~")

APPS = {
    "photoshop": {"name": "Photoshop", "solo": 0.60, "min_gb": 8,
                  "match": r"Contents/MacOS/(Adobe )?Photoshop"},
    "adobe_video": {"name": "Premiere Pro / After Effects / Media Encoder", "solo": 0.70, "min_gb": 12,
                    "match": r"Contents/MacOS/(Adobe Premiere[^/]*|After Effects[^/]*|Adobe After Effects[^/]*|"
                             r"Adobe Media Encoder[^/]*|Adobe Audition[^/]*|PProHeadless)"},
    "resolve": {"name": "DaVinci Resolve", "solo": 0.60, "min_gb": 12,
                "match": r"Contents/MacOS/(DaVinci )?Resolve"},
    "topaz_video": {"name": "Topaz Video", "solo": 0.55, "min_gb": 8,
                    "match": r"Contents/MacOS/Topaz Video"},
}
_MATCH = {k: re.compile(v["match"]) for k, v in APPS.items()}


def plan(total_gb, running, reserve_gb=10, overcommit=1.2, active=None):
    """Her uygulama için GB cinsinden sınır.
    running: şu an açık uygulama anahtarları.
    active: şu an önde kullanılan veya aktif render yapan uygulama anahtarları (varsa 2.2x ağırlık alır)."""
    avail = max(8.0, total_gb - reserve_gb)
    running_set = set(running or ())
    active_set = set(active or ()) & running_set
    out = {}
    for key, spec in APPS.items():
        others = len(running_set - {key})
        if active_set and others > 0:
            competitors = running_set | {key}
            weights = {}
            for c in competitors:
                if c in active_set:
                    weights[c] = 2.2
                elif c == key and key not in running_set:
                    weights[c] = 1.5
                else:
                    weights[c] = 1.0
            share = avail * overcommit * (weights[key] / sum(weights.values()))
        else:
            share = avail * overcommit / (others + 1)
        solo = min(avail, total_gb * spec["solo"])
        out[key] = int(round(max(spec["min_gb"], min(solo, share))))
    return out


def running_apps(exes):
    return {key for key, rx in _MATCH.items() if any(rx.search(e) for e in exes)}


class MemoryBudget:
    def __init__(self, cfg, total_bytes=None, run=subprocess.run, home=HOME):
        self.cfg = cfg
        self.total_gb = (total_bytes or psutil.virtual_memory().total) / GIB
        self.run = run
        self.home = home
        self.last_plan = {}

    # ------------------------------------------------------------------ okuma / yazma
    def _defaults(self, *args):
        r = self.run(["/usr/bin/defaults", *args], capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else None

    def _ps_keys(self):
        out = self._defaults("read", "com.adobe.Photoshop") or ""
        return re.findall(r"(VMMemoryUsagePercent64\d+)\s*=\s*(\d+);", out)

    def _adobe_files(self):
        p1 = glob.glob(os.path.join(self.home, "Library/Preferences/Adobe/dynamiclinkmanager/*/memorybalancer*.xml"))
        p2 = glob.glob(os.path.join(self.home, "Library/Preferences/Adobe/dynamiclinkmanager/**/memorybalancer*.xml"),
                       recursive=True)
        return sorted(set(p1 + p2))

    def _resolve_files(self):
        d = os.path.join(self.home, "Library/Preferences/Blackmagic Design/DaVinci Resolve")
        return [(os.path.join(d, "config.dat"), r"^(Local\.Resource\.Buffers\.1 = )(\d+)"),
                (os.path.join(d, ".config.data"), r"^(Memory = )(\d+)")]

    _ADOBE_RX = re.compile(r"(<key>memorytouseforotherapplications</key>\s*<int[^>]*>)(\d+)(</int>)")

    def read(self, key):
        """Uygulamanın şu an kayıtlı sınırı (GB) ya da ayar yoksa None."""
        try:
            if key == "photoshop":
                keys = self._ps_keys()
                return self.total_gb * int(keys[-1][1]) / 100 if keys else None
            if key == "topaz_video":
                out = self._defaults("read", "com.topazlabs.Topaz Video", "maxMemoryUsage")
                return self.total_gb * int(out.strip()) / 100 if out else None
            if key == "adobe_video":
                for path in self._adobe_files():
                    m = self._ADOBE_RX.search(open(path, encoding="utf-8", newline="").read())
                    if m:
                        return self.total_gb - int(m.group(2)) / GIB
                return None
            if key == "resolve":
                for path, rx in self._resolve_files():
                    if os.path.exists(path):
                        m = re.search(rx, open(path, encoding="latin-1", newline="").read(), re.M)
                        if m:
                            return int(m.group(2)) / 1024
                return None
        except (OSError, ValueError, IndexError):
            return None
        return None

    def write(self, key, gb):
        pct = max(10, min(90, int(round(gb / self.total_gb * 100))))
        if key == "photoshop":
            for name, _ in self._ps_keys():
                self._defaults("write", "com.adobe.Photoshop", name, "-int", str(pct))
        elif key == "topaz_video":
            self._defaults("write", "com.topazlabs.Topaz Video", "maxMemoryUsage", "-int", str(pct))
        elif key == "adobe_video":
            reserve = int(max(6, self.total_gb - gb) * GIB)
            for path in self._adobe_files():
                text = open(path, encoding="utf-8", newline="").read()
                new = self._ADOBE_RX.sub(lambda m: f"{m.group(1)}{reserve}{m.group(3)}", text)
                if new != text:
                    _atomic_write(path, new)
        elif key == "resolve":
            for path, rx in self._resolve_files():
                if os.path.exists(path):
                    text = open(path, encoding="latin-1", newline="").read()
                    new = re.sub(rx, lambda m: f"{m.group(1)}{int(gb * 1024)}", text, flags=re.M)
                    if new != text:
                        _atomic_write(path, new, "latin-1")

    # ------------------------------------------------------------------ döngü
    def tick(self, exes, active_exes=None):
        """Kapalı uygulamaların ve Adobe DynamicLink havuzunun sınırlarını güncel çalışma/onem
        durumuna göre dinamik olarak yeniden yazar. Değişiklik listesi döner."""
        running = running_apps(exes)
        active = running_apps(active_exes) if active_exes else None
        target = plan(self.total_gb, running, self.cfg.get("memory_reserve_gb", 10),
                      self.cfg.get("memory_overcommit", 1.2), active=active)
        self.last_plan = {"running": sorted(running), "active": sorted(active or ()), "plan": target}
        changes = []
        for key, gb in target.items():
            current = self.read(key)
            if current is None:
                continue
            if key in running:
                # Adobe dynamiclinkmanager havuzu (Premiere / AE / Media Encoder / PProHeadless)
                # çalışma ve önem durumuna göre dinamik olarak güncellenir:
                if key != "adobe_video":
                    continue
                if active is None and (current - gb) < 12:
                    continue
                if active is not None and abs(current - gb) < 4 and (current - gb) < 12:
                    continue
            elif abs(current - gb) < 2:
                continue
            try:
                self.write(key, gb)
                changes.append((key, round(current), gb))
                log.info("Bellek sınırı: %s %d GB -> %d GB (açık: %s, aktif: %s)",
                         APPS[key]["name"], round(current), gb,
                         ", ".join(sorted(running)) or "yok",
                         ", ".join(sorted(active or ())) or "yok")
            except OSError as e:
                log.warning("Bellek sınırı yazılamadı %s: %s", key, e)
        return changes

    def report(self):
        parts = []
        for key, spec in APPS.items():
            cur = self.read(key)
            if cur is not None:
                parts.append(f"{spec['name']} {cur:.0f} GB")
        running = self.last_plan.get("running") or []
        active = self.last_plan.get("active") or []
        act_str = f" (aktif öncelikli: {', '.join(APPS[k]['name'] for k in active)})" if active else ""
        return (f"Toplam {self.total_gb:.0f} GB. Uygulama bellek sınırları: " + (", ".join(parts) or "ayar bulunamadı") +
                f". Açık ağır uygulama: {', '.join(APPS[k]['name'] for k in running) or 'yok'}{act_str}. "
                "Sınırlar, açık uygulamaların aktif/bekleme önem sırasına göre dinamik ayarlanır.")


def _atomic_write(path, text, encoding="utf-8"):
    tmp = path + ".mek-tmp"
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
    os.replace(tmp, path)
