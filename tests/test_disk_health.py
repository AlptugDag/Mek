import plistlib
import unittest
from types import SimpleNamespace

from jarvisd.disk_health import inspect_disk, verify_volume


class DiskHealthTests(unittest.TestCase):
    def test_smart_failure_is_reported(self):
        payload = plistlib.dumps({"DeviceIdentifier": "disk3s1", "VolumeName": "Macintosh HD",
                                  "SMARTStatus": "Failing"})
        result = inspect_disk(lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload, stderr=b""))

        self.assertEqual(result["device"], "disk3s1")
        self.assertIn("arıza", result["warning"])

    def test_unsupported_smart_is_not_a_failure(self):
        payload = plistlib.dumps({"DeviceIdentifier": "disk3s1", "VolumeName": "Macintosh HD"})
        result = inspect_disk(lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=payload, stderr=b""))

        self.assertEqual(result["smart"], "Not Supported")
        self.assertIsNone(result["warning"])

    def test_volume_verification_does_not_repair(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="The volume was verified.", stderr="")

        result = verify_volume(runner)

        self.assertTrue(result["ok"])
        self.assertEqual(calls, [["diskutil", "verifyVolume", "/"]])


if __name__ == "__main__":
    unittest.main()