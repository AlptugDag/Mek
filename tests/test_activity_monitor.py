import os
import tempfile
import unittest
from types import SimpleNamespace

from jarvisd.activity_monitor import CrashMonitor, format_snapshot, process_snapshot
from jarvisd.events import EventBus


class ActivityMonitorTests(unittest.TestCase):
    def test_process_snapshot_sorts_cpu_and_memory(self):
        processes = [
            SimpleNamespace(info={"pid": 1, "name": "Light", "cpu_percent": 2,
                                 "memory_info": SimpleNamespace(rss=1024), "status": "running"}),
            SimpleNamespace(info={"pid": 2, "name": "Heavy", "cpu_percent": 80,
                                 "memory_info": SimpleNamespace(rss=2048), "status": "running"}),
        ]
        snapshot = process_snapshot(lambda: processes,
                                    lambda interval=None: 45,
                                    lambda: SimpleNamespace(percent=60))

        self.assertEqual(snapshot["top_cpu"][0]["name"], "Heavy")
        self.assertEqual(snapshot["top_memory"][0]["name"], "Heavy")
        self.assertIn("2 süreç", format_snapshot(snapshot))

    def test_crash_monitor_baselines_old_reports_and_detects_new_ones_once(self):
        with tempfile.TemporaryDirectory() as root:
            reports = os.path.join(root, "reports")
            os.makedirs(reports)
            state = os.path.join(root, "state.json")
            old = os.path.join(reports, "OldApp.crash")
            with open(old, "w", encoding="utf-8") as report:
                report.write("old")

            monitor = CrashMonitor(reports, state)
            self.assertEqual(monitor.scan(), [])

            new = os.path.join(reports, "NewApp.ips")
            with open(new, "w", encoding="utf-8") as report:
                report.write("new")
            events = monitor.scan()

            self.assertEqual([event["name"] for event in events], ["NewApp.ips"])
            self.assertEqual(monitor.scan(), [])

    def test_activity_and_crash_modules_publish_events(self):
        bus = EventBus()
        received = []
        bus.subscribe("activity.snapshot", lambda event, data: received.append(event))
        bus.subscribe("app.crashed", lambda event, data: received.append(event))
        snapshot = process_snapshot(lambda: [], lambda interval=None: 2,
                                    lambda: SimpleNamespace(percent=30), events=bus)
        self.assertEqual(snapshot["processes"], 0)

        with tempfile.TemporaryDirectory() as root:
            reports = os.path.join(root, "reports")
            os.makedirs(reports)
            state = os.path.join(root, "state.json")
            monitor = CrashMonitor(reports, state, events=bus)
            monitor.scan()
            with open(os.path.join(reports, "Editor.crash"), "w", encoding="utf-8") as report:
                report.write("crashed")
            monitor.scan()

        self.assertEqual(received, ["activity.snapshot", "app.crashed"])


if __name__ == "__main__":
    unittest.main()