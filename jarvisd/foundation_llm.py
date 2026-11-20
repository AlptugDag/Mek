# -*- coding: utf-8 -*-
"""Apple Foundation Models (Cihaz İçi LLM) Koordinatörü.

macOS'un yerleşik Apple Foundation Models (`SystemLanguageModel.default`) motorunu
sıfır ek RAM yüküyle (sistemin kendi Neural Engine / modelmanagerd altyapısı üzerinden)
Mek'in tüm zeki karar katmanlarına entegre eder:

  1. Yapılandırılmış Araç Çağırma (`--mode agent`, `DynamicGenerationSchema`):
     Serbest metin JSON ayrıştırma hatalarını ortadan kaldıran donanım-kısıtlı karar üretimi.
  2. Performans & Donanım Ömrü Danışmanı (`--mode advisor`):
     CPU/GPU/NVMe SSD/PSU sıcaklıkları, fan RPM, RAM baskısı, takas (swap) ve disk boşluğuna
     bakarak sağlık puanı, doğal Türkçe değerlendirme ve deterministik güvenlik sınırlarıyla
     doğrulanmış koruyucu eylem önerisi üretir.
  3. Seçenek Sınıflandırma (`--mode classify`) ve Doğal Türkçe Seslendirme (`--mode text`).
"""
import json
import logging
import os
import re
import subprocess
import time

import config

log = logging.getLogger("mek.foundation_llm")

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
_STATUS_CACHE = {"ts": 0.0, "val": None}

VALID_ADVISOR_ACTIONS = frozenset({"none", "optimize", "fan_boost", "fan_silent", "unload_llm", "clean_cache"})


def binary_path():
    """Kurulu veya depo içi Apple Foundation Models köprü ikilisinin yolunu döner."""
    if os.access(config.AI_BIN, os.X_OK):
        return config.AI_BIN
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai", "jarvis-ai")
    if os.access(local, os.X_OK):
        return local
    return None


def status(force=False, runner=subprocess.run):
    """Apple Foundation Models erişilebilirlik durumunu önbellekli olarak döndürür."""
    now = time.monotonic()
    if not force and _STATUS_CACHE["val"] is not None and (now - _STATUS_CACHE["ts"] < 300.0):
        return dict(_STATUS_CACHE["val"])

    bin_path = binary_path()
    if not bin_path:
        res = {"available": False, "model": "Apple Foundation Models (On-Device)", "reason": "binary_missing"}
        _STATUS_CACHE.update(ts=now, val=res)
        return dict(res)

    try:
        r = runner([bin_path, "--status"], capture_output=True, text=True, timeout=8)
        raw = (r.stdout or "").strip()
        if raw.startswith("{"):
            data = json.loads(raw)
            res = {
                "available": bool(data.get("available")),
                "model": str(data.get("model") or "Apple Foundation Models (On-Device)"),
                "reason": str(data.get("reason") or ("ready" if r.returncode == 0 else "unavailable")),
            }
        else:
            res = {
                "available": r.returncode == 0,
                "model": "Apple Foundation Models (On-Device)",
                "reason": "ready" if r.returncode == 0 else f"exit_{r.returncode}",
            }
    except Exception as e:
        res = {"available": False, "model": "Apple Foundation Models (On-Device)", "reason": str(e)}

    _STATUS_CACHE.update(ts=now, val=res)
    return dict(res)


def generate_text(prompt, instructions="", timeout=20, runner=subprocess.run):
    """Doğal Türkçe metin üretir (brifing, bildirim üslubu, özet)."""
    bin_path = binary_path()
    if not bin_path:
        return None
    try:
        cmd = [bin_path, "--mode", "text"]
        if instructions:
            cmd.append(instructions)
        r = runner(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
        out = EMOJI_RE.sub("", r.stdout or "").strip()
        return out if r.returncode == 0 and out else None
    except Exception as e:
        log.debug("Foundation Models text hatası: %s", e)
        return None


def _coerce_arg_value(val_str):
    s = str(val_str or "").strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


def decide_agent(system_prompt, transcript, tool_names=None, timeout=30, runner=subprocess.run):
    """DynamicGenerationSchema ile kısıtlı JSON kullanarak tek adımlı asistan kararı üretir.

    `tool_names` verilirse model yalnızca gerçek araç adlarından birini (veya 'none') seçebilir;
    var olmayan araç uydurması matematiksel olarak engellenir.
    Dönüş: `{"tool": "...", "args": {...}}` veya `{"answer": "..."}` ya da `None`.
    """
    bin_path = binary_path()
    if not bin_path:
        return None
    try:
        cmd = [bin_path, "--mode", "agent"]
        if tool_names:
            cleaned = [str(t).strip() for t in tool_names if str(t).strip()]
            if cleaned:
                cmd.extend(["--tools", ",".join(cleaned)])
        cmd.append(system_prompt)
        r = runner(
            cmd,
            input=transcript,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        obj = json.loads(r.stdout.strip())
        if not isinstance(obj, dict):
            return None
        kind = str(obj.get("kind") or "").strip().lower()
        if kind == "tool":
            tool = str(obj.get("tool") or "").strip()
            if not tool or tool.lower() == "none":
                return None
            args = {}
            ak = str(obj.get("arg_key") or "").strip()
            av = str(obj.get("arg_value") or "").strip()
            if ak and av != "":
                args[ak] = _coerce_arg_value(av)
            return {"tool": tool, "args": args}
        ans = EMOJI_RE.sub("", str(obj.get("answer") or "")).strip()
        if ans:
            return {"answer": ans}
        return None
    except Exception as e:
        log.debug("Foundation Models agent hatası: %s", e)
        return None


def classify_choice(question_text, option_keys, user_reply, timeout=15, runner=subprocess.run):
    """Kullanıcının doğal dil yanıtını kesin seçenek anahtarlarından birine eşler."""
    bin_path = binary_path()
    if not bin_path or not option_keys:
        return None
    keys = [str(k).strip() for k in option_keys if str(k).strip()]
    if not keys:
        return None
    instr = (
        "Kullanıcının yanıtını yalnızca verilen seçenek anahtarlarından birine eşleştir. "
        "Kararsızsa veya hiçbirine uymuyorsa 'none' seç."
    )
    prompt = f"Soru: {question_text}\nSeçenekler: {', '.join(keys)}\nKullanıcı yanıtı: {user_reply}"
    try:
        r = runner(
            [bin_path, "--mode", "classify", "--choices", ",".join(keys), instr],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        obj = json.loads(r.stdout.strip())
        choice = str(obj.get("choice") or "").strip()
        return choice if choice in keys else None
    except Exception as e:
        log.debug("Foundation Models classify hatası: %s", e)
        return None


def _validate_advice_guardrails(advice, telemetry):
    """Küçük cihaz-içi modelin abartılı eylem önerilerini gerçek donanım eşikleriyle sınırlar."""
    action = str(advice.get("recommended_action") or "none").strip().lower()
    if action not in VALID_ADVISOR_ACTIONS:
        action = "none"

    cpu = float(telemetry.get("cpu") or 0.0)
    mem_pct = float(telemetry.get("mem_pct") or 0.0)
    swap_gb = float(telemetry.get("swap_gb") or 0.0)
    pressure = int(telemetry.get("pressure") or 1)
    peak_c = telemetry.get("peak_c")
    ssd_c = telemetry.get("ssd_c")
    disk_free_pct = float(telemetry.get("disk_free_pct") if telemetry.get("disk_free_pct") is not None else 50.0)
    llm_gb = float(telemetry.get("llm_gb") or 0.0)
    fan_mode = str(telemetry.get("fan_mode") or "auto_silent")

    hot_soc = peak_c is not None and float(peak_c) >= 68.0
    hot_ssd = ssd_c is not None and float(ssd_c) >= 56.0
    cool_all = (peak_c is None or float(peak_c) <= 50.0) and (ssd_c is None or float(ssd_c) <= 48.0)

    # Güvenlik korkulukları: sakin, serin, ferah bir makinede gereksiz müdahale yapma!
    if action == "fan_boost" and not (hot_soc or hot_ssd):
        action = "none"
    elif action == "fan_silent" and (not cool_all or fan_mode == "auto_silent"):
        action = "none"
    elif action == "unload_llm" and not (llm_gb >= 2.0 and (pressure >= 2 or mem_pct >= 75.0 or swap_gb >= 2.0)):
        action = "none"
    elif action == "clean_cache" and disk_free_pct >= 18.0:
        action = "none"
    elif action == "optimize" and (pressure < 2 and swap_gb < 2.0 and cpu < 65.0 and mem_pct < 80.0):
        action = "none"

    # Sağlık puanını 0..100 arasında tut ve gerçekten sağlıklı makinede yapay düşük puanı düzelt:
    try:
        score = int(advice.get("health_score", 90))
    except (TypeError, ValueError):
        score = 90
    score = max(0, min(100, score))
    if pressure == 1 and swap_gb < 1.0 and mem_pct < 75.0 and cool_all and disk_free_pct >= 20.0:
        score = max(score, 88)

    return {
        "health_score": score,
        "summary": EMOJI_RE.sub("", str(advice.get("summary") or "")).strip(),
        "recommended_action": action,
        "reason": EMOJI_RE.sub("", str(advice.get("reason") or "")).strip(),
        "tip": EMOJI_RE.sub("", str(advice.get("tip") or "")).strip(),
        "model": "Apple Foundation Models (On-Device)",
    }


def _deterministic_fallback_advice(telemetry):
    """Apple Foundation Models geçici olarak erişilemezse deterministik analiz döner."""
    mem_pct = float(telemetry.get("mem_pct") or 0.0)
    swap_gb = float(telemetry.get("swap_gb") or 0.0)
    pressure = int(telemetry.get("pressure") or 1)
    peak_c = telemetry.get("peak_c")
    ssd_c = telemetry.get("ssd_c")
    disk_free_pct = float(telemetry.get("disk_free_pct") if telemetry.get("disk_free_pct") is not None else 50.0)
    llm_gb = float(telemetry.get("llm_gb") or 0.0)

    score = 96
    action = "none"
    reasons = []
    if peak_c is not None and float(peak_c) >= 72.0:
        score -= 18
        action = "fan_boost"
        reasons.append(f"işlemci/GPU sıcaklığı {peak_c}°C")
    if ssd_c is not None and float(ssd_c) >= 58.0:
        score -= 15
        action = "fan_boost"
        reasons.append(f"NVMe SSD sıcaklığı {ssd_c}°C")
    if pressure >= 2 or swap_gb >= 3.0:
        score -= 20
        action = "unload_llm" if llm_gb >= 2.0 else "optimize"
        reasons.append(f"bellek baskısı (seviye {pressure}, takas {swap_gb:.1f} GB)")
    if disk_free_pct < 15.0:
        score -= 15
        if action == "none":
            action = "clean_cache"
        reasons.append(f"boş disk alanı düşük (%{disk_free_pct:.0f})")

    score = max(10, min(100, score))
    if reasons:
        summary = "Dikkat gerektiren durum: " + ", ".join(reasons) + "."
        tip = "Donanım ömrünü korumak için önerilen eylemi uygulayın."
    else:
        summary = f"Sistem serin, bellek ferah (%{mem_pct:.0f}, takas {swap_gb:.1f} GB) ve donanım ömrü açısından ideal durumda."
        tip = "Mevcut ayarlar performans ve SSD ömrü için en iyi dengede."
    return {
        "health_score": score,
        "summary": summary,
        "recommended_action": action,
        "reason": "; ".join(reasons) if reasons else "Tüm sensörler güvenli aralıkta.",
        "tip": tip,
        "model": "Deterministik Telemetri (yedek)",
    }


def advise_system(telemetry, timeout=25, runner=subprocess.run):
    """Cihaz üzerindeki Apple Foundation Modeli ile performans ve donanım ömrü analizi yapar."""
    instructions = (
        "Sen Mek'sin: Apple Silicon Mac Studio'nun performans ve donanım ömrü mühendisisin. "
        "Verilen gerçek telemetriyi değerlendir ve kısa, net, doğru Türkçe yanıt üret. "
        "Kurallar:\n"
        "- CPU/GPU < 68°C, NVMe SSD < 56°C, RAM < %75, Takas < 2 GB, Bellek Baskısı = 1 ve Boş Disk >= %18 ise "
        "sistem SAĞLIKLIDIR: health_score 90-100 arası ve recommended_action kesinlikle 'none' olmalı.\n"
        "- Yalnızca CPU/GPU >= 70°C veya SSD >= 58°C ise 'fan_boost' öner.\n"
        "- Yalnızca Bellek Baskısı >= 2 veya Takas >= 2 GB iken LLM RAM >= 2 GB ise 'unload_llm', yoksa 'optimize' öner.\n"
        "- Yalnızca Boş Disk < %18 ise 'clean_cache' öner.\n"
        "- Sayı uydurma; yalnızca verilen telemetriyi yorumla."
    )
    prompt = (
        f"Profil: {telemetry.get('profile', 'normal')}, Öndeki uygulama: {telemetry.get('front_app') or 'yok'}\n"
        f"CPU Kullanımı: %{telemetry.get('cpu_usage_pct', telemetry.get('cpu', 0))}, "
        f"GPU Kullanımı: %{telemetry.get('gpu_usage_pct', telemetry.get('gpu', 0))}\n"
        f"RAM Kullanımı: %{telemetry.get('ram_usage_pct', telemetry.get('mem_pct', 0))}, "
        f"Takas (Swap): {telemetry.get('swap_gb', 0)} GB, "
        f"Bellek Baskı Seviyesi: {telemetry.get('memory_pressure_level', telemetry.get('pressure', 1))}\n"
        f"Sıcaklıklar -> CPU: {telemetry.get('cpu_temp_c', telemetry.get('peak_c'))}°C, "
        f"GPU: {telemetry.get('gpu_temp_c')}°C, NVMe SSD: {telemetry.get('nvme_ssd_temp_c', telemetry.get('ssd_c'))}°C, "
        f"PSU: {telemetry.get('psu_temp_c')}°C\n"
        f"Fanlar: {telemetry.get('fan0_rpm')}/{telemetry.get('fan1_rpm')} RPM (Kip: {telemetry.get('fan_mode')})\n"
        f"Boş Disk: {telemetry.get('disk_free_gb')} GB (%{telemetry.get('disk_free_pct')}), "
        f"Ultra-Scratch RAM-Disk: {telemetry.get('ultra_scratch_used_mb', 0)} MB, "
        f"Yerel LLM RAM: {telemetry.get('local_llm_ram_gb', telemetry.get('llm_gb', 0))} GB"
    )
    bin_path = binary_path()
    if bin_path:
        try:
            r = runner(
                [bin_path, "--mode", "advisor", instructions],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if r.returncode == 0 and (r.stdout or "").strip():
                raw = json.loads(r.stdout.strip())
                if isinstance(raw, dict):
                    return _validate_advice_guardrails(raw, telemetry)
        except Exception as e:
            log.debug("Foundation Models advisor hatası: %s", e)

    return _deterministic_fallback_advice(telemetry)


def format_advice(advice, applied_note=""):
    """Analiz sonucunu okunabilir Türkçe metne çevirir."""
    act_tr = {
        "none": "Müdahale gerekmiyor (sistem ideal dengede)",
        "optimize": "Performans optimizasyonu (Tier-0 P-Core & arka plan yalıtımı)",
        "fan_boost": "Termal ön-soğutma (fan devrini artırma)",
        "fan_silent": "Akıllı Sessiz Fan (1100 RPM)",
        "unload_llm": "Boştaki Ollama modellerini bellekten indirme",
        "clean_cache": "Akıllı önbellek temizliği",
    }.get(advice.get("recommended_action", "none"), advice.get("recommended_action", "none"))

    lines = [
        f"Apple Foundation Models Analizi — Sağlık Puanı: {advice['health_score']}/100 ({advice.get('model', '')})",
        f"Özet: {advice['summary']}",
        f"Önerilen Eylem: {act_tr}",
    ]
    if advice.get("tip"):
        lines.append(f"Öneri: {advice['tip']}")
    if applied_note:
        lines.append(f"Uygulanan: {applied_note}")
    return "\n".join(lines)
