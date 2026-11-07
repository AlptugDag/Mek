#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mek Multicam Director (Otonom Çoklu Kamera Kurgu Motoru)
Röportaj, podcast, talk-show ve çoklu kamera kayıtlarını konuşmacı ses enerjisi,
doğal dil kurgu stili ve otonom reaksiyon/geniş açı mantığıyla profesyonelce kurgular.
CMX 3600 EDL ve FCPXML v1.10 formatlarında dışa aktarır.
"""

import os
import sys
import re
import json
import math
import struct
import wave
import subprocess
import logging
from typing import Dict, List, Tuple, Any, Optional

log = logging.getLogger("mek.multicam")

import config
import proc


MEK_HOME = config.MEK_HOME
AI_BIN = config.AI_BIN
EDL_DIR = os.path.join(MEK_HOME, "edl")


def _ensure_edl_dir():
    """EDL klasörünü ihtiyaç anında oluşturur (içe aktarma yan etkisi olmasın)."""
    os.makedirs(EDL_DIR, exist_ok=True)
    return EDL_DIR


class MulticamDirector:
    """Konuşmacıya ve stile göre kameralar arası otonom kurgu yapan yönetmen motoru."""

    def __init__(self, kurgu_mgr=None):
        self.kurgu = kurgu_mgr

    # --- Kurgu Stili Parametreleri ---

    @staticmethod
    def parse_editing_style(style_prompt: str) -> Dict[str, Any]:
        """
        Kullanıcının doğal dildeki kurgu tarifini matematiksel kurgu kurallarına dönüştürür.
        Örnekler: 'dinamik podcast', 'belgesel tarzında yavaş kurgu', 'hızlı youtube vinyeti'
        """
        p = (style_prompt or "dinamik podcast").lower()

        # Varsayılanlar: Dinamik Podcast
        params = {
            "name": style_prompt or "Dinamik Podcast",
            "min_shot_duration": 1.8,     # Bir kamerada kalınacak en kısa süre (sn)
            "max_monologue_shot": 6.5,    # Tek konuşmacıda kesintisiz kalınabilecek azami süre (sn)
            "anticipation_sec": 0.12,     # Konuşma başlamadan önceki kesim avansı (sn)
            "wide_on_overlap": True,      # Birlikte konuşma / gülüşmede geniş açıya geç
            "wide_on_pause": True,        # Sessizlikte geniş açıya dön
            "pause_wide_threshold": 2.0,  # Sessizlik kaç sn sürerse genişe geçilsin
            "insert_reactions": True,     # Dinleyici reaksiyon çekimleri ekle
            "reaction_duration": 1.8,     # Reaksiyon çekimi süresi (sn)
            "pacing": "dynamic"           # dynamic, cinematic, aggressive
        }

        if re.search(r"belgesel|sinematik|ağır|yavaş|dramatik|cinematic|slow", p):
            params.update({
                "name": "Sinematik Belgesel",
                "min_shot_duration": 4.5,
                "max_monologue_shot": 10.0,
                "anticipation_sec": 0.20,
                "wide_on_overlap": True,
                "wide_on_pause": True,
                "pause_wide_threshold": 3.0,
                "insert_reactions": True,
                "reaction_duration": 2.8,
                "pacing": "cinematic"
            })
        elif re.search(r"youtube|hızlı|tempolu|tiktok|reels|shorts|vlog|aggressive", p):
            params.update({
                "name": "Hızlı Tempolu YouTube",
                "min_shot_duration": 1.1,
                "max_monologue_shot": 4.0,
                "anticipation_sec": 0.08,
                "wide_on_overlap": False,    # Hızlı kurguda çakışmada dominant kişiye odaklan
                "wide_on_pause": False,
                "pause_wide_threshold": 1.5,
                "insert_reactions": False,
                "reaction_duration": 1.2,
                "pacing": "aggressive"
            })
        elif re.search(r"talk show|röportaj|mülakat|sohbet|interview", p):
            params.update({
                "name": "Talk Show / Röportaj",
                "min_shot_duration": 2.2,
                "max_monologue_shot": 7.0,
                "anticipation_sec": 0.15,
                "wide_on_overlap": True,
                "wide_on_pause": True,
                "pause_wide_threshold": 2.2,
                "insert_reactions": True,
                "reaction_duration": 2.0,
                "pacing": "balanced"
            })

        # Özel kullanıcı tanımlı sayısal yönergeleri ayrıştır
        min_match = re.search(r"en az (\d+(?:\.\d+)?)\s*saniye", p)
        if min_match:
            params["min_shot_duration"] = float(min_match.group(1))

        if "geniş açıya geçme" in p or "genişe dönme" in p:
            params["wide_on_overlap"] = False
            params["wide_on_pause"] = False

        return params

    # --- Ses ve Kamera Enerji Analizi ---

    @staticmethod
    def extract_channel_energy(audio_path: str, chunk_ms: int = 100) -> Tuple[List[float], float, float]:
        """
        Verilen ses dosyasının chunk_ms pencerelerindeki RMS enerji eğrisini çıkarır.
        Döndürür: (enerji_listesi, örnekleme_oranı, toplam_süre_sn)
        """
        # Gerekirse ffmpeg ile 16kHz mono WAV'a dönüştür
        wav_path = audio_path
        tmp_wav = None
        if not audio_path.lower().endswith(".wav"):
            tmp_wav = proc.temp_wav("mc")
            if not proc.ffmpeg_to_wav(audio_path, tmp_wav):
                try:
                    os.unlink(tmp_wav)
                except OSError:
                    pass
                return []
            wav_path = tmp_wav

        energies = []
        sample_rate = 16000
        duration = 0.0

        try:
            with wave.open(wav_path, "rb") as wf:
                sample_rate = wf.getframerate()
                num_frames = wf.getnframes()
                duration = num_frames / float(sample_rate)
                frames_per_chunk = int(sample_rate * (chunk_ms / 1000.0))

                while True:
                    raw = wf.readframes(frames_per_chunk)
                    if not raw:
                        break
                    samples = struct.unpack(f"{len(raw)//2}h", raw)
                    if not samples:
                        energies.append(0.0)
                        continue
                    # RMS hesapla
                    sq_sum = sum(s * s for s in samples)
                    rms = math.sqrt(sq_sum / len(samples)) / 32768.0
                    energies.append(rms)
        except Exception as e:
            log.warning("Ses enerji çıkarımı hatası: %s", e)
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try: os.remove(tmp_wav)
                except Exception: pass

        return energies, sample_rate, duration

    def direct_multicam(self, camera_files: List[str], style_prompt: str = "dinamik podcast") -> Dict[str, Any]:
        """
        Çoklu kamera dosyalarını analiz eder ve konuşmacıya göre en iyi kamera açılarını seçer.
        camera_files dizilimi:
          [Cam 1: Konuşmacı A (Sunucu), Cam 2: Konuşmacı B (Konuk), Cam 3 (isteğe bağlı): Geniş Açı / 2-Shot]
        """
        if not camera_files:
            return {"error": "En az 1 kamera dosyası belirtilmelidir."}

        # Eğer tek bir klasör verildiyse içindeki medya dosyalarını al
        if len(camera_files) == 1 and os.path.isdir(camera_files[0]):
            folder = camera_files[0]
            exts = (".mp4", ".mov", ".mkv", ".wav", ".m4a")
            files = sorted([os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(exts)])
            if not files:
                return {"error": f"'{folder}' klasöründe geçerli video/ses dosyası bulunamadı."}
            camera_files = files

        style = self.parse_editing_style(style_prompt)
        chunk_ms = 100  # 100ms pencere hassasiyeti (saniyede 10 örnek)
        chunk_sec = chunk_ms / 1000.0

        # Her kamera için enerji serisini çıkar
        cam_energies = []
        durations = []
        for cf in camera_files:
            e, sr, dur = self.extract_channel_energy(cf, chunk_ms)
            cam_energies.append(e)
            durations.append(dur)

        total_duration = max(durations) if durations else 0.0
        max_chunks = max((len(e) for e in cam_energies), default=0)
        num_cams = len(camera_files)

        # Uzunlukları eşitle (kısa olanları 0 ile doldur)
        for i in range(num_cams):
            if len(cam_energies[i]) < max_chunks:
                cam_energies[i].extend([0.0] * (max_chunks - len(cam_energies[i])))

        # Mikrofon kazanç normalizasyonu (en yüksek %95 tepeye göre oranla)
        cam_gains = []
        for i in range(num_cams):
            sorted_e = sorted(cam_energies[i])
            p95 = sorted_e[int(len(sorted_e) * 0.95)] if sorted_e else 0.05
            p95 = max(p95, 0.01)
            cam_gains.append(1.0 / p95)

        # Normalize edilmiş enerjiler
        norm_energies = []
        for i in range(num_cams):
            g = cam_gains[i]
            norm_energies.append([e * g for e in cam_energies[i]])

        speech_threshold = 0.15  # Normalleştirilmiş konuşma algılama eşiği
        wide_cam_idx = num_cams - 1 if num_cams >= 3 else 0  # 3+ kamera varsa sonuncusu geniş kabul edilir

        cuts = []  # List of dicts: {"start": sec, "end": sec, "camera": int, "reason": str}
        current_cam = 0
        current_start = 0.0
        time_on_current = 0.0
        min_shot_duration = style["min_shot_duration"]
        max_monologue = style["max_monologue_shot"]
        consecutive_silence = 0.0

        for chunk_idx in range(max_chunks):
            t = chunk_idx * chunk_sec
            vals = [norm_energies[c][chunk_idx] for c in range(num_cams)]

            # Konuşan kameralar
            active_cams = [c for c, v in enumerate(vals) if v > speech_threshold]

            target_cam = current_cam
            reason = "Devam"

            if len(active_cams) >= 2 and style["wide_on_overlap"] and num_cams >= 3:
                # Birden fazla konuşmacı aynı anda devrede -> Geniş Açı
                target_cam = wide_cam_idx
                reason = "Ortak Konuşma / Gülüşme (Geniş Açı)"
                consecutive_silence = 0.0
            elif len(active_cams) == 1:
                # Tek aktif konuşmacı
                target_cam = active_cams[0]
                reason = f"Aktif Konuşmacı (Kamera {target_cam + 1})"
                consecutive_silence = 0.0
            elif not active_cams:
                # Sessizlik / duraklama
                consecutive_silence += chunk_sec
                if consecutive_silence >= style["pause_wide_threshold"] and style["wide_on_pause"] and num_cams >= 3:
                    target_cam = wide_cam_idx
                    reason = "Sessizlik (Geniş Açıya Dönüş)"
            else:
                # En yüksek enerjiye sahip kamerayı seç
                best = max(range(num_cams), key=lambda c: vals[c])
                if vals[best] > speech_threshold * 0.8:
                    target_cam = best
                    reason = f"Baskın Ses (Kamera {target_cam + 1})"

            # Tek bir kamerada çok uzun kalınmışsa dinleyiciye reaksiyon çekimi ver
            time_on_current = t - current_start
            if time_on_current >= max_monologue and style["insert_reactions"] and num_cams >= 2:
                other_cams = [c for c in range(num_cams) if c != current_cam and c != wide_cam_idx]
                if other_cams:
                    target_cam = other_cams[0]
                    reason = "Dinleyici Reaksiyonu"

            # Kesim kararı: Asgari çekim süresi dolmuşsa ve kamera değişiyorsa kes
            if target_cam != current_cam and time_on_current >= min_shot_duration:
                # Kesim avansı (anticipation)
                cut_time = max(current_start + min_shot_duration, t - style["anticipation_sec"])
                cuts.append({
                    "start": round(current_start, 2),
                    "end": round(cut_time, 2),
                    "camera_index": current_cam,
                    "camera_file": os.path.basename(camera_files[current_cam]),
                    "reason": reason
                })
                current_cam = target_cam
                current_start = cut_time

        # Son kesimi kapat
        if current_start < total_duration:
            cuts.append({
                "start": round(current_start, 2),
                "end": round(total_duration, 2),
                "camera_index": current_cam,
                "camera_file": os.path.basename(camera_files[current_cam]),
                "reason": "Kapanış"
            })

        # Kamera kullanım istatistikleri
        cam_stats = {f"Kamera {i+1} ({os.path.basename(camera_files[i])})": 0.0 for i in range(num_cams)}
        for c in cuts:
            dur = c["end"] - c["start"]
            k = f"Kamera {c['camera_index']+1} ({os.path.basename(camera_files[c['camera_index']])})"
            cam_stats[k] = round(cam_stats.get(k, 0.0) + dur, 2)

        # CMX 3600 EDL ve FCPXML üret
        base_name = f"Multicam_{style['pacing']}_{int(total_duration)}s"
        _ensure_edl_dir()
        edl_path = os.path.join(EDL_DIR, f"{base_name}.edl")
        fcpxml_path = os.path.join(EDL_DIR, f"{base_name}.fcpxml")

        self.export_cmx3600_edl(cuts, edl_path)
        self.export_fcpxml_multicam(cuts, camera_files, fcpxml_path, total_duration)

        return {
            "status": "success",
            "style": style["name"],
            "pacing": style["pacing"],
            "total_duration_sec": round(total_duration, 2),
            "cuts_count": len(cuts),
            "cameras_count": num_cams,
            "camera_usage": cam_stats,
            "cuts_sample": cuts[:8],
            "edl_path": edl_path,
            "fcpxml_path": fcpxml_path,
            "message": (f"'{style['name']}' tarzında {num_cams} kameralı kurgu tamamlandı. "
                        f"Toplam {len(cuts)} kesim yapıldı. CMX 3600 EDL ve FCPXML hazır.")
        }

    # --- Standart CMX 3600 EDL Dışa Aktarımı ---

    @staticmethod
    def _sec_to_tc(sec: float, fps: float = 25.0) -> str:
        total_frames = int(round(sec * fps))
        ff = total_frames % int(fps)
        total_secs = total_frames // int(fps)
        ss = total_secs % 60
        total_mins = total_secs // 60
        mm = total_mins % 60
        hh = total_mins // 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"

    def export_cmx3600_edl(self, cuts: List[Dict[str, Any]], out_path: str, fps: float = 25.0) -> str:
        lines = [
            f"TITLE: {os.path.basename(out_path)}",
            "FCM: NON-DROP FRAME",
            ""
        ]
        rec_start = 0.0
        for i, cut in enumerate(cuts, 1):
            src_in = self._sec_to_tc(cut["start"], fps)
            src_out = self._sec_to_tc(cut["end"], fps)
            dur = cut["end"] - cut["start"]
            rec_in = self._sec_to_tc(rec_start, fps)
            rec_out = self._sec_to_tc(rec_start + dur, fps)
            rec_start += dur

            reel = f"CAM{cut['camera_index'] + 1}"
            lines.append(f"{i:03d}  {reel:<8} V     C        {src_in} {src_out} {rec_in} {rec_out}")
            lines.append(f"* FROM CLIP NAME: {cut.get('camera_file', 'CamClip')}")
            lines.append(f"* REASON: {cut.get('reason', 'Auto Cut')}")
            lines.append("")

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return out_path

    # --- Standart FCPXML v1.10 Çoklu Kamera Dışa Aktarımı ---

    def export_fcpxml_multicam(self, cuts: List[Dict[str, Any]], camera_files: List[str],
                               out_path: str, total_duration: float, fps: int = 25) -> str:
        """
        DaVinci Resolve, Final Cut Pro ve Premiere Pro ile tam uyumlu FCPXML v1.10 üretir.
        """
        dur_frames = int(round(total_duration * fps))
        xml = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE fcpxml>',
            '<fcpxml version="1.10">',
            '  <resources>',
            f'    <format id="r1" name="FFVideoFormat1080p{fps}" frameDuration="1/{fps}s" width="1920" height="1080"/>'
        ]

        for i, cf in enumerate(camera_files, 1):
            f_name = os.path.basename(cf)
            xml.append(f'    <asset id="r{i+1}" name="{f_name}" src="file://{os.path.abspath(cf)}" format="r1" duration="{dur_frames}/{fps}s"/>')

        xml.extend([
            '  </resources>',
            '  <library>',
            '    <event name="Mek Multicam Otomasyonu">',
            '      <project name="Mek Multicam Edit">',
            f'        <sequence format="r1" duration="{dur_frames}/{fps}s">',
            '          <spine>'
        ])

        for cut in cuts:
            dur = int(round((cut["end"] - cut["start"]) * fps))
            start_f = int(round(cut["start"] * fps))
            asset_id = f"r{cut['camera_index'] + 2}"
            clip_name = cut.get("camera_file", f"Cam_{cut['camera_index']+1}")
            xml.append(f'            <asset-clip ref="{asset_id}" name="{clip_name}" offset="{start_f}/{fps}s" duration="{dur}/{fps}s" start="{start_f}/{fps}s"/>')

        xml.extend([
            '          </spine>',
            '        </sequence>',
            '      </project>',
            '    </event>',
            '  </library>',
            '</fcpxml>'
        ])

        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(xml))
        return out_path
