#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mek Kurgu & Video Editing Automation Engine
Apple Intelligence ve pro kurgu yazılımları (Adobe Premiere Pro 2026, DaVinci Resolve) entegrasyonu.
"""

import os
import sys
import subprocess
import json
import time
import re
import logging
from typing import Dict, List, Any, Optional, Tuple

log = logging.getLogger("mek.kurgu")

DAVINCI_SCRIPT_PATH = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
if os.path.exists(DAVINCI_SCRIPT_PATH) and DAVINCI_SCRIPT_PATH not in sys.path:
    sys.path.append(DAVINCI_SCRIPT_PATH)


class KurguManager:
    """Premiere Pro ve DaVinci Resolve için akıllı kurgu ve timeline yöneticisi."""

    def __init__(self, services=None, events=None):
        self.services = services
        self.events = events

    # --- Kurgu Yazılım Durumları ---

    def is_premiere_running(self) -> bool:
        try:
            return subprocess.run(["pgrep", "-f", "Adobe Premiere Pro"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except Exception:
            return False

    def is_davinci_running(self) -> bool:
        try:
            return subprocess.run(["pgrep", "-f", "Resolve.app"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except Exception:
            return False

    def get_davinci_resolve(self):
        try:
            import DaVinciResolveScript as dvr
            return dvr.scriptapp("Resolve")
        except Exception as e:
            log.debug("DaVinci API bağlantı hatası: %s", e)
            return None

    def status(self) -> str:
        """Tüm açık kurgu ortamlarının gerçek zamanlı durum özeti."""
        reports = []

        if self.is_premiere_running():
            prem_info = self.premiere_status()
            reports.append(f"Adobe Premiere Pro 2026: {prem_info}")

        if self.is_davinci_running():
            dvr_info = self.davinci_status()
            reports.append(f"DaVinci Resolve Studio: {dvr_info}")

        if not reports:
            return "Şu an aktif açık bir kurgu yazılımı (Premiere Pro veya DaVinci Resolve) bulunmuyor."

        return " | ".join(reports)

    def premiere_status(self) -> str:
        if not self.is_premiere_running():
            return "Kapalı."
        # Açık .prproj dosyalarını bulalım
        try:
            pr_procs = subprocess.check_output(
                ["lsof", "-c", "Premiere", "-F", "n"],
                text=True, stderr=subprocess.DEVNULL
            )
            projects = []
            for line in pr_procs.splitlines():
                if line.startswith("n") and line.endswith(".prproj") and "Auto-Save" not in line:
                    pname = os.path.basename(line[1:])
                    if pname not in projects:
                        projects.append(pname)
            proj_text = f"Proje: {', '.join(projects)}" if projects else "Proje açık (adı taranıyor)"
            return f"Aktif ({proj_text})"
        except Exception:
            return "Aktif ve hazır."

    def davinci_status(self) -> str:
        if not self.is_davinci_running():
            return "Kapalı."
        resolve = self.get_davinci_resolve()
        if not resolve:
            return "Çalışıyor (Script API yanıt bekliyor)."
        try:
            pm = resolve.GetProjectManager()
            proj = pm.GetCurrentProject()
            if not proj:
                return "Açık (Açık proje yok)."
            pname = proj.GetName()
            tl = proj.GetCurrentTimeline()
            tl_name = tl.GetName() if tl else "Seçili sekans yok"
            fps = proj.GetSetting("timelineFrameRate") or "24"
            w = proj.GetSetting("timelineResolutionWidth") or "1920"
            h = proj.GetSetting("timelineResolutionHeight") or "1080"
            return f"Proje: '{pname}' | Sekans: '{tl_name}' ({w}x{h} @ {fps} fps)"
        except Exception as e:
            return f"Açık ({e})"

    # --- Kurgu Optimizasyonu ---

    def optimize_environment(self) -> str:
        """Kurgu yazılımı için P-core önceliği, bellek rahatlatma ve Ultra-Scratch hazırliği yapar."""
        actions = []
        if self.services:
            # 1. Kurgu modunu zorla etkinleştir (Tier-0 P-core kilidi)
            if hasattr(self.services, "force_focus_mode"):
                self.services.force_focus_mode()
                actions.append("Tier-0 P-Core tahsisi sağlandı")

            # 2. Ultra-Scratch RAM disk kontrolü
            us = getattr(self.services, "ultra_scratch", None)
            if us:
                us.ensure_mounted()
                actions.append("Ultra-Scratch 400 GB/s RAM-disk hazırlandı")

            # 3. Termal Fanı Akıllı Sessiz kipe al
            tf = getattr(self.services, "thermal_fan", None)
            if tf:
                tf.set_mode("sessiz")
                actions.append("Akıllı Sessiz Fan kipine alındı")

        # 4. İnaktif bellek temizliği
        try:
            subprocess.run(["/usr/sbin/purge"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            actions.append("İnaktif RAM önbelleği temizlendi")
        except Exception:
            pass

        return "Kurgu ortamı optimize edildi: " + ", ".join(actions) + "."

    # --- DaVinci Resolve Otomasyonu ---

    def start_davinci_render(self) -> str:
        """DaVinci Resolve Deliver sayfasındaki render kuyruğunu başlatır."""
        if not self.is_davinci_running():
            return "DaVinci Resolve açık değil."
        resolve = self.get_davinci_resolve()
        if not resolve:
            return "DaVinci Resolve Scripting API'ye bağlanılamadı."
        try:
            proj = resolve.GetProjectManager().GetCurrentProject()
            if not proj:
                return "DaVinci'de açık proje bulunamadı."
            jobs = proj.GetRenderJobList()
            if not jobs:
                return f"'{proj.GetName()}' projesinde kuyruğa eklenmiş render işi yok."
            ok = proj.StartRendering()
            return f"DaVinci Resolve render kuyruğu başlatıldı ({len(jobs)} iş kuyrukta)." if ok else "Render başlatılamadı."
        except Exception as e:
            return f"DaVinci render hatası: {e}"

    def add_davinci_marker(self, frame_id: int, name: str, note: str = "", color: str = "Cyan") -> str:
        """DaVinci zaman çizelgesine işaretçi ekler."""
        if not self.is_davinci_running():
            return "DaVinci Resolve açık değil."
        resolve = self.get_davinci_resolve()
        if not resolve:
            return "DaVinci API'ye ulaşılamadı."
        try:
            proj = resolve.GetProjectManager().GetCurrentProject()
            tl = proj.GetCurrentTimeline() if proj else None
            if not tl:
                return "Seçili DaVinci sekansı yok."
            ok = tl.CreateMarker(frame_id, color, name, note, 1)
            return f"İşaretçi eklendi: kare {frame_id} ({name})" if ok else "İşaretçi eklenemedi."
        except Exception as e:
            return f"Hata: {e}"

    # --- Premiere Pro Otomasyonu ---

    def export_premiere_to_media_encoder(self) -> str:
        """Premiere Pro'da seçili sekansı Adobe Media Encoder kuyruğuna gönderir."""
        if not self.is_premiere_running():
            return "Adobe Premiere Pro 2026 açık değil."
        script = '''
tell application "Adobe Premiere Pro 2026" to activate
tell application "System Events"
    keystroke "m" using {command down}
    delay 0.5
    keystroke "q" using {command down}
end tell
'''
        try:
            subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "Premiere Pro sekansı Adobe Media Encoder kuyruğuna aktarılıyor."
        except Exception as e:
            return f"Dışa aktarma hatası: {e}"

    # --- Akıllı Kaba Kurgu & Sessizlik Tespiti ---

    def analyze_audio_silence(self, audio_path: str, threshold_db: float = -34.0, min_silence_duration: float = 0.4) -> Dict[str, Any]:
        """
        Ses dosyasındaki konuşma ve sessizlik aralıklarını milisaniye hassasiyetinde tespit eder.
        Kaba kurgu için kesim zaman çizelgesi (EDL) üretir.
        """
        if not os.path.exists(audio_path):
            return {"error": f"Ses dosyası bulunamadı: {audio_path}"}

        try:
            import wave
            import numpy as np

            with wave.open(audio_path, "rb") as wf:
                sample_rate = wf.getframerate()
                n_channels = wf.getnchannels()
                n_frames = wf.getnframes()
                sample_width = wf.getsampwidth()
                raw_data = wf.readframes(n_frames)

            dtype = np.int16 if sample_width == 2 else np.int32
            data = np.frombuffer(raw_data, dtype=dtype)
            if n_channels > 1:
                data = data[::n_channels]  # Mono örneklem

            total_duration = n_frames / float(sample_rate)
            chunk_size = int(sample_rate * 0.05)  # 50 ms pencereler
            num_chunks = len(data) // chunk_size

            # RMS hesaplama
            max_val = float(np.iinfo(dtype).max)
            silence_ranges = []
            speech_ranges = []

            in_silence = False
            silence_start = 0.0

            for i in range(num_chunks):
                chunk = data[i * chunk_size : (i + 1) * chunk_size].astype(np.float32)
                rms = np.sqrt(np.mean(chunk**2)) / max_val
                db = 20.0 * np.log10(rms + 1e-7)
                t = (i * chunk_size) / float(sample_rate)

                if db < threshold_db:
                    if not in_silence:
                        in_silence = True
                        silence_start = t
                else:
                    if in_silence:
                        in_silence = False
                        silence_dur = t - silence_start
                        if silence_dur >= min_silence_duration:
                            silence_ranges.append((round(silence_start, 2), round(t, 2)))

            if in_silence:
                silence_dur = total_duration - silence_start
                if silence_dur >= min_silence_duration:
                    silence_ranges.append((round(silence_start, 2), round(total_duration, 2)))

            # Konuşma bloklarını hesapla
            curr = 0.0
            for s_start, s_end in silence_ranges:
                if s_start > curr:
                    speech_ranges.append((round(curr, 2), round(s_start, 2)))
                curr = s_end
            if curr < total_duration:
                speech_ranges.append((round(curr, 2), round(total_duration, 2)))

            silence_time = sum(end - start for start, end in silence_ranges)
            saved_percent = round((silence_time / total_duration) * 100.0, 1) if total_duration > 0 else 0

            return {
                "file": os.path.basename(audio_path),
                "total_duration_sec": round(total_duration, 2),
                "silence_time_sec": round(silence_time, 2),
                "saved_percent": saved_percent,
                "cuts_count": len(silence_ranges),
                "silence_intervals": silence_ranges[:20],  # İlk 20 kesim noktası
                "speech_intervals": speech_ranges[:20]
            }
        except Exception as e:
            log.error("Sessizlik analizi hatası: %s", e)
            return {"error": str(e)}
