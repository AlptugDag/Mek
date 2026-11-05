#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for Mek Autonomous Multicam Director & Viral Hook Synthesizer.
"""

import os
import sys
import tempfile
import unittest
import wave
import struct
import math
import json
import time
from unittest.mock import patch, MagicMock

# Add jarvisd to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../jarvisd")))

from multicam_director import MulticamDirector
from viral_hook import ViralHookSynthesizer
from kurgu import KurguManager


def create_dummy_wav(filepath: str, duration_sec: float = 4.0, freq: float = 440.0, volume: float = 0.5):
    """Creates a synthetic mono 16kHz WAV file for audio analysis testing."""
    sample_rate = 16000
    num_samples = int(duration_sec * sample_rate)
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(num_samples):
            t = float(i) / sample_rate
            sample = int(volume * 32767.0 * math.sin(2.0 * math.pi * freq * t))
            frames.extend(struct.pack("<h", max(-32768, min(32767, sample))))
        wf.writeframes(frames)


class TestMulticamDirector(unittest.TestCase):

    def setUp(self):
        self.director = MulticamDirector()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        if hasattr(self, "km") and hasattr(self.km, "gateway"):
            self.km.gateway.stop()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_parse_editing_styles(self):
        # 1. Dinamik Podcast
        s1 = self.director.parse_editing_style("dinamik podcast")
        self.assertEqual(s1["pacing"], "dynamic")
        self.assertTrue(s1["wide_on_overlap"])
        self.assertAlmostEqual(s1["min_shot_duration"], 1.8)

        # 2. Belgesel
        s2 = self.director.parse_editing_style("belgesel tarzında sinematik kurgu")
        self.assertEqual(s2["pacing"], "cinematic")
        self.assertTrue(s2["insert_reactions"])
        self.assertAlmostEqual(s2["min_shot_duration"], 4.5)

        # 3. Hızlı YouTube
        s3 = self.director.parse_editing_style("hızlı tempolu youtube reels")
        self.assertEqual(s3["pacing"], "aggressive")
        self.assertAlmostEqual(s3["min_shot_duration"], 1.1)

        # 4. Özel zaman parametresi
        s4 = self.director.parse_editing_style("sohbet kurgusu en az 3.5 saniye")
        self.assertAlmostEqual(s4["min_shot_duration"], 3.5)

    def test_multicam_switching_and_export(self):
        # Üç kamera için test sesleri oluştur:
        # Cam 1 (Sunucu): İlk 3 saniye konuşuyor
        # Cam 2 (Konuk): 3-6 saniye arası konuşuyor
        # Cam 3 (Geniş Açı): Düşük arka plan sesi
        cam1 = os.path.join(self.tmp_dir, "cam1_host.wav")
        cam2 = os.path.join(self.tmp_dir, "cam2_guest.wav")
        cam3 = os.path.join(self.tmp_dir, "cam3_wide.wav")

        create_dummy_wav(cam1, duration_sec=5.0, freq=300.0, volume=0.8)
        create_dummy_wav(cam2, duration_sec=5.0, freq=600.0, volume=0.1)
        create_dummy_wav(cam3, duration_sec=5.0, freq=100.0, volume=0.05)

        res = self.director.direct_multicam([cam1, cam2, cam3], style_prompt="dinamik podcast")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["cameras_count"], 3)
        self.assertGreater(res["cuts_count"], 0)
        self.assertTrue(os.path.exists(res["edl_path"]))
        self.assertTrue(os.path.exists(res["fcpxml_path"]))

        # EDL dosyasını kontrol et
        with open(res["edl_path"], "r", encoding="utf-8") as f:
            edl_content = f.read()
            self.assertIn("TITLE:", edl_content)
            self.assertIn("CAM1", edl_content)

        # FCPXML dosyasını kontrol et
        with open(res["fcpxml_path"], "r", encoding="utf-8") as f:
            fcpxml_content = f.read()
            self.assertIn("<fcpxml version=\"1.10\">", fcpxml_content)
            self.assertIn("cam1_host.wav", fcpxml_content)


class TestViralHookSynthesizer(unittest.TestCase):

    def setUp(self):
        self.synthesizer = ViralHookSynthesizer(gemini_api_key="mock_test_key")
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        if hasattr(self, "km") and hasattr(self.km, "gateway"):
            self.km.gateway.stop()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_acoustic_climax_hook_detection(self):
        # 10 saniyelik sentetik ses dosyası oluştur, 6-8. saniyeler arasında yüksek tepe noktası olsun
        test_wav = os.path.join(self.tmp_dir, "sample_interview.wav")
        sample_rate = 16000
        total_samples = 10 * sample_rate
        with wave.open(test_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(total_samples):
                t = float(i) / sample_rate
                # 6.0 ile 8.0 sn arası yüksek enerji
                vol = 0.9 if 6.0 <= t <= 8.0 else 0.1
                sample = int(vol * 32767.0 * math.sin(2.0 * math.pi * 440.0 * t))
                frames.extend(struct.pack("<h", max(-32768, min(32767, sample))))
            wf.writeframes(frames)

        res = self.synthesizer.synthesize_viral_hook(test_wav)
        self.assertEqual(res["status"], "success")
        self.assertIn("winning_hook", res)
        win = res["winning_hook"]
        self.assertGreater(win["viral_score"], 70)
        self.assertTrue(os.path.exists(res["fcpxml_path"]))
        self.assertTrue(os.path.exists(res["edl_path"]))

        # FCPXML dosyasında [VIRAL HOOK] işaretçisi ve offset=0s kontrolü
        with open(res["fcpxml_path"], "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("VIRAL HOOK", content)
            self.assertIn("offset=\"0/25s\"", content)

    @patch("urllib.request.urlopen")
    def test_gemini_api_integration(self, mock_urlopen):
        # Mock Gemini JSON response
        mock_response = MagicMock()
        mock_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps([
                                    {
                                        "start_sec": 12.4,
                                        "end_sec": 16.8,
                                        "quote": "Bunu ilk duyduğumda şok geçirdim!",
                                        "hook_type": "şok",
                                        "viral_score": 98,
                                        "reason": "Şok edici itiraf retention'ı zirveye taşır."
                                    }
                                ])
                            }
                        ]
                    }
                }
            ]
        }
        mock_response.read.return_value = json.dumps(mock_data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        # Test video
        test_wav = os.path.join(self.tmp_dir, "gemini_test.wav")
        create_dummy_wav(test_wav, duration_sec=30.0)

        res = self.synthesizer.synthesize_viral_hook(test_wav, transcript_or_prompt="Transkript metni")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["engine"], "Gemini API (Google AI)")
        win = res["winning_hook"]
        self.assertEqual(win["viral_score"], 98)
        self.assertEqual(win["quote"], "Bunu ilk duyduğumda şok geçirdim!")


class TestKurguManagerIntegration(unittest.TestCase):

    def setUp(self):
        self.km = KurguManager()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        if hasattr(self, "km") and hasattr(self.km, "gateway"):
            self.km.gateway.stop()
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_kurgu_manager_multicam_and_hook(self):
        c1 = os.path.join(self.tmp_dir, "c1.wav")
        c2 = os.path.join(self.tmp_dir, "c2.wav")
        create_dummy_wav(c1, 4.0)
        create_dummy_wav(c2, 4.0)

        # 1. Multicam
        msg_mc = self.km.direct_multicam([c1, c2], "dinamik podcast")
        self.assertIn("kurgu tamamlandı", msg_mc)

        # 2. Viral Hook (dosya analizi)
        msg_vh = self.km.extract_viral_hook(c1)
        self.assertIn("kanca", msg_vh)

    def test_premiere_gateway_and_hook(self):
        # Premiere bağlı değilken nazik yönlendirme mesajı
        msg_offline = self.km.apply_viral_hook_in_premiere()
        self.assertIn("Premiere Pro bağlantısı bekleniyor", msg_offline)

        # Mock Premiere Client bağlantısı ve canlı hook testi
        import socket
        import threading
        gw = self.km.gateway
        gw.start()
        time.sleep(0.05)

        try:
            def mock_client_worker():
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.connect(("127.0.0.1", 45732))
                    # Hello
                    s.sendall(b'{"type":"status_update","state":{"host":"PPRO","ready":true}}\n')
                    data = s.recv(4096).decode("utf-8")
                    if "apply_viral_hook" in data:
                        req = json.loads(data.strip())
                        reply = {
                            "type": "response",
                            "req_id": req.get("req_id"),
                            "data": {
                                "ok": True,
                                "message": "İlk 3 saniyeye Smooth Punch Zoom, Camera Shake darbesi ve Viral Kanca Grafiği uygulandı."
                            }
                        }
                        s.sendall((json.dumps(reply) + "\n").encode("utf-8"))
                    s.close()
                except Exception:
                    pass

            client_thread = threading.Thread(target=mock_client_worker, daemon=True)
            client_thread.start()
            time.sleep(0.15)

            # Canlı timeline hook gönder
            res_online = self.km.apply_viral_hook_in_premiere(title="BÜYÜK SIR")
            self.assertIn("Punch Zoom", res_online)
        finally:
            gw.stop()


if __name__ == "__main__":
    unittest.main()
