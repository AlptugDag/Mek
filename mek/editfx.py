# -*- coding: utf-8 -*-
"""Mek kurgu motoru v2: üst üste eklenebilen işlemler, efektler, hook, alt yazı.

Çalışma mantığı
  Her medya için bir 'kurgu durumu' tutulur (state/edits/<ad>.json): sıralı parçalar,
  ek ses (SFX) izi, işaretçiler. Her komut bu durumu değiştirir ve yeni bir sürüm üretir:
  ~/Movies/Mek/<ad>/v3.xml (+ v3.srt). "Boşlukları kes" -> "girişe reverb ekle" ->
  "hook yap" gibi komutlar birbirinin üstüne biner; "geri al" bir önceki sürüme döner.

Neden efektler önceden işleniyor?
  Premiere ve Resolve, birbirinin eklenti/efekt ayarlarını XML'den okuyamaz. Efekt uygulanan
  parçalar M1 Max'in donanım ProRes kodlayıcısıyla ayrı klip olarak işlenir ve zaman
  çizelgesine o klip konur. Böylece iki programda da birebir aynı sonuç alınır. Orijinal
  dosyalara hiç dokunulmaz.
"""
import copy
import difflib
import json
import os
import re
import subprocess
import time
import wave

import numpy as np

import editkit as ek

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".jarvis/state/edits")
ASSETS = os.path.join(HOME, ".jarvis/state/assets")
SR = 48000

# --------------------------------------------------------------------------- efekt kataloğu
# ad: (Türkçe tetikleyiciler, ffmpeg ses zinciri ya da özel işleyici, süre çarpanı)
AUDIO_FX = {
    "reverb_in": (r"reverb(le|la)? (gir|başla|aç)|reverb ile gir|reverbli giriş|yankıyla gir", "IR_IN", 1.0),
    "reverb": (r"reverb|yankılı salon|katedral|hol|oda sesi", "IR", 1.0),
    "robot": (r"robot", "afftfilt=real='hypot(re,im)*cos(0)':imag='hypot(re,im)*sin(0)':win_size=512:overlap=0.75,"
              "aecho=0.8:0.6:6:0.4,volume=1.4", 1.0),
    "telefon": (r"telefon|ahize", "highpass=f=300,lowpass=f=3400,acrusher=bits=12:mix=0.25,acompressor=threshold=-20dB:ratio=4,volume=1.6", 1.0),
    "radyo": (r"radyo|eski kayıt|gramofon", "highpass=f=450,lowpass=f=4200,acrusher=bits=10:mix=0.35,volume=1.5", 1.0),
    "megafon": (r"megafon|hoparlör|anons", "highpass=f=700,lowpass=f=3000,volume=4,asoftclip=type=hard,volume=0.7", 1.0),
    "sualti": (r"su ?altı|boğuk|duvar arkası", "lowpass=f=450,aecho=0.8:0.7:40:0.3", 1.0),
    "yanki": (r"yankı|eko|echo", "aecho=0.8:0.85:420|840:0.45|0.25", 1.0),
    "kalin": (r"kalın|derin ses|bas ses|canavar", "asetrate=48000*0.78,aresample=48000,atempo=1.282", 1.0),
    "ince": (r"ince ses|sincap|çocuk sesi|helyum", "asetrate=48000*1.35,aresample=48000,atempo=0.741", 1.0),
    "seytan": (r"şeytan|iblis|korku sesi", "asetrate=48000*0.68,aresample=48000,atempo=1.47,aecho=0.8:0.8:60|120:0.5|0.3", 1.0),
    "ters": (r"ters(ten)? (çal|oynat)|ters ses", "areverse", 1.0),
    "temiz_ses": (r"sesi (temizle|iyileştir|güzelleştir|profesyonel)|stüdyo sesi|yayın sesi|gürültü(yü)? (al|temizle|sil)",
                  "afftdn=nf=-25,highpass=f=80,deesser=i=0.4,acompressor=threshold=-18dB:ratio=3:attack=5:release=80,"
                  "equalizer=f=3500:t=q:w=1.2:g=2.5,loudnorm=I=-14:TP=-1:LRA=11", 1.0),
    "normalize": (r"normalize|ses seviyesi(ni)? (eşitle|ayarla)|loudness|lufs", "loudnorm=I=-14:TP=-1:LRA=11", 1.0),
    "sessiz": (r"sesi (kapat|sustur|sil)|sessize al", "volume=0", 1.0),
}
VIDEO_FX = {
    "punch_in": (r"punch ?in|yakınlaş(arak)?|zoom ?in|yaklaş", 1.0),
    "zoom": (r"(sabit )?zoom|kırp(arak)? yaklaş", 1.0),
    "slowmo": (r"ağır çekim|yavaşlat|slow ?mo", 2.0),
    "hizli": (r"hızlandır|hızlı (oynat|çekim)|speed ?up", 0.5),
    "siyahbeyaz": (r"siyah ?beyaz", 1.0),
    "flas": (r"flaş|beyaz geçiş|flash", 1.0),
    "titreme": (r"titre|sars|shake|deprem", 1.0),
    "glitch": (r"glitch|bozulma|parazit|dijital hata", 1.0),
    "vinyet": (r"vinyet|kenarları karart", 1.0),
    "dondur": (r"dondur|freeze|kareyi durdur", 1.0),
}
SFX = {"whoosh": r"whoosh|vuuş|geçiş sesi|swoosh", "riser": r"riser|yükselen|gerilim sesi",
       "impact": r"impact|boom|vuruş|darbe", "bip": r"bip|sansür sesi"}


# --------------------------------------------------------------------------- kurgu durumu
def _slug(name):
    return re.sub(r"[^\w\- ]", "", os.path.splitext(os.path.basename(name))[0]).strip()[:60] or "kurgu"


class Edit:
    def __init__(self, data):
        self.d = data

    # --- oluşturma / kaydetme
    @classmethod
    def from_media(cls, paths):
        pieces = []
        for p in paths:
            m = ek.probe(p)
            pieces.append({"src": m["path"], "in": 0.0, "out": m["duration"]})
        m0 = ek.probe(paths[0])
        return cls({"name": _slug(paths[0]), "fps": m0["fps"], "w": m0["width"], "h": m0["height"],
                    "pieces": pieces, "sfx": [], "markers": [], "version": 0, "history": []})

    @classmethod
    def load_for(cls, paths):
        p = os.path.join(STATE, _slug(paths[0]) + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                e = cls(json.load(f))
            srcs = {x["src"] for x in e.d["pieces"]}
            if any(os.path.abspath(x) in srcs for x in paths):
                return e
        return cls.from_media(paths)

    @property
    def folder(self):
        f = os.path.join(ek.OUT_DIR, self.d["name"])
        os.makedirs(os.path.join(f, "render"), exist_ok=True)
        return f

    def snapshot(self):
        h = self.d.setdefault("history", [])
        h.append(copy.deepcopy({k: v for k, v in self.d.items() if k != "history"}))
        del h[:-15]

    def undo(self):
        h = self.d.get("history") or []
        if not h:
            return False
        prev = h.pop()
        prev["history"] = h
        self.d = prev
        return True

    def save(self):
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, self.d["name"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False)

    # --- zaman yardımcıları
    def plen(self, p):
        return p.get("len", p["out"] - p["in"])

    def duration(self):
        return sum(self.plen(p) for p in self.d["pieces"])

    def starts(self):
        t, out = 0.0, []
        for p in self.d["pieces"]:
            out.append(t)
            t += self.plen(p)
        return out

    def split_at(self, t):
        """Zaman çizelgesinde t anında parçayı böler (işlenmiş parçalar bölünmez)."""
        acc = 0.0
        for i, p in enumerate(self.d["pieces"]):
            L = self.plen(p)
            if acc < t < acc + L - 0.04 and not p.get("vfile") and not p.get("afile"):
                cut = p["in"] + (t - acc)
                a, b = dict(p), dict(p)
                a["out"], b["in"] = cut, cut
                a.pop("len", None), b.pop("len", None)
                self.d["pieces"][i:i + 1] = [a, b]
                return
            acc += L

    def range_indices(self, a, b):
        self.split_at(a)
        self.split_at(b)
        idx, t = [], 0.0
        for i, p in enumerate(self.d["pieces"]):
            L = self.plen(p)
            if t >= a - 0.04 and t + L <= b + 0.04:
                idx.append(i)
            t += L
        return idx

    # --- transkript (zaman çizelgesi zamanında)
    def transcript(self, stt):
        words, sents = [], []
        cache = {}
        for start, p in zip(self.starts(), self.d["pieces"]):
            if p["src"] not in cache:
                cache[p["src"]] = ek.transcribe(p["src"], stt)
            k = self.plen(p) / max(1e-6, p["out"] - p["in"])
            for s in cache[p["src"]]:
                if s["end"] <= p["in"] or s["start"] >= p["out"]:
                    continue
                ts = start + (max(s["start"], p["in"]) - p["in"]) * k
                te = start + (min(s["end"], p["out"]) - p["in"]) * k
                sents.append({"s": ts, "e": te, "text": s["text"]})
                for w in s["words"]:
                    if p["in"] <= w["s"] < p["out"]:
                        words.append({"w": w["w"], "s": start + (w["s"] - p["in"]) * k,
                                      "e": start + (min(w["e"], p["out"]) - p["in"]) * k})
        return words, sents


# --------------------------------------------------------------------------- işleme (render)
def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "ffmpeg hatası")


def _ir_path(seconds=2.2):
    """Sentetik salon yankısı (üstel sönümlü stereo gürültü) — bir kez üretilir."""
    os.makedirs(ASSETS, exist_ok=True)
    p = os.path.join(ASSETS, "hall_ir.wav")
    if os.path.exists(p):
        return p
    n = int(SR * seconds)
    t = np.arange(n) / SR
    rng = np.random.default_rng(7)
    env = np.exp(-t * 3.2)
    ir = rng.standard_normal((n, 2)) * env[:, None]
    ir[:int(SR * 0.012)] *= np.linspace(0, 1, int(SR * 0.012))[:, None]  # ön gecikme
    ir /= np.abs(ir).max()
    _write_wav(p, ir * 0.6)
    return p


def _write_wav(path, x):
    x = np.clip(x, -1, 1)
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def sfx_path(kind):
    """Telif sorunu olmayan, matematiksel üretilmiş efekt sesleri."""
    os.makedirs(ASSETS, exist_ok=True)
    p = os.path.join(ASSETS, f"sfx_{kind}.wav")
    if os.path.exists(p):
        return p
    rng = np.random.default_rng(3)
    if kind == "whoosh":
        n = int(SR * 0.7)
        t = np.linspace(0, 1, n)
        noise = rng.standard_normal(n)
        # kayan bant geçiren: basit tek kutuplu filtre katsayısı zamanla açılıp kapanır
        out, y = np.zeros(n), 0.0
        for i in range(n):
            a = 0.02 + 0.5 * np.sin(np.pi * t[i]) ** 2
            y += a * (noise[i] - y)
            out[i] = y
        out *= np.sin(np.pi * t) ** 1.5
        st = np.stack([out * (1 - t * 0.6), out * (0.4 + t * 0.6)], 1)  # soldan sağa geçiş
        x = st / np.abs(st).max() * 0.8
    elif kind == "riser":
        n = int(SR * 1.6)
        t = np.arange(n) / SR
        f = 200 * (2 ** (t * 2.2))
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * (t / t[-1]) ** 2 * 0.5 + rng.standard_normal(n) * (t / t[-1]) ** 3 * 0.15
    elif kind == "impact":
        n = int(SR * 1.2)
        t = np.arange(n) / SR
        x = np.sin(2 * np.pi * (55 - 25 * t) * t) * np.exp(-t * 4) * 0.9 + rng.standard_normal(n) * np.exp(-t * 30) * 0.4
    else:  # bip
        n = int(SR * 0.5)
        t = np.arange(n) / SR
        x = np.sin(2 * np.pi * 1000 * t) * 0.5
    _write_wav(p, x)
    return p


def render_audio(src, a, b, fx, out, speed=1.0):
    """Kaynağın [a,b] aralığını efektle WAV olarak işler."""
    base = ["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src]
    tempo = ""
    if speed != 1.0:
        tempo = ",".join(["atempo=0.5"] * int(np.log2(1 / speed))) if speed < 1 else \
            ",".join(["atempo=2.0"] * int(np.log2(speed)))
    chain = AUDIO_FX[fx][1] if fx else "anull"
    if chain in ("IR", "IR_IN"):
        wet_vol = "volume=0.55" if chain == "IR" else "volume='max(0.1,1-t/1.6)':eval=frame"
        fc = (f"[0:a]aresample={SR},apad=pad_dur=1.5,asplit[d][w];[w][1:a]afir=dry=10:wet=10[r];"
              f"[r]{wet_vol}[rw];[d][rw]amix=inputs=2:weights='1 1':normalize=0,alimiter=limit=0.95"
              + (f",{tempo}" if tempo else "") + "[o]")
        _run(base + ["-i", _ir_path(), "-filter_complex", fc, "-map", "[o]", "-c:a", "pcm_s24le", "-ar", str(SR), out])
    else:
        af = f"aresample={SR},{chain}" + (f",{tempo}" if tempo else "")
        _run(base + ["-vn", "-af", af, "-c:a", "pcm_s24le", "-ar", str(SR), "-ac", "2", out])
    return out


def video_filter(fx, w, h, fps, dur):
    n = max(1, int(dur * fps))
    return {
        # zoompan çok yavaş; kare kare ölçekleyip ortadan kırpmak ~100 kat hızlı
        "punch_in": (f"scale=w='trunc({w}*(1+0.18*t/{max(dur, 0.1):.3f})/2)*2':"
                     f"h='trunc({h}*(1+0.18*t/{max(dur, 0.1):.3f})/2)*2':eval=frame,crop={w}:{h}"),
        "zoom": f"crop=iw/1.2:ih/1.2,scale={w}:{h}",
        "slowmo": "setpts=2.0*PTS",
        "hizli": "setpts=0.5*PTS",
        "siyahbeyaz": "hue=s=0,eq=contrast=1.15",
        "flas": "fade=in:st=0:d=0.3:color=white",
        "titreme": (f"crop=iw*0.92:ih*0.92:x='(iw-ow)/2+(random(1)-0.5)*24':y='(ih-oh)/2+(random(2)-0.5)*24',"
                    f"scale={w}:{h}"),
        "glitch": "rgbashift=rh=10:bh=-10:enable='lt(mod(t,0.6),0.15)',noise=alls=14:allf=t",
        "vinyet": "vignette=PI/4",
        "dondur": "tpad=stop_mode=clone:stop_duration=1.2",
    }[fx]


def render_video(src, a, b, fxs, out, w, h, fps):
    vf = ",".join(video_filter(f, w, h, fps, b - a) for f in fxs) + f",fps={fps:.3f},format=yuv422p10le"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src, "-an", "-vf", vf]
    try:  # M1 Max ProRes donanım motoru
        _run(cmd + ["-c:v", "prores_videotoolbox", "-profile:v", "2", out])
    except RuntimeError:
        _run(cmd + ["-c:v", "prores_ks", "-profile:v", "2", out])
    return out


# --------------------------------------------------------------------------- işlemler
def _media_info(edit, path, cache={}):
    if path not in cache:
        cache[path] = ek.probe(path)
    return cache[path]


def op_silence(edit, stt=None, clean=False):
    edit.snapshot()
    new = []
    for p in edit.d["pieces"]:
        if p.get("vfile") or p.get("afile"):
            new.append(p)
            continue
        m = _media_info(edit, p["src"])
        keep, _ = ek.speech_ranges(p["src"], m["duration"])
        if clean and stt:
            segs = ek.transcribe(p["src"], stt)
            keep = ek.subtract(keep, ek.filler_cuts(segs) + ek.retake_cuts(segs))
        for s, e in keep:
            s, e = max(s, p["in"]), min(e, p["out"])
            if e - s > 0.1:
                new.append({"src": p["src"], "in": s, "out": e})
    before = edit.duration()
    edit.d["pieces"] = new
    return f"{before - edit.duration():.0f} saniye boşluk temizlendi, yeni süre {edit.duration():.0f} saniye"


def op_audio_fx(edit, fx, a, b):
    edit.snapshot()
    idx = edit.range_indices(a, b)
    if not idx:
        return "Hedef aralığı bulamadım"
    rd = os.path.join(edit.folder, "render")
    for i in idx:
        p = edit.d["pieces"][i]
        if p.get("afile"):
            continue
        sp = p.get("speed", 1.0)
        out = os.path.join(rd, f"a_{fx}_{int(time.time() * 1000) % 10 ** 9}_{i}.wav")
        render_audio(p["src"], p["in"], p["out"], fx, out, speed=sp)
        p["afile"] = out
        p["afx"] = fx
    return f"{len(idx)} parçaya {fx} uygulandı"


def op_video_fx(edit, fx, a, b):
    edit.snapshot()
    idx = edit.range_indices(a, b)
    if not idx:
        return "Hedef aralığı bulamadım"
    rd = os.path.join(edit.folder, "render")
    W, H, fps = edit.d["w"], edit.d["h"], edit.d["fps"]
    factor = VIDEO_FX[fx][1]
    for i in idx:
        p = edit.d["pieces"][i]
        if p.get("vfile"):
            continue
        out = os.path.join(rd, f"v_{fx}_{int(time.time() * 1000) % 10 ** 9}_{i}.mov")
        render_video(p["src"], p["in"], p["out"], [fx], out, W, H, fps)
        p["vfile"] = out
        p["vfx"] = fx
        if factor != 1.0 or fx == "dondur":
            src_len = p["out"] - p["in"]
            p["len"] = src_len * factor + (1.2 if fx == "dondur" else 0)
            p["speed"] = 1 / factor if fx != "dondur" else 1.0
            # ses de aynı süreye uysun
            aout = out[:-4] + ".wav"
            if fx == "dondur":
                _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{p['in']:.3f}", "-t", f"{src_len:.3f}", "-i", p["src"],
                      "-af", f"aresample={SR},apad=pad_dur=1.2", "-c:a", "pcm_s24le", out[:-4] + ".wav"])
            else:
                render_audio(p["src"], p["in"], p["out"], None, aout, speed=p["speed"])
            p["afile"] = aout
    return f"{len(idx)} parçaya {fx} uygulandı"


def op_sfx(edit, kind, at):
    edit.snapshot()
    edit.d["sfx"].append({"file": sfx_path(kind), "at": max(0.0, at), "kind": kind})
    return f"{kind} sesi eklendi"


def op_censor(edit, stt, words_to_hide):
    """Belirtilen kelimeleri bip sesiyle örter."""
    words, _ = edit.transcript(stt)
    n = 0
    for w in words:
        if any(difflib.SequenceMatcher(None, ek._norm(w["w"]), x).ratio() > 0.8 for x in words_to_hide):
            op_audio_fx_silent(edit, w["s"], w["e"])
            edit.d["sfx"].append({"file": sfx_path("bip"), "at": w["s"], "len": w["e"] - w["s"], "kind": "bip"})
            n += 1
    return f"{n} kelime sansürlendi"


def op_audio_fx_silent(edit, a, b):
    idx = edit.range_indices(a, b)
    rd = os.path.join(edit.folder, "render")
    for i in idx:
        p = edit.d["pieces"][i]
        out = os.path.join(rd, f"a_mute_{int(time.time() * 1000) % 10 ** 9}_{i}.wav")
        render_audio(p["src"], p["in"], p["out"], "sessiz", out)
        p["afile"] = out


def op_captions(edit, stt):
    """Zaman çizelgesine göre SRT (Premiere: altyazı olarak içe aktar; Resolve: Altyazı izi)."""
    edit.snapshot()
    words, _ = edit.transcript(stt)
    lines, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        if len(text) > 32 or w["w"].endswith((".", "?", "!", ",")):
            lines.append((cur[0]["s"], cur[-1]["e"], text))
            cur = []
    if cur:
        lines.append((cur[0]["s"], cur[-1]["e"], " ".join(x["w"] for x in cur)))

    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    srt = "\n".join(f"{i}\n{ts(s)} --> {ts(max(e, s + 0.5))}\n{t}\n" for i, (s, e, t) in enumerate(lines, 1))
    edit.d["srt"] = srt
    return f"{len(lines)} satır alt yazı hazırlandı"


HOOK_WORDS = r"\b(sır|kimse|asla|hiç|en|hata|inanılmaz|nasıl|neden|gerçek|şok|bedava|kolay|yanlış|dikkat|\d+)\b"


def op_hook(edit, stt, llm=None):
    """Videoyu analiz eder, en çarpıcı cümleyi başa koyar ve ona uygun efektleri seçer."""
    words, sents = edit.transcript(stt)
    sents = [s for s in sents if 1.2 <= s["e"] - s["s"] <= 7 and len(s["text"]) > 8]
    if not sents:
        return "Hook için uygun bir cümle bulamadım"
    # Görsel/işitsel enerji: sahne değişimleri ve ses yüksekliği
    src0 = edit.d["pieces"][0]["src"]
    sc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", src0, "-vf",
                         "scale=160:-2,scdet=threshold=12", "-an", "-f", "null", "-"],
                        capture_output=True, text=True).stderr
    scene_times = [float(x) for x in re.findall(r"lavfi\.scd\.time: ([\d.]+)", sc)]

    def score(s):
        sc_ = 2.0 * len(re.findall(HOOK_WORDS, s["text"].lower())) + (2 if s["text"].strip().endswith(("?", "!")) else 0)
        sc_ += sum(1 for t in scene_times if s["s"] <= t <= s["e"]) * 0.5
        sc_ -= 0.15 * abs((s["e"] - s["s"]) - 3.5)
        return sc_

    choice, plan = max(sents, key=score), None
    if llm:
        try:
            table = "\n".join(f"{i} [{s['e'] - s['s']:.1f}s] {s['text']}" for i, s in enumerate(sents[:150]))
            raw = llm("Sen viral kısa video uzmanı bir kurgucusun. İzleyiciyi ilk 3 saniyede yakalayacak tek "
                      "cümleyi seç ve ona uygun efektleri öner. Video efektleri: punch_in, zoom, titreme, glitch, "
                      "flas, siyahbeyaz. Ses efektleri: reverb_in, yanki, none. SFX: whoosh, riser, impact. "
                      'YALNIZCA JSON: {"id": <id>, "video": [..], "audio": "..", "sfx": "..", "why": ".."}',
                      table)
            plan = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            if 0 <= int(plan.get("id", -1)) < len(sents):
                choice = sents[int(plan["id"])]
        except Exception:
            plan = None
    vfx = [v for v in (plan or {}).get("video", []) if v in VIDEO_FX and v not in ("slowmo", "hizli", "dondur")][:2] \
        or ["punch_in"]
    afx = (plan or {}).get("audio") if (plan or {}).get("audio") in AUDIO_FX else None
    sfx = (plan or {}).get("sfx") if (plan or {}).get("sfx") in SFX else "whoosh"

    edit.snapshot()
    hist = list(edit.d["history"])  # hook ve efektleri tek adımda geri alınabilsin
    a, b = choice["s"], choice["e"]
    # Hook parçalarını kaynak aralıklarıyla kopyala ve başa ekle
    hook, t = [], 0.0
    for start, p in zip(edit.starts(), edit.d["pieces"]):
        L = edit.plen(p)
        lo, hi = max(a, start), min(b, start + L)
        if hi - lo > 0.05 and not p.get("len"):
            hook.append({"src": p["src"], "in": p["in"] + (lo - start), "out": p["in"] + (hi - start)})
    if not hook:
        return "Hook parçası oluşturulamadı"
    hook[-1]["out"] += 0.25  # cümle nefes alsın
    edit.d["pieces"] = hook + edit.d["pieces"]
    hook_len = sum(p["out"] - p["in"] for p in hook)
    edit.d["markers"].append({"at": 0.0, "name": "HOOK"})
    edit.d["markers"] = [{"at": m["at"] + (hook_len if m["name"] != "HOOK" else 0), "name": m["name"]}
                         for m in edit.d["markers"]]
    edit.d["sfx"] = [dict(s, at=s["at"] + hook_len) for s in edit.d["sfx"]]
    for f in vfx:
        op_video_fx(edit, f, 0.0, hook_len)
    if afx:
        op_audio_fx(edit, afx, 0.0, hook_len)
    op_sfx(edit, sfx, max(0.0, hook_len - 0.35))
    edit.d["history"] = hist  # hook tek adımda geri alınabilsin
    why = (plan or {}).get("why", "")
    return (f"Hook hazır: “{choice['text']}” başa alındı; {', '.join(vfx)}"
            + (f", {afx}" if afx else "") + f" ve {sfx} eklendi. {why}").strip()


# --------------------------------------------------------------------------- XML dışa aktarım
def export(edit):
    """FCP7 XML: V1 video, A1 ses (efektli parçalar işlenmiş dosyalardan), A2 SFX, işaretçiler."""
    fps = edit.d["fps"]
    rate_xml, _ = ek._rate(fps)
    files, fid = {}, [0]

    def fref(path, kind_dur):
        if path in files:
            return f'<file id="{files[path]}"/>'
        fid[0] += 1
        files[path] = f"file-{fid[0]}"
        m = _media_info(edit, path)
        url = "file://localhost" + ek.urllib.parse.quote(path)
        media = ((f'<video><samplecharacteristics><width>{m["width"]}</width><height>{m["height"]}</height>'
                  f'</samplecharacteristics></video>') if m["has_video"] else "") + \
                (f'<audio><channelcount>{m["channels"] or 2}</channelcount></audio>' if m["has_audio"] else "")
        return (f'<file id="{files[path]}"><name>{ek.escape(os.path.basename(path))}</name><pathurl>{url}</pathurl>'
                f'{rate_xml}<duration>{int(round(m["duration"] * fps))}</duration><media>{media}</media></file>')

    def clip(cid, path, src_in, length, start, extra=""):
        m = _media_info(edit, path)
        fin = int(round(src_in * fps))
        return (f'<clipitem id="{cid}"><name>{ek.escape(os.path.basename(path))}</name>'
                f'<duration>{int(round(m["duration"] * fps))}</duration>{rate_xml}<start>{start}</start>'
                f'<end>{start + length}</end><in>{fin}</in><out>{fin + length}</out>{fref(path, 0)}{extra}</clipitem>')

    v, a1, a2, t = [], [], [], 0
    for i, p in enumerate(edit.d["pieces"]):
        L = int(round(edit.plen(p) * fps))
        if L <= 0:
            continue
        m = _media_info(edit, p["src"])
        if m["has_video"]:
            vp, vin = (p["vfile"], 0.0) if p.get("vfile") else (p["src"], p["in"])
            v.append(clip(f"v{i}", vp, vin, L, t))
        ap, ain = (p["afile"], 0.0) if p.get("afile") else (p["src"], p["in"])
        if m["has_audio"] or p.get("afile"):
            a1.append(clip(f"a{i}", ap, ain, L, t, "<sourcetrack><mediatype>audio</mediatype>"
                                                    "<trackindex>1</trackindex></sourcetrack>"))
        t += L
    for j, s in enumerate(edit.d["sfx"]):
        sl = int(round((s.get("len") or _media_info(edit, s["file"])["duration"]) * fps))
        st = int(round(s["at"] * fps))
        a2.append(clip(f"s{j}", s["file"], 0.0, sl, st))
    markers = "".join(f'<marker><name>{ek.escape(m["name"])}</name><in>{int(round(m["at"] * fps))}</in>'
                      f'<out>-1</out></marker>' for m in edit.d["markers"])
    video = (f'<video><format><samplecharacteristics>{rate_xml}<width>{edit.d["w"]}</width>'
             f'<height>{edit.d["h"]}</height></samplecharacteristics></format><track>{"".join(v)}</track></video>'
             if v else "")
    audio = f'<audio><track>{"".join(a1)}</track>' + (f'<track>{"".join(a2)}</track>' if a2 else "") + "</audio>"
    edit.d["version"] = edit.d.get("version", 0) + 1
    name = f'{edit.d["name"]} v{edit.d["version"]}'
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
           f'<sequence id="seq-1"><name>{ek.escape(name)}</name><duration>{t}</duration>{rate_xml}{markers}'
           f'<media>{video}{audio}</media></sequence></xmeml>')
    path = os.path.join(edit.folder, f'v{edit.d["version"]}.xml')
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)
    srt = None
    if edit.d.get("srt"):
        srt = path[:-4] + ".srt"
        with open(srt, "w", encoding="utf-8") as f:
            f.write(edit.d["srt"])
    edit.save()
    return path, srt


# --------------------------------------------------------------------------- hedef çözümleme
def resolve_target(edit, text, stt):
    """'girişe', 'son 3 saniyeye', '“merhaba” dediğim yere', 'tamamına' -> (a, b) zaman çizelgesi saniyesi."""
    t = text.lower()
    total = edit.duration()
    m = re.search(r"ilk (\d+) saniye", t)
    if m:
        return 0.0, min(total, float(m.group(1)))
    m = re.search(r"son (\d+) saniye", t)
    if m:
        return max(0.0, total - float(m.group(1))), total
    if re.search(r"tamam|bütün|hepsi|tüm video|her yer", t):
        return 0.0, total
    m = re.search(r"[\"“'‘](.+?)[\"”'’]|(\w+(?: \w+){0,4}) (?:dediğim|dediği|diye|geçen|kısm)", t)
    need_sents = bool(m) or re.search(r"giriş|başa|başı|ilk cümle|son cümle|kapanış|sona|sonu|bitiş", t)
    if need_sents:
        words, sents = edit.transcript(stt)
        if m:
            phrase = ek._norm(m.group(1) or m.group(2))
            best = max(sents, key=lambda s: difflib.SequenceMatcher(None, phrase, ek._norm(s["text"])).ratio()
                       + (1 if phrase in ek._norm(s["text"]) else 0), default=None)
            if best:
                # cümle içindeki kelimelere daralt
                ws = [w for w in words if best["s"] - 0.05 <= w["s"] <= best["e"]]
                pw = phrase.split()
                for i in range(len(ws)):
                    seq = " ".join(ek._norm(x["w"]) for x in ws[i:i + len(pw)])
                    if difflib.SequenceMatcher(None, seq, phrase).ratio() > 0.75:
                        return ws[i]["s"], ws[min(len(ws) - 1, i + len(pw) - 1)]["e"]
                return best["s"], best["e"]
        if sents and re.search(r"giriş|başa|başı|ilk cümle", t):
            return sents[0]["s"], sents[0]["e"]
        if sents and re.search(r"son cümle|kapanış|sona|sonu|bitiş", t):
            return sents[-1]["s"], sents[-1]["e"]
    return None


# --------------------------------------------------------------------------- komut ayrıştırıcı
def plan_from_text(text):
    """Türkçe komutu işlem listesine çevirir (yerel; API harcamaz). Bulamazsa boş liste."""
    t = text.lower()
    ops = []
    for clause in re.split(r",| ve | sonra | ardından ", t):
        c = clause.strip()
        if not c:
            continue
        if re.search(r"kurgu\w* geri al|son (işlemi|efekti|değişikliği) geri al|önceki sürüme dön", c):
            ops.append({"op": "undo"})
        elif re.search(r"hook|kanca|dikkat çekici (giriş|başlangıç)|ilk 3 saniye", c):
            ops.append({"op": "hook"})
        elif re.search(r"(boşluk|sessizlik)\w*", c) and re.search(r"kes|at|temizle|sil", c):
            ops.append({"op": "clean" if re.search(r"ııı|eee|dolgu|tekrar|take", t) else "silence"})
        elif re.search(r"(ııı|eee|dolgu|tekrar\w*|take\w*)", c) and re.search(r"kes|at|temizle", c):
            ops.append({"op": "clean"})
        elif re.search(r"alt ?yazı|altyazı|subtitle|caption", c):
            ops.append({"op": "captions"})
        elif re.search(r"sansür|bipl|bip(le)? (at|kapat)", c):
            m = re.search(r"[\"“'‘](.+?)[\"”'’]|(\w+) (kelimesini|lafını|sözünü)", c)
            ops.append({"op": "censor", "words": [ek._norm(m.group(1) or m.group(2))] if m else []})
        elif re.search(r"dışa aktar|xml|zaman çizelgesi(ni)? (oluştur|ver)|aktar", c):
            ops.append({"op": "export"})
        else:
            hit = False
            for name, rx in SFX.items():
                if re.search(rx, c):
                    ops.append({"op": "sfx", "sfx": name, "target": c})
                    hit = True
                    break
            if hit:
                continue
            for name, (rx, _, _) in AUDIO_FX.items():
                if re.search(rx, c):
                    ops.append({"op": "audio_fx", "fx": name, "target": c})
                    hit = True
                    break
            for name, (rx, _) in VIDEO_FX.items():
                if re.search(rx, c):
                    ops.append({"op": "video_fx", "fx": name, "target": c})
                    hit = True
                    break
            if not hit and ops and ops[-1].get("target"):
                ops[-1]["target"] += " " + c  # "girişe" gibi ayrı düşen hedef parçası
    # hedef bir sonraki cümlede kalmışsa önceki işleme taşı
    return ops


PLANNER_SYSTEM = """Kurgu komutunu işlem listesine çevir. İşlemler:
silence | clean | hook | captions | export | undo
audio_fx {fx: reverb|reverb_in|robot|telefon|radyo|megafon|sualti|yanki|kalin|ince|seytan|ters|temiz_ses|normalize|sessiz, target}
video_fx {fx: punch_in|zoom|slowmo|hizli|siyahbeyaz|flas|titreme|glitch|vinyet|dondur, target}
sfx {sfx: whoosh|riser|impact|bip, target}
censor {words: [..]}
target Türkçe tarif: "girişe", "son cümleye", "ilk 3 saniyeye", "'merhaba' dediğim yere", "tamamına".
YALNIZCA JSON: {"ops": [{"op": "...", ...}]}"""


def run_ops(edit, ops, stt, llm=None, log=print):
    msgs = []
    for o in ops:
        kind = o.get("op")
        if kind == "undo":
            msgs.append("Bir önceki sürüme döndüm" if edit.undo() else "Geri alınacak adım yok")
        elif kind in ("silence", "clean"):
            msgs.append(op_silence(edit, stt, clean=(kind == "clean")))
        elif kind == "captions":
            msgs.append(op_captions(edit, stt))
        elif kind == "hook":
            msgs.append(op_hook(edit, stt, llm))
        elif kind == "censor":
            msgs.append(op_censor(edit, stt, o.get("words") or []) if o.get("words") else
                        "Hangi kelimeyi sansürleyeceğimi tırnak içinde söyleyin")
        elif kind in ("audio_fx", "video_fx", "sfx"):
            rng = resolve_target(edit, o.get("target", ""), stt)
            if kind == "sfx":
                at = rng[0] if rng else 0.0
                msgs.append(op_sfx(edit, o["sfx"], at))
                continue
            if not rng:
                # varsayılan hedef: temizlik tüm videoya, efektler ilk cümleye
                rng = (0.0, edit.duration()) if o.get("fx") in ("temiz_ses", "normalize", "siyahbeyaz", "vinyet") \
                    else resolve_target(edit, "girişe", stt) or (0.0, min(3.0, edit.duration()))
            fn = op_audio_fx if kind == "audio_fx" else op_video_fx
            msgs.append(fn(edit, o["fx"], *rng))
        log(msgs[-1] if msgs else "")
    return msgs
