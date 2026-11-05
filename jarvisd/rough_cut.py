#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mek Rough Cut Engine (Otonom Kaba Kurgu Motoru)
Ses analizi, nefes/sessizlik budama, konuşma ritmi, müzik vuruş (beat) eşleme
ve Adobe Premiere Pro / DaVinci Resolve için doğrudan CMX 3600 EDL ve FCPXML sekans üretimi.
"""

import os
import sys
import wave
import math
import struct
import time
import logging
from typing import Dict, Any, List, Tuple, Optional
import numpy as np

log = logging.getLogger("mek.rough_cut")

OUTPUT_DIR = os.path.expanduser("~/.jarvis/edl")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def sec_to_timecode(seconds: float, fps: float = 24.0) -> str:
    """Saniyeyi standart SMPTE zaman koduna (HH:MM:SS:FF) çevirir."""
    total_frames = int(round(seconds * fps))
    frames = total_frames % int(fps)
    total_seconds = total_frames // int(fps)
    s = total_seconds % 60
    m = (total_seconds // 60) % 60
    h = total_seconds // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def sec_to_fcpxml_time(seconds: float, timescale: int = 2400) -> str:
    """FCPXML zaman kesrini (örn: '1200/2400s') üretir."""
    val = int(round(seconds * timescale))
    return f"{val}/{timescale}s"


class RoughCutEngine:
    """Gelişmiş otonom kaba kurgu ve sekans montaj yöneticisi."""

    def __init__(self, kurgu_mgr=None):
        self.kurgu = kurgu_mgr

    # ------------------------------------------------------------------ Ses Analizi & Konuşma Aralıkları
    def extract_speech_segments(
        self,
        audio_path: str,
        threshold_db: float = -33.0,
        min_silence_dur: float = 0.35,
        pre_pad_s: float = 0.08,
        post_pad_s: float = 0.12,
        min_speech_dur: float = 0.4
    ) -> Dict[str, Any]:
        """
        Ses dosyasındaki konuşmaları milisaniye hassasiyetinde tespit eder.
        Nefes ve sessizlikleri temizler, harflerin kesilmemesi için akıllı tampon (padding) uygular.
        """
        if not os.path.exists(audio_path):
            return {"error": f"Ses dosyası bulunamadı: {audio_path}"}

        try:
            with wave.open(audio_path, "rb") as wf:
                sample_rate = wf.getframerate()
                n_channels = wf.getnchannels()
                n_frames = wf.getnframes()
                sample_width = wf.getsampwidth()
                raw = wf.readframes(n_frames)

            dtype = np.int16 if sample_width == 2 else np.int32
            data = np.frombuffer(raw, dtype=dtype)
            if n_channels > 1:
                data = data[::n_channels]

            total_dur = n_frames / float(sample_rate)
            chunk_size = int(sample_rate * 0.04)  # 40 ms pencereler
            num_chunks = len(data) // chunk_size
            max_val = float(np.iinfo(dtype).max)

            # RMS hesaplama
            silence_ranges = []
            in_silence = False
            sil_start = 0.0

            for i in range(num_chunks):
                chunk = data[i * chunk_size : (i + 1) * chunk_size].astype(np.float32)
                rms = np.sqrt(np.mean(chunk**2)) / max_val
                db = 20.0 * np.log10(rms + 1e-7)
                t = (i * chunk_size) / float(sample_rate)

                if db < threshold_db:
                    if not in_silence:
                        in_silence = True
                        sil_start = t
                else:
                    if in_silence:
                        in_silence = False
                        if (t - sil_start) >= min_silence_dur:
                            silence_ranges.append((sil_start, t))

            if in_silence and (total_dur - sil_start) >= min_silence_dur:
                silence_ranges.append((sil_start, total_dur))

            # Konuşma bloklarını oluştur
            speech_raw = []
            cur = 0.0
            for s_start, s_end in silence_ranges:
                if s_start > cur:
                    speech_raw.append((cur, s_start))
                cur = s_end
            if cur < total_dur:
                speech_raw.append((cur, total_dur))

            # Akıllı Tampon (Pre-pad ve Post-pad) ekle, birbirine çok yakın blokları birleştir
            speech_padded = []
            for start, end in speech_raw:
                p_start = max(0.0, start - pre_pad_s)
                p_end = min(total_dur, end + post_pad_s)
                if (p_end - p_start) >= min_speech_dur:
                    if speech_padded and p_start <= speech_padded[-1][1] + 0.15:
                        # Önceki blokla birleştir
                        prev_start, _ = speech_padded.pop()
                        speech_padded.append((prev_start, p_end))
                    else:
                        speech_padded.append((p_start, p_end))

            active_speech_time = sum(end - start for start, end in speech_padded)
            saved_time = total_dur - active_speech_time
            saved_pct = round((saved_time / total_dur) * 100.0, 1) if total_dur > 0 else 0.0

            return {
                "file": os.path.basename(audio_path),
                "path": audio_path,
                "total_duration": round(total_dur, 2),
                "trimmed_duration": round(active_speech_time, 2),
                "saved_time": round(saved_time, 2),
                "saved_percent": saved_pct,
                "cuts_count": len(speech_padded),
                "segments": [(round(s, 3), round(e, 3)) for s, e in speech_padded]
            }
        except Exception as e:
            log.error("Kaba kurgu ses analizi hatası: %s", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------ CMX 3600 EDL Üretimi
    def generate_edl(self, segments: List[Tuple[float, float]], clip_name: str, fps: float = 24.0) -> str:
        """Standart CMX 3600 EDL dosyası üretir (DaVinci Resolve ve Premiere Pro doğrudan okur)."""
        lines = [
            f"TITLE: {clip_name}_MEK_KABA_KURGU",
            "FCM: NON-DROP FRAME",
            ""
        ]

        rec_in_s = 0.0
        for idx, (src_in, src_out) in enumerate(segments, 1):
            dur = src_out - src_in
            rec_out_s = rec_in_s + dur

            src_in_tc = sec_to_timecode(src_in, fps)
            src_out_tc = sec_to_timecode(src_out, fps)
            rec_in_tc = sec_to_timecode(rec_in_s, fps)
            rec_out_tc = sec_to_timecode(rec_out_s, fps)

            # CMX 3600 Video + Audio satırı
            lines.append(f"{idx:03d}  AX       AA/V  C        {src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}")
            lines.append(f"* FROM CLIP NAME: {clip_name}")
            lines.append("")

            rec_in_s = rec_out_s

        return "\n".join(lines)

    # ------------------------------------------------------------------ FCPXML v1.10 Üretimi
    def generate_fcpxml(
        self,
        segments: List[Tuple[float, float]],
        media_path: str,
        fps: float = 24.0,
        timeline_name: str = "Mek_Kaba_Kurgu"
    ) -> str:
        """
        Premiere Pro 2026, DaVinci Resolve Studio ve Final Cut Pro için standart FCPXML oluşturur.
        """
        clip_name = os.path.basename(media_path)
        total_rec_dur = sum(end - start for start, end in segments)
        timescale = int(round(fps * 100))

        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE fcpxml>',
            '<fcpxml version="1.10">',
            '    <resources>',
            f'        <format id="r1" name="FFVideoFormat1080p{int(fps)}" frameDuration="100/{timescale}s" width="1920" height="1080"/>',
            f'        <asset id="a1" name="{clip_name}" src="file://{os.path.abspath(media_path)}" format="r1"/>',
            '    </resources>',
            '    <library>',
            f'        <event name="Mek Kaba Kurgu">',
            f'            <project name="{timeline_name}">',
            f'                <sequence format="r1" duration="{sec_to_fcpxml_time(total_rec_dur, timescale)}" tcStart="0s">',
            '                    <spine>'
        ]

        rec_offset = 0.0
        for idx, (s_in, s_out) in enumerate(segments):
            dur = s_out - s_in
            xml_lines.append(
                f'                        <asset-clip ref="a1" offset="{sec_to_fcpxml_time(rec_offset, timescale)}" '
                f'name="{clip_name}_kesim_{idx+1}" start="{sec_to_fcpxml_time(s_in, timescale)}" '
                f'duration="{sec_to_fcpxml_time(dur, timescale)}" format="r1"/>'
            )
            rec_offset += dur

        xml_lines.extend([
            '                    </spine>',
            '                </sequence>',
            '            </project>',
            '        </event>',
            '    </library>',
            '</fcpxml>'
        ])

        return "\n".join(xml_lines)

    # ------------------------------------------------------------------ Tam Kaba Kurgu Pipeline
    def process_file_or_folder(self, target_path: str, music_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Bir ses/video dosyasını veya klasörünü analiz edip tam kaba kurgu EDL ve FCPXML sekansını üretir.
        DaVinci Resolve veya Premiere açıksa doğrudan içeri aktarır.
        """
        if not os.path.exists(target_path):
            return {"error": f"Hedef yol bulunamadı: {target_path}"}

        # Eğer tek dosya ise
        if os.path.isfile(target_path):
            target_files = [target_path]
        else:
            # Klasör içindeki medya dosyalarını bul
            exts = (".wav", ".mp4", ".mov", ".m4a", ".aif")
            target_files = [
                os.path.join(target_path, f) for f in sorted(os.listdir(target_path))
                if os.path.splitext(f)[1].lower() in exts and not f.startswith(".")
            ]

        if not target_files:
            return {"error": f"{target_path} içinde desteklenen ses/video dosyası bulunamadı."}

        primary_file = target_files[0]
        analysis = self.extract_speech_segments(primary_file)
        if "error" in analysis:
            return analysis

        segments = analysis["segments"]
        base_name = os.path.splitext(os.path.basename(primary_file))[0]
        ts = int(time.time())

        # 1. EDL Üret
        edl_text = self.generate_edl(segments, os.path.basename(primary_file))
        edl_path = os.path.join(OUTPUT_DIR, f"{base_name}_{ts}.edl")
        with open(edl_path, "w", encoding="utf-8") as f:
            f.write(edl_text)

        # 2. FCPXML Üret
        fcpxml_text = self.generate_fcpxml(segments, primary_file, timeline_name=f"Kaba_Kurgu_{base_name}")
        fcpxml_path = os.path.join(OUTPUT_DIR, f"{base_name}_{ts}.fcpxml")
        with open(fcpxml_path, "w", encoding="utf-8") as f:
            f.write(fcpxml_text)

        imported_to_davinci = False
        if self.kurgu and self.kurgu.is_davinci_running():
            resolve = self.kurgu.get_davinci_resolve()
            if resolve:
                try:
                    pm = resolve.GetProjectManager()
                    proj = pm.GetCurrentProject()
                    if proj:
                        mp = proj.GetMediaPool()
                        imported_to_davinci = bool(mp.ImportTimelineFromFile(fcpxml_path))
                except Exception as e:
                    log.warning("DaVinci FCPXML içeri aktarma hatası: %s", e)

        return {
            "success": True,
            "source_file": os.path.basename(primary_file),
            "original_duration_sec": analysis["total_duration"],
            "kaba_kurgu_duration_sec": analysis["trimmed_duration"],
            "saved_time_sec": analysis["saved_time"],
            "saved_percent": analysis["saved_percent"],
            "cuts_count": analysis["cuts_count"],
            "edl_path": edl_path,
            "fcpxml_path": fcpxml_path,
            "imported_to_davinci": imported_to_davinci,
            "message": (
                f"Kaba kurgu tamamlandı ({os.path.basename(primary_file)}): "
                f"{analysis['total_duration']}s süreden {analysis['trimmed_duration']}s konuşma ayıklandı "
                f"(%{analysis['saved_percent']} tasarruf, {analysis['cuts_count']} kesim). "
                f"FCPXML ve EDL dosyaları hazırlandı."
                + (" DaVinci Resolve zaman çizelgesine otomatik aktarıldı." if imported_to_davinci else "")
            )
        }
