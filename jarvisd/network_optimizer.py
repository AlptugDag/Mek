"""Adaptive, read-only network health sampling for macOS."""
import re
import subprocess
import time

import psutil


def default_route(runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {"interface": None, "gateway": None}
    if result.returncode:
        return {"interface": None, "gateway": None}
    values = dict(re.findall(r"^\s*(interface|gateway):\s*(\S+)", result.stdout, re.M))
    return {"interface": values.get("interface"), "gateway": values.get("gateway")}


def ping_host(host, runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["ping", "-n", "-c", "2", "-W", "1000", host],
                        capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.SubprocessError):
        return {"latency_ms": None, "loss_pct": None}
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    loss_match = re.search(r"([\d.]+)%\s*packet loss", output, re.I)
    latency_match = re.search(r"(?:round-trip|rtt).*?=\s*[\d.]+/([\d.]+)/", output, re.I)
    return {"latency_ms": float(latency_match.group(1)) if latency_match else None,
            "loss_pct": float(loss_match.group(1)) if loss_match else None}


def active_quality_test(interface, runner=None):
    runner = runner or subprocess.run
    if not interface or not re.fullmatch(r"[A-Za-z0-9._-]+", interface):
        return {"ok": False, "message": "Varsayılan ağ arayüzü bulunamadı."}
    try:
        result = runner(["networkQuality", "-I", interface], capture_output=True,
                        text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": f"Aktif ağ testi çalıştırılamadı: {exc}"}
    output = (result.stdout or result.stderr or "").strip()
    if result.returncode:
        return {"ok": False, "message": f"Ağ testi başarısız: {output[-500:]}"}
    fields = {}
    for label in ("Upload capacity", "Download capacity", "Responsiveness", "Idle Latency"):
        match = re.search(rf"^{re.escape(label)}:\s*(.+)$", output, re.M | re.I)
        if match:
            fields[label] = match.group(1).strip()
    if not fields:
        return {"ok": False, "message": f"Ağ testi sonucu çözümlenemedi: {output[-500:]}"}
    details = "; ".join(f"{label}: {value}" for label, value in fields.items())
    return {"ok": True, "message": f"{interface} aktif ağ testi: {details}."}


def classify(interface, link_up, gateway, internet, utilization_pct):
    if not interface or not link_up:
        return "offline", "Varsayılan ağ arayüzü kullanılamıyor."

    probes = (gateway or {}, internet or {})
    observed = [probe for probe in probes if probe.get("loss_pct") is not None]
    partial_loss = any(0 < probe["loss_pct"] < 100 for probe in observed)
    high_latency = any(probe.get("latency_ms") is not None and probe["latency_ms"] >= 150
                       for probe in probes)
    if partial_loss or high_latency:
        return "degraded", "Gecikme veya paket kaybı yüksek."
    if utilization_pct is not None and utilization_pct >= 80:
        return "busy", "Arayüz kapasitesi yoğun kullanılıyor."
    if observed and all(probe["loss_pct"] == 100 for probe in observed):
        return "unknown", "ICMP yanıtı alınamadı; ağ durumu ping ile doğrulanamıyor."
    if not observed:
        return "unknown", "ICMP ölçümü alınamadı; ağ durumu belirsiz."
    return "healthy", "Gecikme ve paket kaybı normal."


class NetworkOptimizer:
    """Samples link health and suggests safe actions without changing network settings."""

    def __init__(self, route_getter=None, probe=None, stats_getter=None, counters_getter=None, clock=None,
                 events=None):
        self.route_getter = route_getter or default_route
        self.probe = probe or ping_host
        self.stats_getter = stats_getter or psutil.net_if_stats
        self.counters_getter = counters_getter or psutil.net_io_counters
        self.clock = clock or time.monotonic
        self.events = events
        self.last_state = None
        self.previous = None

    @staticmethod
    def recommended_interval(state, performance=False):
        if state in ("offline", "degraded"):
            return 60
        if state == "busy":
            return 90
        if state == "unknown":
            return 600
        return 180 if performance else 300

    def sample(self):
        route = self.route_getter()
        interface = route.get("interface")
        stats = self.stats_getter().get(interface) if interface else None
        counters = self.counters_getter(pernic=True).get(interface) if interface else None
        now = self.clock()
        rx_mbps = tx_mbps = utilization = None
        if counters and stats and self.previous and self.previous[0] == interface:
            previous_interface, previous_time, previous_rx, previous_tx = self.previous
            elapsed = now - previous_time
            if elapsed > 0:
                rx_mbps = max(0, counters.bytes_recv - previous_rx) * 8 / elapsed / 1_000_000
                tx_mbps = max(0, counters.bytes_sent - previous_tx) * 8 / elapsed / 1_000_000
                if stats.speed:
                    utilization = max(rx_mbps, tx_mbps) / stats.speed * 100
        if counters:
            self.previous = (interface, now, counters.bytes_recv, counters.bytes_sent)
        else:
            self.previous = None

        gateway = self.probe(route["gateway"]) if route.get("gateway") else None
        internet = self.probe("1.1.1.1") if interface and stats and stats.isup else None
        state, reason = classify(interface, bool(stats and stats.isup), gateway, internet, utilization)
        result = {"interface": interface,
                  "gateway_address": route.get("gateway"),
                  "link_up": bool(stats and stats.isup),
                  "speed_mbps": stats.speed if stats else None,
                  "rx_mbps": rx_mbps,
                  "tx_mbps": tx_mbps,
                  "utilization_pct": utilization,
                  "gateway": gateway,
                  "internet": internet,
                  "state": state,
                  "reason": reason}
        if self.events:
            self.events.publish("network.sample", result=result)
            if state != self.last_state:
                self.events.publish("network.state_changed", previous=self.last_state,
                                    state=state, result=result)
        self.last_state = state
        return result

    def active_quality_test(self):
        return active_quality_test(self.route_getter().get("interface"))