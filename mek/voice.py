# -*- coding: utf-8 -*-
"""Ses katmanı: kalıcı EMA Lightning TTS (model bir kez yüklenir) + sürekli dinleyen mikrofon.

Dinleme zinciri: enerji tabanlı VAD -> konuşma parçası -> mlx-whisper (Neural Engine/GPU)
-> 'Mek' ile başlıyorsa komut olarak kuyruğa. Mek konuşurken kendi sesini duymasın diye
mikrofon parçaları atılır.
"""
import logging
import queue
import re
import sys
import threading
import time
import unicodedata

import numpy as np
import sounddevice as sd

sys.path.insert(0, __import__("os").path.expanduser("~/.jarvis"))
import jarvis as legacy  # EQ ve Mac Studio hoparlör yönlendirmesi oradan
import ui
from wakeword import WakeWord

# Whisper'ın kısa/sessiz seste uydurduğu bilinen cümleler
HALLUCINATIONS = re.compile(r"altyazı m ?k|izlediğiniz için teşekkür|abone ol|altyazı|teşekkürler izlediğiniz|"
                            r"^(bir|ve|evet|hı|hm+)$")

log = logging.getLogger("mek.voice")
SR = 16000

try:  # MLX'in GPU önbelleğini sınırla; boşta bellek tutmasın
    import mlx.core as _mx
    _mx.set_cache_limit(128 * 1024 * 1024)
except Exception:
    _mx = None


def normalize(text):
    t = text.lower().replace("i̇", "i")
    t = unicodedata.normalize("NFC", t)
    return re.sub(r"[^\wçğıöşü ]+", " ", t).strip()


class Speaker:
    def __init__(self):
        self.on_done = None  # konuşma bitince çağrılır (sohbet modu)
        self._tts = None
        self._lock = threading.Lock()
        self.speaking = threading.Event()
        self.last_spoke = 0.0
        self._stop = threading.Event()

    def _engine(self):
        if self._tts is None:
            from ema_lightning import EMA
            self._tts = EMA()
        return self._tts

    def warmup(self):
        try:
            self._engine()
        except Exception as e:
            log.error("TTS yüklenemedi: %s", e)

    def stop(self):
        self._stop.set()
        sd.stop()

    def say(self, text):
        if not text:
            return
        log.info("SAY: %s", text)
        with self._lock:
            self._stop.clear()
            self.speaking.set()
            ui.set("speaking", text)
            try:
                speech = self._engine().say(text, speed=legacy.SPEECH_SPEED)
                audio = legacy.apply_mac_studio_eq(speech.audio, fs=speech.sample_rate)
                ca_id, sd_idx = legacy.get_mac_studio_devices()
                if ca_id is not None:
                    legacy.set_mac_studio_hardware_volume(ca_id, legacy.TARGET_VOLUME)
                if not self._stop.is_set():
                    sd.play(audio, samplerate=speech.sample_rate, device=sd_idx)
                    sd.wait()
            except Exception as e:
                log.error("TTS hatası: %s", e)
                # Son çare: macOS'un yerleşik sesi
                import subprocess
                subprocess.run(["say", "-v", "Yelda", text], check=False)
            finally:
                self.last_spoke = time.time()
                self.speaking.clear()
                ui.set("listening", "")
                if self.on_done and not self._stop.is_set():
                    self.on_done()


class Listener(threading.Thread):
    """Mikrofonu dinler, 'Mek ...' ile başlayan cümleleri out_queue'ya koyar."""

    FRAME = 480  # 30 ms

    def __init__(self, cfg, speaker, out_queue, is_paused, has_pending=lambda: False):
        super().__init__(daemon=True, name="listener")
        self.cfg = cfg
        self.speaker = speaker
        self.out = out_queue
        self.is_paused = is_paused
        self.has_pending = has_pending
        self.wake = set(cfg["wake_words"])
        self._audio = queue.Queue(maxsize=400)
        self._stt = None
        self.awaiting_followup_until = 0.0  # "Mek" tek başına söylenince sonraki cümle komuttur
        self.wakeword = WakeWord()          # kişisel "Mek" şablonları (MFCC + DTW)
        if not self.wakeword.ready:
            log.warning("Kişisel uyandırma şablonu yok; yalnızca Whisper'a güveniliyor")

    def _transcribe(self, audio):
        import mlx_whisper
        res = mlx_whisper.transcribe(
            audio, path_or_hf_repo=self.cfg["stt_model"], language="tr",
            condition_on_previous_text=False, temperature=0.0,
            no_speech_threshold=0.6)
        if _mx is not None:
            _mx.clear_cache()
        segs = res.get("segments") or []
        # Whisper gürültüde/müzikte halüsinasyon üretir; düşük güvenli parçaları at
        if segs and all(s.get("no_speech_prob", 0) > 0.5 or s.get("avg_logprob", 0) < -1.0 for s in segs):
            return ""
        text = (res.get("text") or "").strip()
        # Tek başına "Mek" için daha sıkı güven eşiği (gürültüden uydurulmasın)
        if normalize(text) in self.wake and segs and \
                (segs[0].get("no_speech_prob", 0) > 0.3 or segs[0].get("avg_logprob", 0) < -0.6):
            return ""
        return text

    def _device(self):
        hint = self.cfg.get("input_device_hint", "").lower()
        if not hint:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and hint in d["name"].lower():
                return i
        return None

    def _cb(self, indata, frames, t, status):
        try:
            self._audio.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass

    def run(self):
        while True:
            try:
                self._loop()
            except Exception as e:
                log.error("Dinleyici hatası, 5 sn sonra yeniden: %s", e)
                time.sleep(5)

    def _loop(self):
        noise = 0.004
        speech, silent, active = [], 0, False
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                            blocksize=self.FRAME, device=self._device(), callback=self._cb):
            log.info("Mikrofon açık")
            while True:
                frame = self._audio.get()
                if self.is_paused():
                    if ui.current() not in ("paused", "busy_mic"):
                        ui.set("paused", "")
                    speech, silent, active = [], 0, False
                    continue
                if self.speaker.speaking.is_set() or time.time() - self.speaker.last_spoke < 0.4:
                    speech, silent, active = [], 0, False
                    continue
                if ui.current() in ("paused", "busy_mic"):
                    ui.set("listening", "")
                if ui.current() == "awake" and time.time() > self.awaiting_followup_until:
                    ui.set("listening", "")
                rms = float(np.sqrt(np.mean(frame ** 2)))
                if not active:
                    noise = 0.995 * noise + 0.005 * rms  # gürültü tabanını takip et
                    if rms > max(noise * 3.0, 0.008):
                        active, speech, silent = True, [frame], 0
                    continue
                speech.append(frame)
                silent = silent + 1 if rms < max(noise * 2.0, 0.006) else 0
                dur = len(speech) * self.FRAME / SR
                if silent > 30 or dur > 12:  # ~900 ms sessizlik veya 12 sn sınır
                    active = False
                    if 0.15 < dur:  # "Mek" ~0.2-0.3 sn sürüyor; kısa sesleri atmıyoruz
                        self._handle(np.concatenate(speech))
                    speech = []

    def _wake(self):
        self.awaiting_followup_until = time.time() + 8
        ui.set("awake", "Dinliyorum…")
        self.out.put(("wake", ""))

    def _handle(self, audio):
        dur = len(audio) / SR
        expecting = time.time() < self.awaiting_followup_until or self.has_pending()
        ww = self.wakeword
        if ww.ready and not expecting:
            # 1) Kısa ses: yalnızca kişisel şablonla kontrol (Whisper'a hiç gitmez -> halüsinasyon yok)
            if dur <= 1.0:
                if ww.match(audio):
                    log.info("WAKE (şablon, %.2f sn)", dur)
                    self._wake()
                return
            # 2) Uzun ses: "Mek, ..." ile başlamıyorsa hiç yazıya dökülmez (gizlilik + işlemci tasarrufu)
            if not ww.match_prefix(audio):
                return
            log.info("WAKE+KOMUT (şablon, %.2f sn)", dur)
            try:
                text = self._transcribe(audio)
            except Exception as e:
                log.error("STT hatası: %s", e)
                return
            words = normalize(text).split()
            # Whisper "Mek"i farklı yazmış olabilir: ilk kelime kısa/uydurma ise at
            if words and (words[0] in self.wake or len(words[0]) <= 4 or words[0] in ("mac", "mak", "meg", "mex", "bek")):
                words = words[1:]
            cmd = " ".join(words).strip()
            if cmd and not HALLUCINATIONS.search(cmd):
                log.info("HEARD: %s", cmd)
                ui.set("thinking", "“" + cmd + "”")
                self.out.put(("voice", cmd))
            else:
                self._wake()
            return
        try:
            text = self._transcribe(audio)
        except Exception as e:
            log.error("STT hatası: %s", e)
            return
        if not text:
            return
        norm = normalize(text)
        words = norm.split()
        log.info("HEARD: %s", norm)
        if not words or HALLUCINATIONS.search(norm):
            return
        # Geri sayım sürerken uyandırma kelimesi olmadan da "dur / iptal" kabul edilir
        if self.has_pending() and re.search(r"\b(dur|iptal|vazgeç\w*|hayır)\b", norm):
            self.out.put(("voice", "iptal"))
            return
        # Uyandırma kelimesi ilk kelime olmalı ("mek kapan"), ya da "hey mek ..."
        idxs = [0] + ([1] if words[0] in ("hey", "hay", "ey") else [])
        for idx in idxs:
            if idx < len(words) and words[idx] in self.wake:
                cmd = " ".join(words[idx + 1:]).strip()
                if cmd:
                    ui.set("thinking", "“" + cmd + "”")
                    self.out.put(("voice", cmd))
                else:
                    self._wake()
                return
        if time.time() < self.awaiting_followup_until:
            self.awaiting_followup_until = 0
            ui.set("thinking", "“" + norm + "”")
            self.out.put(("voice", norm))
