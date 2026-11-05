# -*- coding: utf-8 -*-
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "jarvisd"))
import memory_budget
import services


class PerformanceEngineTests(unittest.TestCase):
    def setUp(self):
        with patch.object(services, "priv", return_value=True), \
             patch.object(services, "_run"), \
             patch("os.access", return_value=False):
            self.mgr = services.ServiceManager({"deferrable_agents": [], "cleaner_min_gap_h": 12})

    def test_idle_background_premiere_with_cep_spin_is_not_considered_working(self):
        # Premiere Pro arka planda açık ve CEP eklentileri yüzünden %160 CPU harcıyor; önde Safari var
        snap = SimpleNamespace(
            front="Safari",
            front_pid=100,
            pro_pids=[4511],
            pro_procs=[(4511, "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026", 160.0, 15 * 1024**3)],
        )
        self.assertEqual(services.ServiceManager.working_pids(snap), [])

    def test_foreground_or_actively_rendering_pro_app_is_working(self):
        snap_front = SimpleNamespace(
            front="Adobe Premiere Pro 2026",
            front_pid=4511,
            pro_pids=[4511, 5000],
            pro_procs=[
                (4511, "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026", 40.0, 10 * 1024**3),
                (5000, "/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app/Contents/MacOS/Adobe Photoshop 2026", 20.0, 8 * 1024**3),
            ],
        )
        self.assertEqual(services.ServiceManager.working_pids(snap_front), [4511])

        snap_render = SimpleNamespace(
            front="Safari",
            front_pid=100,
            pro_pids=[4511, 6000],
            pro_procs=[
                (4511, "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026", 350.0, 15 * 1024**3),
                (6000, "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/PProHeadless.app/Contents/MacOS/PProHeadless", 95.0, 4 * 1024**3),
            ],
        )
        self.assertEqual(services.ServiceManager.working_pids(snap_render), [4511, 6000])

    def test_instant_focus_change_boosts_foreground_and_parks_background_pro(self):
        snap = SimpleNamespace(
            front="Adobe Premiere Pro 2026",
            front_pid=4511,
            mem_pct=60,
            pressure=1,
            swap_gb=0.0,
            pro_pids=[4511, 5000],
            pro_procs=[
                (4511, "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026", 120.0, 12 * 1024**3),
                (5000, "/Applications/Adobe Photoshop 2026/Adobe Photoshop 2026.app/Contents/MacOS/Adobe Photoshop 2026", 15.0, 8 * 1024**3),
            ],
        )
        with patch.object(services, "_run") as run_mock, \
             patch.object(services, "_renice_safe") as renice_mock:
            # Kullanıcı Cmd+Tab ile Photoshop'a (pid 5000) geçti:
            self.mgr.on_focus_change(5000, "Adobe Photoshop 2026", snap)

            self.assertIn(5000, self.mgr.boosted)
            self.assertNotIn(5000, self.mgr.parked_pro)
            self.assertIn(4511, self.mgr.parked_pro)
            self.assertNotIn(4511, self.mgr.boosted)
            cmds = [c.args[0] for c in run_mock.call_args_list]
            self.assertIn(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", "5000"], cmds)
            # Kurgu uygulamaları ASLA taskpolicy -b (DARWIN_BG) veya pozitif nice altına alınmaz:
            self.assertNotIn(["/usr/sbin/taskpolicy", "-b", "-p", "4511"], cmds)
            renice_mock.assert_not_called()

    def test_orphan_helpers_never_killed_even_with_force_when_adobe_main_app_open(self):
        snap = SimpleNamespace(
            procs=[
                (4511, "Adobe Premiere Pro 2026", "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026", 40.0, 10_000_000_000),
                (4600, "CEPHtmlEngine", "/Applications/Adobe Premiere Pro 2026/CEPHtmlEngine", 0.0, 100_000),
                (4601, "dynamiclinkmanager", "/Library/Application Support/Adobe/Common/dynamiclink/1.0/dynamiclinkmanager", 0.0, 100_000),
            ]
        )
        with patch("os.kill") as kill_mock:
            cleaned = self.mgr.cleanup_orphans(snap, force=True)
            self.assertEqual(cleaned, 0)
            kill_mock.assert_not_called()

    def test_guard_clamps_wallpaper_and_bloat_while_protecting_creative_ipc(self):
        snap = SimpleNamespace(
            procs=[
                (684, "WallpaperAgent", "/System/Library/CoreServices/WallpaperAgent.app/Contents/MacOS/WallpaperAgent", 5.0, 50_000_000),
                (690, "WallpaperMacintoshExtension", "/System/Library/ExtensionKit/Extensions/WallpaperMacintoshExtension.appex/Contents/MacOS/WallpaperMacintoshExtension", 2.0, 40_000_000),
                (4600, "CEPHtmlEngine", "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/CEPHtmlEngine.app/Contents/MacOS/CEPHtmlEngine", 12.0, 150_000_000),
                (4700, "CCXProcess", "/Applications/Utilities/Adobe Creative Cloud Experience/CCXProcess/CCXProcess.app/Contents/MacOS/CCXProcess", 8.0, 90_000_000),
                (4511, "Adobe Premiere Pro 2026", "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026", 100.0, 10_000_000_000),
            ]
        )
        with patch.object(services, "_run") as run_mock, patch("os.kill") as kill_mock:
            self.mgr.guard(snap)
            kill_mock.assert_not_called()
            self.assertIn(684, self.mgr.bg_clamped)
            self.assertIn(690, self.mgr.bg_clamped)
            self.assertIn(4700, self.mgr.clamped)
            self.assertNotIn(4600, self.mgr.clamped)
            self.assertIn(4600, self.mgr.unthrottled_ipc)
            cmds = [c.args[0] for c in run_mock.call_args_list]
            self.assertIn(["/usr/sbin/taskpolicy", "-b", "-p", "684"], cmds)
            self.assertIn(["/usr/sbin/taskpolicy", "-b", "-p", "690"], cmds)
            self.assertIn(["/usr/sbin/taskpolicy", "-b", "-p", "4700"], cmds)
            # CEPHtmlEngine senkron IPC yaptığı için DARWIN_BG'den çıkarılır (-B), asla -b yapılmaz:
            self.assertIn(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", "4600"], cmds)
            self.assertNotIn(["/usr/sbin/taskpolicy", "-b", "-p", "4600"], cmds)

    def test_orphan_helpers_cleaned_when_no_adobe_main_app_open(self):
        snap = SimpleNamespace(
            procs=[
                (4600, "CEPHtmlEngine", "/Applications/Adobe Premiere Pro 2026/CEPHtmlEngine", 0.0, 100_000),
                (4601, "dynamiclinkmanager", "/Library/Application Support/Adobe/Common/dynamiclink/1.0/dynamiclinkmanager", 0.0, 100_000),
            ]
        )
        self.mgr.last_adobe_seen = 0
        with patch("os.kill") as kill_mock:
            cleaned = self.mgr.cleanup_orphans(snap)
            self.assertEqual(cleaned, 2)
            self.assertEqual(kill_mock.call_count, 2)

    def test_orphan_dynamiclinkmanager_does_not_count_as_running_adobe_video_in_memory_budget(self):
        exes = ["/Library/Application Support/Adobe/Common/dynamiclink/1.0/dynamiclinkmanager"]
        self.assertEqual(memory_budget.running_apps(exes), set())


if __name__ == "__main__":
    unittest.main()
