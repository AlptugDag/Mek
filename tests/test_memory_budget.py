# -*- coding: utf-8 -*-
import unittest
from jarvisd import memory_budget


class MemoryBudgetTests(unittest.TestCase):
    def test_plan_single_heavy_app(self):
        # 64 GB makine, sadece Photoshop açık
        p = memory_budget.plan(total_gb=64, running=["photoshop"], reserve_gb=10, overcommit=1.2)
        # Photoshop 64 * 0.60 = 38 GB civarı
        self.assertGreaterEqual(p["photoshop"], 30)
        self.assertLessEqual(p["photoshop"], 40)

    def test_plan_multiple_heavy_apps_balanced(self):
        # 64 GB makine, Photoshop, Premiere ve Topaz birlikte açık
        p = memory_budget.plan(total_gb=64, running=["photoshop", "adobe_video", "topaz_video"], reserve_gb=10, overcommit=1.2)
        # Açık 3 uygulama varken hiçbiri tek başına 40-50 GB almamalı, dengeli dağılmalı
        self.assertLessEqual(p["photoshop"], 25)
        self.assertLessEqual(p["topaz_video"], 25)
        self.assertLessEqual(p["adobe_video"], 25)
        # Her biri en az tanımlı minimumunu almalı
        self.assertGreaterEqual(p["photoshop"], 8)
        self.assertGreaterEqual(p["adobe_video"], 12)
        self.assertGreaterEqual(p["topaz_video"], 8)

    def test_running_apps_detection(self):
        exes = [
            "/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app/Contents/MacOS/Adobe Photoshop",
            "/Applications/Topaz Video.app/Contents/MacOS/Topaz Video",
            "/Applications/Safari.app/Contents/MacOS/Safari"
        ]
        detected = memory_budget.running_apps(exes)
        self.assertIn("photoshop", detected)
        self.assertIn("topaz_video", detected)
        self.assertNotIn("resolve", detected)


if __name__ == "__main__":
    unittest.main()
