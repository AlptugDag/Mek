import unittest

from jarvisd.events import EventBus
from jarvisd.power_guard import PowerGuard, parse_power_status, should_shutdown


class PowerGuardTests(unittest.TestCase):
    def test_parses_ups_percent_and_remaining_time(self):
        state = parse_power_status("""Now drawing from 'UPS Power'
-UPS-0 (id=123) 12%; discharging; (3:40 remaining)
""")

        self.assertTrue(state["is_ups"])
        self.assertTrue(state["on_battery"])
        self.assertEqual(state["percent"], 12)
        self.assertEqual(state["remaining_minutes"], 220)

    def test_shutdown_is_opt_in_and_requires_external_ups(self):
        ups = {"available": True, "is_ups": True, "on_battery": True,
               "percent": 10, "remaining_minutes": 20}
        laptop = {**ups, "is_ups": False}

        self.assertFalse(should_shutdown(ups))
        self.assertTrue(should_shutdown(ups, enabled=True))
        self.assertFalse(should_shutdown(laptop, enabled=True))

    def test_power_guard_publishes_changes_and_critical_once(self):
        states = iter([
            {"source": "AC Power", "available": True, "is_ups": True, "on_battery": False,
             "percent": 100, "remaining_minutes": None},
            {"source": "UPS Power", "available": True, "is_ups": True, "on_battery": True,
             "percent": 10, "remaining_minutes": 4},
            {"source": "UPS Power", "available": True, "is_ups": True, "on_battery": True,
             "percent": 9, "remaining_minutes": 3},
        ])
        bus = EventBus()
        critical = []
        bus.subscribe("power.ups_critical", lambda event, data: critical.append(data["state"]))
        guard = PowerGuard(bus, reader=lambda: next(states))

        guard.sample()
        guard.sample()
        guard.sample()

        self.assertEqual(len(critical), 1)


if __name__ == "__main__":
    unittest.main()