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
        from vision_color import VisionColorEngine
        from rough_cut import RoughCutEngine
        from multicam_director import MulticamDirector
        from viral_hook import ViralHookSynthesizer
        from premiere_gateway import PremiereGateway
        self.vision = VisionColorEngine(kurgu_mgr=self)
        self.rough_cut = RoughCutEngine(kurgu_mgr=self)
        self.multicam = MulticamDirector(kurgu_mgr=self)
        self.viral_hook = ViralHookSynthesizer(kurgu_mgr=self)
        self.gateway = PremiereGateway(kurgu_mgr=self)

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

    # --- Sinematik Renk, Efekt & Otonom Kaba Kurgu ---

    def apply_film_color(self, film_name: str) -> str:
        res = self.vision.apply_film_grade(film_name)
        davinci_msg = " DaVinci Resolve klibine uygulandı." if res.get("applied_to_davinci") else ""
        return f"{res['film']} film rengi 3D LUT olarak üretildi ({res['lut_name']}). Premiere ve DaVinci'ye kaydedildi.{davinci_msg}"

    def clone_image_color(self, image_path: str, look_name: str = "Referans") -> str:
        res = self.vision.clone_color_from_image(image_path, look_name)
        if "error" in res:
            return f"Hata: {res['error']}"
        davinci_msg = " DaVinci klibine atandı." if res.get("applied_to_davinci") else ""
        return f"'{res['reference_image']}' görselinin renk dağılımı 3D LUT olarak çıkarıldı ({res['lut_name']}).{davinci_msg}"

    def synthesize_effect(self, prompt: str) -> str:
        res = self.vision.synthesize_effect(prompt)
        fx = res.get("optical_fx", {})
        fx_desc = f"Bloom: %{fx.get('bloom', 0)*100:.0f}, Gren: %{fx.get('film_grain', 0)*100:.0f}, Vinyet: %{fx.get('vignette', 0)*100:.0f}"
        davinci_msg = " DaVinci Resolve'a uygulandı." if res.get("applied_to_davinci") else ""
        return f"Efekt üretildi ({res['name']}): {res['description']} ({fx_desc}). LUT ve parametreler kaydedildi.{davinci_msg}"

    def build_rough_cut(self, target_path: str, music_path: Optional[str] = None) -> str:
        res = self.rough_cut.process_file_or_folder(target_path, music_path)
        if "error" in res:
            return f"Kaba kurgu hatası: {res['error']}"
        return res["message"]

    # --- Otonom Çoklu Kamera Yönetmeni (Multicam Director) ---

    def direct_multicam(self, sources: Any, style_prompt: str = "dinamik podcast") -> str:
        if isinstance(sources, str):
            sources_list = [s.strip() for s in sources.split(",") if s.strip()]
        else:
            sources_list = list(sources)
        res = self.multicam.direct_multicam(sources_list, style_prompt)
        if "error" in res:
            return f"Çoklu kamera kurgu hatası: {res['error']}"
        return res["message"]

    # --- Otonom Viral Hook Sentezleyici ---

    def extract_viral_hook(self, video_path: str, transcript: Optional[str] = None, gemini_key: Optional[str] = None) -> str:
        if gemini_key:
            self.viral_hook.gemini_api_key = gemini_key
        res = self.viral_hook.synthesize_viral_hook(video_path, transcript)
        if "error" in res:
            return f"Viral hook hatası: {res['error']}"
        return res["message"]

    # --- Premiere Pro Canlı Timeline Müdahaleleri ---

    def set_gemini_key(self, api_key: str) -> str:
        """Gemini API anahtarını kaydeder ve viral hook motorunu günceller."""
        k = api_key.strip()
        self.viral_hook.gemini_api_key = k
        try:
            cfg_path = os.path.join(os.path.expanduser("~/.jarvis"), "config.json")
            data = {}
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    pass
            data["gemini_api_key"] = k
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return "Gemini API anahtarı başarıyla kaydedildi. Yapay zeka destekli viral kanca motoru aktif."
        except Exception as e:
            return f"API anahtarı hafızaya alındı fakat dosyaya yazılamadı: {e}"

    def apply_viral_hook_in_premiere(self, title: Optional[str] = None, style: str = "punch_zoom", gemini_key: Optional[str] = None) -> str:
        """Premiere Pro açık timeline'ının ilk 3 saniyesine (00:00:00 - 00:03:00) anında viral hook efektleri uygular."""
        if gemini_key:
            self.viral_hook.gemini_api_key = gemini_key

        if not self.gateway.is_connected():
            return ("Premiere Pro bağlantısı bekleniyor. Lütfen Premiere Pro'da Window > Extensions > "
                    "Alptuguide Studio panelinin açık olduğundan emin olun.")

        # Eğer stil ai_smart ise veya başlık boş ve Gemini anahtarı varsa AI ile kanca üret
        hook_title = title
        chosen_style = style
        if (style == "ai_smart" or not hook_title) and self.viral_hook.gemini_api_key:
            try:
                # Timeline sekans adını al
                seq_info = self.gateway.send_command("analyze_timeline", {})
                seq_name = seq_info.get("name", "Video")
                ai_prompt = (
                    f"Bir video kurgusu için ilk 3 saniyede izleyicinin kaydırmasını önleyecek (retention hook), "
                    f"Instagram Reels ve TikTok'ta viral olan çarpıcı bir kanca başlığı ve kurgu stili öner. "
                    f"Video sekans adı / konusu: '{seq_name}'. "
                    f"Sadece şu JSON formatında yanıt ver: {{\"title\": \"⚡ BAŞLIK\", \"style\": \"punch_zoom\" veya \"hormozi_title\" veya \"strobe_flash\" veya \"glitch_shock\" veya \"jump_snap\"}}"
                )
                res_ai = self.viral_hook.analyze_with_gemini(ai_prompt)
                if res_ai and len(res_ai) > 0 and isinstance(res_ai[0], dict):
                    hook_title = res_ai[0].get("title", hook_title)
                    chosen_style = res_ai[0].get("style", "punch_zoom")
            except Exception:
                pass

        res = self.gateway.send_command("apply_viral_hook", {"title": hook_title, "style": chosen_style})
        if not res.get("ok"):
            return f"Premiere Pro hook hatası: {res.get('message', 'Bilinmeyen hata')}"
        return res.get("message", "Timeline'ın ilk 3 saniyesine viral hook efektleri uygulandı.")

    def analyze_premiere_timeline(self) -> Dict[str, Any]:
        """Açık Premiere Pro timeline'ını anlık olarak sorgular ve analiz eder."""
        if not self.gateway.is_connected():
            return {"error": "Premiere Pro bağlantısı yok. Lütfen Alptuguide Studio panelini açın."}
        return self.gateway.send_command("analyze_timeline", {})

    def auto_edit_with_hook(self, target_path: str, style_prompt: str = "dinamik podcast") -> str:
        """Önce çoklu kamera / kaba kurguyu yapar, ardından en vurucu hook'u başa monte eder."""
        if os.path.isdir(target_path):
            mc_res = self.multicam.direct_multicam([target_path], style_prompt)
            if "error" in mc_res:
                return f"Hata: {mc_res['error']}"
            return f"Çoklu kamera kurgusu tamamlandı ({mc_res['cuts_count']} kesim). CMX 3600 EDL ve FCPXML hazır: {mc_res['fcpxml_path']}"
        else:
            vh_res = self.viral_hook.synthesize_viral_hook(target_path)
            if "error" in vh_res:
                return f"Hata: {vh_res['error']}"
            return vh_res["message"]
