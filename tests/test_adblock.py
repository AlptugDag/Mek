import os
import tempfile
import unittest
from types import SimpleNamespace

from jarvisd.adblock import BEGIN, END, change, status


class AdblockTests(unittest.TestCase):
    def test_status_counts_only_managed_entries(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as hosts:
            hosts.write("127.0.0.1 localhost\n")
            hosts.write(f"{BEGIN}\n0.0.0.0 ads.example\n0.0.0.0 tracker.example\n{END}\n")
            path = hosts.name
        try:
            self.assertEqual(status(path), {"enabled": True, "count": 2, "error": None})
        finally:
            os.unlink(path)

    def test_status_reports_malformed_managed_block(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as hosts:
            hosts.write(f"{BEGIN}\n0.0.0.0 ads.example\n")
            path = hosts.name
        try:
            self.assertIsNotNone(status(path)["error"])
        finally:
            os.unlink(path)

    def test_change_invokes_only_fixed_actions(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        self.assertTrue(change("enable", helper="/helper", runner=runner)["ok"])
        self.assertEqual(calls, [["sudo", "-n", "/helper", "adblock-enable"]])
        with self.assertRaises(ValueError):
            change("arbitrary", runner=runner)


if __name__ == "__main__":
    unittest.main()