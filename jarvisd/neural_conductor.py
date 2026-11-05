#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mek Neural Conductor
Apple Intelligence (FoundationModels) ile gerçek zamanlı dinamik süreç ve kurgu yönetimi.
"""

import os
import sys
import json
import re
import logging
import subprocess
from typing import Dict, Any, List, Optional

log = logging.getLogger("mek.neural_conductor")

JARVIS_AI_BIN = os.path.expanduser("~/.jarvis/ai/jarvis-ai")


class NeuralConductor:
    """Apple Intelligence tabanlı akıllı süreç ve kurgu orkestratörü."""

    def __init__(self, services=None, kurgu_mgr=None):
        self.services = services
        self.kurgu = kurgu_mgr

    def is_available(self) -> bool:
        return os.path.exists(JARVIS_AI_BIN) and os.access(JARVIS_AI_BIN, os.X_OK)

    def consult(self, prompt: str, instructions: str = "") -> Optional[str]:
        """Cihaz üzerindeki Apple Intelligence (FoundationModels) modeline doğrudan sorgu gönderir."""
        if not self.is_available():
            return None
        try:
            cmd = [JARVIS_AI_BIN]
            if instructions:
                cmd.append(instructions)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            out, err = proc.communicate(input=prompt, timeout=12)
            if proc.returncode == 0 and out.strip():
                return out.strip()
            log.warning("Apple Intelligence hata (%d): %s", proc.returncode, err)
            return None
        except Exception as e:
            log.error("Neural Conductor sorgu hatası: %s", e)
            return None

    def orchestrate(self, user_query: str = "", telemetry: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Sistem telemetrisini ve kurgu durumunu Apple Intelligence ile analiz eder,
        dinamik optimizasyon ve kurgu eylemlerini planlar ve uygular.
        """
        telemetry = telemetry or {}
        kurgu_state = self.kurgu.status() if self.kurgu else "Bilinmiyor"

        prompt = f"""
Sistem Telemetrisi:
- Öndeki uygulama: {telemetry.get("front_app", "Bilinmiyor")}
- CPU Kullanımı: %{telemetry.get("cpu", 0):.0f}
- GPU Kullanımı: %{telemetry.get("gpu", 0):.0f}
- Bellek Basıncı: %{telemetry.get("ram", 0):.0f} | Takas: {telemetry.get("swap", "0 GB")}
- Termal Durum: {telemetry.get("thermal", "Normal")}
- Aktif Kurgu Ortamı: {kurgu_state}

Kullanıcı İsteği: "{user_query}"

GÖREV:
Mac Studio M serisi donanımını ve kurgu yazılımlarını (Premiere Pro, DaVinci Resolve) en yüksek verimde yönet.
Gereken kararı analiz et ve aşağıdaki JSON şemasında çıktı ver:
{{
  "aciklama": "Kullanıcıya Mac Studio dahili hoparlöründen seslendirilecek doğal Türkçe yanıt",
  "eylemler": ["kurgu_optimize", "kurgu_modu_ac", "davinci_render", "premiere_export", "fan_sessiz", "onbellek_temizle"]
}}
"""
        instructions = (
            "Sen Mac Studio üzerinde çalışan profesyonel Apple Intelligence Kurgu ve Süreç Yöneticisisin (Mek Neural Conductor). "
            "Kurgucunun zaman kaybetmesini önleyen, sistem kaynaklarını ve pro video araçlarını en üst seviyede yöneten bir yapay zekâsın. "
            "Yalnızca geçerli JSON formatında yanıt ver."
        )

        raw_response = self.consult(prompt, instructions=instructions)
        executed_actions = []
        explanation = ""

        if raw_response:
            try:
                # JSON bloklarını ayıkla
                json_match = re.search(r"\{.*\}", raw_response, re.DOTALL)
                if json_match:
                    plan = json.loads(json_match.group(0))
                    explanation = plan.get("aciklama", "")
                    actions = plan.get("eylemler", [])

                    # Eylemleri icra et
                    for act in actions:
                        res = self.execute_action(act)
                        if res:
                            executed_actions.append(res)
            except Exception as e:
                log.warning("Apple Intelligence JSON ayrıştırma hatası: %s", e)
                explanation = raw_response

        if not explanation:
            explanation = "Apple Intelligence sistem ve kurgu ortamını optimize etti."

        return {
            "explanation": explanation,
            "actions": executed_actions,
            "raw": raw_response
        }

    def execute_action(self, action_name: str) -> Optional[str]:
        """Planlanan kurgu veya süreç eylemini icra eder."""
        act = action_name.lower().strip()
        try:
            if act in ("kurgu_optimize", "optimize", "kurgu_modu_ac"):
                if self.kurgu:
                    return self.kurgu.optimize_environment()
            elif act == "davinci_render":
                if self.kurgu:
                    return self.kurgu.start_davinci_render()
            elif act == "premiere_export":
                if self.kurgu:
                    return self.kurgu.export_premiere_to_media_encoder()
            elif act == "fan_sessiz":
                if self.services and hasattr(self.services, "thermal_fan") and self.services.thermal_fan:
                    return self.services.thermal_fan.set_mode("sessiz")
            elif act in ("onbellek_temizle", "ram_relief"):
                subprocess.run(["/usr/sbin/purge"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "İnaktif dosya önbelleği temizlendi."
            elif act.startswith("film_rengi"):
                parts = action_name.split(":", 1)
                film = parts[1].strip() if len(parts) > 1 else "matrix"
                if self.kurgu:
                    return self.kurgu.apply_film_color(film)
            elif act.startswith("efekt"):
                parts = action_name.split(":", 1)
                desc = parts[1].strip() if len(parts) > 1 else "sinematik parıltı"
                if self.kurgu:
                    return self.kurgu.synthesize_effect(desc)
            elif act.startswith("kaba_kurgu"):
                parts = action_name.split(":", 1)
                p = parts[1].strip() if len(parts) > 1 else ""
                if self.kurgu and p:
                    return self.kurgu.build_rough_cut(p)
            elif act.startswith("renk_klonla"):
                parts = action_name.split(":", 1)
                p = parts[1].strip() if len(parts) > 1 else ""
                if self.kurgu and p:
                    return self.kurgu.clone_image_color(p)
        except Exception as e:
            log.error("Eylem icra hatası (%s): %s", action_name, e)
        return None
