# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "jarvisd"))
import context
import foundation_llm
from jarvisd import jarvisd as jarvisd_mod


class FoundationLLMTests(unittest.TestCase):
    def test_status_parses_json_response(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, 0, '{"available":true,"model":"Apple Foundation Models (On-Device)","reason":"ready"}', ""
            )

        with patch.object(foundation_llm, "binary_path", return_value="/fake/jarvis-ai"):
            st = foundation_llm.status(force=True, runner=fake_run)
        self.assertTrue(st["available"])
        self.assertEqual(st["reason"], "ready")

    def test_decide_agent_passes_constrained_tool_names(self):
        seen_cmd = []

        def fake_run(cmd, **kw):
            seen_cmd.extend(cmd)
            out = json.dumps({"kind": "tool", "tool": "donanim_saglik", "arg_key": "", "arg_value": "", "answer": ""})
            return subprocess.CompletedProcess(cmd, 0, out, "")

        with patch.object(foundation_llm, "binary_path", return_value="/fake/jarvis-ai"):
            dec = foundation_llm.decide_agent("sys", "Soru", tool_names=["durum", "donanim_saglik"], runner=fake_run)
        self.assertEqual(dec, {"tool": "donanim_saglik", "args": {}})
        self.assertIn("--tools", seen_cmd)
        self.assertIn("durum,donanim_saglik", seen_cmd)

    def test_advisor_guardrails_block_unnecessary_actions_when_cool_and_healthy(self):
        telemetry = {
            "cpu": 12.0,
            "mem_pct": 45.0,
            "swap_gb": 0.0,
            "pressure": 1,
            "peak_c": 43.0,
            "ssd_c": 30.0,
            "disk_free_pct": 58.0,
            "llm_gb": 0.0,
            "fan_mode": "auto_silent",
        }
        noisy_model_out = json.dumps({
            "health_score": 60,
            "summary": "Sistem normal.",
            "recommended_action": "fan_boost",
            "reason": "Önlem",
            "tip": "İyi görünüyor.",
        })

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, noisy_model_out, "")

        with patch.object(foundation_llm, "binary_path", return_value="/fake/jarvis-ai"):
            adv = foundation_llm.advise_system(telemetry, runner=fake_run)

        self.assertEqual(adv["recommended_action"], "none")
        self.assertGreaterEqual(adv["health_score"], 88)

    def test_advisor_guardrails_allow_fan_boost_when_ssd_or_soc_hot(self):
        telemetry = {
            "cpu": 80.0,
            "mem_pct": 60.0,
            "swap_gb": 0.0,
            "pressure": 1,
            "peak_c": 75.0,
            "ssd_c": 60.0,
            "disk_free_pct": 50.0,
            "llm_gb": 0.0,
            "fan_mode": "auto",
        }
        model_out = json.dumps({
            "health_score": 72,
            "summary": "Sıcaklık yüksek.",
            "recommended_action": "fan_boost",
            "reason": "75C CPU ve 60C SSD",
            "tip": "Ön soğutma başlatın.",
        })

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, model_out, "")

        with patch.object(foundation_llm, "binary_path", return_value="/fake/jarvis-ai"):
            adv = foundation_llm.advise_system(telemetry, runner=fake_run)

        self.assertEqual(adv["recommended_action"], "fan_boost")
        self.assertEqual(adv["health_score"], 72)

    def test_snapshot_has_llm_gb_slot_and_crash_name_parser_handles_excuserfault(self):
        s = context.Snapshot()
        s.llm_gb = 4.2
        self.assertEqual(s.llm_gb, 4.2)

        name, is_uf = jarvisd_mod.Mek._parse_crash_app_name("ExcUserFault_nsattributedstringagent-2026-11-17-173014.000.ips")
        self.assertTrue(is_uf)
        self.assertEqual(name, "nsattributedstringagent")

        name2, is_uf2 = jarvisd_mod.Mek._parse_crash_app_name("Adobe Premiere Pro 2027-2026-11-19-165912.ips")
        self.assertFalse(is_uf2)
        self.assertEqual(name2, "Adobe Premiere Pro 2027")

    def test_status_json_command_returns_valid_json(self):
        mek = jarvisd_mod.Mek.__new__(jarvisd_mod.Mek)
        mek.snap = SimpleNamespace(front="Safari", cpu=10.0, gpu=5, mem_pct=42.0, swap_gb=0.0, disk_free_gb=500.0, pro_pids=[])
        mek.services = SimpleNamespace(
            profile="normal",
            thermal_fan=SimpleNamespace(last_telemetry={"cpu_c": 44.0, "gpu_c": 40.0, "ssd_c": 30.0, "psu_c": 35.0, "peak_c": 44.0, "fan0_rpm": 1330, "fan1_rpm": 1330, "mode": "auto_silent"}),
            ultra_scratch=SimpleNamespace(status_dict=lambda: {"mounted": True, "used_mb": 50.0, "size_gb": 12}),
        )
        mek.renders = SimpleNamespace(active=lambda: [])
        mek._last_llm_gb = 0.0
        with patch.object(foundation_llm, "status", return_value={"available": True, "model": "Apple FM", "reason": "ready"}):
            raw = mek.command("status_json")
        data = json.loads(raw)
        self.assertEqual(data["profile"], "normal")
        self.assertTrue(data["fm_available"])
        self.assertEqual(data["ssd_c"], 30.0)


if __name__ == "__main__":
    unittest.main()
