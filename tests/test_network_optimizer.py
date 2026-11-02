import unittest
from types import SimpleNamespace

from jarvisd.network_optimizer import NetworkOptimizer, active_quality_test, classify
from jarvisd.events import EventBus


class NetworkOptimizerTests(unittest.TestCase):
    def test_healthy_link(self):
        state, _ = classify("en0", True, {"latency_ms": 4, "loss_pct": 0},
                            {"latency_ms": 28, "loss_pct": 0}, 15)
        self.assertEqual(state, "healthy")

    def test_congestion_and_packet_loss_are_detected(self):
        state, _ = classify("en0", True, {"latency_ms": 5, "loss_pct": 10},
                            {"latency_ms": 35, "loss_pct": 0}, 10)
        self.assertEqual(state, "degraded")

    def test_high_utilization_is_reported_without_false_link_failure(self):
        state, _ = classify("en0", True, {"latency_ms": 5, "loss_pct": 0},
                            {"latency_ms": 30, "loss_pct": 0}, 92)
        self.assertEqual(state, "busy")

    def test_silent_icmp_is_unknown_not_offline(self):
        state, _ = classify("en0", True, {"latency_ms": None, "loss_pct": 100},
                            {"latency_ms": None, "loss_pct": 100}, None)
        self.assertEqual(state, "unknown")

    def test_missing_default_interface_is_offline(self):
        state, _ = classify(None, False, None, None, None)
        self.assertEqual(state, "offline")

    def test_sampling_computes_link_throughput(self):
        times = iter((1.0, 3.0))
        counters = iter((SimpleNamespace(bytes_recv=0, bytes_sent=0),
                         SimpleNamespace(bytes_recv=20_000_000, bytes_sent=5_000_000)))
        optimizer = NetworkOptimizer(
            route_getter=lambda: {"interface": "en0", "gateway": "192.0.2.1"},
            probe=lambda host: {"latency_ms": 5, "loss_pct": 0},
            stats_getter=lambda: {"en0": SimpleNamespace(isup=True, speed=100)},
            counters_getter=lambda pernic: {"en0": next(counters)},
            clock=lambda: next(times))

        optimizer.sample()
        sample = optimizer.sample()

        self.assertAlmostEqual(sample["rx_mbps"], 80)
        self.assertAlmostEqual(sample["tx_mbps"], 20)
        self.assertAlmostEqual(sample["utilization_pct"], 80)

    def test_sampling_interval_adapts_to_network_state(self):
        self.assertLess(NetworkOptimizer.recommended_interval("degraded"),
                        NetworkOptimizer.recommended_interval("healthy"))

    def test_network_module_publishes_sample_and_only_changed_states(self):
        bus = EventBus()
        state_changes = []
        samples = []
        bus.subscribe("network.state_changed", lambda event, data: state_changes.append(data["state"]))
        bus.subscribe("network.sample", lambda event, data: samples.append(data["result"]))
        optimizer = NetworkOptimizer(route_getter=lambda: {"interface": None, "gateway": None},
                                     stats_getter=lambda: {}, counters_getter=lambda pernic: {}, events=bus)

        optimizer.sample()
        optimizer.sample()

        self.assertEqual(state_changes, ["offline"])
        self.assertEqual(len(samples), 2)
        self.assertLess(NetworkOptimizer.recommended_interval("healthy", performance=True),
                        NetworkOptimizer.recommended_interval("healthy"))

    def test_active_quality_test_parses_macos_metrics(self):
        output = ("Upload capacity: 42.1 Mbps\nDownload capacity: 510.2 Mbps\n"
                  "Responsiveness: Medium (115 RPM)\nIdle Latency: 18.3 milliseconds\n")
        result = active_quality_test("en0", lambda *args, **kwargs:
                                     type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})())

        self.assertTrue(result["ok"])
        self.assertIn("Download capacity: 510.2 Mbps", result["message"])


if __name__ == "__main__":
    unittest.main()