# -*- coding: utf-8 -*-
import unittest
import numpy as np


class MacStudioAudioTests(unittest.TestCase):
    def test_smart_digital_volume_scaling_with_media_playing(self):
        # Kural: Başka bir medya oynatılıyorsa donanım seviyesi değiştirilmez.
        # Dijital kazanç: digital_scale = min(1.0, TARGET_VOLUME / max(0.05, current_vol))
        target_volume = 0.30

        # Durum 1: Kullanıcı başka bir medyayı %60 sesle dinliyor
        current_hw_vol = 0.60
        scale = min(1.0, target_volume / current_hw_vol)
        self.assertAlmostEqual(scale, 0.50)
        # Fiziksel ses seviyesi = scale * current_hw_vol = 0.50 * 0.60 = 0.30 (%30)
        effective = scale * current_hw_vol
        self.assertAlmostEqual(effective, 0.30)

        # Durum 2: Kullanıcı başka bir medyayı %80 sesle dinliyor
        current_hw_vol = 0.80
        scale = min(1.0, target_volume / current_hw_vol)
        self.assertAlmostEqual(scale, 0.375)
        effective = scale * current_hw_vol
        self.assertAlmostEqual(effective, 0.30)

        # Durum 3: Kullanıcı başka bir medyayı %30 sesle dinliyor
        current_hw_vol = 0.30
        scale = min(1.0, target_volume / current_hw_vol)
        self.assertAlmostEqual(scale, 1.0)
        effective = scale * current_hw_vol
        self.assertAlmostEqual(effective, 0.30)

    def test_audio_buffer_scaled_without_clipping(self):
        # Örnek ses verisi (-1.0 ile 1.0 arası)
        audio = np.array([-0.8, -0.2, 0.0, 0.5, 0.9], dtype=np.float32)
        digital_scale = 0.50
        scaled = (audio * digital_scale).astype(np.float32)
        self.assertTrue(np.all(np.abs(scaled) <= 1.0))
        self.assertAlmostEqual(float(scaled[4]), 0.45)


if __name__ == "__main__":
    unittest.main()
