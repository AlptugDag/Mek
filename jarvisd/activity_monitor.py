"""Read-only process activity reporting and application crash report monitoring."""
import glob
import json
import os
import subprocess
import time

import psutil


REPORT_GLOBS = ("*.crash", "*.ips", "*.hang")


def process_snapshot(process_iter=None, cpu_percent=None, virtual_memory=None, events=None):
    process_iter = process_iter or (lambda: psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_info", "status"]))
    cpu_percent = cpu_percent or psutil.cpu_percent
    virtual_memory = virtual_memory or psutil.virtual_memory
    processes = []
    for process in process_iter():
        try:
            info = process.info
            memory = info.get("memory_info")
            processes.append({"pid": info.get("pid"), "name": info.get("name") or "?",
                              "cpu": float(info.get("cpu_percent") or 0),
                              "rss": memory.rss if memory else 0,
                              "status": info.get("status") or "unknown"})
        except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, TypeError):
            continue
    snapshot = {"cpu_pct": cpu_percent(interval=None),
                "memory_pct": virtual_memory().percent,
                "processes": len(processes),
                "top_cpu": sorted(processes, key=lambda p: p["cpu"], reverse=True)[:5],
                "top_memory": sorted(processes, key=lambda p: p["rss"], reverse=True)[:5]}
    if events:
        events.publish("activity.snapshot", snapshot=snapshot)
    return snapshot


def format_snapshot(snapshot):
    cpu = ", ".join(f"{p['name']} {p['cpu']:.0f}%" for p in snapshot["top_cpu"] if p["cpu"] > 0) or "ölçüm yok"
    memory = ", ".join(f"{p['name']} {p['rss'] / 1024 ** 3:.1f} GB" for p in snapshot["top_memory"])
    return (f"CPU %{snapshot['cpu_pct']:.0f}, bellek %{snapshot['memory_pct']:.0f}, "
            f"{snapshot['processes']} süreç. En yüksek CPU: {cpu}. "
            f"En yüksek bellek: {memory or 'ölçüm yok'}.")


def open_activity_monitor(runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["/usr/bin/open", "-a", "Activity Monitor"],
                        capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": result.returncode == 0,
            "message": (result.stderr or "Activity Monitor açıldı.").strip()}


class CrashMonitor:
    def __init__(self, reports_dir=None, state_path=None, clock=None, events=None):
        home = os.path.expanduser("~")
        self.reports_dir = reports_dir or os.path.join(home, "Library/Logs/DiagnosticReports")
        self.state_path = state_path or os.path.join(home, ".jarvis/state/crash_reports.json")
        self.clock = clock or time.time
        self.events = events
        self.seen = self._load()
        self.initialized = os.path.exists(self.state_path)

    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as state_file:
                data = json.load(state_file)
            return set(data if isinstance(data, list) else [])
        except (OSError, ValueError):
            return set()

    def _save(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        temp = self.state_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as state_file:
            json.dump(sorted(self.seen)[-2000:], state_file)
        os.replace(temp, self.state_path)
        self.seen = set(sorted(self.seen)[-2000:])

    def scan(self):
        paths = []
        for pattern in REPORT_GLOBS:
            paths.extend(glob.glob(os.path.join(self.reports_dir, pattern)))
        reports = []
        fresh = set()
        for path in paths:
            try:
                stat = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            identity = f"{path}\t{stat.st_mtime_ns}\t{stat.st_size}"
            fresh.add(identity)
            if identity not in self.seen:
                reports.append({"path": path, "name": os.path.basename(path),
                                "time": stat.st_mtime, "kind": os.path.splitext(path)[1][1:]})

        if not self.initialized:
            self.seen = fresh
            self.initialized = True
            self._save()
            return []

        self.seen.update(fresh)
        self._save()
        reports = sorted(reports, key=lambda report: report["time"], reverse=True)
        if self.events:
            for report in reports:
                self.events.publish("app.crashed", report=report)
        return reports

    def recent(self, limit=8):
        reports = []
        for pattern in REPORT_GLOBS:
            for path in glob.glob(os.path.join(self.reports_dir, pattern)):
                try:
                    stat = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                reports.append({"path": path, "name": os.path.basename(path),
                                "time": stat.st_mtime, "kind": os.path.splitext(path)[1][1:]})
        return sorted(reports, key=lambda report: report["time"], reverse=True)[:limit]