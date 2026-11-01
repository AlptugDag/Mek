# -*- coding: utf-8 -*-
"""Kurgu/tasarım uygulamalarında API harcamadan yardım: sesli komut -> uygulama kısayolu.

Eşleşme tamamen yereldir. Yalnızca 'ekrana bak' gibi açık isteklerde görüntü buluta gider.
Tuş gönderimi için python'a Erişilebilirlik izni gerekir (Sistem Ayarları > Gizlilik).
"""
import re
import subprocess

# tuş: harf ya da özel ad; mods: command/shift/option/control
KEYCODES = {"return": 36, "space": 49, "delete": 51, "escape": 53, "left": 123, "right": 124,
            "down": 125, "up": 126, "home": 115, "end": 119}

COMMON = [
    (r"kaydet", ("s", ["command"]), "Kaydettim."),
    (r"geri al", ("z", ["command"]), ""),
    (r"yinele|ileri al", ("z", ["command", "shift"]), ""),
    (r"(oynat|durdur|başlat)$", ("space", []), ""),
]

APPS = {
    "Premiere": [
        (r"(tümünü|bütün izleri) (kes|böl)", ("k", ["command", "shift"]), ""),
        (r"\b(kes|böl|kesik at)\b", ("k", ["command"]), ""),
        (r"dışa aktar|export|çıktı al", ("m", ["command"]), "Dışa aktarma penceresi açıldı."),
        (r"render|önizleme oluştur", ("return", []), "Render başladı."),
        (r"(işaretçi|marker) (ekle|koy)", ("m", []), ""),
        (r"sığdır|tamamını göster", ("\\", []), ""),
        (r"yakınlaştır", ("=", []), ""),
        (r"uzaklaştır", ("-", []), ""),
        (r"boşluğu kapat|ripple sil", ("delete", ["shift"]), ""),
        (r"ses(i)? (normalize|seviyele)", ("g", []), "Ses kazancı penceresi açıldı."),
    ],
    "DaVinci Resolve": [
        (r"\b(kes|böl)\b", ("\\", ["command"]), ""),
        (r"(işaretçi|marker) (ekle|koy)", ("m", []), ""),
        (r"sığdır|tamamını göster", ("z", ["shift"]), ""),
        (r"yakınlaştır", ("=", ["command"]), ""),
        (r"uzaklaştır", ("-", ["command"]), ""),
        (r"boşluğu kapat|ripple sil", ("delete", ["shift"]), ""),
        (r"render sırasına ekle", ("r", ["option", "shift"]), ""),
    ],
    "After Effects": [
        (r"render sırasına ekle|render kuyruğuna ekle|dışa aktar", ("m", ["command", "control"]), "Render sırasına eklendi."),
        (r"katmanı (böl|kes)", ("d", ["command", "shift"]), ""),
        (r"önizleme", ("space", []), ""),
        (r"önbelleği temizle", ("delete", ["command", "option"]), ""),
    ],
    "Photoshop": [
        (r"katman(ı)? (kopyala|çoğalt)", ("j", ["command"]), ""),
        (r"yeni katman", ("n", ["command", "shift"]), ""),
        (r"serbest dönüştür|boyutlandır", ("t", ["command"]), ""),
        (r"seçimi kaldır", ("d", ["command"]), ""),
        (r"tümünü seç", ("a", ["command"]), ""),
        (r"seviyeler", ("l", ["command"]), ""),
        (r"eğriler", ("m", ["command"]), ""),
        (r"web için kaydet|dışa aktar", ("w", ["command", "option", "shift"]), ""),
    ],
}


def app_family(front):
    f = (front or "").lower()
    if "premiere" in f:
        return "Premiere"
    if "resolve" in f or "davinci" in f:
        return "DaVinci Resolve"
    if "after effects" in f:
        return "After Effects"
    if "photoshop" in f:
        return "Photoshop"
    return None


def press(key, mods):
    using = (" using {" + ", ".join(m + " down" for m in mods) + "}") if mods else ""
    if key in KEYCODES:
        cmd = f"key code {KEYCODES[key]}{using}"
    else:
        esc = key.replace("\\", "\\\\").replace('"', '\\"')
        cmd = f'keystroke "{esc}"{using}'
    r = subprocess.run(["osascript", "-e", f'tell application "System Events" to {cmd}'],
                       capture_output=True, text=True)
    return r.returncode == 0


def try_command(front, text):
    """Öndeki uygulamaya göre komutu kısayola çevirir. Eşleşme yoksa None."""
    fam = app_family(front)
    if not fam:
        return None
    # Soru cümleleri ("nasıl kesilir?") kısayol değil, beyne gider
    if re.search(r"\b(nasıl|neden|niye|nedir|nerede|hangi|ne|mı|mi|mu|mü)\b", text):
        return None
    for rx, (key, mods), reply in APPS[fam] + COMMON:
        if re.search(rx, text):
            if not press(key, mods):
                return ("Tuş gönderemedim. Sistem Ayarları, Gizlilik, Erişilebilirlik bölümünden "
                        "Python'a izin vermeniz gerekiyor.")
            return reply
    return None


def capabilities(front):
    fam = app_family(front)
    if not fam:
        return ""
    return f"{fam} açık; yerel kısayol komutları hazır."
