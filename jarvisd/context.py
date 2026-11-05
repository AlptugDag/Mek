# -*- coding: utf-8 -*-
"""Bağlam motoru: o an ne yaptığınızı anlar (ön plandaki uygulama, boşta kalma,
render süreçleri, bellek/disk/GPU yükü, mikrofonu kullanan uygulama — görüşme/kayıtta susmak için)."""
import ctypes
import os
import re
import subprocess
import time

import psutil

from config import PRO_APP_PATTERN, RENDER_PROCS

_PRO_RE = re.compile(PRO_APP_PATTERN)
_RENDER_RE = {k: re.compile(v) for k, v in RENDER_PROCS.items()}


def frontmost_app():
    return front_info()[0]


def front_info():
    """(ön plandaki uygulamanın adı, pid). lsappinfo her seferinde taze bilgi verir
    (NSWorkspace, runloop'u olmayan süreçte bayatlar)."""
    try:
        asn = subprocess.check_output(["lsappinfo", "front"], text=True).strip()
        out = subprocess.check_output(["lsappinfo", "info", "-only", "name", "-only", "pid", asn], text=True)
        m = re.search(r'"(?:LSDisplayName|name)"="([^"]*)"', out) or re.match(r'\s*"([^"]+)"', out)
        p = re.search(r"pid\s*=\s*(\d+)", out) or re.search(r'"pid"=(\d+)', out)
        return (m.group(1) if m else ""), (int(p.group(1)) if p else None)
    except Exception:
        return "", None


def memory_pressure():
    """1 normal, 2 uyarı, 4 kritik (çekirdeğin kendi bellek baskısı seviyesi; '% dolu' yanıltıcıdır,
    macOS boş belleği önbellek olarak kullanır)."""
    try:
        out = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                                      text=True, timeout=5)
        return int(out.strip() or 1)
    except Exception:
        return 1


def running_app_names():
    try:
        out = subprocess.check_output(["lsappinfo", "list"], text=True)
        return set(re.findall(r'^\s*\d+\) "([^"]+)"', out, re.M))
    except Exception:
        return set()


def idle_seconds():
    try:
        out = subprocess.check_output(["ioreg", "-c", "IOHIDSystem", "-d", "4"], text=True)
        m = re.search(r'"HIDIdleTime" = (\d+)', out)
        return int(m.group(1)) / 1e9 if m else 0.0
    except Exception:
        return 0.0


def gpu_utilization():
    """GPU yüzdesi (sudo gerektirmez, IOAccelerator istatistiğinden)."""
    try:
        out = subprocess.check_output(["ioreg", "-r", "-c", "IOAccelerator", "-d", "1"], text=True)
        m = re.search(r'"Device Utilization %"=(\d+)', out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


# --- Mikrofonu başka bir süreç kullanıyor mu? (CoreAudio süreç nesneleri, macOS 14+) ----------
_ca = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")


class _Addr(ctypes.Structure):
    _fields_ = [("s", ctypes.c_uint32), ("sc", ctypes.c_uint32), ("e", ctypes.c_uint32)]


def _fc(x):
    return int.from_bytes(x, "big")


def _ca_get(obj, sel, ctype):
    a = _Addr(_fc(sel), _fc(b"glob"), 0)
    size = ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(a), 0, None, ctypes.byref(size)):
        return []
    n = size.value // ctypes.sizeof(ctype)
    if n == 0:
        return []
    buf = (ctype * n)()
    if _ca.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(size), buf):
        return []
    return list(buf)


# Sürekli mikrofon tutan sistem süreçleri (Siri vb.) sayılmaz. Mek mikrofonu hiç açmaz.
_MIC_IGNORE = re.compile(r"corespeechd|coreaudiod|siri|assistantd|heard|ControlCenter|universalaccessd")


def other_mic_users():
    """Mikrofondan kayıt yapan (Mek dışındaki) süreçlerin adları."""
    me = os.getpid()
    users = []
    for obj in _ca_get(1, b"prs#", ctypes.c_uint32):
        running = _ca_get(obj, b"piri", ctypes.c_uint32)
        if not running or not running[0]:
            continue
        pid = (_ca_get(obj, b"ppid", ctypes.c_int32) or [0])[0]
        if pid in (0, me):
            continue
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = str(pid)
        if not _MIC_IGNORE.search(name):
            users.append(name)
    return users


def thermal_state():
    """0 normal, 1 hafif, 2 ciddi, 3 kritik (NSProcessInfo; maliyetsiz)."""
    try:
        from Foundation import NSProcessInfo
        return int(NSProcessInfo.processInfo().thermalState())
    except Exception:
        return 0


class Snapshot:
    __slots__ = ("t", "front", "front_pid", "idle", "cpu", "gpu", "mem_pct", "swap_gb", "disk_free_gb",
                 "pro_pids", "pro_procs", "render_cpu", "apps", "top_mem", "hot", "thermal", "pressure", "exes")


def _proc_scan():
    """Tek geçişte: kurgu süreçleri, render yükü, en çok bellek, CPU'yu yoranlar ve çalışan program yolları."""
    pro, pro_procs, render, mem, hot, exes = [], [], {k: 0.0 for k in _RENDER_RE}, [], [], set()
    for p in psutil.process_iter(["pid", "exe", "cmdline", "cpu_percent", "memory_info", "name"]):
        try:
            cmd = p.info["exe"] or " ".join(p.info["cmdline"] or [])
            if not cmd:
                continue
            exes.add(cmd)
            cpu = p.info["cpu_percent"] or 0.0
            mi = p.info["memory_info"]
            rss = mi.rss if mi else 0
            if _PRO_RE.search(cmd):
                pro.append(p.info["pid"])
                pro_procs.append((p.info["pid"], cmd, cpu, rss))
            elif cpu > 120:
                hot.append((p.info["pid"], p.info["name"] or "", cpu, cmd))
            for k, rx in _RENDER_RE.items():
                if rx.search(cmd):
                    render[k] += cpu
            if mi:
                mem.append((rss, p.info["name"] or ""))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    mem.sort(reverse=True)
    return pro, pro_procs, render, mem[:8], hot, exes


_slow = {}  # ad -> (zaman, değer): pahalı alt süreç sorguları her turda çalışmasın


def _cached(key, ttl, fn):
    now = time.monotonic()
    hit = _slow.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _slow[key] = (now, val)
    return val


def snapshot(events=None):
    s = Snapshot()
    s.t = time.time()
    s.front, s.front_pid = front_info()
    s.idle = idle_seconds()
    s.cpu = psutil.cpu_percent(interval=None)
    s.gpu = _cached("gpu", 30, gpu_utilization)
    vm = psutil.virtual_memory()
    s.mem_pct = vm.percent
    s.swap_gb = psutil.swap_memory().used / 1024 ** 3
    st = os.statvfs("/System/Volumes/Data")
    s.disk_free_gb = st.f_bavail * st.f_frsize / 1024 ** 3
    s.pro_pids, s.pro_procs, s.render_cpu, s.top_mem, s.hot, s.exes = _proc_scan()
    s.apps = _cached("apps", 60, running_app_names)
    s.thermal = thermal_state()
    s.pressure = memory_pressure()
    if events:
        events.publish("system.snapshot", snapshot=s)
    return s


class RenderTracker:
    """Bir uygulamanın CPU'su uzun süre yüksek kalıp sonra düşerse 'iş bitti' sayar."""

    def __init__(self, busy=180.0, calm=25.0, min_busy_s=60, calm_s=25):
        self.busy, self.calm, self.min_busy_s, self.calm_s = busy, calm, min_busy_s, calm_s
        self.state = {}  # name -> [busy_since, calm_since]

    def update(self, render_cpu, now):
        finished = []
        for name, cpu in render_cpu.items():
            st = self.state.setdefault(name, [None, None])
            if cpu >= self.busy:
                st[0] = st[0] or now
                st[1] = None
            elif cpu > self.calm:
                st[1] = None
            elif st[0] and cpu <= self.calm:
                st[1] = st[1] or now
                if now - st[1] >= self.calm_s:
                    dur = st[1] - st[0]
                    if dur >= self.min_busy_s:
                        finished.append((name, dur))
                    self.state[name] = [None, None]
        return finished

    def active(self):
        return [n for n, (b, c) in self.state.items() if b and not c]
