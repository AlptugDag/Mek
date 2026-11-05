# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "jarvisd"))
import app_tuner
import memory_budget
import prewarmer
import services


class PerformanceEngineTests(unittest.TestCase):
    def setUp(self):
        with patch.object(services, "priv", return_value=True), \
             patch.object(services, "_run"), \
             patch.object(services.ServiceManager, "_tune_and_prewarm_bg"), \
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
            # İkincil boşta kurgu uygulaması geri alınabilir Tier-3 (-B -t 3 -l 3) kademesine alınır, ASLA -b veya nice>0 yapılmaz:
            self.assertIn(["/usr/sbin/taskpolicy", "-B", "-t", "3", "-l", "3", "-p", "4511"], cmds)
            self.assertNotIn(["/usr/sbin/taskpolicy", "-b", "-p", "4511"], cmds)
            renice_mock.assert_called_once_with(5000, 0)

    def test_orphan_helpers_never_killed_even_with_force_when_adobe_main_app_open(self):
        snap = SimpleNamespace(
            procs=[
                (4511, "Adobe Premiere Pro 2030", "/Applications/Adobe Premiere Pro 2030/Adobe Premiere Pro 2030.app/Contents/MacOS/Adobe Premiere Pro 2030", 40.0, 10_000_000_000),
                (4600, "CEPHtmlEngine", "/Applications/Adobe Premiere Pro 2030/CEPHtmlEngine", 0.0, 100_000),
                (4601, "dynamiclinkmanager", "/Library/Application Support/Adobe/Common/dynamiclink/1.0/dynamiclinkmanager", 0.0, 100_000),
            ]
        )
        with patch("os.kill") as kill_mock:
            cleaned = self.mgr.cleanup_orphans(snap, force=True)
            self.assertEqual(cleaned, 0)
            kill_mock.assert_not_called()

    def test_guard_clamps_wallpaper_and_bloat_while_protecting_creative_and_plugin_services(self):
        snap = SimpleNamespace(
            pro_pids=[4511],
            ppids={4650: 4511},
            procs=[
                (684, "WallpaperAgent", "/System/Library/CoreServices/WallpaperAgent.app/Contents/MacOS/WallpaperAgent", 5.0, 50_000_000),
                (690, "WallpaperMacintoshExtension", "/System/Library/ExtensionKit/Extensions/WallpaperMacintoshExtension.appex/Contents/MacOS/WallpaperMacintoshExtension", 2.0, 40_000_000),
                (4600, "CEPHtmlEngine", "/Applications/Adobe Premiere Pro 2030/Adobe Premiere Pro 2030.app/Contents/MacOS/CEPHtmlEngine.app/Contents/MacOS/CEPHtmlEngine", 12.0, 150_000_000),
                (4620, "Red Giant Service", "/Library/Application Support/Red Giant/Services/Red Giant Service.app/Contents/MacOS/Red Giant Service", 4.0, 80_000_000),
                (4650, "CustomPluginHelper", "/usr/local/bin/CustomPluginHelper", 15.0, 60_000_000),
                (4700, "Dropbox", "/Applications/Dropbox.app/Contents/MacOS/Dropbox", 8.0, 90_000_000),
                (4511, "Adobe Premiere Pro 2030", "/Applications/Adobe Premiere Pro 2030/Adobe Premiere Pro 2030.app/Contents/MacOS/Adobe Premiere Pro 2030", 100.0, 10_000_000_000),
            ]
        )
        with patch.object(services, "_run") as run_mock, patch("os.kill") as kill_mock:
            self.mgr.guard(snap)
            kill_mock.assert_not_called()
            self.assertIn(684, self.mgr.bg_clamped)
            self.assertIn(690, self.mgr.bg_clamped)
            self.assertIn(4700, self.mgr.clamped)
            self.assertNotIn(4600, self.mgr.clamped)
            self.assertNotIn(4620, self.mgr.clamped)
            self.assertIn(4600, self.mgr.unthrottled_ipc)
            self.assertIn(4620, self.mgr.unthrottled_ipc)
            self.assertIn(4650, self.mgr.unthrottled_ipc)
            cmds = [c.args[0] for c in run_mock.call_args_list]
            self.assertIn(["/usr/sbin/taskpolicy", "-b", "-p", "684"], cmds)
            self.assertIn(["/usr/sbin/taskpolicy", "-b", "-p", "690"], cmds)
            self.assertIn(["/usr/sbin/taskpolicy", "-b", "-p", "4700"], cmds)
            # CEPHtmlEngine, Red Giant Service ve kurgu alt süreçleri (4650) Tier-0'a açılır (-B), asla -b yapılmaz:
            self.assertIn(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", "4600"], cmds)
            self.assertIn(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", "4620"], cmds)
            self.assertIn(["/usr/sbin/taskpolicy", "-B", "-t", "0", "-l", "0", "-p", "4650"], cmds)
            self.assertNotIn(["/usr/sbin/taskpolicy", "-b", "-p", "4600"], cmds)

    def test_dynamic_memory_plan_prioritizes_active_app_and_supports_2030_versions(self):
        exes = [
            "/Applications/Adobe Premiere Pro 2030/Adobe Premiere Pro 2030.app/Contents/MacOS/Adobe Premiere Pro 2030",
            "/Applications/Adobe Photoshop 2030/Adobe Photoshop 2030.app/Contents/MacOS/Adobe Photoshop 2030",
        ]
        running = memory_budget.running_apps(exes)
        self.assertEqual(running, {"adobe_video", "photoshop"})

        # Premiere aktifken Premiere daha yüksek bütçe alır:
        p_premiere_active = memory_budget.plan(64, running, active={"adobe_video"})
        # Photoshop aktifken Photoshop daha yüksek bütçe alır:
        p_photoshop_active = memory_budget.plan(64, running, active={"photoshop"})
        self.assertGreater(p_premiere_active["adobe_video"], p_photoshop_active["adobe_video"])
        self.assertGreater(p_photoshop_active["photoshop"], p_premiere_active["photoshop"])

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

    def test_growth_sdk_helper_is_excluded_from_creative_protection_and_killed_when_spinning(self):
        bloat_cmd = (
            "/Users/alptug/Library/Application Support/Adobe/Adobe Desktop Common/HEX/"
            "Creative Cloud Helper.app/Contents/MacOS/Creative Cloud Helper --remoteApplet=GrowthSDKHelper_BL"
        )
        self.assertFalse(services.is_creative_or_plugin(bloat_cmd))
        snap = SimpleNamespace(
            pro_pids=[4511],
            ppids={},
            procs=[(27159, "Creative Cloud Helper", bloat_cmd, 98.3, 120_000_000)],
        )
        with patch.object(services, "_run"), patch("os.kill") as kill_mock:
            self.mgr.guard(snap)
            kill_mock.assert_called_once_with(27159, services.signal.SIGKILL)

    def test_app_tuner_optimizes_debug_database_and_premiere_xml_prefs(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "Debug Database.txt")
            with open(db_path, "w", encoding="utf-8") as f:
                f.write(
                    "ExporterCalcMaxRequests.OverrideMaxRequests\t1\t\n"
                    "GF.DisableMemoryPooling\ttrue\ttrue\n"
                    "GF.UseMetalHeap\tfalse\tfalse\n"
                    "MediaCoreAsyncFrameCount3\t10\t10\n"
                    "BeePostedCmdTraceMode (0:None,1:Posted,2:PostedDelta,3:PostedDeltaAndDecode)\t2\t2\n"
                )
            changes = app_tuner.tune_debug_database_file(db_path)
            self.assertEqual(changes, 5)
            content = open(db_path, encoding="utf-8").read()
            self.assertIn("ExporterCalcMaxRequests.OverrideMaxRequests\t\t\n", content)
            self.assertIn("GF.DisableMemoryPooling\tfalse\ttrue\n", content)
            self.assertIn("GF.UseMetalHeap\ttrue\tfalse\n", content)
            self.assertIn("MediaCoreAsyncFrameCount3\t32\t10\n", content)

            latest_app = os.path.join(td, "Adobe Premiere Pro 2030.app")
            os.makedirs(latest_app, exist_ok=True)
            xml_path = os.path.join(td, "Adobe Premiere Pro Prefs")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write(
                    "<PremiereData>\n"
                    "\t<BE.Prefs.Undo.MaxLevels>1000</BE.Prefs.Undo.MaxLevels>\n"
                    "\t<BE.Prefs.AutoSave.Interval>5</BE.Prefs.AutoSave.Interval>\n"
                    "\t<HSL.Watermark>/Applications/Adobe Premiere Pro 2023/Adobe Premiere Pro 2023.app/Contents/w.png</HSL.Watermark>\n"
                    "</PremiereData>\n"
                )
            xml_changes = app_tuner.tune_premiere_prefs_file(xml_path, latest_app=latest_app)
            self.assertEqual(xml_changes, 3)
            xml_out = open(xml_path, encoding="utf-8").read()
            self.assertIn("<BE.Prefs.Undo.MaxLevels>100</BE.Prefs.Undo.MaxLevels>", xml_out)
            self.assertIn("<BE.Prefs.AutoSave.Interval>15</BE.Prefs.AutoSave.Interval>", xml_out)
            self.assertIn(latest_app, xml_out)

    def test_prewarmer_advises_files_into_page_cache(self):
        with tempfile.TemporaryDirectory() as td:
            sample = os.path.join(td, "plugin.bundle")
            with open(sample, "wb") as f:
                f.write(b"x" * 4096)
            self.assertEqual(prewarmer.advise_file(sample), 4096)


if __name__ == "__main__":
    unittest.main()
