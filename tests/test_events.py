import unittest
from unittest.mock import patch

from jarvisd.events import EventBus


class EventBusTests(unittest.TestCase):
    def test_subscribers_receive_named_and_wildcard_events(self):
        bus = EventBus()
        received = []
        bus.subscribe("network.changed", lambda event, data: received.append((event, data["state"])))
        bus.subscribe("*", lambda event, data: received.append(("*", event)))

        bus.publish("network.changed", state="degraded")

        self.assertEqual(received, [("network.changed", "degraded"), ("*", "network.changed")])

    def test_unsubscribe_and_handler_failure_are_isolated(self):
        bus = EventBus()
        received = []
        bus.subscribe("crash", lambda *_: (_ for _ in ()).throw(RuntimeError("failure")))
        unsubscribe = bus.subscribe("crash", lambda event, data: received.append(event))

        with patch("jarvisd.events.log.exception") as logged:
            bus.publish("crash")
            logged.assert_called_once()
        unsubscribe()
        bus.publish("crash")

        self.assertEqual(received, ["crash"])


if __name__ == "__main__":
    unittest.main()