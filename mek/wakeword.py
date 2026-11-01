# -*- coding: utf-8 -*-
"""Kişisel uyandırma kelimesi algılayıcı: kullanıcının kendi "Mek" kayıtlarından şablon çıkarır,
gelen sesi MFCC + DTW ile karşılaştırır. Whisper'a göre çok daha güvenilir (kısa tek kelimede
Whisper "Altyazı M.K." gibi halüsinasyonlar üretiyor) ve neredeyse sıfır işlemci harcar.

Şablon ekle:  python wakeword.py ekle <kayit.wav>   (ardı ardına birkaç kez "Mek" deyin)
"""
import json
import os
import sys
import wave

import numpy as np
from scipy.fft import dct

SR = 16000
STATE = os.path.expanduser("~/.jarvis/state/wake_templates.npz")
META = os.path.expanduser("~/.jarvis/state/wake_meta.json")


def _mel_fb(n_fft=512, n_mels=26, fmin=80, fmax=7600):
    def hz2mel(f):
        return 2595 * np.log10(1 + f / 700)

    def mel2hz(m):
        return 700 * (10 ** (m / 2595) - 1)
    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    bins = np.floor((n_fft + 1) * pts / SR).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(1, n_mels + 1):
        l, c, r = bins[i - 1], bins[i], bins[i + 1]
        fb[i - 1, l:c] = (np.arange(l, c) - l) / max(1, c - l)
        fb[i - 1, c:r] = (r - np.arange(c, r)) / max(1, r - c)
    return fb


_FB = _mel_fb()
_WIN = np.hamming(400)


def mfcc(x):
    x = np.append(x[0], x[1:] - 0.97 * x[:-1])  # ön vurgu
    if len(x) < 400:
        x = np.pad(x, (0, 400 - len(x)))
    n = 1 + (len(x) - 400) // 160
    frames = np.stack([x[i * 160:i * 160 + 400] * _WIN for i in range(n)])
    pw = np.abs(np.fft.rfft(frames, 512)) ** 2 / 512
    feat = dct(np.log(pw @ _FB.T + 1e-10), type=2, axis=1, norm="ortho")[:, 1:13]
    feat -= feat.mean(0)  # kanal/mikrofon farkını sil
    return feat / (feat.std(0) + 1e-6)


def dtw(a, b):
    n, m = len(a), len(b)
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1))
    D = np.full((n + 1, m + 1), np.inf)
    D[0, 0] = 0
    for i in range(1, n + 1):
        ci = cost[i - 1]
        for j in range(max(1, i - 15), min(m, i + 15) + 1):  # Sakoe-Chiba bandı
            D[i, j] = ci[j - 1] + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return D[n, m] / (n + m)


def segments(x, min_len=0.12, max_len=1.2):
    F = 480
    fr = x[:len(x) // F * F].reshape(-1, F)
    rms = np.sqrt((fr ** 2).mean(1))
    thr = max(np.percentile(rms, 20) * 3, 0.008)
    act, out, i = rms > thr, [], 0
    while i < len(act):
        if act[i]:
            j = i
            while j < len(act) and (act[j] or act[j:j + 8].any()):
                j += 1
            s, e = max(0, i * F - 1600), min(len(x), j * F + 1600)
            if min_len <= (j - i) * F / SR <= max_len:
                out.append(x[s:e])
            i = j
        else:
            i += 1
    return out


def read_wav(path):
    import subprocess
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16) / 32768.0


def trim(x):
    """Baştaki/sondaki sessizliği at (şablon ve aday aynı biçimde kırpılır)."""
    F = 160
    fr = x[:len(x) // F * F].reshape(-1, F)
    rms = np.sqrt((fr ** 2).mean(1))
    on = np.where(rms > max(rms.max() * 0.08, 0.004))[0]
    if len(on) == 0:
        return x
    return x[max(0, on[0] * F - 480): (on[-1] + 1) * F + 480]


class WakeWord:
    def __init__(self):
        self.templates, self.threshold = [], None
        if os.path.exists(STATE):
            d = np.load(STATE, allow_pickle=True)
            self.templates = list(d["t"])
            try:
                with open(META) as f:
                    self.threshold = json.load(f)["threshold"]
            except Exception:
                pass

    @property
    def ready(self):
        return len(self.templates) >= 3 and self.threshold is not None

    def score(self, audio):
        """En iyi 2 şablona ortalama DTW uzaklığı (küçük = daha benzer)."""
        f = mfcc(trim(audio))
        ds = sorted(dtw(f, t) for t in self.templates)
        return float(np.mean(ds[:2]))

    def match(self, audio):
        return self.ready and self.score(audio) <= self.threshold

    def match_prefix(self, audio):
        """'Mek, Premiere'i aç' tek nefeste söylenirse: baştaki 0.2-0.6 sn'yi dene."""
        if not self.ready:
            return False
        best = min(self.score(audio[:int(SR * L)]) for L in (0.35, 0.45, 0.55, 0.65) if len(audio) > SR * L)
        return best <= self.threshold

    def add(self, path):
        segs = [trim(s) for s in segments(read_wav(path))]
        feats = [mfcc(s) for s in segs]
        self.templates += feats
        self.templates = self.templates[-20:]
        # eşik: şablonların birbirine uzaklığının (birini dışarıda bırak) üst sınırı + pay
        intra = []
        for i, f in enumerate(self.templates):
            others = [dtw(f, t) for j, t in enumerate(self.templates) if j != i]
            intra.append(np.mean(sorted(others)[:2]))
        self.threshold = float(np.max(intra) * 1.15)
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        np.savez(STATE, t=np.array(self.templates, dtype=object))
        with open(META, "w") as f:
            json.dump({"threshold": self.threshold, "n": len(self.templates)}, f)
        return len(feats), self.threshold, intra


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "ekle":
        n, thr, intra = WakeWord().add(sys.argv[2])
        print(f"{n} şablon eklendi, eşik {thr:.3f}, iç uzaklıklar {np.round(intra, 3)}")
