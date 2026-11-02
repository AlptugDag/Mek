"""Read-only macOS AC/UPS telemetry and opt-in safe-shutdown threshold."""
import re
import subprocess


def parse_power_status(output):
    source_match = re.search(r"Now drawing from ['\"]([^'\"]+)['\"]", output, re.I)
    source = source_match.group(1) if source_match else "Unknown"
    lines = output.splitlines()
    ups_lines = [line for line in lines if re.search(r"\bUPS\b", line, re.I)]
    detail_lines = [line for line in ups_lines if "%" in line or "remaining" in line.casefold()]
    battery_lines = detail_lines or [line for line in lines if "%" in line] or ups_lines
    detail = battery_lines[0] if battery_lines else ""
    percent_match = re.search(r"(\d{1,3})%", detail)
    remaining_match = re.search(r"(\d+):(\d+)\s+remaining", detail, re.I)
    remaining = (int(remaining_match.group(1)) * 60 + int(remaining_match.group(2))
                 if remaining_match else None)
    is_ups = "ups" in source.casefold() or bool(ups_lines)
    on_battery = ("battery" in source.casefold() or "ups" in source.casefold() or
                  bool(re.search(r"\bdischarging\b", detail, re.I)))
    return {"source": source,
            "is_ups": is_ups,
            "on_battery": on_battery,
            "percent": int(percent_match.group(1)) if percent_match else None,
            "remaining_minutes": remaining,
            "available": source != "Unknown" or bool(detail)}


def read_power_status(runner=None):
    runner = runner or subprocess.run
    try:
        result = runner(["/usr/bin/pmset", "-g", "batt"], capture_output=True,
                        text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"source": "Unknown", "is_ups": False, "on_battery": False,
                "percent": None, "remaining_minutes": None, "available": False,
                "error": str(exc)}
    if result.returncode:
        return {"source": "Unknown", "is_ups": False, "on_battery": False,
                "percent": None, "remaining_minutes": None, "available": False,
                "error": (result.stderr or "pmset başarısız")[:300]}
    return parse_power_status(result.stdout)


def should_shutdown(state, enabled=False, percent_threshold=15, minutes_threshold=5):
    if not enabled or not state.get("available") or not state.get("is_ups") or not state.get("on_battery"):
        return False
    percent = state.get("percent")
    remaining = state.get("remaining_minutes")
    return ((percent is not None and percent <= percent_threshold) or
            (remaining is not None and remaining <= minutes_threshold))


class PowerGuard:
    def __init__(self, events=None, reader=None, critical_percent=15, critical_minutes=5):
        self.events = events
        self.reader = reader or read_power_status
        self.critical_percent = critical_percent
        self.critical_minutes = critical_minutes
        self.last_signature = None
        self.was_critical = False

    def sample(self):
        state = self.reader()
        signature = (state.get("source"), state.get("on_battery"), state.get("is_ups"))
        if self.events and signature != self.last_signature:
            self.events.publish("power.state_changed", previous=self.last_signature,
                                state=state)
        critical = state.get("is_ups") and state.get("on_battery") and (
            state.get("percent") is not None and state["percent"] <= self.critical_percent or
            state.get("remaining_minutes") is not None and state["remaining_minutes"] <= self.critical_minutes)
        if self.events and critical and not self.was_critical:
            self.events.publish("power.ups_critical", state=state)
        self.last_signature = signature
        self.was_critical = bool(critical)
        return state