#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mek Vision & Color Engine
Apple Intelligence ve CoreImage tabanlı sinematik renk derecelendirme (color grading),
film renk emülasyonu, görselden renk klonlama ve doğal dil efekt sentezi.
"""

import os
import sys
import re
import json
import logging
import subprocess
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import proc


log = logging.getLogger("mek.vision_color")

JARVIS_AI_BIN = os.path.expanduser("~/.jarvis/ai/jarvis-ai")
RESOLVE_LUT_DIR = os.path.expanduser("~/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Mek")
PREMIERE_LUT_DIR = os.path.expanduser("~/Library/Application Support/Adobe/Common/LUTs/Creative")
MEK_LUT_DIR = os.path.expanduser("~/.jarvis/luts")

os.makedirs(RESOLVE_LUT_DIR, exist_ok=True)
os.makedirs(PREMIERE_LUT_DIR, exist_ok=True)
os.makedirs(MEK_LUT_DIR, exist_ok=True)


class VisionColorEngine:
    """Apple Intelligence & CoreImage destekli profesyonel renk ve efekt motoru."""

    def __init__(self, kurgu_mgr=None):
        self.kurgu = kurgu_mgr

    # ------------------------------------------------------------------ 3D LUT Üretimi
    @staticmethod
    def generate_3d_lut(name: str, transform_func, size: int = 33) -> str:
        """
        33x33x33 3D .cube LUT üretir ve hem DaVinci Resolve hem Adobe Premiere Pro dizinlerine kaydeder.
        """
        clean_name = re.sub(r"[^\w\-]", "_", name)
        lut_filename = f"Mek_{clean_name}.cube"

        r = np.linspace(0.0, 1.0, size, dtype=np.float32)
        g = np.linspace(0.0, 1.0, size, dtype=np.float32)
        b = np.linspace(0.0, 1.0, size, dtype=np.float32)
        R, G, B = np.meshgrid(r, g, b, indexing="ij")

        # Giriş kafesini [N, 3] şekline getir
        rgb_in = np.stack([R, G, B], axis=-1)
        rgb_out = transform_func(rgb_in)
        rgb_out = np.clip(rgb_out, 0.0, 1.0)

        header = [
            f"# Mek Vision Engine 3D LUT: {name}",
            f'TITLE "{clean_name}"',
            f"LUT_3D_SIZE {size}",
            "DOMAIN_MIN 0.0 0.0 0.0",
            "DOMAIN_MAX 1.0 1.0 1.0"
        ]

        # .cube formatı B dışta, G ortada, R içte sırayla yazılır
        lines = header
        for bi in range(size):
            for gi in range(size):
                for ri in range(size):
                    ro, go, bo = rgb_out[ri, gi, bi]
                    lines.append(f"{ro:.6f} {go:.6f} {bo:.6f}")

        content = "\n".join(lines) + "\n"

        target_paths = [
            os.path.join(RESOLVE_LUT_DIR, lut_filename),
            os.path.join(PREMIERE_LUT_DIR, lut_filename),
            os.path.join(MEK_LUT_DIR, lut_filename)
        ]

        for p in target_paths:
            try:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception as e:
                log.warning("LUT kaydedilemedi (%s): %s", p, e)

        return target_paths[0]

    # ------------------------------------------------------------------ Film Stilleri
    def apply_film_grade(self, film_name: str) -> Dict[str, Any]:
        """
        Belirtilen filmin ikonik renk paletini 3D LUT olarak üretir ve kurgu yazılımına entegre eder.
        """
        film = film_name.lower().strip()
        display_name = film_name.title()

        def matrix_transform(rgb):
            # The Matrix: Yeşil-camgöbeği gölgeler, derin kontrast, doymuş yeşiller
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            r_out = np.power(r, 1.25) * 0.78
            g_out = np.power(g, 0.92) * 1.18 + (1.0 - lum) * 0.06
            b_out = np.power(b, 1.30) * 0.82 + (1.0 - lum) * 0.02
            return np.stack([r_out, g_out, b_out], axis=-1)

        def blade_runner_transform(rgb):
            # Blade Runner 2049: Kehribar/neon turuncu aydınlıklar vs. derin teal/cyan puslu gölgeler
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            r_out = np.power(r, 0.95) * 1.15 + lum * 0.08
            g_out = np.power(g, 1.05) * 0.96
            b_out = np.power(b, 1.20) * 0.75 + (1.0 - lum) * 0.10
            return np.stack([r_out, g_out, b_out], axis=-1)

        def oppenheimer_transform(rgb):
            # Oppenheimer B&W: Yüksek dinamik aralıklı gümüş-jelatin siyah-beyaz tonlama
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            # S-eğrisi kontrast
            mono = np.where(lum < 0.5, 2.0 * lum * lum, 1.0 - 2.0 * (1.0 - lum) * (1.0 - lum))
            mono = np.power(mono, 1.1)
            return np.stack([mono, mono, mono], axis=-1)

        def amelie_transform(rgb):
            # Amélie: Sıcak altın sarısı, canlı kırmızı ve yeşiller, masalsı sıcaklık
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            r_out = np.power(r, 0.92) * 1.14 + 0.03
            g_out = np.power(g, 0.96) * 1.10 + 0.02
            b_out = np.power(b, 1.35) * 0.70
            return np.stack([r_out, g_out, b_out], axis=-1)

        def dune_transform(rgb):
            # Dune (Arrakis): Bleach bypass, çöl bronzu, soluk kum tonları, sert mikro kontrast
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            desat_r = lum * 0.4 + r * 0.6
            desat_g = lum * 0.4 + g * 0.6
            desat_b = lum * 0.4 + b * 0.6
            r_out = desat_r * 1.12 + 0.04
            g_out = desat_g * 1.02 + 0.02
            b_out = desat_b * 0.82
            return np.stack([r_out, g_out, b_out], axis=-1)

        def seven_fincher_transform(rgb):
            # Seven / Fincher: Kasvetli sarı-yeşil zeytin tonları, düşük doygunluk, sert gölgeler
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            r_out = (lum * 0.5 + r * 0.5) * 0.95
            g_out = (lum * 0.5 + g * 0.5) * 1.05 + 0.03
            b_out = (lum * 0.5 + b * 0.5) * 0.80
            return np.stack([r_out, g_out, b_out], axis=-1)

        def wes_anderson_transform(rgb):
            # Wes Anderson: Pastel pembeler, tatlı sarı ve camgöbeği, yumuşak kontrast
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            r_out = np.power(r, 0.95) * 1.08 + 0.04
            g_out = np.power(g, 0.98) * 1.04 + 0.03
            b_out = np.power(b, 1.05) * 0.92 + 0.02
            return np.stack([r_out, g_out, b_out], axis=-1)

        def teal_orange_transform(rgb):
            # Hollywood Teal & Orange
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            r_out = np.power(r, 0.95) + lum * 0.10
            g_out = np.power(g, 1.02)
            b_out = np.power(b, 1.15) + (1.0 - lum) * 0.12
            return np.stack([r_out, g_out, b_out], axis=-1)

        def kodachrome_transform(rgb):
            # Vintage Kodachrome 64: Doygun sıcak kırmızılar, kadife mavi, yumuşak ışık yuvarlaması
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            r_out = np.clip(np.power(r, 0.90) * 1.10, 0, 1)
            g_out = np.clip(np.power(g, 1.02) * 0.98, 0, 1)
            b_out = np.clip(np.power(b, 1.08) * 0.95, 0, 1)
            return np.stack([r_out, g_out, b_out], axis=-1)

        def noir_transform(rgb):
            # Film Noir: Derin gölgeler, parlak beyazlar, yüksek kontrast
            r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            mono = np.power(lum, 1.35) * 1.15
            return np.stack([mono, mono, mono], axis=-1)

        profiles = {
            "matrix": (matrix_transform, "The Matrix yeşil-siyan sinematik renk paleti"),
            "blade runner": (blade_runner_transform, "Blade Runner 2049 kehribar/teal neon renk paleti"),
            "blade runner 2049": (blade_runner_transform, "Blade Runner 2049 kehribar/teal neon renk paleti"),
            "oppenheimer": (oppenheimer_transform, "Oppenheimer gümüş-jelatin siyah-beyaz tonlaması"),
            "amelie": (amelie_transform, "Amélie masalsı sıcak altın-yeşil renk paleti"),
            "dune": (dune_transform, "Dune Arrakis çöl bronzu ve bleach bypass tonlaması"),
            "seven": (seven_fincher_transform, "Seven / David Fincher kasvetli sarı-yeşil zeytin renk paleti"),
            "fincher": (seven_fincher_transform, "Seven / David Fincher kasvetli sarı-yeşil zeytin renk paleti"),
            "wes anderson": (wes_anderson_transform, "Wes Anderson pastel pembe/krem masalsı renk paleti"),
            "teal orange": (teal_orange_transform, "Hollywood Teal & Orange tamamlayıcı renk paleti"),
            "kodachrome": (kodachrome_transform, "Klasik 35mm Kodachrome zengin film renk tonlaması"),
            "noir": (noir_transform, "Klasik Film Noir derin siyah-beyaz yüksek kontrast tonlaması"),
        }

        matched_key = next((k for k in profiles if k in film), None)
        if not matched_key:
            # Tanınmayan film ise genel sinematik teal & orange veya varsayılan stil
            transform_fn = teal_orange_transform
            desc = f"{display_name} estetiğinde sinematik kontrast ve sıcak/soğuk renk ayrımı"
            clean_tag = film.replace(" ", "_")
        else:
            transform_fn, desc = profiles[matched_key]
            clean_tag = matched_key.replace(" ", "_")

        lut_path = self.generate_3d_lut(clean_tag, transform_fn)
        applied_to_resolve = False

        # DaVinci Resolve açıksa aktif klibe doğrudan uygula
        if self.kurgu and self.kurgu.is_davinci_running():
            applied_to_resolve = self._apply_lut_to_davinci(lut_path)

        return {
            "film": display_name,
            "description": desc,
            "lut_path": lut_path,
            "lut_name": os.path.basename(lut_path),
            "applied_to_davinci": applied_to_resolve,
            "premiere_ready": True
        }

    # ------------------------------------------------------------------ Görselden Renk Klonlama
    def clone_color_from_image(self, ref_image_path: str, look_name: str = "Referans") -> Dict[str, Any]:
        """
        Referans görselin renk dağılımını (histogram ve renk momentleri) analiz ederek 3D LUT üretir.
        """
        if not os.path.exists(ref_image_path):
            return {"error": f"Referans görsel bulunamadı: {ref_image_path}"}

        try:
            import Quartz
            from Cocoa import NSBitmapImageRep

            url = Quartz.CFURLCreateFromFileSystemRepresentation(None, ref_image_path.encode(), len(ref_image_path), False)
            source = Quartz.CGImageSourceCreateWithURL(url, None)
            if not source:
                return {"error": "Görsel formatı okunamadı."}
            cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
            if not cg_image:
                return {"error": "Görsel verisi yüklenemedi."}

            width = Quartz.CGImageGetWidth(cg_image)
            height = Quartz.CGImageGetHeight(cg_image)

            # Piksel verisini örnekle (maks 256x256 hızlı analiz için)
            sample_w, sample_h = min(256, width), min(256, height)
            color_space = Quartz.CGColorSpaceCreateDeviceRGB()
            raw_bytes = bytearray(sample_w * sample_h * 4)
            context = Quartz.CGBitmapContextCreate(
                raw_bytes, sample_w, sample_h, 8, sample_w * 4, color_space, Quartz.kCGImageAlphaPremultipliedLast
            )
            Quartz.CGContextDrawImage(context, Quartz.CGRectMake(0, 0, sample_w, sample_h), cg_image)

            pixels = np.frombuffer(raw_bytes, dtype=np.uint8).reshape((sample_h, sample_w, 4))
            rgb_pixels = pixels[:, :, :3].astype(np.float32) / 255.0

            # Ortalama ve standart sapma (Renk Transferi)
            mean_color = np.mean(rgb_pixels, axis=(0, 1))
            std_color = np.std(rgb_pixels, axis=(0, 1)) + 1e-4

            def clone_transform(rgb_in):
                # Giriş kafesini referans görselin ortalama ve yayılımına uyarla
                in_mean = np.array([0.5, 0.5, 0.5], dtype=np.float32)
                in_std = np.array([0.288, 0.288, 0.288], dtype=np.float32)
                norm = (rgb_in - in_mean) / in_std
                out = (norm * (std_color * 1.1)) + mean_color
                return out

            tag = f"Clone_{os.path.splitext(os.path.basename(ref_image_path))[0]}_{look_name}"
            lut_path = self.generate_3d_lut(tag, clone_transform)

            applied = False
            if self.kurgu and self.kurgu.is_davinci_running():
                applied = self._apply_lut_to_davinci(lut_path)

            return {
                "success": True,
                "reference_image": os.path.basename(ref_image_path),
                "dominant_color_rgb": [round(float(c), 3) for c in mean_color],
                "lut_path": lut_path,
                "lut_name": os.path.basename(lut_path),
                "applied_to_davinci": applied
            }
        except Exception as e:
            log.error("Görsel renk klonlama hatası: %s", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------ Doğal Dil Efekt Sentezi
    def synthesize_effect(self, prompt: str) -> Dict[str, Any]:
        """
        Kullanıcının serbest tarif ettiği efekti Apple Intelligence (FoundationModels) ile
        ayrıştırır; renk derecelendirmesi ve optik efekt bileşenlerini üretir.
        """
        system = (
            "Sen profesyonel bir Colorist ve Görsel Efekt Süpervizörüsün. "
            "Kullanıcının tarif ettiği atmosfer ve efekt isteğini sinematik parametrelere dönüştür. "
            "Yalnızca aşağıdaki JSON formatında yanıt ver:\n"
            "{\n"
            '  "ad": "KısaEfektAdi",\n'
            '  "aciklama": "Efektin Türkçe estetik açıklaması",\n'
            '  "r_mul": 1.1, "g_mul": 1.0, "b_mul": 0.9,\n'
            '  "kontrast": 1.15, "doygunluk": 0.9,\n'
            '  "parilti_bloom": 0.4, "film_greni": 0.35, "vinyet": 0.25\n'
            "}"
        )

        ai_out = None
        if os.access(JARVIS_AI_BIN, os.X_OK):
            out, _err, rc = proc.capture_with_input([JARVIS_AI_BIN, system], prompt, timeout=15)
            if rc == 0 and out.strip():
                ai_out = out.strip()

        # Varsayılan değerler
        fx_params = {
            "ad": "Sinematik_Ozel",
            "aciklama": f"Tarif edilen atmosfer: {prompt}",
            "r_mul": 1.05, "g_mul": 1.0, "b_mul": 0.95,
            "kontrast": 1.1, "doygunluk": 1.0,
            "parilti_bloom": 0.3, "film_greni": 0.2, "vinyet": 0.2
        }

        if ai_out:
            try:
                match = re.search(r"\{.*\}", ai_out, re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))
                    fx_params.update(parsed)
            except Exception as e:
                log.warning("JSON ayrıştırma hatası: %s", e)

        r_m = float(fx_params.get("r_mul", 1.0))
        g_m = float(fx_params.get("g_mul", 1.0))
        b_m = float(fx_params.get("b_mul", 1.0))
        contrast = float(fx_params.get("kontrast", 1.0))
        sat = float(fx_params.get("doygunluk", 1.0))

        def custom_fx_transform(rgb):
            # Kontrast eğrisi
            out = np.power(rgb, 1.0 / max(0.2, contrast))
            # Renk çarpanı
            out[..., 0] *= r_m
            out[..., 1] *= g_m
            out[..., 2] *= b_m
            # Doygunluk
            lum = 0.299 * out[..., 0] + 0.587 * out[..., 1] + 0.114 * out[..., 2]
            lum = lum[..., np.newaxis]
            out = lum + (out - lum) * sat
            return out

        lut_name = fx_params["ad"]
        lut_path = self.generate_3d_lut(lut_name, custom_fx_transform)

        applied = False
        if self.kurgu and self.kurgu.is_davinci_running():
            applied = self._apply_lut_to_davinci(lut_path)

        return {
            "name": fx_params["ad"],
            "description": fx_params["aciklama"],
            "optical_fx": {
                "bloom": fx_params.get("parilti_bloom", 0.0),
                "film_grain": fx_params.get("film_greni", 0.0),
                "vignette": fx_params.get("vinyet", 0.0)
            },
            "lut_path": lut_path,
            "lut_name": os.path.basename(lut_path),
            "applied_to_davinci": applied
        }

    # ------------------------------------------------------------------ DaVinci API Entegrasyonu
    def _apply_lut_to_davinci(self, lut_path: str) -> bool:
        """Açık DaVinci Resolve projesinde seçili klibe üretilen LUT'ı doğrudan atar."""
        if not self.kurgu:
            return False
        resolve = self.kurgu.get_davinci_resolve()
        if not resolve:
            return False
        try:
            proj = resolve.GetProjectManager().GetCurrentProject()
            if not proj:
                return False
            tl = proj.GetCurrentTimeline()
            if not tl:
                return False
            # İlk video kanalındaki aktif klip
            items = tl.GetItemListInTrack("video", 1)
            if not items:
                return False
            cur_item = items[0]
            # 1 numaralı renk düğümüne LUT uygula
            rel_name = os.path.basename(lut_path)
            return cur_item.SetLUT(1, f"Mek/{rel_name}")
        except Exception as e:
            log.warning("DaVinci LUT uygulama hatası: %s", e)
            return False
