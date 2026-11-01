# -*- coding: utf-8 -*-
"""Apple servisleri: Takvim ve Anımsatıcılar (EventKit, hızlı ve yerel), Mail (salt okunur),
piyasalar (borsa, döviz, altın, kripto — yalnızca fiyat bilgisi, işlem yapmaz).

İlk kullanımda macOS, Python için Takvim/Anımsatıcılar/Otomasyon izni ister.
"""
import datetime as dt
import json
import logging
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger("mek.apple")

DAYS = ["pazartesi", "salı", "çarşamba", "perşembe", "cuma", "cumartesi", "pazar"]
MONTHS = ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim",
          "kasım", "aralık"]
NUM = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9,
       "on": 10, "on bir": 11, "on iki": 12, "yarım": 30, "çeyrek": 15}


# ----------------------------------------------------------------------------- EventKit
class Calendar:
    def __init__(self):
        self._store = None
        self._ok = {"event": None, "reminder": None}
        self._cache = {"t": 0, "events": []}

    def _ensure(self, kind):
        import EventKit
        from Foundation import NSDate, NSRunLoop
        if self._store is None:
            self._store = EventKit.EKEventStore.alloc().init()
        if self._ok[kind] is not None:
            return self._ok[kind]
        done = threading.Event()
        res = {}

        def cb(granted, err):
            res["g"] = bool(granted)
            done.set()
        if kind == "event":
            fn = getattr(self._store, "requestFullAccessToEventsWithCompletion_", None)
            fn(cb) if fn else self._store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeEvent, cb)
        else:
            fn = getattr(self._store, "requestFullAccessToRemindersWithCompletion_", None)
            fn(cb) if fn else self._store.requestAccessToEntityType_completion_(EventKit.EKEntityTypeReminder, cb)
        end = time.time() + 60
        while not done.is_set() and time.time() < end:  # izin penceresi için runloop'u döndür
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        self._ok[kind] = res.get("g", False)
        if not self._ok[kind]:
            log.warning("%s izni yok", kind)
        return self._ok[kind]

    @staticmethod
    def _nsdate(d):
        from Foundation import NSDate
        return NSDate.dateWithTimeIntervalSince1970_(d.timestamp())

    @staticmethod
    def _py(nsd):
        return dt.datetime.fromtimestamp(nsd.timeIntervalSince1970())

    def events(self, start, end):
        if not self._ensure("event"):
            return None
        pred = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            self._nsdate(start), self._nsdate(end), None)
        out = []
        for e in self._store.eventsMatchingPredicate_(pred) or []:
            out.append({"title": str(e.title() or "Etkinlik"), "start": self._py(e.startDate()),
                        "end": self._py(e.endDate()), "allday": bool(e.isAllDay()),
                        "location": str(e.location() or "")})
        return sorted(out, key=lambda x: x["start"])

    def upcoming(self, minutes=15):
        """Önbellekli (10 dk) yakın etkinlikler: proaktif hatırlatma için."""
        now = dt.datetime.now()
        if time.time() - self._cache["t"] > 600:
            self._cache = {"t": time.time(), "events": self.events(now, now + dt.timedelta(hours=12)) or []}
        return [e for e in self._cache["events"]
                if not e["allday"] and now <= e["start"] <= now + dt.timedelta(minutes=minutes)]

    def add_event(self, title, start, minutes=60, location=""):
        import EventKit
        if not self._ensure("event"):
            return False
        ev = EventKit.EKEvent.eventWithEventStore_(self._store)
        ev.setTitle_(title)
        ev.setStartDate_(self._nsdate(start))
        ev.setEndDate_(self._nsdate(start + dt.timedelta(minutes=minutes)))
        if location:
            ev.setLocation_(location)
        ev.setCalendar_(self._store.defaultCalendarForNewEvents())
        ok, err = self._store.saveEvent_span_commit_error_(ev, EventKit.EKSpanThisEvent, True, None)
        self._cache["t"] = 0
        return bool(ok)

    def reminders(self):
        if not self._ensure("reminder"):
            return None
        done, box = threading.Event(), {}
        pred = self._store.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, None)

        def cb(items):
            box["r"] = list(items or [])
            done.set()
        self._store.fetchRemindersMatchingPredicate_completion_(pred, cb)
        from Foundation import NSDate, NSRunLoop
        end = time.time() + 10
        while not done.is_set() and time.time() < end:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
        return [str(r.title()) for r in box.get("r", [])]

    def add_reminder(self, title, due=None):
        import EventKit
        if not self._ensure("reminder"):
            return False
        r = EventKit.EKReminder.reminderWithEventStore_(self._store)
        r.setTitle_(title)
        r.setCalendar_(self._store.defaultCalendarForNewReminders())
        if due:
            r.addAlarm_(EventKit.EKAlarm.alarmWithAbsoluteDate_(self._nsdate(due)))
        ok, err = self._store.saveReminder_commit_error_(r, True, None)
        return bool(ok)


# ----------------------------------------------------------------------------- Türkçe tarih/saat
def parse_when(text, now=None):
    """'yarın saat 3'te', 'cuma 15:30', 'bugün akşam 8', '12 kasım 10'da' -> datetime (bulamazsa None)."""
    now = now or dt.datetime.now()
    t = text.lower()
    day = None
    if "öbür gün" in t or "ertesi gün" in t:
        day = now.date() + dt.timedelta(days=2)
    elif "yarın" in t:
        day = now.date() + dt.timedelta(days=1)
    elif "bugün" in t or "bu akşam" in t:
        day = now.date()
    else:
        for i, d in enumerate(DAYS):
            if re.search(rf"\b{d}", t):
                delta = (i - now.weekday()) % 7 or 7
                day = now.date() + dt.timedelta(days=delta)
                break
        m = re.search(r"(\d{1,2}) (" + "|".join(MONTHS) + ")", t)
        if m:
            mon = MONTHS.index(m.group(2)) + 1
            year = now.year + (1 if (mon, int(m.group(1))) < (now.month, now.day) else 0)
            day = dt.date(year, mon, int(m.group(1)))
    hh = mm = None
    m = re.search(r"(\d{1,2})[:.](\d{2})", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"saat (\d{1,2}|" + "|".join(sorted(NUM, key=len, reverse=True)) + r")\b", t) or \
            re.search(r"(?:akşam|sabah|öğlen|gece|sonra) (\d{1,2})\b", t) or \
            re.search(r"\b(\d{1,2}) ?['’]?(?:te|ta|de|da|e|a|ye|ya)\b", t)
        if m:
            v = m.group(1)
            hh, mm = (int(v) if v.isdigit() else NUM.get(v, 0)), 0
            if re.search(r"buçuk", t):
                mm = 30
    if hh is None and day is None:
        return None
    if hh is None:
        hh, mm = 9, 0
    # "akşam 8" -> 20, "öğleden sonra 3" -> 15; belirtilmemiş 1-7 arası saatleri öğleden sonra say
    if re.search(r"akşam|gece|öğleden sonra|öğlen", t) and hh < 12:
        hh += 12
    elif not re.search(r"sabah", t) and 1 <= hh <= 7:
        hh += 12
    day = day or (now.date() if dt.time(hh, mm) > now.time() else now.date() + dt.timedelta(days=1))
    return dt.datetime.combine(day, dt.time(hh % 24, mm))


def say_time(d):
    now = dt.date.today()
    if d.date() == now:
        dd = "bugün"
    elif d.date() == now + dt.timedelta(days=1):
        dd = "yarın"
    else:
        dd = f"{d.day} {MONTHS[d.month - 1].capitalize()} {DAYS[d.weekday()].capitalize()}"
    return f"{dd} saat {d:%H:%M}"


# ----------------------------------------------------------------------------- Mail (salt okunur)
def unread_mail(limit=5):
    script = f'''
    tell application "Mail"
        set out to ""
        set msgs to (messages of inbox whose read status is false)
        set n to count of msgs
        repeat with i from 1 to (n as integer)
            if i > {limit} then exit repeat
            set m to item i of msgs
            set out to out & (extract name from sender of m) & " | " & (subject of m) & linefeed
        end repeat
        return (n as text) & linefeed & out
    end tell'''
    if subprocess.run(["pgrep", "-x", "Mail"], capture_output=True).returncode != 0:
        return None  # Mail açık değilse açtırmıyoruz
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
    if not lines:
        return None
    try:
        n = int(lines[0])
    except ValueError:
        return None
    return n, [tuple(x.split(" | ", 1)) for x in lines[1:]]


# ----------------------------------------------------------------------------- piyasalar
MARKET_ALIASES = {
    "bist": "XU100.IS", "borsa istanbul": "XU100.IS", "bist 100": "XU100.IS", "borsa": "XU100.IS",
    "dolar": "TRY=X", "euro": "EURTRY=X", "avro": "EURTRY=X", "sterlin": "GBPTRY=X",
    "altın": "GC=F", "gram altın": "GAU", "ons": "GC=F", "gümüş": "SI=F", "petrol": "BZ=F",
    "bitcoin": "BTC-USD", "ethereum": "ETH-USD", "nasdaq": "^IXIC", "s&p": "^GSPC", "dow": "^DJI",
    "thy": "THYAO.IS", "türk hava yolları": "THYAO.IS", "aselsan": "ASELS.IS", "garanti": "GARAN.IS",
    "akbank": "AKBNK.IS", "tüpraş": "TUPRS.IS", "ereğli": "EREGL.IS", "bim": "BIMAS.IS", "koç": "KCHOL.IS",
    "sabancı": "SAHOL.IS", "şişecam": "SISE.IS", "turkcell": "TCELL.IS", "apple": "AAPL", "nvidia": "NVDA",
    "tesla": "TSLA", "microsoft": "MSFT", "adobe": "ADBE", "google": "GOOGL", "amazon": "AMZN",
}
NAMES = {"XU100.IS": "BIST 100", "TRY=X": "Dolar", "EURTRY=X": "Euro", "GBPTRY=X": "Sterlin", "GC=F": "Ons altın",
         "SI=F": "Gümüş", "BZ=F": "Brent petrol", "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "GAU": "Gram altın"}
_qcache = {}


def _get(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def quote(symbol):
    """(fiyat, günlük % değişim, para birimi). 60 sn önbellek. Yalnızca bilgi; işlem yapmaz."""
    c = _qcache.get(symbol)
    if c and time.time() - c[0] < 60:
        return c[1]
    if symbol == "GAU":  # gram altın = ons(USD) * USD/TRY / 31.1035
        o, u = quote("GC=F"), quote("TRY=X")
        if not o or not u:
            return None
        price = o[0] * u[0] / 31.1035
        res = (price, o[1] + u[1], "TRY")
    else:
        d = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d")
        meta = d["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
        res = (price, (price - prev) / prev * 100 if prev else 0.0, meta.get("currency", ""))
    _qcache[symbol] = (time.time(), res)
    return res


def find_symbol(text):
    t = text.lower()
    for k in sorted(MARKET_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}", t):
            return MARKET_ALIASES[k]
    m = re.search(r"([a-zçğıöşü0-9 ]{2,30}?) (hisse\w*|kaç para|ne kadar|fiyat\w*)", t)
    if m:
        try:
            d = _get("https://query1.finance.yahoo.com/v1/finance/search?q=" + urllib.parse.quote(m.group(1).strip()))
            if d.get("quotes"):
                return d["quotes"][0]["symbol"]
        except Exception:
            pass
    return None


def _fmt(sym, q):
    price, chg, cur = q
    name = NAMES.get(sym, sym.replace(".IS", ""))
    unit = {"TRY": "lira", "USD": "dolar", "EUR": "euro"}.get(cur, cur)
    if sym in ("XU100.IS", "^IXIC", "^GSPC", "^DJI"):
        val = f"{price:,.0f} puan".replace(",", ".")
    else:
        val = f"{price:,.2f} {unit}".replace(",", "X").replace(".", ",").replace("X", ".")
    yon = "yükselişte" if chg > 0.05 else "düşüşte" if chg < -0.05 else "yatay"
    return f"{name} {val}, günlük yüzde {abs(chg):.1f} {yon}".replace(f"{abs(chg):.1f}",
                                                                         f"{abs(chg):.1f}".replace(".", ","))


def market_report(text="", watchlist=None):
    sym = find_symbol(text) if text else None
    syms = [sym] if sym and not re.search(r"borsa nasıl|piyasalar|piyasa nasıl|genel", text.lower()) \
        else ["XU100.IS", "TRY=X", "EURTRY=X", "GAU", "BTC-USD"] + list(watchlist or [])
    parts = []
    for s in syms[:8]:
        try:
            q = quote(s)
            if q:
                parts.append(_fmt(s, q))
        except Exception as e:
            log.warning("Fiyat alınamadı %s: %s", s, e)
    if not parts:
        return "Piyasa verisine şu an ulaşamadım."
    return ". ".join(parts) + ". Veriler gecikmeli olabilir, yatırım tavsiyesi değildir."
