# -*- coding: utf-8 -*-
"""Mek kurgu otomasyonu.

  sessizlik kesme   ffmpeg silencedetect -> konuşan kısımlar
  temiz kurgu       + 'ııı/eee' dolguları ve tekrar alınan cümleler (son take kalır)
  kaba kurgu        transkript -> dil modeli brief'e göre parçaları seçer -> zaman çizelgesi
  renk              referans görüntüden ya da tarif edilen bakıştan 33'lük .cube LUT

Çıktı FCP7 XML: Premiere (Dosya > İçe Aktar) ve Resolve (Dosya > İçe Aktar > Zaman Çizelgesi)
ikisi de açar. Resolve açıksa ve harici betiklere izin verilmişse doğrudan içeri aktarılır.
Ağır işler yalnızca istenince çalışır; modeller iş bitince bellekten atılır.
"""
import difflib
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import urllib.parse
from xml.sax.saxutils import escape

import numpy as np

log = logging.getLogger("mek.editkit")
HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".jarvis/state/transcripts")
OUT_DIR = os.path.join(HOME, "Movies/Mek")
LUT_DIRS = [os.path.join(HOME, "Library/Application Support/Adobe/Common/LUTs/Creative"),
            os.path.join(HOME, "Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Mek")]
VIDEO_EXT = (".mp4", ".mov", ".mxf", ".m4v", ".mkv", ".avi", ".braw", ".r3d")
AUDIO_EXT = (".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp")
FILLERS = re.compile(r"^(ı+|ıh+|ııh+|e+h*|ee+|eh+m*|a+h*|aa+|hm+|hı+|ım+|mm+)$")


# ----------------------------------------------------------------------------- medya
def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                          "-show_format", path], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    fps = 25.0
    if v:
        num, den = (v.get("r_frame_rate") or "25/1").split("/")
        fps = float(num) / float(den or 1) or 25.0
    return {
        "path": os.path.abspath(path), "name": os.path.basename(path),
        "duration": float(d["format"].get("duration", 0)), "fps": fps,
        "width": int(v["width"]) if v else 1920, "height": int(v["height"]) if v else 1080,
        "has_video": v is not None, "has_audio": a is not None,
        "channels": int(a.get("channels", 2)) if a else 0,
        "rate": int(a.get("sample_rate", 48000)) if a else 48000,
    }


def speech_ranges(path, duration, min_silence=0.45, pad=0.12):
    """Konuşma (sessiz olmayan) aralıkları. Eşik, kaydın ortalama sesine göre ayarlanır."""
    vol = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    mean = float(m.group(1)) if m else -25.0
    thr = max(-50.0, min(-25.0, mean - 16))
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
                          f"silencedetect=noise={thr}dB:d={min_silence}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    silences = list(zip(starts, ends + [duration] * (len(starts) - len(ends))))
    keep, cur = [], 0.0
    for s, e in silences:
        if s > cur:
            keep.append((max(0.0, cur - pad), min(duration, s + pad)))
        cur = e
    if cur < duration:
        keep.append((max(0.0, cur - pad), duration))
    return merge(keep), thr


def merge(ranges, gap=0.15):
    out = []
    for s, e in sorted(ranges):
        if e - s < 0.08:
            continue
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def subtract(ranges, cuts):
    """ranges'ten cuts aralıklarını çıkarır."""
    out = []
    for s, e in ranges:
        pieces = [(s, e)]
        for cs, ce in cuts:
            nxt = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:
                    nxt.append((ps, pe))
                    continue
                if cs > ps:
                    nxt.append((ps, cs))
                if ce < pe:
                    nxt.append((ce, pe))
            pieces = nxt
        out += pieces
    return merge(out, gap=0.05)


# ----------------------------------------------------------------------------- transkript
def transcribe(path, model):
    """Kelime zamanlı transkript; dosya değişmedikçe önbellekten gelir."""
    os.makedirs(CACHE, exist_ok=True)
    st = os.stat(path)
    key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()
    cp = os.path.join(CACHE, key + ".json")
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    import mlx_whisper
    res = mlx_whisper.transcribe(path, path_or_hf_repo=model, language="tr", word_timestamps=True,
                                 condition_on_previous_text=False)
    segs = [{"id": i, "start": s["start"], "end": s["end"], "text": s["text"].strip(),
             "words": [{"w": w["word"].strip(), "s": w["start"], "e": w["end"]} for w in s.get("words", [])]}
            for i, s in enumerate(res.get("segments", []))]
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False)
    try:
        import mlx.core as mx
        mx.clear_cache()
    except Exception:
        pass
    return segs


def _norm(t):
    return re.sub(r"[^\wçğıöşü ]", "", t.lower()).strip()


def filler_cuts(segs):
    cuts = []
    for s in segs:
        for w in s["words"]:
            if FILLERS.match(_norm(w["w"])):
                cuts.append((w["s"], w["e"]))
    return cuts


def retake_cuts(segs):
    """Aynı cümle tekrar söylenmişse öncekini atar (son take genelde doğrusudur)."""
    cuts = []
    for i, s in enumerate(segs):
        a = _norm(s["text"])
        if len(a) < 12:
            continue
        for j in (i + 1, i + 2):
            if j < len(segs):
                b = _norm(segs[j]["text"])
                head = b[:len(a) + 10]
                if difflib.SequenceMatcher(None, a, head).ratio() > 0.72:
                    cuts.append((s["start"], s["end"]))
                    break
    return cuts


# ----------------------------------------------------------------------------- FCP7 XML
def _rate(fps):
    tb = round(fps)
    ntsc = abs(fps - tb) > 0.01
    return f"<rate><timebase>{tb}</timebase><ntsc>{'TRUE' if ntsc else 'FALSE'}</ntsc></rate>", fps


def write_xml(name, pieces, out_path):
    """pieces: [(media_info, src_in_sec, src_out_sec)] sırayla zaman çizelgesine dizilir."""
    first = pieces[0][0]
    rate_xml, fps = _rate(first["fps"])
    has_video = any(p[0]["has_video"] for p in pieces)
    files, vclips, aclips, t = {}, [], [], 0
    for idx, (m, si, so) in enumerate(pieces):
        fin, fout = int(round(si * fps)), int(round(so * fps))
        if fout <= fin:
            continue
        dur_total = int(round(m["duration"] * fps))
        if m["path"] not in files:
            fid = f"file-{len(files) + 1}"
            files[m["path"]] = fid
            url = "file://localhost" + urllib.parse.quote(m["path"])
            fxml = (f'<file id="{fid}"><name>{escape(m["name"])}</name><pathurl>{url}</pathurl>{rate_xml}'
                    f'<duration>{dur_total}</duration><media>'
                    + (f'<video><samplecharacteristics><width>{m["width"]}</width><height>{m["height"]}</height>'
                       f'</samplecharacteristics></video>' if m["has_video"] else "")
                    + (f'<audio><channelcount>{m["channels"] or 2}</channelcount></audio>' if m["has_audio"] else "")
                    + '</media></file>')
        else:
            fxml = f'<file id="{files[m["path"]]}"/>'
        length = fout - fin
        common = (f'<name>{escape(m["name"])}</name><duration>{dur_total}</duration>{rate_xml}'
                  f'<start>{t}</start><end>{t + length}</end><in>{fin}</in><out>{fout}</out>')
        vid, aid = f"v{idx}", f"a{idx}"
        link = (f'<link><linkclipref>{vid}</linkclipref><mediatype>video</mediatype></link>'
                f'<link><linkclipref>{aid}</linkclipref><mediatype>audio</mediatype></link>'
                if m["has_video"] and m["has_audio"] else "")
        if m["has_video"]:
            vclips.append(f'<clipitem id="{vid}">{common}{fxml}{link}</clipitem>')
            fxml = f'<file id="{files[m["path"]]}"/>'
        if m["has_audio"]:
            aclips.append(f'<clipitem id="{aid}">{common}{fxml}<sourcetrack><mediatype>audio</mediatype>'
                          f'<trackindex>1</trackindex></sourcetrack>{link}</clipitem>')
        t += length
    video = (f'<video><format><samplecharacteristics>{rate_xml}<width>{first["width"]}</width>'
             f'<height>{first["height"]}</height></samplecharacteristics></format>'
             f'<track>{"".join(vclips)}</track></video>') if has_video else ""
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
           f'<sequence id="seq-1"><name>{escape(name)}</name><duration>{t}</duration>{rate_xml}'
           f'<media>{video}<audio><track>{"".join(aclips)}</track></audio></media></sequence></xmeml>')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)
    return out_path, t / fps


# ----------------------------------------------------------------------------- işler
def _out(name, suffix, ext=".xml"):
    base = os.path.splitext(name)[0]
    return os.path.join(OUT_DIR, f"{base} - {suffix}{ext}")


def silence_cut(path, stt_model=None, clean=False):
    """clean=True: dolgu sesleri ve tekrar alınan cümleler de atılır (transkript gerekir)."""
    m = probe(path)
    keep, thr = speech_ranges(path, m["duration"])
    removed_fill = removed_retake = 0
    if clean and stt_model:
        segs = transcribe(path, stt_model)
        fc, rc = filler_cuts(segs), retake_cuts(segs)
        removed_fill, removed_retake = len(fc), len(rc)
        keep = subtract(keep, fc + rc)
    suffix = "temiz kurgu" if clean else "boşluksuz"
    out, new_dur = write_xml(f"{os.path.splitext(m['name'])[0]} ({suffix})",
                             [(m, s, e) for s, e in keep], _out(m["name"], suffix))
    return {"xml": out, "before": m["duration"], "after": new_dur, "cuts": max(0, len(keep) - 1),
            "fillers": removed_fill, "retakes": removed_retake, "threshold_db": thr}


ROUGH_SYSTEM = """Sen deneyimli bir video kurgucususun. Sana kaynak dosyalardan transkript parçaları
(id, süre, metin) ve bir kurgu brief'i verilecek. Brief'e en uygun, akıcı bir anlatı oluşturacak
parçaları seç ve izlenecek sırayla diz. Tekrarları, yarım cümleleri, hatalı take'leri ve konu dışını at.
Brief bir süre söylüyorsa toplam süreyi ona yakın tut ve köşeli parantezdeki süreleri topla.
Brief'te istenmeyen bir şey (giriş, kapanış, bir konu) söylenmişse o parçaları kesinlikle seçme.
YALNIZCA JSON döndür:
{"ids": [<id>, ...], "title": "<kısa başlık>", "note": "<tek cümle kurgu notu>"}"""


def rough_cut(paths, brief, stt_model, llm):
    """llm(system, user) -> str. Birden çok dosya desteklenir."""
    medias, table, lookup, gid = [], [], {}, 0
    for p in paths:
        m = probe(p)
        medias.append(m)
        segs = transcribe(p, stt_model)
        dropped = {(c[0], c[1]) for c in retake_cuts(segs)}  # hatalı take'leri modele hiç gösterme
        for s in segs:
            if not s["text"] or (s["start"], s["end"]) in dropped:
                continue
            lookup[gid] = (m, s["start"], s["end"])
            table.append(f'{gid} [{s["end"] - s["start"]:.1f}s] {s["text"]}')
            gid += 1
    if not table:
        raise RuntimeError("Transkriptte konuşma bulunamadı.")
    text = "\n".join(table)
    if len(text) > 60000:  # çok uzun kayıtlarda modele sığdır
        text = text[:60000]
    raw = llm(ROUGH_SYSTEM, f"Brief: {brief or 'En güçlü anları içeren akıcı bir kaba kurgu.'}\n\nParçalar:\n{text}")
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    ids = [int(i) for i in data.get("ids", []) if int(i) in lookup]
    if not ids:
        raise RuntimeError("Model uygun parça seçemedi.")
    pieces = [(lookup[i][0], max(0, lookup[i][1] - 0.08), lookup[i][2] + 0.12) for i in ids]
    title = data.get("title") or "Kaba kurgu"
    out, dur = write_xml(title, pieces, _out(medias[0]["name"], "kaba kurgu"))
    return {"xml": out, "after": dur, "parts": len(ids), "title": title, "note": data.get("note", "")}


# ----------------------------------------------------------------------------- renk
def _srgb_to_lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _lin_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


_M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
_WHITE = np.array([0.95047, 1.0, 1.08883])


def rgb_to_lab(rgb):
    xyz = _srgb_to_lin(rgb) @ _M.T / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def lab_to_rgb(lab):
    fy = (lab[..., 0] + 16) / 116
    fx, fz = fy + lab[..., 1] / 500, fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], -1)
    xyz = np.where(f ** 3 > 0.008856, f ** 3, (f - 16 / 116) / 7.787) * _WHITE
    return _lin_to_srgb(xyz @ np.linalg.inv(_M).T)


def _frames(path, n=6):
    """Videodan n kare (ya da görüntünün kendisi), 256 px, 0-1 RGB."""
    if path.lower().endswith(IMAGE_EXT):
        cmd = ["ffmpeg", "-v", "error", "-i", path, "-vf", "scale=256:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(-1, 3) / 255.0
    dur = probe(path)["duration"] or 10
    px = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                              "-vf", "scale=256:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        if raw:
            px.append(np.frombuffer(raw, np.uint8).reshape(-1, 3) / 255.0)
    return np.concatenate(px)


def _grid(size=33):
    r = np.linspace(0, 1, size)
    b, g, rr = np.meshgrid(r, r, r, indexing="ij")  # .cube: kırmızı en hızlı değişir
    return np.stack([rr, g, b], -1).reshape(-1, 3)


def write_cube(rgb, name, size=33):
    paths = []
    body = f'TITLE "{name}"\nLUT_3D_SIZE {size}\n' + "\n".join(
        f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in np.clip(rgb, 0, 1))
    for d in LUT_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, f"{name}.cube")
            with open(p, "w") as f:
                f.write(body + "\n")
            paths.append(p)
        except OSError:
            pass
    return paths


def match_lut(source, reference, strength=0.85, name=None):
    """Kaynağın renk dağılımını referansa taşıyan LUT (Lab uzayında istatistik aktarımı)."""
    src, ref = rgb_to_lab(_frames(source)), rgb_to_lab(_frames(reference))
    ms, ss = src.mean(0), src.std(0) + 1e-6
    mr, sr = ref.mean(0), ref.std(0) + 1e-6
    g = _grid()
    lab = rgb_to_lab(g)
    moved = (lab - ms) / ss * sr + mr
    out = lab_to_rgb(lab + (moved - lab) * strength)
    name = name or f"Mek eslesme {os.path.splitext(os.path.basename(reference))[0]}"
    return write_cube(out, name)


# Tarif edilen bakış -> parametreler (yerel anahtar kelimelerle; API harcamaz)
LOOK_WORDS = [
    (r"sıcak|turuncu|altın|gün batımı", {"temp": 0.25}),
    (r"soğuk|mavi|buz", {"temp": -0.25}),
    (r"yeşil", {"tint": -0.15}), (r"mor|magenta|pembe", {"tint": 0.15}),
    (r"kontrast(ı)? (artır|yüksek|sert)|sert|vurucu|punch", {"contrast": 0.25}),
    (r"kontrast(ı)? (azalt|düşür)|yumuşak|düz", {"contrast": -0.2}),
    (r"canlı|doygun|renkli|parlak renk", {"sat": 0.3}),
    (r"soluk|desatüre|mat renk|sönük", {"sat": -0.3}),
    (r"siyah beyaz|monokrom", {"sat": -1.0}),
    (r"sinematik|filmik|film gibi|teal|camgöbeği", {"teal_orange": 0.6, "contrast": 0.12, "fade": 0.03}),
    (r"vintage|retro|eski", {"fade": 0.08, "sat": -0.2, "temp": 0.12}),
    (r"karanlık|koyu|moody|dramatik", {"gamma": -0.12, "contrast": 0.15}),
    (r"aydınlık|ferah|açık", {"gamma": 0.12}),
    (r"siyahları (kaldır|aç)|fade|solgun siyah", {"fade": 0.06}),
]


def parse_look(text, base=None):
    """'biraz daha sıcak', 'çok daha kontrastlı' gibi göreli ifadeleri de anlar."""
    p = dict(base or {})
    t = text.lower()
    scale = 0.5 if re.search(r"biraz|hafif|azıcık", t) else 1.6 if re.search(r"çok|aşırı|epey|baya", t) else 1.0
    if re.search(r"sıfırla|orijinal|temizle|kaldır", t) and "siyah" not in t:
        return {}, True
    hit = False
    for rx, delta in LOOK_WORDS:
        if re.search(rx, t):
            hit = True
            for k, v in delta.items():
                p[k] = round(p.get(k, 0.0) + v * scale, 3)
    return p, hit


def look_lut(params, name="Mek bakis"):
    g = _grid()
    x = g.copy()
    temp, tint = params.get("temp", 0), params.get("tint", 0)
    x[:, 0] *= 1 + 0.25 * temp
    x[:, 2] *= 1 - 0.25 * temp
    x[:, 1] *= 1 - 0.2 * tint
    gamma = params.get("gamma", 0)
    x = np.clip(x, 0, 1) ** (1 / (1 + gamma))
    c = params.get("contrast", 0)
    x = 0.5 + (x - 0.5) * (1 + c) + c * 0.08 * np.sin((x - 0.5) * np.pi)  # yumuşak S eğrisi
    luma = (x @ np.array([0.2126, 0.7152, 0.0722]))[:, None]
    to = params.get("teal_orange", 0)
    if to:
        shadow = np.clip(1 - luma * 2, 0, 1)
        hi = np.clip(luma * 2 - 1, 0, 1)
        x += to * 0.06 * (shadow * np.array([-1, 0.3, 0.8]) + hi * np.array([0.9, 0.35, -0.8]))
    sat = params.get("sat", 0)
    x = luma + (x - luma) * max(0.0, 1 + sat)
    fade = params.get("fade", 0)
    x = fade + x * (1 - fade)
    return write_cube(x, name)


# ----------------------------------------------------------------------------- Resolve
def resolve():
    """Çalışan Resolve'a bağlanır (Tercihler > Sistem > Genel > Harici betik: Yerel)."""
    lib = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
    mod = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib)
    if mod not in sys.path:
        sys.path.append(mod)
    try:
        import DaVinciResolveScript as dvr
        r = dvr.scriptapp("Resolve")
        return r
    except Exception as e:
        log.warning("Resolve bağlantısı yok: %s", e)
        return None


def resolve_current_media():
    r = resolve()
    if not r:
        return []
    tl = r.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
    if not tl:
        return []
    paths = []
    for tr in range(1, tl.GetTrackCount("video") + 1):
        for it in tl.GetItemListInTrack("video", tr) or []:
            mpi = it.GetMediaPoolItem()
            p = mpi.GetClipProperty("File Path") if mpi else ""
            if p and p not in paths:
                paths.append(p)
    return paths


def resolve_timeline_pieces():
    """Resolve'daki güncel zaman çizelgesinin V1 kesimleri: [(dosya, kaynak_giriş_sn, kaynak_çıkış_sn)]."""
    r = resolve()
    if not r:
        return [], None
    proj = r.GetProjectManager().GetCurrentProject()
    tl = proj.GetCurrentTimeline() if proj else None
    if not tl:
        return [], None
    fps = float(tl.GetSetting("timelineFrameRate") or 25)
    items = sorted(tl.GetItemListInTrack("video", 1) or [], key=lambda it: it.GetStart())
    out = []
    for it in items:
        mpi = it.GetMediaPoolItem()
        path = mpi.GetClipProperty("File Path") if mpi else ""
        if path and os.path.exists(path):
            a = it.GetLeftOffset() / fps
            out.append((path, a, a + it.GetDuration() / fps))
    return out, tl.GetName()


def resolve_import_srt(srt_path):
    r = resolve()
    if not r:
        return False
    return bool(r.GetProjectManager().GetCurrentProject().GetMediaPool().ImportMedia([srt_path]))


def resolve_import(xml_path):
    r = resolve()
    if not r:
        return False
    proj = r.GetProjectManager().GetCurrentProject()
    tl = proj.GetMediaPool().ImportTimelineFromFile(xml_path, {"importSourceClips": True})
    if tl:
        proj.SetCurrentTimeline(tl)
    return bool(tl)


def resolve_apply_lut(lut_path):
    r = resolve()
    if not r:
        return 0
    proj = r.GetProjectManager().GetCurrentProject()
    try:
        proj.RefreshLUTList()
    except Exception:
        pass
    tl = proj.GetCurrentTimeline()
    n = 0
    for tr in range(1, tl.GetTrackCount("video") + 1):
        for it in tl.GetItemListInTrack("video", tr) or []:
            # Son düğüme uygula: varsa log dönüşümü ilk düğümlerde kalsın
            node = max(1, it.GetNumNodes() if hasattr(it, "GetNumNodes") else 1)
            if it.SetLUT(node, lut_path):
                n += 1
    return n


# ----------------------------------------------------------------------------- dosya seçimi
def finder_selection():
    r = subprocess.run(["osascript", "-e", 'tell application "Finder" to get POSIX path of '
                        '(selection as alias list)'], capture_output=True, text=True)
    # birden çok seçimde virgülle ayrılır
    return [p.strip() for p in r.stdout.strip().split(", ") if p.strip() and os.path.exists(p.strip())]


def pick_media(front="", kinds=VIDEO_EXT + AUDIO_EXT):
    """Öncelik: Resolve'daki zaman çizelgesi > Finder seçimi > son 2 günde eklenen en yeni dosya."""
    if "resolve" in front.lower():
        paths = [p for p in resolve_current_media() if p.lower().endswith(kinds)]
        if paths:
            return paths
    sel = [p for p in finder_selection() if p.lower().endswith(kinds)]
    if sel:
        return sel
    import time
    best = None
    for d in ("Movies", "Desktop", "Downloads"):
        root = os.path.join(HOME, d)
        for dp, dn, fn in os.walk(root):
            dn[:] = [x for x in dn if not x.startswith(".") and x != "Mek"]
            for f in fn:
                if f.lower().endswith(kinds):
                    p = os.path.join(dp, f)
                    mt = os.path.getmtime(p)
                    if time.time() - mt < 2 * 86400 and (not best or mt > best[0]):
                        best = (mt, p)
    return [best[1]] if best else []


def open_in_editor(xml, front):
    if "resolve" in front.lower() and resolve_import(xml):
        return "Resolve'a yeni zaman çizelgesi olarak ekledim."
    if "premiere" in front.lower():
        subprocess.Popen(["open", "-a", front, xml])
        return "Premiere'e içe aktarıyorum."
    subprocess.Popen(["open", "-R", xml])
    return "XML dosyasını Filmler, Mek klasörüne kaydettim."
