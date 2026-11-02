import datetime as dt
import ctypes
import queue
import tempfile
import threading
import unittest
from datetime import datetime as RealDateTime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from jarvisd import config
from jarvisd.events import EventBus

with patch.object(ctypes.cdll, "LoadLibrary", return_value=Mock()):
    from jarvisd.jarvisd import Mek


class WatchlistCommandTests(unittest.TestCase):
    def setUp(self):
        self.mek = Mek.__new__(Mek)
        self.mek.cfg = {
            "market_watchlist": [],
            "market_alert_pct": {"default": 3.0},
        }
        self.mek.snap = None

    @patch("jarvisd.jarvisd.config.save")
    def test_add_and_remove_symbols_persisted(self, save):
        added = self.mek.watchlist_command("izleme ekle THYAO.IS AAPL,BTC-USD")

        self.assertIn("THYAO.IS, AAPL, BTC-USD", added)
        self.assertEqual(self.mek.cfg["market_watchlist"], ["THYAO.IS", "AAPL", "BTC-USD"])
        self.assertEqual(save.call_args.args[0]["market_watchlist"], self.mek.cfg["market_watchlist"])

        removed = self.mek.watchlist_command("izleme çıkar AAPL")

        self.assertIn("AAPL", removed)
        self.assertEqual(self.mek.cfg["market_watchlist"], ["THYAO.IS", "BTC-USD"])

    @patch("jarvisd.jarvisd.config.save")
    def test_invalid_symbols_and_missing_removals_do_not_change_settings(self, save):
        self.assertIn("Geçersiz", self.mek.watchlist_command("izleme ekle ' ; rm"))
        self.assertIn("bulunmuyor", self.mek.watchlist_command("izleme çıkar AAPL"))
        save.assert_not_called()
        self.assertEqual(self.mek.cfg["market_watchlist"], [])

    def test_command_help_and_unknown_command_are_explained(self):
        self.assertIn("keyfî terminal komutu çalıştırmaz", self.mek.command("yardım"))
        self.assertIn("mek yardım", self.mek.command("xyzzy"))

    @patch("jarvisd.jarvisd.config.save", side_effect=OSError("disk full"))
    def test_persistence_failure_is_reported_without_changing_runtime_config(self, _save):
        result = self.mek.watchlist_command("izleme ekle AAPL")

        self.assertIn("disk full", result)
        self.assertEqual(self.mek.cfg["market_watchlist"], [])


class MarketAlertTests(unittest.TestCase):
    def setUp(self):
        self.mek = Mek.__new__(Mek)
        self.mek.cfg = {
            "market_watchlist": [],
            "market_alert_pct": {"default": 3.0},
        }
        self.mek.market_seen = {}
        self.mek.events = EventBus()
        self.mek.notify = Mock()

    @patch("jarvisd.jarvisd.apple.quote")
    def test_empty_watchlist_skips_market_requests(self, quote):
        self.mek.market_tick()
        quote.assert_not_called()
        self.mek.notify.assert_not_called()

    @patch("jarvisd.jarvisd.ai_phrase", return_value=None)
    @patch("jarvisd.jarvisd.apple._fmt", return_value="AAPL rose")
    @patch("jarvisd.jarvisd.apple.quote")
    @patch("jarvisd.jarvisd.dt.datetime")
    def test_only_watched_symbol_is_checked_and_alerted(self, mock_datetime, quote, _fmt, _ai):
        mock_datetime.now.return_value = RealDateTime(2026, 11, 2, 12, 0)
        quote.return_value = (100, 4.0, None)
        self.mek.cfg["market_watchlist"] = ["AAPL"]

        self.mek.market_tick()

        quote.assert_called_once_with("AAPL")
        self.mek.notify.assert_called_once()
        self.assertIn("AAPL rose", self.mek.notify.call_args.args[0])


class SpeechQueueTests(unittest.TestCase):
    def test_speech_requests_are_queued_in_order(self):
        mek = Mek.__new__(Mek)
        mek.speech_queue = queue.Queue()
        mek.speech_worker_lock = threading.Lock()
        mek.speech_worker = Mock()
        mek.speech_worker.is_alive.return_value = True

        mek.speak("Günaydın")
        mek.speak("Brifing")

        self.assertEqual(mek.speech_queue.get_nowait(), "Günaydın")
        self.assertEqual(mek.speech_queue.get_nowait(), "Brifing")

    def test_startup_greeting_is_queued_before_briefing(self):
        mek = Mek.__new__(Mek)
        mek.cfg = {"away_threshold_s": 1200}
        mek.T = "Alptuğ Bey"
        mek.was_away = False
        mek.away_events = []
        mek.pro_session_start = None
        mek.renders = Mock()
        mek.renders.update.return_value = []
        mek.renders.active.return_value = []
        timeline = []
        mek.speak = Mock(side_effect=lambda text: timeline.append(("speech", text)))
        mek.notify = Mock(side_effect=lambda text, **kwargs: timeline.append(("notify", text)))
        mek.briefing = Mock(side_effect=lambda: timeline.append(("briefing", None)) or "Hava ve takvim özeti")
        observed = SimpleNamespace(
            t=RealDateTime(2026, 11, 2, 11, 5),
            idle=0,
            render_cpu={},
            pro_pids=[],
            disk_free_gb=100,
            thermal=0,
            swap_gb=0,
            top_mem=[],
        )

        class ImmediateThread:
            def __init__(self, target, daemon):
                self.target = target

            def start(self):
                self.target()

        with tempfile.TemporaryDirectory() as state_dir:
            mek.greet_file = state_dir + "/greeted"
            with patch.object(mek, "_quiet_hours", return_value=False), \
                    patch("jarvisd.jarvisd.threading.Thread", ImmediateThread):
                mek.observe(observed)

        mek.speak.assert_called_once_with("Günaydın Alptuğ Bey. Mek hazır.")
        mek.briefing.assert_called_once_with()
        mek.notify.assert_called_once_with("Hava ve takvim özeti", urgent=True)
        self.assertEqual(timeline[0], ("speech", "Günaydın Alptuğ Bey. Mek hazır."))


if __name__ == "__main__":
    unittest.main()
