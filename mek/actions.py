# -*- coding: utf-8 -*-
"""Mek'in elleri: macOS üzerinde yapabildiği her şey (Siri'nin kullandığı Kestirmeler,
AppleScript, Takvim/Anımsatıcılar, uygulama ve sistem kontrolü)."""
import difflib
import glob
import logging
import os
import subprocess

log = logging.getLogger("mek.actions")


def osa(script, timeout=20):
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        log.warning("osascript: %s", r.stderr.strip())
    return r.stdout.strip()


def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- uygulamalar -------------------------------------------------------------
ALIASES = {
    "premiere": "Adobe Premiere Pro 2026", "prömiyer": "Adobe Premiere Pro 2026",
    "after effects": "Adobe After Effects 2026", "aftır efekt": "Adobe After Effects 2026",
    "davinci": "DaVinci Resolve", "resolve": "DaVinci Resolve", "rızolv": "DaVinci Resolve",
    "topaz": "Topaz Video", "media encoder": "Adobe Media Encoder 2026",
    "audition": "Adobe Audition 2026", "krom": "Google Chrome", "chrome": "Google Chrome",
    "safari": "Safari", "müzik": "Music", "spotify": "Spotify", "finder": "Finder",
    "takvim": "Calendar", "notlar": "Notes", "terminal": "Terminal", "mesajlar": "Messages",
}


def installed_apps():
    paths = glob.glob("/Applications/*.app") + glob.glob("/Applications/*/*.app") + \
        glob.glob("/System/Applications/*.app") + glob.glob(os.path.expanduser("~/Applications/*.app"))
    return {os.path.basename(p)[:-4]: p for p in paths}


def resolve_app(spoken):
    s = spoken.strip().lower()
    for k, v in ALIASES.items():
        if k in s:
            return v
    apps = installed_apps()
    low = {n.lower(): n for n in apps}
    for ln, n in low.items():
        if s and s in ln:
            return n
    m = difflib.get_close_matches(s, list(low), n=1, cutoff=0.55)
    return low[m[0]] if m else None


def open_app(name):
    app = resolve_app(name)
    if not app:
        return f"{name} adında bir uygulama bulamadım."
    subprocess.Popen(["open", "-a", app])
    return f"{app} açılıyor."


def quit_app(name):
    app = resolve_app(name)
    if not app:
        return f"{name} adında açık bir uygulama bulamadım."
    osa(f"tell application {_q(app)} to quit")
    return f"{app} kapatılıyor."


def open_url(url):
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    subprocess.Popen(["open", url])
    return "Açıyorum."


# --- sistem ------------------------------------------------------------------
def shutdown():
    osa('tell application "System Events" to shut down')


def restart():
    osa('tell application "System Events" to restart')


def sleep_now():
    subprocess.Popen(["pmset", "sleepnow"])


def lock_screen():
    osa('tell application "System Events" to keystroke "q" using {control down, command down}')


def set_volume(pct):
    pct = max(0, min(100, int(pct)))
    osa(f"set volume output volume {pct}")
    return f"Ses yüzde {pct}."


def change_volume(delta):
    cur = osa("output volume of (get volume settings)")
    try:
        return set_volume(int(cur) + delta)
    except ValueError:
        return "Ses seviyesini okuyamadım."


def mute(on=True):
    osa(f"set volume output muted {'true' if on else 'false'}")
    return "Sessize aldım." if on else "Ses açıldı."


def media(cmd):
    """cmd: playpause | next | previous | pause"""
    for app in ("Spotify", "Music"):
        if osa(f'application {_q(app)} is running') == "true":
            verb = {"playpause": "playpause", "next": "next track",
                    "previous": "previous track", "pause": "pause"}[cmd]
            osa(f"tell application {_q(app)} to {verb}")
            return "Tamam."
    return "Çalan bir müzik uygulaması yok."


def dark_mode(toggle=None):
    val = "not dark mode" if toggle is None else ("true" if toggle else "false")
    osa(f'tell application "System Events" to tell appearance preferences to set dark mode to {val}')
    return "Görünüm değişti."


def notify(text, title="Mek"):
    osa(f"display notification {_q(text)} with title {_q(title)}")


def empty_trash_forbidden():
    return "Çöp kutusunu boşaltmak gibi kalıcı silme işlemlerini sizin yapmanızı tercih ederim efendim."


# --- Kişiler ve arama (iPhone üzerinden, Süreklilik/Continuity) ---------------------------
_contacts = {"t": 0, "rows": []}


def _contact_rows():
    import time as _t
    if _t.time() - _contacts["t"] < 3600 and _contacts["rows"]:
        return _contacts["rows"]
    script = '''
    set out to ""
    tell application "Contacts"
        repeat with p in people
            set nm to name of p
            set nick to ""
            try
                set nick to nickname of p
            end try
            repeat with ph in phones of p
                set out to out & nm & "|" & nick & "|" & (value of ph) & linefeed
            end repeat
        end repeat
    end tell
    return out'''
    try:
        raw = osa(script, timeout=40)
    except subprocess.TimeoutExpired:
        raw = ""
    rows = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and parts[2].strip():
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    _contacts.update(t=_t.time(), rows=rows)
    return rows


def _stems(spoken):
    """'babamı' -> ['babamı','babam','baba'] gibi Türkçe ek soyma."""
    w = spoken.strip().lower()
    out = [w]
    for suf in ("yı", "yi", "yu", "yü", "nı", "ni", "nu", "nü", "ı", "i", "u", "ü"):
        if w.endswith(suf) and len(w) - len(suf) >= 2:
            out.append(w[: -len(suf)])
            break
    base = out[-1]
    for suf in ("ım", "im", "um", "üm", "m"):
        if base.endswith(suf) and len(base) - len(suf) >= 2:
            out.append(base[: -len(suf)])
            break
    return out


def find_contact(spoken):
    rows = _contact_rows()
    if not rows:
        return None
    for stem in _stems(spoken):
        for name, nick, phone in rows:
            for field in (nick.lower(), name.lower()):
                if field and (field == stem or stem in field.split()):
                    return name, phone
    names = {r[0].lower(): r for r in rows}
    for stem in _stems(spoken):
        m = difflib.get_close_matches(stem, list(names), n=1, cutoff=0.75)
        if m:
            r = names[m[0]]
            return r[0], r[2]
    return None


def call(spoken):
    found = find_contact(spoken)
    if not found:
        return f"Rehberde {spoken} adında birini bulamadım."
    name, phone = found
    number = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    subprocess.Popen(["open", f"tel://{number}"])
    return f"{name} aranıyor."


# --- Siri ekosistemi: Kestirmeler, Takvim, Anımsatıcılar, Notlar ---------------
def list_shortcuts():
    r = subprocess.run(["shortcuts", "list"], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()]


def run_shortcut(name, text_input=None):
    names = list_shortcuts()
    m = difflib.get_close_matches(name, names, n=1, cutoff=0.5) or \
        [n for n in names if name.lower() in n.lower()][:1]
    if not m:
        return f"{name} adında bir kestirme bulamadım."
    cmd = ["shortcuts", "run", m[0]]
    r = subprocess.run(cmd, input=text_input, capture_output=True, text=True, timeout=120)
    out = r.stdout.strip()
    return out[:300] if out else f"{m[0]} kestirmesi çalıştı."


def todays_events():
    script = '''
    set out to ""
    set d0 to current date
    set hours of d0 to 0
    set minutes of d0 to 0
    set seconds of d0 to 0
    set d1 to d0 + 1 * days
    tell application "Calendar"
        repeat with c in calendars
            repeat with e in (every event of c whose start date ≥ d0 and start date < d1)
                set out to out & (time string of (start date of e)) & " " & (summary of e) & linefeed
            end repeat
        end repeat
    end tell
    return out'''
    try:
        return osa(script, timeout=30)
    except subprocess.TimeoutExpired:
        return ""


def add_reminder(text, minutes=None):
    due = f", remind me date:((current date) + {int(minutes)} * minutes)" if minutes else ""
    osa(f'tell application "Reminders" to make new reminder with properties {{name:{_q(text)}{due}}}')
    return "Anımsatıcıya ekledim."


def add_note(text):
    osa(f'tell application "Notes" to make new note with properties {{body:{_q(text)}}}')
    return "Not aldım."


