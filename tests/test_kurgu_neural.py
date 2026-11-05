import os
import sys
import tempfile
import wave
import struct
import math
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jarvisd"))
from kurgu import KurguManager  # noqa: E402
from neural_conductor import NeuralConductor  # noqa: E402
import agent  # noqa: E402


def create_synthetic_wav(path, duration_s=2.0, sample_rate=16000):
    """0.5s ses, 1.0s sessizlik, 0.5s ses içeren test WAV dosyası üretir."""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        total_frames = int(duration_s * sample_rate)
        frames = []
        for i in range(total_frames):
            t = float(i) / sample_rate
            # 0.0 - 0.5s: 440 Hz sinüs (sesli)
            # 0.5 - 1.5s: 0 (sessiz)
            # 1.5 - 2.0s: 440 Hz sinüs (sesli)
            if 0.5 <= t < 1.5:
                val = 0
            else:
                val = int(16000.0 * math.sin(2.0 * math.pi * 440.0 * t))
            frames.append(struct.pack("<h", val))
        wf.writeframes(b"".join(frames))


class KurguManagerTest(unittest.TestCase):
    def setUp(self):
        self.mock_services = MagicMock()
        self.mock_services.ultra_scratch = MagicMock()
        self.mock_services.thermal_fan = MagicMock()
        self.kurgu = KurguManager(services=self.mock_services)

    def test_optimize_environment(self):
        with patch("subprocess.run") as mock_run:
            msg = self.kurgu.optimize_environment()
            self.mock_services.force_focus_mode.assert_called_once()
            self.mock_services.ultra_scratch.ensure_mounted.assert_called_once()
            self.mock_services.thermal_fan.set_mode.assert_called_with("sessiz")
            self.assertIn("optimize edildi", msg)

    def test_audio_silence_analysis(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = tf.name
        try:
            create_synthetic_wav(wav_path, duration_s=2.0)
            res = self.kurgu.analyze_audio_silence(wav_path, threshold_db=-30.0, min_silence_duration=0.3)
            self.assertNotIn("error", res)
            self.assertAlmostEqual(res["total_duration_sec"], 2.0, delta=0.1)
            self.assertGreater(res["silence_time_sec"], 0.7)
            self.assertGreater(res["cuts_count"], 0)
            self.assertGreaterEqual(len(res["speech_intervals"]), 1)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)


class NeuralConductorTest(unittest.TestCase):
    def setUp(self):
        self.mock_services = MagicMock()
        self.mock_kurgu = MagicMock()
        self.conductor = NeuralConductor(services=self.mock_services, kurgu_mgr=self.mock_kurgu)

    def test_orchestrate_with_mock_ai(self):
        fake_plan = {
            "aciklama": "Premiere kurgu ortamı hazırlandı ve sessiz fan kipine geçildi.",
            "eylemler": ["kurgu_optimize", "fan_sessiz"]
        }
        with patch.object(self.conductor, "consult", return_value=json.dumps(fake_plan)):
            self.mock_kurgu.optimize_environment.return_value = "Ortam hazırlandı"
            self.mock_services.thermal_fan.set_mode.return_value = "Sessiz kip"

            result = self.conductor.orchestrate(
                user_query="Kurgu için sistemi hazırla",
                telemetry={"front_app": "Adobe Premiere Pro 2026", "cpu": 15, "gpu": 5, "ram": 40}
            )

            self.assertIn("Premiere kurgu ortamı", result["explanation"])
            self.assertIn("Ortam hazırlandı", result["actions"])
            self.assertIn("Sessiz kip", result["actions"])


class AgentKurguIntegrationTest(unittest.TestCase):
    def test_agent_has_kurgu_tools(self):
        mock_mek = MagicMock()
        mock_mek.kurgu = MagicMock()
        mock_mek.kurgu.status.return_value = "Premiere Pro aktif"
        mock_mek.kurgu.optimize_environment.return_value = "Kurgu optimize edildi"

        ag = agent.Agent(mock_mek)
        self.assertIn("kurgu_durumu", ag.tools)
        self.assertIn("kurgu_optimize", ag.tools)
        self.assertIn("davinci_render", ag.tools)
        self.assertIn("premiere_export", ag.tools)
        self.assertIn("kurgu_sessizlik", ag.tools)

        # Test direct tool run
        self.assertEqual(ag.tools["kurgu_durumu"]["run"]({}), "Premiere Pro aktif")
        self.assertEqual(ag.tools["kurgu_optimize"]["run"]({}), "Kurgu optimize edildi")


if __name__ == "__main__":
    unittest.main()
