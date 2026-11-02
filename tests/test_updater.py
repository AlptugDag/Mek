import unittest
import json
from types import SimpleNamespace

from jarvisd.updater import (parse_attached_devices, parse_available_updates, parse_brew_outdated,
                             parse_brew_services, parse_mas_outdated, is_on_ac_power, check_updates, install_update,
                             safe_install_window, update_third_party)


class UpdaterTests(unittest.TestCase):
    def test_selects_recommended_updates_but_skips_major_os_upgrade(self):
        output = """Software Update found the following new or updated software:
* Label: macOS Sequoia 15.6.1-24G90
    Title: macOS Sequoia 15.6.1, Version: 15.6.1, Size: 1000K, Recommended: YES, Action: restart,
* Label: macOS Tahoe 26.0-25A1
    Title: macOS Tahoe 26.0, Version: 26.0, Size: 1000K, Recommended: YES, Action: restart,
* Label: Safari 18.6-20622
    Title: Safari, Version: 18.6, Size: 1000K, Recommended: YES,
* Label: Optional Tool 2.0
    Title: Optional Tool, Version: 2.0, Size: 10K, Recommended: NO,
"""
        updates = parse_available_updates(output, "15.5.0")

        self.assertEqual([update["label"] for update in updates],
                         ["macOS Sequoia 15.6.1-24G90", "Safari 18.6-20622"])
        self.assertTrue(updates[0]["restart_required"])
        self.assertFalse(updates[1]["restart_required"])

    def test_automatic_install_requires_safe_idle_window(self):
        self.assertTrue(safe_install_window(True, False, True, 80, 0, "healthy"))
        self.assertFalse(safe_install_window(True, True, True, 80, 0, "healthy"))
        self.assertFalse(safe_install_window(True, False, False, 80, 0, "healthy"))
        self.assertFalse(safe_install_window(True, False, True, 15, 0, "healthy"))
        self.assertFalse(safe_install_window(True, False, True, 80, 2, "healthy"))
        self.assertFalse(safe_install_window(True, False, True, 80, 0, "degraded"))

    def test_ac_power_detection_fails_closed(self):
        ac = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Now drawing from 'AC Power'.")
        battery = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Now drawing from 'Battery Power'.")

        self.assertTrue(is_on_ac_power(ac))
        self.assertFalse(is_on_ac_power(battery))

    def test_apple_update_check_uses_fixed_system_commands(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if args[0] == "/usr/bin/sw_vers":
                return SimpleNamespace(returncode=0, stdout="15.5.0", stderr="")
            return SimpleNamespace(returncode=0, stdout="No new software available.", stderr="")

        result = check_updates(runner=runner)

        self.assertTrue(result["ok"])
        self.assertEqual(calls, [["/usr/bin/sw_vers", "-productVersion"],
                                 ["/usr/sbin/softwareupdate", "--list"]])

    def test_apple_install_uses_fixed_privileged_helper(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="installed", stderr="")

        result = install_update("Safari Update", helper="/helper", runner=runner)

        self.assertTrue(result["ok"])
        self.assertEqual(calls, [["sudo", "-n", "/helper", "software-update-install", "Safari Update"]])

    def test_brew_parser_skips_pinned_and_preserves_cask_names(self):
        data = {"formulae": [{"name": "wget"}, {"name": "openssl@3", "pinned": True}],
                "casks": [{"token": "demo-app", "name": ["Demo App"]}]}

        updates = parse_brew_outdated(json.dumps(data))

        self.assertEqual([(item["kind"], item["token"]) for item in updates],
                         [("formula", "wget"), ("cask", "demo-app")])
        self.assertEqual(updates[1]["app_names"], ["Demo App"])

    def test_mas_parser_extracts_app_ids(self):
        updates = parse_mas_outdated("12345 Demo App (1.0 -> 2.0)\nnot an update\n")
        self.assertEqual(updates, [{"id": "12345", "name": "Demo App", "versions": "1.0 -> 2.0"}])

    def test_brew_service_parser_tracks_running_and_unknown(self):
        self.assertEqual(parse_brew_services('[{"name":"postgresql@16","status":"started"}]'),
                         {"postgresql@16"})
        self.assertIsNone(parse_brew_services("not json"))

    def test_hardware_parser_reads_usb_and_thunderbolt_inventory(self):
        profile = {"SPUSBDataType": [{"_name": "Dock", "_items": [{
            "_name": "External SSD", "manufacturer": "Acme", "vendor_id": "0x1234",
            "firmware_version": "1.2"}]}],
                   "SPThunderboltDataType": [{"_items": [{
                       "device_name": "Display", "vendor_name": "Example Inc.",
                       "device_uid": "device-1"}]}]}

        devices = parse_attached_devices(json.dumps(profile))

        self.assertEqual({device["name"] for device in devices}, {"External SSD", "Display"})
        self.assertEqual(next(d for d in devices if d["name"] == "External SSD")["firmware"], "1.2")

    def test_third_party_updates_skip_running_cask_apps(self):
        calls = []
        brew_json = json.dumps({"formulae": [{"name": "wget"}], "casks": [
            {"token": "demo-app", "name": ["Demo App"]},
            {"token": "editor", "name": ["Editor"]}]})

        def runner(args, **kwargs):
            calls.append(args)
            if args[1:4] == ["services", "list", "--json"]:
                return SimpleNamespace(returncode=0, stdout="[]", stderr="")
            if args[1:3] == ["outdated", "--json=v2"]:
                return SimpleNamespace(returncode=0, stdout=brew_json, stderr="")
            if args[1] == "outdated":
                return SimpleNamespace(returncode=0, stdout="12345 Demo App (1.0 -> 2.0)\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="updated", stderr="")

        result = update_third_party(running_apps={"Editor.app"}, install=True, runner=runner,
                                    which=lambda name: {"brew": "/brew", "mas": "/mas"}.get(name))

        self.assertEqual(result["deferred"], ["Editor"])
        self.assertIn("Demo App", result["installed"])
        self.assertIn("wget", result["installed"])
        self.assertIn(["/mas", "upgrade", "12345"], calls)

    def test_third_party_updates_defer_when_safety_gate_closes(self):
        calls = []
        brew_json = json.dumps({"formulae": [{"name": "wget"}], "casks": []})

        def runner(args, **kwargs):
            calls.append(args)
            if args[1:4] == ["services", "list", "--json"]:
                return SimpleNamespace(returncode=0, stdout="[]", stderr="")
            if args[1:3] == ["outdated", "--json=v2"]:
                return SimpleNamespace(returncode=0, stdout=brew_json, stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = update_third_party(install=True, allow_install=lambda: False, runner=runner,
                                    which=lambda name: "/brew" if name == "brew" else None)

        self.assertEqual(result["deferred"], ["wget"])
        self.assertNotIn(["/brew", "upgrade", "--formula", "wget"], calls)

    def test_running_brew_services_are_not_upgraded(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if args[1:4] == ["services", "list", "--json"]:
                return SimpleNamespace(returncode=0,
                                       stdout='[{"name":"postgresql@16","status":"started"}]', stderr="")
            if args[1:3] == ["outdated", "--json=v2"]:
                data = {"formulae": [{"name": "postgresql@16"}], "casks": []}
                return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = update_third_party(install=True, runner=runner,
                                    which=lambda name: "/brew" if name == "brew" else None)

        self.assertEqual(result["deferred"], ["postgresql@16"])
        self.assertNotIn(["/brew", "upgrade", "--formula", "postgresql@16"], calls)

    def test_brew_update_failure_does_not_install_packages(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if args[1:4] == ["services", "list", "--json"]:
                return SimpleNamespace(returncode=0, stdout="[]", stderr="")
            if args[1] == "update":
                return SimpleNamespace(returncode=1, stdout="", stderr="offline")
            return SimpleNamespace(returncode=0, stdout=json.dumps({"formulae": [{"name": "wget"}],
                                                                      "casks": []}), stderr="")

        result = update_third_party(install=True, runner=runner,
                                    which=lambda name: "/brew" if name == "brew" else None)

        self.assertEqual(result["installed"], [])
        self.assertNotIn(["/brew", "upgrade", "--formula", "wget"], calls)


if __name__ == "__main__":
    unittest.main()