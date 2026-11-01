#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jarvis — sizi dinlemeyen, gün içinde duruma göre kendisi konuşan ve arka planda sistemi
sürekli en iyi durumda tutan servis.

  Konuşur:  günün ilk brifingi, yaklaşan takvim etkinlikleri, piyasadaki belirgin hareketler,
            yağmur uyarısı, biten renderlar, temizlik sonuçları, sağlık ve güvenlik uyarıları
  Yapar:    performans profilleri (kurgu açıkken her şey ona), kaçak süreç kontrolü,
            boştayken temizlik, madenci/zararlı yazılım bekçiliği, karantina
  Apple Intelligence: söylenecek cümleleri cihaz üzerindeki Apple modeli doğal Türkçeye çevirir
            (internet ve kota gerekmez); kullanılamazsa hazır şablonlar devreye girer.
Mikrofon hiç açılmaz. Konuşma ayrı bir süreçte üretilir ve bitince bellek tamamen boşalır.
"""
import datetime as dt
import json
import logging
import logging.handlers
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, os.path.expanduser("~/.jarvis"))  # jarvis.py: hava durumu ve ses (EMA Lightning)
import apple
import config
import context as ctx
from security import Security
from services import ServiceManager

log = logging.getLogger("jarvis")
PY = os.path.join(config.JARVIS_DIR, "venv/bin/python")
SPEAK = os.path.join(config.JARVIS_DIR, "jarvis.py")
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")


def ai_phrase(facts, style="kısa ve doğal", timeout=20):
    """Apple Intelligence (cihaz üzerinde) ile bilgileri Jarvis üslubunda cümleye döker."""
    if not os.access(config.AI_BIN, os.X_OK):
        return None
    instr = ("Sen Jarvis'sin: Alptuğ Bey'in Mac'inde yaşayan, zeki, kibar, hafif esprili bir asistan. "
             f"Verilen bilgileri sesli okunmak üzere {style} Türkçeyle aktar. Bilgileri değiştirme, "
             "sayıları aynen koru, yeni bilgi uydurma. Emoji, madde işareti, başlık kullanma.")
    try:
        r = subprocess.run([config.AI_BIN, instr], input=facts, capture_output=True, text=True, timeout=timeout)
        out = EMOJI.sub("", r.stdout).strip()
        return out if r.returncode == 0 and out else None
    except Exception:
        return None


class Jarvis:
    def __init__(self):
        self.cfg = config.load()
        self.T = self.cfg["user_title"]
        self.services = ServiceManager(self.cfg)
        self.security = Security(self.notify)
        self.cal = apple.Calendar()
        self.renders = ctx.RenderTracker()
        self.snap = None
        self.last_spoke = 0.0
        self.muted_until = 0.0
        self.warned = {}
        self.alerted_events = set()
        self.market_seen = {}
        self.away_events = []
        self.was_away = False
        self.pro_session_start = None
        self.greet_file = os.path.join(config.STATE_DIR, "greeted")
        self.speak_lock = threading.Lock()
        self.running = True

    # ------------------------------------------------------------------ konuşma
    def _speech_blocked(self):
        s = self.snap
        if time.time() < self.muted_until:
            return True
        try:
            if ctx.other_mic_users():  # görüşme / kayıt sürüyor
                return True
        except Exception:
            pass
        return bool(s and s.idle > self.cfg["away_threshold_s"])

    def _quiet_hours(self):
        h = dt.datetime.now().hour
        return self.cfg["quiet_start_hour"] <= h < self.cfg["quiet_end_hour"]

    def speak(self, text):
        """Ayrı süreçte konuşur: TTS modeli yalnızca konuşurken bellekte durur."""
        def run():
            with self.speak_lock:
                log.info("SAY: %s", text)
                busy = bool(self.renders.active())
                if busy:  # render sürerken sıfır yük: macOS'un yerleşik sesi
                    subprocess.run(["say", "-v", "Yelda", text])
                else:
                    subprocess.run([PY, SPEAK, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   preexec_fn=lambda: os.nice(5))
        threading.Thread(target=run, daemon=True).start()

    def notify(self, text, urgent=False, title="Jarvis"):
        """Bildirim her zaman; sesli söyleme yalnızca uygunsa (sessiz saat, görüşme, aralık)."""
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(text)} with title {json.dumps(title)}'],
                       capture_output=True)
        if self._speech_blocked() and not (urgent and not self._quiet_hours()):
            if self.snap and self.snap.idle > self.cfg["away_threshold_s"]:
                self.away_events.append(text)
            return
        if not urgent and (self._quiet_hours() or time.time() - self.last_spoke < self.cfg["proactive_min_gap_s"]):
            return
        self.last_spoke = time.time()
        self.speak(text)

    def _once(self, key, gap):
        now = time.time()
        if now - self.warned.get(key, 0) > gap:
            self.warned[key] = now
            return True
        return False

    # ------------------------------------------------------------------ brifing
    def briefing(self):
        now = dt.datetime.now()
        greet = "Günaydın" if now.hour < 12 else "Tünaydın" if now.hour < 18 else "İyi akşamlar"
        facts = [f"Selam olarak '{greet}' kullan. Saat {now:%H:%M}, {now.day} {apple.MONTHS[now.month - 1]} "
                 f"{apple.DAYS[now.weekday()]}."]
        try:
            import jarvis as legacy
            w = legacy.get_weather(timeout=4)
            if w:
                facts.append(w)
        except Exception:
            pass
        try:
            evs = self.cal.events(now, dt.datetime.combine(now.date(), dt.time(23, 59)))
            if evs is None:
                facts.append("Takvime erişim izni henüz verilmedi.")
            elif evs:
                facts.append("Bugünkü takvim: " + "; ".join(
                    ("tüm gün " if e["allday"] else f"{e['start']:%H:%M} ") + e["title"] for e in evs[:6]) + ".")
            else:
                facts.append("Bugün takvimde etkinlik yok.")
        except Exception as e:
            log.warning("Takvim: %s", e)
        try:
            rem = self.cal.reminders() or []
            if rem:
                facts.append(f"{len(rem)} tamamlanmamış anımsatıcı var, ilki: {rem[0]}.")
        except Exception:
            pass
        if now.weekday() < 5:
            facts.append("Piyasalar: " + apple.market_report("genel", self.cfg["market_watchlist"]).split(" Veriler")[0])
        issues = self.health_summary()
        facts.append(issues or "Sistem sağlıklı, güvenlik denetimleri temiz.")
        text = " ".join(facts)
        return ai_phrase(text, "4-6 cümlelik akıcı bir sabah brifingi olarak, selamla başlayarak") or \
            f"{greet} {self.T}. " + text.split(". ", 1)[-1]

    def health_summary(self):
        s = self.snap
        parts = []
        if s and s.disk_free_gb < 80:
            parts.append(f"Diskte {s.disk_free_gb:.0f} gigabayt boş yer kaldı.")
        try:
            sec = self.security.posture()
            if sec:
                parts.append("Güvenlik ayarlarında dikkat: " + ", ".join(sec) + ".")
        except Exception:
            pass
        return " ".join(parts)

    # ------------------------------------------------------------------ olaylar
    def observe(self, s):
        now = s.t
        today = dt.date.today()
        away = s.idle > self.cfg["away_threshold_s"]

        # Günün ilk brifingi: sessiz saatler bittikten sonra masaya ilk oturuşta
        greeted = open(self.greet_file).read().strip() if os.path.exists(self.greet_file) else ""
        if not away and not self._quiet_hours() and greeted != today.isoformat():
            open(self.greet_file, "w").write(today.isoformat())
            threading.Thread(target=lambda: self.notify(self.briefing(), urgent=True), daemon=True).start()
        elif self.was_away and not away and self.away_events:
            summary = "; ".join(self.away_events[-5:])
            self.away_events = []
            self.notify(ai_phrase(f"Kullanıcı masaya döndü. Yokken olanlar: {summary}", "2-3 cümleyle, "
                                  "'tekrar hoş geldiniz' diyerek") or f"Tekrar hoş geldiniz {self.T}. {summary}.",
                        urgent=True)
        self.was_away = away

        for name, dur in self.renders.update(s.render_cpu, now):
            self.notify(f"{self.T}, {name} işi bitti, yaklaşık {int(dur // 60)} dakika sürdü.", urgent=True)

        if s.pro_pids and not away:
            self.pro_session_start = self.pro_session_start or now
            mins = (now - self.pro_session_start) / 60
            if mins > self.cfg["break_after_min"] and self._once("break", 3600) and not self.renders.active():
                self.notify(f"{self.T}, {int(mins)} dakikadır kesintisiz kurgudasınız; kısa bir mola iyi gelir.")
        else:
            self.pro_session_start = None

        if s.disk_free_gb < 60 and self._once("disk", 6 * 3600):
            self.notify(f"Diskte yalnızca {s.disk_free_gb:.0f} gigabayt kaldı. Boştayken temizleyeceğim.",
                        urgent=s.disk_free_gb < 25)
        if s.thermal >= 2 and self._once("thermal", 1800):
            self.notify("Sistem ısısı yükseldi; havalandırmanın önünün açık olduğundan emin olur musunuz?", urgent=True)
        if s.pro_pids and s.swap_gb > 6 and self._once("swap", 1800):
            hogs = [n for _, n in s.top_mem if not re.search(
                r"Premiere|After Effects|Resolve|Topaz|Media Encoder|ffmpeg|kernel|WindowServer", n)][:2]
            self.notify(f"Takas alanı {s.swap_gb:.0f} gigabayta çıktı." +
                        (f" En çok belleği {' ve '.join(hogs)} kullanıyor; kapatırsanız kurgu hızlanır." if hogs else ""))

    def tame_runaways(self, s):
        """Kurgu dışı bir süreç ~1 dk işlemciyi yorarsa verimlilik çekirdeklerine alınır."""
        seen = self.__dict__.setdefault("_hot", {})
        tamed = self.__dict__.setdefault("_tamed", set())
        alive = set()
        for pid, name, cpu, cmd in s.hot:
            if pid == os.getpid() or re.search(r"kernel_task|WindowServer|coreaudiod|launchd", name):
                continue
            alive.add(pid)
            seen[pid] = seen.get(pid, 0) + 1
            if seen[pid] >= 6 and pid not in tamed:
                subprocess.run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)], capture_output=True)
                tamed.add(pid)
                log.info("Arka plana alındı: %s (%d) %%%d", name, pid, cpu)
                if s.pro_pids and self._once("hot:" + name, 3 * 3600):
                    self.notify(f"{name} arka planda işlemciyi yoruyordu, kurguyu yavaşlatmasın diye "
                                "verimlilik çekirdeklerine aldım.")
        self.__dict__["_hot"] = {p: c for p, c in seen.items() if p in alive}

    def calendar_tick(self):
        try:
            for e in self.cal.upcoming(self.cfg["event_alert_min"]):
                key = (e["title"], e["start"].isoformat())
                if key in self.alerted_events:
                    continue
                self.alerted_events.add(key)
                mins = max(1, int((e["start"] - dt.datetime.now()).total_seconds() // 60))
                loc = f", {e['location']}" if e["location"] else ""
                self.notify(f"{self.T}, {mins} dakika sonra {e['title']}{loc}.", urgent=True, title="Takvim")
        except Exception as e:
            log.warning("Takvim kontrolü: %s", e)

    def market_tick(self):
        """Hafta içi piyasa saatlerinde belirgin hareketleri bir kez söyler; kapanışta özet."""
        now = dt.datetime.now()
        if now.weekday() >= 5:
            return
        th = self.cfg["market_alert_pct"]
        syms = ["XU100.IS", "TRY=X", "GAU", "BTC-USD"] + list(self.cfg["market_watchlist"])
        if 10 <= now.hour < 18:
            moves = []
            for sym in syms:
                try:
                    price, chg, _ = apple.quote(sym)
                except Exception:
                    continue
                limit = th.get(sym, th["default"])
                level = int(abs(chg) // limit)  # her eşik katında bir kez
                key = (sym, now.date().isoformat(), 1 if chg > 0 else -1)
                if level >= 1 and self.market_seen.get(key, 0) < level:
                    self.market_seen[key] = level
                    moves.append(apple._fmt(sym, (price, chg, _)))
            if moves:
                facts = "Piyasada belirgin hareket: " + "; ".join(moves) + "."
                self.notify(ai_phrase(facts, "1-2 kısa cümleyle, yatırım tavsiyesi vermeden") or facts,
                            title="Piyasa")
        elif now.hour == 18 and now.minute >= 15 and self._once(f"close:{now.date()}", 86400):
            rep = apple.market_report("genel", self.cfg["market_watchlist"])
            self.notify(ai_phrase("Borsa kapanış özeti: " + rep, "2-3 kısa cümleyle") or rep, title="Piyasa")

    def weather_tick(self):
        """Önümüzdeki 3 saatte yağmur ihtimali yüksekse bir kez haber verir."""
        try:
            import urllib.request
            req = urllib.request.Request("https://wttr.in/?format=j1", headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.load(r)
            now_h = dt.datetime.now().hour
            for slot in d["weather"][0]["hourly"]:
                h = int(slot["time"]) // 100
                if now_h < h <= now_h + 3 and int(slot["chanceofrain"]) >= 70:
                    if self._once(f"rain:{dt.date.today()}", 86400):
                        self.notify(f"Saat {h:02d} civarında yağmur bekleniyor, yüzde {slot['chanceofrain']} "
                                    "ihtimalle. Çıkacaksanız şemsiye alın.", title="Hava")
                    break
        except Exception:
            pass

    def start_clean(self, tier):
        """Temizlik ayrı süreçte, en düşük öncelikte (E-çekirdek, arka plan G/Ç) çalışır."""
        if getattr(self, "_cleaning", False):
            return
        self._cleaning = True
        open(os.path.join(config.STATE_DIR, f"clean_{tier}"), "w").write(str(time.time()))

        def run():
            try:
                proc = subprocess.Popen([PY, os.path.join(os.path.dirname(__file__), "cleaner.py"), "--tier", tier],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                subprocess.run(["/usr/sbin/taskpolicy", "-b", "-p", str(proc.pid)], capture_output=True)
                out, _ = proc.communicate(timeout=3600)
                r = json.loads(out.strip().splitlines()[-1])
            except Exception as e:
                log.warning("Temizlik hatası: %s", e)
                return
            finally:
                self._cleaning = False
            total = max(r.get("toplam") or 0, r.get("gercek_bosalan") or 0) / 1e9
            top = sorted(r["kategoriler"].items(), key=lambda x: -x[1])[:3]
            detail = ", ".join(f"{k} {v / 1e9:.1f} GB" for k, v in top if v > 5e7)
            facts = f"{'Haftalık derin' if tier == 'haftalik' else 'Acil' if tier == 'acil' else 'Günlük'} " \
                    f"temizlik bitti, toplam {total:.1f} gigabayt boşaldı." + (f" En çok: {detail}." if detail else "")
            rep = r.get("rapor") or {}
            inst, big = rep.get("eski_kurulum_dosyalari", {}), rep.get("acilmamis_buyuk_dosyalar", {})
            if inst.get("adet"):
                facts += f" İndirilenler'de {inst['adet']} eski kurulum dosyası {inst['bayt'] / 1e9:.1f} GB yer kaplıyor; silmek size kalmış."
            if big.get("adet"):
                facts += f" Ayrıca 6 aydır açılmamış {big['adet']} büyük dosya {big['bayt'] / 1e9:.0f} GB tutuyor."
            log.info("%s | %s", facts, json.dumps(r["kategoriler"], ensure_ascii=False))
            if tier != "gunluk" or total >= 0.5:
                self.notify(ai_phrase(facts, "1-3 kısa cümleyle") or facts, urgent=(tier == "acil"), title="Bakım")
        threading.Thread(target=run, daemon=True).start()

    def _since(self, tier):
        try:
            return time.time() - float(open(os.path.join(config.STATE_DIR, f"clean_{tier}")).read())
        except Exception:
            return 1e12

    # ------------------------------------------------------------------ döngüler
    def loop(self):
        while self.running:
            t0 = time.time()
            try:
                s = ctx.snapshot()
                self.snap = s
                self.services.tick(s)                 # performans profili + QoS (her turda)
                self.security.check_miners(s.hot)     # ek maliyetsiz
                self.tame_runaways(s)
                self.observe(s)
                busy = bool(s.pro_pids) or bool(self.renders.active())
                idle = s.idle > 900
                sec = self.security
                if sec._due("cal", 60):
                    self.calendar_tick()
                if sec._due("pool", 120 if not busy else 300):
                    sec.check_pool_connections()
                if sec._due("market", 1800):
                    threading.Thread(target=self.market_tick, daemon=True).start()
                if sec._due("weather", 3600):
                    threading.Thread(target=self.weather_tick, daemon=True).start()
                if not busy and sec._due("persist", 900):
                    sec.audit_persistence()
                if not busy and sec._due("posture", 6 * 3600):
                    issues = sec.posture()
                    if issues and self._once("posture", 24 * 3600):
                        self.notify("Güvenlik ayarlarında dikkat: " + ", ".join(issues) +
                                    ". Sistem Ayarları'ndan açmanızı öneririm.", title="Güvenlik")
                # Ağır işler yalnızca siz yokken ve kurgu/render yokken
                # Disk kritik: kurgu yoksa hemen (siz başındayken bile, en düşük öncelikte)
                if s.disk_free_gb < 60 and not busy and self._since("acil") > 2 * 3600:
                    self.start_clean("acil")
                if idle and not busy:
                    if self._since("haftalik") > 7 * 86400:
                        self.start_clean("haftalik")
                    elif self._since("gunluk") > 20 * 3600 and self._since("haftalik") > 20 * 3600:
                        self.start_clean("gunluk")
                    if sec._due("freshclam", 24 * 3600):
                        threading.Thread(target=sec.update_av, daemon=True).start()
                    if sec._due("av", 3 * 3600):
                        threading.Thread(target=sec.av_scan, daemon=True).start()
            except Exception as e:
                log.exception("Döngü hatası: %s", e)
            # Uyarlanır hız: kurgu sırasında 15 sn (yalnızca hafif işler), başınızdayken 10, uzakta 45
            s = self.snap
            gap = 45 if (s and s.idle > 600 and not s.pro_pids) else 15 if (s and s.pro_pids) else 10
            time.sleep(max(1, gap - (time.time() - t0)))

    def socket_loop(self):
        """Terminal / Kestirmeler için: `jarvis brifing`, `jarvis durum`, `jarvis sus 60`."""
        try:
            os.unlink(config.SOCKET_PATH)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(config.SOCKET_PATH)
        os.chmod(config.SOCKET_PATH, 0o600)
        srv.listen(4)
        while self.running:
            try:
                conn, _ = srv.accept()
                conn.settimeout(60)
                with conn:
                    cmd = conn.recv(1024).decode("utf-8", "ignore").strip()
                    reply = self.command(cmd)
                    conn.sendall((reply or "ok").encode())
            except Exception as e:
                log.warning("Soket: %s", e)

    def command(self, cmd):
        c = cmd.lower()
        if c.startswith("brifing"):
            text = self.briefing()
            self.speak(text)
            return text
        if c.startswith("piyasa") or c.startswith("borsa"):
            text = apple.market_report(cmd.split(" ", 1)[1] if " " in cmd else "genel", self.cfg["market_watchlist"])
            self.speak(text)
            return text
        if c.startswith("temizle"):
            tier = "haftalik" if "derin" in c else "gunluk"
            if self.snap and self.snap.pro_pids:
                return "Kurgu programı açıkken temizlik yapmıyorum."
            self.start_clean(tier)
            return f"{'Derin' if tier == 'haftalik' else 'Günlük'} temizlik başladı, bitince haber vereceğim."
        if c.startswith("sus"):
            mins = int(re.findall(r"\d+", c)[0]) if re.findall(r"\d+", c) else 60
            self.muted_until = time.time() + mins * 60
            return f"{mins} dakika sessizim."
        if c.startswith("güvenlik"):
            issues = self.security.posture()
            q = len(os.listdir(config.QUARANTINE)) - (1 if os.path.exists(os.path.join(config.QUARANTINE, "kayit.txt")) else 0)
            return ("Güvenlik ayarları: " + (", ".join(issues) if issues else "hepsi açık") +
                    f". Karantinada {max(0, q)} öğe var.")
        if c.startswith("söyle "):
            self.speak(cmd[6:])
            return "ok"
        s = self.snap
        if not s:
            return "Henüz ölçüm yok."
        return (f"Öndeki uygulama {s.front}, CPU %{s.cpu:.0f}, GPU %{s.gpu}, bellek %{s.mem_pct:.0f}, "
                f"takas {s.swap_gb:.1f} GB, boş disk {s.disk_free_gb:.0f} GB, profil {self.services.profile}, "
                f"süren render: {', '.join(self.renders.active()) or 'yok'}.")

    def run(self):
        threading.Thread(target=self.socket_loop, daemon=True).start()
        log.info("Jarvis hazır")
        self.loop()

    def stop(self, *_):
        self.running = False
        self.services.shutdown()
        sys.exit(0)


def main():
    h = logging.handlers.RotatingFileHandler(config.LOG_FILE, maxBytes=2_000_000, backupCount=2)
    logging.basicConfig(level=logging.INFO, handlers=[h], format="%(asctime)s %(name)s %(levelname)s %(message)s")
    j = Jarvis()
    signal.signal(signal.SIGTERM, j.stop)
    j.run()


if __name__ == "__main__":
    main()
