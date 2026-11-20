# -*- coding: utf-8 -*-
"""Yerel LLM (Ollama, llama-server, LM Studio, MLX) RAM koordinatörü.

64 GB birleşik bellekte kurgu uygulamaları ve yerel büyük dil modelleri birlikte
yaşar. Bu modül:
  - Etkin LLM süreçlerini ve yüklü Ollama modellerini (`/api/ps`) tespit eder,
  - LLM sunucularının `tame_runaways` tarafından E-çekirdeklere kısılmasını önler,
  - Toplam LLM bellek ayak izini `MemoryBudget`'a rezerv olarak bildirir,
  - Bellek baskısı yükseldiğinde (veya kurgu uygulamaları RAM istediğinde) uzun süredir
    boşta bekleyen Ollama modellerini temiz biçimde bellekten düşürür (`keep_alive: 0`).
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request

import psutil

log = logging.getLogger("mek.llm_ram")

OLLAMA_PROC_RE = re.compile(r"(?:^|/)(?:ollama|ollama_llama_server)(?:\s|$)", re.I)
LLM_ENGINE_RE = re.compile(
    r"(?:^|/)(?:ollama|ollama_llama_server|llama-server|llama-cli|llama-bench|koboldcpp|vllm|lm-studio|mlx_lm)(?:\b|$)|"
    r"LM Studio\.app|llama\.cpp",
    re.I,
)
HEAVY_AI_HOST_RE = re.compile(r"Alptuguide", re.I)


def is_llm_process(target):
    """Verilen süreç adı/komut yolu yerel bir LLM motoruna mı ait?"""
    if not target:
        return False
    return bool(LLM_ENGINE_RE.search(target))


def _default_rss_bytes(pid):
    try:
        return psutil.Process(int(pid)).memory_info().rss
    except (psutil.Error, ValueError, TypeError, OSError):
        return 0


def ollama_loaded_models(url="http://127.0.0.1:11434/api/ps", timeout=1.5):
    """Yerel Ollama sunucusunda şu an bellekte yüklü modelleri döndürür."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
        models = data.get("models") or []
        return [m for m in models if isinstance(m, dict) and (m.get("name") or m.get("model"))]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return []


def ollama_unload(model_name, url="http://127.0.0.1:11434/api/generate", timeout=3.0):
    """Belirtilen Ollama modelini `keep_alive: 0` ile anında RAM/VRAM'den indirir."""
    if not model_name:
        return False
    payload = json.dumps({"model": model_name, "keep_alive": 0}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        log.debug("Ollama model indirme (%s) başarısız: %s", model_name, e)
        return False


def _iter_snapshot_procs(procs=None):
    if procs is not None:
        return procs
    out = []
    for p in psutil.process_iter(["pid", "name", "exe", "cpu_percent"]):
        try:
            info = p.info
            out.append((
                info["pid"],
                info.get("name") or "",
                info.get("exe") or "",
                info.get("cpu_percent") or 0.0,
                0,
            ))
        except (psutil.Error, OSError):
            pass
    return out


def inspect(procs=None, check_ollama=True, rss_lookup=None, ps_fetcher=None):
    """Çalışan yerel LLM süreçlerini ve yüklü Ollama modellerini özetler."""
    rows = _iter_snapshot_procs(procs)
    get_rss = rss_lookup or _default_rss_bytes
    fetch_ps = ps_fetcher or ollama_loaded_models

    ollama_rss_bytes = 0
    ollama_cpu = 0.0
    other_llm_bytes = 0
    llm_procs = []
    active_busy = False

    for row in rows:
        pid, name, exe, cpu = row[0], row[1] or "", row[2] or "", float(row[3] or 0.0)
        target = exe or name
        is_ollama = bool(OLLAMA_PROC_RE.search(target) or OLLAMA_PROC_RE.search(name))
        is_llm = is_ollama or is_llm_process(target) or bool(HEAVY_AI_HOST_RE.search(target))
        if not is_llm:
            continue

        rss = int(get_rss(pid) or 0)
        # Alptuguide gibi masaüstü kabuklarını yalnızca gerçekten büyük bir model taşırken (>512 MB) say
        if not is_ollama and not is_llm_process(target) and rss < 512 * 1024 * 1024:
            continue

        if cpu >= 15.0:
            active_busy = True

        rss_gb = rss / (1024 ** 3)
        if is_ollama:
            ollama_rss_bytes += rss
            ollama_cpu += cpu
        else:
            other_llm_bytes += rss
        if rss_gb >= 0.05 or is_ollama:
            llm_procs.append((round(rss_gb, 2), pid, name or exe, round(cpu, 1)))

    models = fetch_ps() if check_ollama else []
    ollama_api_bytes = sum(int(m.get("size") or m.get("size_vram") or 0) for m in models)
    effective_ollama_bytes = max(ollama_rss_bytes, ollama_api_bytes)
    total_bytes = other_llm_bytes + effective_ollama_bytes
    llm_procs.sort(reverse=True)

    return {
        "total_gb": round(total_bytes / (1024 ** 3), 2),
        "ollama_gb": round(effective_ollama_bytes / (1024 ** 3), 2),
        "other_llm_gb": round(other_llm_bytes / (1024 ** 3), 2),
        "ollama_cpu": round(ollama_cpu, 1),
        "ollama_models": models,
        "processes": llm_procs,
        "active_busy": active_busy,
    }


class LLMRamCoordinator:
    """Yerel LLM bellek kullanımını izler ve baskı anında boştaki Ollama modellerini indirir."""

    def __init__(self, ps_fetcher=None, unloader=None, rss_lookup=None):
        self._ps_fetcher = ps_fetcher or ollama_loaded_models
        self._unloader = unloader or ollama_unload
        self._rss_lookup = rss_lookup
        self._model_last_busy = {}
        self._last_unload_try = {}
        self.last_summary = None

    def inspect_snapshot(self, snap=None, check_ollama=True):
        procs = getattr(snap, "procs", None) if snap is not None else None
        summary = inspect(
            procs=procs,
            check_ollama=check_ollama,
            rss_lookup=self._rss_lookup,
            ps_fetcher=self._ps_fetcher,
        )
        self.last_summary = summary
        return summary

    def maybe_relieve(self, snap, cfg=None, pro_active=False, now=None):
        """Bellek baskısı varsa uzun süre boşta kalmış Ollama modellerini temizler."""
        ts = time.time() if now is None else float(now)
        cfg = cfg or {}
        summary = self.inspect_snapshot(snap=snap, check_ollama=True)
        models = summary["ollama_models"]

        active_names = set()
        for m in models:
            name = m.get("name") or m.get("model")
            if not name:
                continue
            active_names.add(name)
            if name not in self._model_last_busy or summary["ollama_cpu"] >= 8.0:
                self._model_last_busy[name] = ts

        for stale in list(self._model_last_busy):
            if stale not in active_names:
                self._model_last_busy.pop(stale, None)

        pressure = int(getattr(snap, "pressure", 1) or 1)
        mem_pct = float(getattr(snap, "mem_pct", 0.0) or 0.0)

        base_idle_s = max(60, int(cfg.get("llm_auto_unload_idle_min", 15)) * 60)
        if pressure >= 3 or mem_pct >= 85.0:
            idle_threshold = min(base_idle_s, 180)
        elif pro_active and (pressure >= 2 or mem_pct >= 70.0):
            idle_threshold = min(base_idle_s, 300)
        elif pressure >= 2 or mem_pct >= 75.0:
            idle_threshold = base_idle_s
        else:
            return {"unloaded": [], "kept": sorted(active_names), "total_gb": summary["total_gb"]}

        unloaded = []
        kept = []
        for m in models:
            name = m.get("name") or m.get("model")
            if not name:
                continue
            idle_s = ts - self._model_last_busy.get(name, ts)
            last_try = self._last_unload_try.get(name, 0.0)
            if idle_s >= idle_threshold and (ts - last_try) >= 120.0 and summary["ollama_cpu"] < 8.0:
                self._last_unload_try[name] = ts
                if self._unloader(name):
                    unloaded.append(name)
                    self._model_last_busy.pop(name, None)
                    log.info(
                        "Bellek koruması: %.0f dk boşta kalan Ollama modeli bellekten indirildi: %s",
                        idle_s / 60.0,
                        name,
                    )
                    continue
            kept.append(name)

        return {"unloaded": unloaded, "kept": kept, "total_gb": summary["total_gb"]}

    def format_report(self, snap=None):
        s = self.inspect_snapshot(snap=snap, check_ollama=True)
        parts = [f"Yerel LLM Bellek Ayak İzi: {s['total_gb']:.2f} GB"]
        if s["ollama_models"]:
            names = ", ".join(
                f"{m.get('name') or m.get('model')} ({(m.get('size') or 0) / (1024**3):.1f} GB)"
                for m in s["ollama_models"]
            )
            parts.append(f"  Yüklü Ollama modelleri: {names}")
        else:
            parts.append("  Yüklü Ollama modeli yok (bellek tamamen boş).")
        other = [(gb, pid, nm, cpu) for gb, pid, nm, cpu in s["processes"] if gb >= 0.25]
        if other:
            desc = ", ".join(f"{nm} {gb:.1f} GB" for gb, _pid, nm, _cpu in other[:5])
            parts.append(f"  Etkin LLM süreçleri: {desc}")
        return "\n".join(parts)
