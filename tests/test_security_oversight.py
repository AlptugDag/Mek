import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jarvisd"))
import security  # noqa: E402


class OwnItemTest(unittest.TestCase):
    def test_own_menubar_is_exempt(self):
        prog = os.path.join(security.MEK_HOME, "mek-menubar")
        self.assertTrue(security.is_own_item("com.alptug.mek.ui", prog))

    def test_foreign_item_is_not_exempt(self):
        self.assertFalse(security.is_own_item("com.evil.x", "/tmp/x"))
        self.assertFalse(security.is_own_item("com.alptug.mek.ui", "/tmp/x"))


if __name__ == "__main__":
    unittest.main()
