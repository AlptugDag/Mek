import os
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from jarvisd.app_uninstaller import app_identity, collect_artifacts, remove_artifact


class AppUninstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.temp.name, "home")
        self.app = os.path.join(self.home, "Applications", "Demo.app")
        os.makedirs(os.path.join(self.app, "Contents"))
        with open(os.path.join(self.app, "Contents", "Info.plist"), "wb") as info_file:
            plistlib.dump({"CFBundleIdentifier": "com.example.demo", "CFBundleName": "Demo"}, info_file)

    def tearDown(self):
        self.temp.cleanup()

    def _touch(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as artifact:
            artifact.write("fixture")
        return path

    def test_finds_exact_app_artifacts_and_owned_agent(self):
        cache = self._touch(os.path.join(self.home, "Library/Caches/com.example.demo/cache"))
        pref = self._touch(os.path.join(self.home, "Library/Preferences/com.example.demo.plist"))
        saved = self._touch(os.path.join(self.home, "Library/Saved Application State/com.example.demo.savedState/state"))
        agent = os.path.join(self.home, "Library/LaunchAgents/com.example.demo.agent.plist")
        os.makedirs(os.path.dirname(agent), exist_ok=True)
        with open(agent, "wb") as agent_file:
            plistlib.dump({"Label": "com.example.demo.agent"}, agent_file)
        unrelated = self._touch(os.path.join(self.home, "Library/Caches/com.example.other/cache"))
        shared = self._touch(os.path.join(self.home, "Library/Group Containers/group.example.shared/data"))

        found = collect_artifacts("com.example.demo", "Demo", self.home, self.app)

        self.assertEqual(set(found), {self.app, os.path.dirname(cache), pref,
                                      os.path.dirname(saved), agent})
        self.assertNotIn(unrelated, found)
        self.assertNotIn(os.path.dirname(shared), found)

    def test_reads_bundle_id_from_app_and_rejects_system_location(self):
        self.assertEqual(app_identity(self.app)[:2], ("com.example.demo", "Demo"))
        with patch("jarvisd.app_uninstaller.os.path.realpath", return_value="/System/Applications/Demo.app"):
            with self.assertRaisesRegex(ValueError, "sistem konumları"):
                app_identity(self.app)

    def test_remove_artifact_does_not_follow_symlink(self):
        target = self._touch(os.path.join(self.temp.name, "target"))
        link = os.path.join(self.temp.name, "link")
        os.symlink(target, link)

        remove_artifact(link)

        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.isfile(target))


if __name__ == "__main__":
    unittest.main()