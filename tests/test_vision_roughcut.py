import os
import sys
import tempfile
import wave
import struct
import math
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jarvisd"))
from vision_color import VisionColorEngine  # noqa: E402
from rough_cut import RoughCutEngine, sec_to_timecode, sec_to_fcpxml_time  # noqa: E402
import jarvis  # noqa: E402


def create_synthetic_audio(path, duration_s=3.0, sample_rate=16000):
    """0.8s ses, 1.2s sessizlik, 1.0s ses içeren sentetik WAV dosyası."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        total_frames = int(duration_s * sample_rate)
        frames = []
        for i in range(total_frames):
            t = float(i) / sample_rate
            if 0.8 <= t < 2.0:
                val = 0
            else:
                val = int(18000.0 * math.sin(2.0 * math.pi * 440.0 * t))
            frames.append(struct.pack("<h", val))
        wf.writeframes(b"".join(frames))


class VisionColorEngineTest(unittest.TestCase):
    def setUp(self):
        self.mock_kurgu = MagicMock()
        self.mock_kurgu.is_davinci_running.return_value = False
        self.engine = VisionColorEngine(kurgu_mgr=self.mock_kurgu)

    def test_apply_film_grade_matrix(self):
        res = self.engine.apply_film_grade("matrix")
        self.assertIn("The Matrix", res["description"])
        self.assertTrue(os.path.exists(res["lut_path"]))
        with open(res["lut_path"], "r", encoding="utf-8") as f:
            lines = f.readlines()
            self.assertEqual(lines[1].strip(), 'TITLE "matrix"')
            self.assertEqual(lines[2].strip(), "LUT_3D_SIZE 33")
            self.assertEqual(len(lines), 35942)  # 5 başlık satırı + 35937 veri

    def test_apply_film_grade_oppenheimer(self):
        res = self.engine.apply_film_grade("oppenheimer")
        self.assertIn("Oppenheimer", res["description"])
        self.assertTrue(os.path.exists(res["lut_path"]))

    def test_apply_film_grade_blade_runner(self):
        res = self.engine.apply_film_grade("blade runner 2049")
        self.assertIn("Blade Runner", res["description"])
        self.assertTrue(os.path.exists(res["lut_path"]))

    def test_synthesize_effect(self):
        res = self.engine.synthesize_effect("rüya gibi parıltı ve gren")
        self.assertIn("name", res)
        self.assertTrue(os.path.exists(res["lut_path"]))
        self.assertIn("bloom", res["optical_fx"])


class RoughCutEngineTest(unittest.TestCase):
    def setUp(self):
        self.mock_kurgu = MagicMock()
        self.mock_kurgu.is_davinci_running.return_value = False
        self.engine = RoughCutEngine(kurgu_mgr=self.mock_kurgu)

    def test_timecode_formatting(self):
        self.assertEqual(sec_to_timecode(0.0), "00:00:00:00")
        self.assertEqual(sec_to_timecode(65.5, fps=24.0), "00:01:05:12")
        self.assertEqual(sec_to_fcpxml_time(2.5, timescale=2400), "6000/2400s")

    def test_speech_extraction_and_edl(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name
        try:
            create_synthetic_audio(wav_path, duration_s=3.0)
            res = self.engine.extract_speech_segments(wav_path, threshold_db=-30.0)
            self.assertNotIn("error", res)
            self.assertAlmostEqual(res["total_duration"], 3.0, delta=0.1)
            self.assertGreater(res["cuts_count"], 0)

            # EDL üretimi testi
            edl = self.engine.generate_edl(res["segments"], "test_clip.wav")
            self.assertIn("TITLE: test_clip.wav_MEK_KABA_KURGU", edl)
            self.assertIn("FCM: NON-DROP FRAME", edl)
            self.assertIn("001  AX       AA/V  C", edl)

            # FCPXML üretimi testi
            fcpxml = self.engine.generate_fcpxml(res["segments"], wav_path)
            self.assertIn("<fcpxml version=\"1.10\">", fcpxml)
            self.assertIn("<asset-clip", fcpxml)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_full_process_pipeline(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name
        try:
            create_synthetic_audio(wav_path, duration_s=3.0)
            res = self.engine.process_file_or_folder(wav_path)
            self.assertTrue(res["success"])
            self.assertTrue(os.path.exists(res["edl_path"]))
            self.assertTrue(os.path.exists(res["fcpxml_path"]))
            self.assertGreater(res["saved_percent"], 0)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)


class TTSPronunciationTest(unittest.TestCase):
    def test_apple_pronunciation(self):
        sample = "Apple Intelligence desteği ile Apple Studio üzerinde apple güncellemeleri tamamlandı."
        expected = "Epıl Intelligence desteği ile Epıl Studio üzerinde epıl güncellemeleri tamamlandı."
        self.assertEqual(jarvis.normalize_phonetics(sample), expected)


if __name__ == "__main__":
    unittest.main()
