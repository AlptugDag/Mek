#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Jarvis Personal Assistant
Exclusively powered by EMA Lightning TTS (canberkkkkkk/ema-lightning)
Configured for Alptuğ Bey.
Acoustically tuned specifically for Apple Mac Studio built-in speaker.
Lightweight, zero-load architecture.
"""

import sys
import os
import time
import subprocess
import datetime
import urllib.request
import argparse
import random
import ctypes
import numpy as np
import scipy.signal as signal
import sounddevice as sd

JARVIS_DIR = os.path.expanduser("~/.jarvis")
TARGET_DEVICE_NAME = "mac studio"
TARGET_VOLUME = 0.30  # %30 ses seviyesi
SPEECH_SPEED = 1.08   # Çevik, tetikte ve net bir asistan ritmi

TURKISH_MONTHS = [
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"
]

TURKISH_DAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"
]

# CoreAudio ctypes binding
coreaudio = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")

class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def get_mac_studio_devices():
    """
    Genel sistem ses aygıtına dokunmadan:
    1. CoreAudio Device ID'sini bulur (doğrudan donanım sesini ayarlamak için)
    2. sounddevice aygıt indexini bulur (doğrudan bu hoparlöre ses göndermek için)
    """
    addr = AudioObjectPropertyAddress(
        int.from_bytes(b"dev#", "big"),
        int.from_bytes(b"glob", "big"),
        int.from_bytes(b"mstr", "big"),
    )
    size = ctypes.c_uint32(0)
    coreaudio.AudioObjectGetPropertyDataSize(1, ctypes.byref(addr), 0, None, ctypes.byref(size))
    count = size.value // 4
    dev_ids = (ctypes.c_uint32 * count)()
    coreaudio.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), dev_ids)

    ca_id = None
    for did in dev_ids:
        name_addr = AudioObjectPropertyAddress(
            int.from_bytes(b"name", "big"),
            int.from_bytes(b"glob", "big"),
            int.from_bytes(b"mstr", "big"),
        )
        name_buf = ctypes.create_string_buffer(256)
        name_size = ctypes.c_uint32(256)
        if coreaudio.AudioObjectGetPropertyData(did, ctypes.byref(name_addr), 0, None, ctypes.byref(name_size), ctypes.byref(name_buf)) == 0:
            if TARGET_DEVICE_NAME in name_buf.value.decode("utf-8", errors="ignore").lower():
                ca_id = did
                break

    sd_idx = None
    for idx, d in enumerate(sd.query_devices()):
        if TARGET_DEVICE_NAME in d["name"].lower() and d["max_output_channels"] > 0:
            sd_idx = idx
            break

    return ca_id, sd_idx


def set_mac_studio_hardware_volume(ca_id, volume=TARGET_VOLUME):
    """Sistemin genel sesini değiştirmeden sadece Mac Studio hoparlörünün sesini ayarlar."""
    if not ca_id:
        return
    try:
        vol_addr = AudioObjectPropertyAddress(
            int.from_bytes(b"volm", "big"),
            int.from_bytes(b"outp", "big"),
            0,
        )
        v = ctypes.c_float(volume)
        coreaudio.AudioObjectSetPropertyData(ca_id, ctypes.byref(vol_addr), 0, None, ctypes.sizeof(v), ctypes.byref(v))

        mute_addr = AudioObjectPropertyAddress(
            int.from_bytes(b"mute", "big"),
            int.from_bytes(b"outp", "big"),
            0,
        )
        unmute = ctypes.c_uint32(0)
        coreaudio.AudioObjectSetPropertyData(ca_id, ctypes.byref(mute_addr), 0, None, ctypes.sizeof(unmute), ctypes.byref(unmute))
    except Exception as e:
        print(f"[Jarvis] Donanım ses seviyesi ayarlanamadı: {e}", file=sys.stderr)


# --- MAC STUDIO HOPARLÖRÜ İÇİN ÖZEL AKUSTİK EKOLAYZIR (DSP) ---
def biquad_peaking(gain_db, f0, q, fs=48000):
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * f0 / fs
    alpha = np.sin(w0) / (2 * q)
    b0 = 1 + alpha * A
    b1 = -2 * np.cos(w0)
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * np.cos(w0)
    a2 = 1 - alpha / A
    return np.array([b0, b1, b2]) / a0, np.array([a0, a1, a2]) / a0


def biquad_highshelf(gain_db, f0, fs=48000):
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * f0 / fs
    alpha = np.sin(w0) / 2 * np.sqrt(2)
    b0 = A * ((A + 1) + (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha)
    b1 = -2 * A * ((A - 1) + (A + 1) * np.cos(w0))
    b2 = A * ((A + 1) + (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha)
    a0 = (A + 1) - (A - 1) * np.cos(w0) + 2 * np.sqrt(A) * alpha
    a1 = 2 * ((A - 1) - (A + 1) * np.cos(w0))
    a2 = (A + 1) - (A - 1) * np.cos(w0) - 2 * np.sqrt(A) * alpha
    return np.array([b0, b1, b2]) / a0, np.array([a0, a1, a2]) / a0


def apply_mac_studio_eq(audio, fs=48000):
    """
    Mac Studio'nun dahili mono gövde hoparlörünün akustik profiline göre tasarlanmış filtre zinciri:
    1. High-Pass (130 Hz): Sürücüyü bozan dip basları temizler.
    2. Boxiness Cut (400 Hz, -3.5 dB, Q=1.2): Kasa içindeki boğuk rezonansı tıraşlar.
    3. Vocal Presence (3200 Hz, +3.2 dB, Q=1.0): Harflerin netliğini ve anlaşılırlığını parlatır.
    4. Air Shelf (7500 Hz, +2.2 dB): Modern arayüz ses berraklığı sağlar.
    5. Normalizasyon: Tepe seviyesini -1 dBFS ile sınırlar.
    """
    sos_hpf = signal.butter(2, 130, btype="highpass", fs=fs, output="sos")
    out = signal.sosfilt(sos_hpf, audio)

    b_mid, a_mid = biquad_peaking(-3.5, 400, 1.2, fs=fs)
    out = signal.lfilter(b_mid, a_mid, out)

    b_pres, a_pres = biquad_peaking(3.2, 3200, 1.0, fs=fs)
    out = signal.lfilter(b_pres, a_pres, out)

    b_air, a_air = biquad_highshelf(2.2, 7500, fs=fs)
    out = signal.lfilter(b_air, a_air, out)

    max_val = np.max(np.abs(out))
    if max_val > 0:
        target = 10 ** (-1.0 / 20.0)
        out = (out / max_val) * target

    return out.astype(np.float32)


def is_quiet_hours(now: datetime.datetime) -> bool:
    """Gece 00:00 ile sabah 11:00 arası kesin sessiz saatler."""
    return 0 <= now.hour < 11


def is_user_present(max_idle_seconds: int = 600) -> bool:
    """Kullanıcının bilgisayar başında olup olmadığını kontrol eder (boş odaya konuşmamak için)."""
    try:
        cmd = "ioreg -c IOHIDSystem | awk '/HIDIdleTime/ {print int($NF/1000000000); exit}'"
        res = subprocess.check_output(cmd, shell=True).decode().strip()
        idle_seconds = int(res)
        return idle_seconds <= max_idle_seconds
    except Exception:
        return True


# wttr.in Türkçe çeviri vermiyor; WWO hava kodlarını kendimiz çeviriyoruz
WEATHER_TR = {
    113: "açık", 116: "parçalı bulutlu", 119: "bulutlu", 122: "kapalı", 143: "puslu",
    176: "yer yer yağmurlu", 179: "yer yer karlı", 182: "yer yer karla karışık yağmurlu",
    185: "yer yer çiseleyen dondurucu yağmurlu", 200: "yer yer gök gürültülü",
    227: "tipili", 230: "kar fırtınalı", 248: "sisli", 260: "dondurucu sisli",
    263: "yer yer çiseleyen", 266: "çiseleyen", 281: "dondurucu çiseleyen",
    284: "yoğun dondurucu çiseleyen", 293: "yer yer hafif yağmurlu", 296: "hafif yağmurlu",
    299: "ara ara orta şiddetli yağmurlu", 302: "orta şiddetli yağmurlu",
    305: "ara ara sağanak yağışlı", 308: "şiddetli yağmurlu", 311: "hafif dondurucu yağmurlu",
    314: "dondurucu yağmurlu", 317: "hafif karla karışık yağmurlu", 320: "karla karışık yağmurlu",
    323: "yer yer hafif karlı", 326: "hafif karlı", 329: "yer yer orta karlı", 332: "orta karlı",
    335: "yer yer yoğun karlı", 338: "yoğun karlı", 350: "dolu yağışlı",
    353: "hafif sağanak yağışlı", 356: "sağanak yağışlı", 359: "çok şiddetli sağanak yağışlı",
    362: "hafif karla karışık sağanaklı", 365: "karla karışık sağanaklı", 368: "hafif kar sağanaklı",
    371: "kar sağanaklı", 374: "hafif dolu sağanaklı", 377: "dolu sağanaklı",
    386: "yer yer gök gürültülü yağmurlu", 389: "gök gürültülü sağanak yağışlı",
    392: "yer yer gök gürültülü karlı", 395: "gök gürültülü yoğun karlı",
}


def get_weather(timeout: float = 2.5) -> str:
    """Hava durumunu çeker ve doğal Türkçe cümleye çevirir."""
    try:
        import json
        req = urllib.request.Request("https://wttr.in/?format=j1", headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            cur = json.load(response)["current_condition"][0]
        desc = WEATHER_TR.get(int(cur["weatherCode"]))
        temp = int(cur["temp_C"])
        temp_str = f"eksi {abs(temp)}" if temp < 0 else str(temp)
        if desc:
            return f"Hava {desc}, {temp_str} derece efendim."
        return f"Hava {temp_str} derece efendim."
    except Exception:
        pass
    return ""


def check_critical_system_health() -> list[str]:
    """
    Sadece GERÇEK ve KRİTİK donanım/sistem durumlarını denetler.
    Her şey normalse boş liste döner (gereksiz laf kalabalığı yapmaz).
    """
    issues = []

    # 1. Disk boş alan kontrolü (%10 kritik, %15 uyarı)
    try:
        st = os.statvfs("/")
        free_pct = (st.f_bavail / st.f_blocks) * 100
        free_gb = int((st.f_bavail * st.f_frsize) / (1024 ** 3))
        if free_pct < 10.0:
            issues.append(f"Kritik depolama uyarısı Alptuğ Bey: Boş alanınız yüzde 10 altına indi, yalnızca {free_gb} gigabayt kaldı.")
        elif free_pct < 15.0:
            issues.append(f"Depolama uyarısı: Kullanılabilir alan yüzde 15 sınırına yaklaştı efendim.")
    except Exception:
        pass

    # 2. SMART Disk donanım sağlığı kontrolü
    try:
        smart_out = subprocess.check_output("diskutil info / | grep 'SMART Status'", shell=True).decode().strip()
        if "verified" not in smart_out.lower():
            issues.append("Acil donanım uyarısı Alptuğ Bey: Ana diskin SMART donanım sağlığında risk tespit edildi!")
    except Exception:
        pass

    # 3. Swap / Aşırı bellek darboğazı kontrolü (> 4GB swap)
    try:
        swap_out = subprocess.check_output("sysctl -n vm.swapusage", shell=True).decode().strip()
        if "used =" in swap_out:
            used_str = swap_out.split("used =")[1].split("free")[0].strip()
            if "G" in used_str:
                val = float(used_str.replace("G", ""))
                if val > 4.0:
                    issues.append("Bellek uyarısı: Yüksek takas alanı kullanımı tespit edildi efendim.")
    except Exception:
        pass

    return issues


def build_startup_message() -> str:
    now = datetime.datetime.now()
    day_name = TURKISH_DAYS[now.weekday()]
    month_name = TURKISH_MONTHS[now.month]
    time_str = now.strftime("%H:%M")
    date_str = f"Bugün {now.day} {month_name} {day_name}, saat {time_str}."

    # Her açılışta hava durumu mutlaka alınır
    weather_str = get_weather(timeout=2.5)

    # Gerçek sistem sağlığı kontrolü (sadece sorun varsa konuşur)
    critical_issues = check_critical_system_health()

    if now.hour < 18:
        greetings = [
            "İyi günler Alptuğ Bey.",
            "Tünaydın Alptuğ Bey.",
            "Hoş geldiniz Alptuğ Bey."
        ]
    else:
        greetings = [
            "İyi akşamlar Alptuğ Bey.",
            "Hayırlı akşamlar Alptuğ Bey."
        ]

    greeting = random.choice(greetings)
    parts = [greeting, date_str]

    if weather_str:
        parts.append(weather_str)

    if critical_issues:
        # Gerçek bir problem varsa doğrudan bildir
        parts.extend(critical_issues)
    else:
        # Sorun yoksa gereksiz laf kalabalığı yapmadan, doğal ve saygılı bir kapanış
        closers = [
            "Hizmetinizdeyim Alptuğ Bey.",
            "Emrinizdeyim efendim.",
            "Çalışmalarınızda muvaffakiyetler dilerim Alptuğ Bey.",
            "Tüm donanım durumları stabil. İyi çalışmalar dilerim Alptuğ Bey.",
            "Her şey yolunda, emrinize amadeyim efendim."
        ]
        parts.append(random.choice(closers))

    return " ".join(parts)


def build_periodic_message() -> str:
    """Gün içindeki ara saatlerde (15:00 ve 19:00) çalışan akıllı durum kontrolü."""
    now = datetime.datetime.now()
    time_str = now.strftime("%H:%M")
    critical_issues = check_critical_system_health()

    if critical_issues:
        # Kritik bir durum oluşmuşsa ara saatlerde de mutlaka bildir
        return " ".join(critical_issues)

    if 14 <= now.hour < 17:
        midday_phrases = [
            f"Tünaydın Alptuğ Bey. Saat {time_str}. Çalışma temponuzu bölmeden her şeyin yolunda olduğunu bildirmek isterim. İzninizle kısa bir mola verip su içmenizi tavsiye ederim efendim.",
            f"Alptuğ Bey, saat {time_str}. Donanım ve bellek değerleri son derece stabil. Çalışmalarınızda muvaffakiyetler dilerim efendim.",
            f"Tünaydın Alptuğ Bey. Günün ikinci yarısındayız. Sistemler kusursuz işliyor, hizmetinizdeyim efendim."
        ]
        return random.choice(midday_phrases)
    elif 17 <= now.hour < 22:
        evening_phrases = [
            f"İyi akşamlar Alptuğ Bey. Saat {time_str}. Günlük ana çalışma saatlerinin sonuna yaklaşıyoruz. Harika bir akşam dilerim efendim.",
            f"Alptuğ Bey, gün sonu kontrolü tamamlandı. Donanım durumları normal seviyede, emirlerinizi bekliyorum.",
            f"İyi akşamlar Alptuğ Bey. Saat {time_str}. Hizmetinizdeyim efendim."
        ]
        return random.choice(evening_phrases)
    else:
        return f"Alptuğ Bey, saat {time_str}. Her şey yolunda, emrinizdeyim efendim."


def speak(text: str):
    print(f"[Jarvis] Sentezleniyor: {text}")
    try:
        from ema_lightning import EMA
        tts = EMA()
        speech = tts.say(text, speed=SPEECH_SPEED)

        # Akustik ekolayzır uygula
        audio_eq = apply_mac_studio_eq(speech.audio, fs=speech.sample_rate)

        # Donanım çıkışı ve ses seviyesi kontrolü
        ca_id, sd_idx = get_mac_studio_devices()
        if ca_id is not None:
            set_mac_studio_hardware_volume(ca_id, TARGET_VOLUME)

        pct = int(TARGET_VOLUME * 100)
        print(f"[Jarvis] EMA Lightning sesi üretildi ve Mac Studio EQ uygulandı.")
        print(f"[Jarvis] Genel ses kaynağına dokunulmadan doğrudan Mac Studio Hoparlörü üzerinden %{pct} seste çalınıyor...")

        if sd_idx is not None:
            sd.play(audio_eq, samplerate=speech.sample_rate, device=sd_idx)
            sd.wait()
        else:
            sd.play(audio_eq, samplerate=speech.sample_rate)
            sd.wait()

        print("[Jarvis] Oynatma başarıyla tamamlandı.")
    except Exception as e:
        print(f"[Jarvis] Hata: {e}", file=sys.stderr)


def main():
    os.makedirs(JARVIS_DIR, exist_ok=True)

    parser = argparse.ArgumentParser(description="Jarvis AI Assistant")
    parser.add_argument("text", nargs="*", help="Söylenecek metin (opsiyonel)")
    parser.add_argument("--periodic", action="store_true", help="Günün ara saatlerindeki periyodik kontrol modu")
    parser.add_argument("--timer", type=int, help="Belirtilen dakika sonra konuş (arka planda bekler)")
    parser.add_argument("--force", action="store_true", help="Sessiz saat kontrolünü atla")
    args = parser.parse_args()

    # Zamanlayıcı (Timer) modu: jarvis --timer 30 "Mola bitti Alptuğ Bey"
    if args.timer:
        minutes = args.timer
        msg = " ".join(args.text) if args.text else f"Alptuğ Bey, {minutes} dakikalık süreniz doldu efendim."
        print(f"[Jarvis] Zamanlayıcı kuruldu: {minutes} dakika sonra konuşulacak.")
        time.sleep(minutes * 60)
        speak(msg)
        return

    # Doğrudan özel metin verilmişse: jarvis "Mesaj"
    if args.text and not args.periodic:
        custom_message = " ".join(args.text)
        speak(custom_message)
        return

    now = datetime.datetime.now()

    # Sessiz saatler (00:00 - 11:00)
    if is_quiet_hours(now) and not args.force:
        print(f"[Jarvis] Sessiz saat aralığındayız (00:00 - 11:00). Şu an saat: {now.strftime('%H:%M')}. Konuşma iptal edildi.")
        sys.exit(0)

    # Periyodik mod: kullanıcı bilgisayar başında değilse (idle > 10 dk) boş odaya konuşmaz
    if args.periodic:
        if not is_user_present(max_idle_seconds=600) and not args.force:
            print("[Jarvis] Kullanıcı aktif değil (bilgisayar başında değil). Periyodik konuşma atlandı.")
            sys.exit(0)
        message = build_periodic_message()
    else:
        # Standart başlangıç brifingi
        message = build_startup_message()

    speak(message)


if __name__ == "__main__":
    main()
