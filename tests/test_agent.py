import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jarvisd"))
import agent  # noqa: E402


class FakeMek:
    T = "Test"
    cfg = {}
    snap = None
    security = None

    def __init__(self):
        self.calls = []

    def command(self, c):
        self.calls.append(c)
        return "ok " + c


class ParseTest(unittest.TestCase):
    def test_answer_and_tool(self):
        self.assertEqual(agent.parse_decision('x {"answer":"merhaba"} y'), {"answer": "merhaba"})
        self.assertEqual(agent.parse_decision('{"tool":"durum"}'), {"tool": "durum", "args": {}})

    def test_garbage(self):
        self.assertIsNone(agent.parse_decision("selam"))
        self.assertIsNone(agent.parse_decision("{bozuk"))
        self.assertIsNone(agent.parse_decision('["a"]'))


class GateTest(unittest.TestCase):
    def setUp(self):
        self.mek = FakeMek()
        self.ag = agent.Agent(self.mek)

    def script(self, *decisions):
        it = iter(decisions)
        self.ag._decide = lambda *a: next(it)

    def test_confirm_tool_waits_for_user(self):
        self.script({"tool": "guncelle_simdi", "args": {}})
        reply = self.ag.ask("güncelle")
        self.assertIn("onay", reply.lower())
        self.assertEqual(self.mek.calls.count("güncelle şimdi"), 0)
        self.ag.confirm()
        self.assertIn("güncelle şimdi", self.mek.calls)

    def test_cancel_and_expiry(self):
        self.script({"tool": "guncelle_simdi", "args": {}})
        self.ag.ask("güncelle")
        self.ag.cancel()
        self.assertIn("yok", self.ag.confirm())
        self.script({"tool": "guncelle_simdi", "args": {}})
        self.ag.ask("güncelle")
        n, a, d, _ = self.ag.pending
        self.ag.pending = (n, a, d, time.time() - 1)
        self.assertIn("dolmuştu", self.ag.confirm())
        self.assertNotIn("güncelle şimdi", self.mek.calls)

    def test_read_tool_then_answer(self):
        self.script({"tool": "durum", "args": {}}, {"answer": "iyi"})
        self.assertEqual(self.ag.ask("nasılsın"), "iyi")

    def test_unknown_tool_is_rejected(self):
        self.script({"tool": "rm_rf", "args": {}}, {"answer": "olmaz"})
        self.assertEqual(self.ag.ask("sil"), "olmaz")

    def test_bad_args_do_not_crash(self):
        self.script({"tool": "sustur", "args": {"dakika": "çok"}}, {"answer": "tamam"})
        self.assertEqual(self.ag.ask("sus"), "tamam")


if __name__ == "__main__":
    unittest.main()


class MissingArgsTest(unittest.TestCase):
    def test_missing_arg_never_reaches_confirmation(self):
        ag = agent.Agent(FakeMek())
        it = iter([{"tool": "uygulama_kapat", "args": {}}, {"answer": "hangi uygulama?"}])
        ag._decide = lambda *a: next(it)
        self.assertEqual(ag.ask("kapat"), "hangi uygulama?")
        self.assertIsNone(ag.pending)
