#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mek — tek, sürekli çalışan akıllı asistan servisi.

Eski jarvis.startup / jarvis.periodic / keepawake-videoapps / smart-cleaner ajanlarının
yerini alır. Saat tabanlı değil, olay tabanlıdır: ne yaptığınıza bakar, gerektiğinde konuşur,
'Mek ...' diye seslendiğinizde komut alır.
"""
import datetime
import json
import logging
import logging.handlers
import os
import queue
import random
import re
import signal
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actions as act
import config
import apphelp
import editkit
import editfx
import context as ctx
import ui
from brain import Brain
from services import ServiceManager, priv
from voice import Listener, Speaker, normalize

log = logging.getLogger("mek")

NUM_WORDS = {"sıfır": 0, "on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50, "altmış": 60,
             "yetmiş": 70, "seksen": 80, "doksan": 90, "yüz": 100, "bir": 1, "iki": 2, "üç": 3,
             "dört": 4, "beş": 5, "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9}


def later(delay, fn):
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()
    return t


def parse_number(text):
    m = re.search(r"\d+", text)
    if m:
        return int(m.group())
    total, found = 0, False
    for w in text.split():
        if w in NUM_WORDS:
            total += NUM_WORDS[w]
            found = True
    return total if found else None


class Mek:
    def __init__(self):
        self.cfg = config.load()
        self.title = self.cfg["user_title"]
        self.inbox = queue.Queue()
        self.speaker = Speaker()
        self.services = ServiceManager(self.cfg)
        self.brain = Brain(self.cfg)
        self.renders = ctx.RenderTracker()
        self.snap = None
        self.pending = None           # (açıklama, fonksiyon, threading.Timer)
        self.recording_mode = False
        self.muted_until = 0.0
        self.last_proactive = 0.0
        self.pro_session_start = None
        self.last_break_hint = 0.0
        self.was_away = False
        self.away_events = []
        self._greet_file = os.path.join(config.STATE_DIR, "greeted")
        try:
            with open(self._greet_file) as f:
                self.greeted_date = datetime.date.fromisoformat(f.read().strip())
        except Exception:
            self.greeted_date = None
        self.warned = {}
        self.mic_others = []
        self.hot_seen = {}           # pid -> art arda yüksek CPU sayısı
        self.tamed = set()           # arka plana aldığımız süreçler
        self.look_params = {}        # o anki renk bakışı (göreli komutlar için)
        self.edit_busy = False
        self.running = True

    # ---------------------------------------------------------------- konuşma
    def say(self, text):
        threading.Thread(target=self.speaker.say, args=(text,), daemon=True).start()

    def can_speak_proactively(self, urgent=False):
        now = time.time()
        hour = datetime.datetime.now().hour
        if self.recording_mode or now < self.muted_until:
            return False
        if self.in_recording_app() or self.mic_others:
            return False
        if urgent:
            return True
        if self.cfg["quiet_start_hour"] <= hour < self.cfg["quiet_end_hour"]:
            return False
        return now - self.last_proactive > self.cfg["proactive_min_gap_s"]

    def proactive(self, text, urgent=False):
        if self.can_speak_proactively(urgent):
            self.last_proactive = time.time()
            self.say(text)
        else:
            act.notify(text)

    # ---------------------------------------------------------------- dinleme
    def mic_paused(self):
        if not self.cfg.get("listen", True):
            return True
        if self.mic_others:  # kayıt, görüşme, sesli mesaj: başka uygulama mikrofonda
            return True
        return self.in_recording_app() and not self.recording_mode

    def in_recording_app(self):
        front = (self.snap.front if self.snap else "").lower()
        return bool(front) and any(r.lower() in front for r in self.cfg["recording_apps"])

    def busy(self):
        return bool(self.snap and self.snap.pro_pids) or bool(self.renders.active())

    # ---------------------------------------------------------------- bekleyen eylemler
    def schedule(self, desc, fn, delay):
        self.cancel_pending(silent=True)
        self.pending = (desc, fn, later(delay, self._fire_pending))

    def _fire_pending(self):
        if self.pending:
            desc, fn, _ = self.pending
            self.pending = None
            log.info("Bekleyen eylem çalışıyor: %s", desc)
            self.services.shutdown()
            fn()

    def cancel_pending(self, silent=False):
        if self.pending:
            self.pending[2].cancel()
            desc = self.pending[0]
            self.pending = None
            if not silent:
                self.say(f"{desc} iptal edildi efendim.")
            return True
        return False

    # ---------------------------------------------------------------- komutlar
    def handle(self, source, text):
        t = normalize(text)
        t = re.sub(r"\bdk\b", "dakika", t)
        t = re.sub(r"\bsn\b", "saniye", t)
        t = re.sub(r"\byarım saat\b", "30 dakika", t)
        log.info("CMD[%s]: %s", source, t)
        if source == "wake":
            # Konuşarak cevap verirsek mikrofon o sırada kapanır ve komutun kaçar;
            # kısa bir sinyal sesi çalıp dinlemeye devam ediyoruz
            self.speaker.stop()
            import subprocess
            subprocess.Popen(["afplay", "-v", "0.5", "/System/Library/Sounds/Tink.aiff"])
            return
        reply = self.rules(t)
        if reply is None:
            reply = self.agent(text)
            self.conversing = bool(reply)   # serbest sohbet: karşılıklı akış açık
        else:
            self.conversing = False         # komut: iş bitti, tekrar "Mek" beklenir
            self.brain.record(text, reply)
        if reply:
            self.say(reply)
        elif ui.current() == "thinking":
            ui.set("listening", "")

    def rules(self, t):
        T = self.title
        cd = self.cfg["shutdown_countdown_s"]
        if re.search(r"\b(dur|iptal|vazgeç|vazgeçtim|bekle|hayır)\b", t) and self.pending:
            self.cancel_pending()
            return ""
        system_acts = [
            (r"^(kapan|bilgisayarı kapat|sistemi kapat|mac[ıi]? kapat)$", "Kapatma", act.shutdown),
            (r"^(bilgisayarı|sistemi|mac[ıi]?) (yeniden başlat|restart et)$|^yeniden başlat$", "Yeniden başlatma", act.restart),
            (r"^(uyu|uyku moduna geç|uykuya geç|bilgisayarı uyut)$", "Uyku", act.sleep_now),
        ]
        m = re.search(r"((?:\d+|\w+)(?: \w+)?) (saniye|dakika|saat) sonra (.+)", t)
        if m and parse_number(m.group(1)):
            n, unit, rest = parse_number(m.group(1)), m.group(2), m.group(3).strip()
            secs = n * {"saniye": 1, "dakika": 60, "saat": 3600}[unit]
            for rx, desc, fn in system_acts:
                if re.search(rx, rest):
                    self.schedule(desc, fn, secs)
                    return f"{desc} {n} {unit} sonra. İptal için 'Mek iptal'."
            later(secs, lambda: self.proactive(f"{T}, hatırlatma: {rest}", urgent=True))
            return f"Tamam, {n} {unit} sonra hatırlatacağım."
        for rx, desc, fn in system_acts:
            if re.search(rx, t):
                self.schedule(desc, fn, cd)
                return f"{desc} {cd} saniye içinde {T}. Vazgeçerseniz 'dur' ya da 'iptal' demeniz yeterli."
        if re.search(r"^(kendini kapat|dinlemeyi (kapat|bırak)|kulaklarını kapat)$", t):
            self.cfg["listen"] = False
            return "Mikrofonu kapattım. Tekrar açmak için terminalde: mek dinle."
        if t in ("dinle", "dinlemeye başla"):
            self.cfg["listen"] = True
            return "Dinliyorum."
        if re.fullmatch(r"(sus|sessiz ol|yeter|kes sesini)", t):
            self.speaker.stop()
            self.muted_until = time.time() + 3600
            return ""
        # Önce kurgu otomasyonu ("boşlukları kes" kısayoldaki "kes" ile karışmasın), sonra kısayollar
        job = self.edit_intent(t)
        if job:
            return self.start_edit(*job)
        front = self.snap.front if self.snap else ""
        if apphelp.app_family(front):
            r = apphelp.try_command(front, t)
            if r is not None:
                return r
        if re.search(r"ekrana bak|ekranda ne|bu ekran|şuna bak|buna bak", t):
            return self.look_screen(t)
        m = re.fullmatch(r"(.+?) (ara|arar mısın|araya bağla)", t)
        if m and not re.search(r"internet|google|web|youtube", t):
            return act.call(m.group(1))
        if re.fullmatch(r"(ekranı kilitle|kilitle)", t):
            act.lock_screen()
            return ""
        if re.search(r"kayıt modu|kayda gir|kayda başlıyorum", t):
            self.recording_mode = True
            return "Kayıt modu açık. Susuyorum; bitince 'Mek kayıt bitti' deyin."
        if re.search(r"kayıt bitti|kayıt modunu kapat", t):
            self.recording_mode = False
            return "Kayıt modu kapandı, tekrar yanınızdayım."
        if re.search(r"\bsaat kaç\b", t):
            return f"Saat {datetime.datetime.now():%H:%M}."
        if re.search(r"\b(hava|hava durumu)\b", t):
            return act_legacy("get_weather") or "Hava durumunu alamadım."
        if re.search(r"bugün (ne var|neler var|programım)|takvim", t):
            ev = act.todays_events()
            return ("Bugünkü takviminiz: " + ev.replace("\n", ", ")) if ev else "Bugün takviminiz boş."
        if re.search(r"(durum|sistem nasıl|rapor|ne durumdayız)", t):
            return self.status_report()
        if re.search(r"temizlik|temizle|bakım yap", t):
            if self.busy():
                return "Kurgu ya da render sürerken temizlik yapmıyorum; boşa çıktığınızda kendim hallederim."
            return "Temizliğe başladım, bitince haber vereceğim." if self.start_clean(announce=True) \
                else "Temizlik betiğini bulamadım."
        if re.search(r"çöp", t) and "boşalt" in t:
            return act.empty_trash_forbidden()
        if re.search(r"sesi? (aç|yükselt|artır)", t) and not re.search(r"yüzde", t):
            return act.change_volume(+15)
        if re.search(r"sesi? (kıs|azalt|düşür)", t):
            return act.change_volume(-15)
        m = re.search(r"ses(i|ini)? yüzde (.+)|yüzde (.+) ses", t)
        if m:
            n = parse_number(t)
            return act.set_volume(n) if n is not None else None
        if re.search(r"(müzi[kğ]|şarkı)\w* (durdur|duraklat)|^duraklat$", t):
            return act.media("pause")
        if re.search(r"(müzi[kğ]|şarkı)\w* (çal|başlat|devam)", t):
            return act.media("playpause")
        if re.search(r"(sonraki|sıradaki) (şarkı|parça)", t):
            return act.media("next")
        if re.search(r"karanlık mod|koyu mod", t):
            return act.dark_mode(None if "aç" not in t and "kapat" not in t else "aç" in t)
        m = re.search(r"not al (.+)", t)
        if m:
            return act.add_note(m.group(1))
        m = re.search(r"kestirme(yi|sini)? (çalıştır)? ?(.+)", t)
        if m and "çalıştır" in t:
            return act.run_shortcut(m.group(3).replace("çalıştır", "").strip())
        m = re.fullmatch(r"(.+?)(['’]?[iıuüyı]?|y[iıuü])? (aç|başlat)", t)
        raw = t.rsplit(" ", 1)[0]
        if m and not re.search(r"ses|müzi|mod", t) and (act.resolve_app(raw) or act.resolve_app(m.group(1))):
            return act.open_app(raw if act.resolve_app(raw) else m.group(1))
        m = re.fullmatch(r"(.+?)(['’]?[iıuüyı]?|y[iıuü])? kapat", t)
        if m and not re.search(r"ses|mod|bilgisayar", t) and (act.resolve_app(raw) or act.resolve_app(m.group(1))):
            return act.quit_app(raw if act.resolve_app(raw) else m.group(1))
        return None

    def agent(self, text):
        front = self.snap.front if self.snap else ""
        result = self.brain.think(text, self.context_line(), app=apphelp.app_family(front) or "")
        log.info("BRAIN: %s", result)
        for a in result.get("actions", [])[:4]:
            tool, args = a.get("tool"), a.get("args") or {}
            if tool == "look_screen" and not re.search(r"ekran|bak|gör", normalize(text)):
                continue
            if tool == "app_key" and apphelp.try_command.__name__ and not apphelp.app_family(
                    self.snap.front if self.snap else ""):
                continue
            try:
                self.run_tool(tool, args)
            except Exception as e:
                log.error("Araç %s hata: %s", tool, e)
        return result.get("say", "")

    def run_tool(self, tool, a):
        cd = self.cfg["shutdown_countdown_s"]
        simple = {
            "open_app": lambda: act.open_app(a["name"]), "quit_app": lambda: act.quit_app(a["name"]),
            "open_url": lambda: act.open_url(a["url"]), "set_volume": lambda: act.set_volume(a["pct"]),
            "media": lambda: act.media(a.get("cmd", "playpause")),
            "run_shortcut": lambda: act.run_shortcut(a["name"], a.get("input")),
            "add_reminder": lambda: act.add_reminder(a["text"], a.get("minutes")),
            "add_note": lambda: act.add_note(a["text"]), "dark_mode": lambda: act.dark_mode(a.get("on")),
            "lock": act.lock_screen,
            "edit": lambda: self.say(self.start_edit(*(self.edit_intent(normalize(a.get("command", "")))
                                                      or ("plan", a.get("command", ""))))),
            "clean": lambda: self.start_clean(announce=True),
            "call": lambda: self.say(act.call(a["name"])),
            "look_screen": lambda: self.say(self.look_screen(a.get("question", ""))),
            "app_key": lambda: apphelp.try_command(self.snap.front if self.snap else "", a.get("phrase", "")),
            "system_status": lambda: self.say(self.status_report()),
            "timer": lambda: later(float(a["minutes"]) * 60,
                                   lambda: self.proactive(a.get("text", "Süre doldu."), True)),
        }
        # Geri dönüşü zor eylemler her zaman iptal edilebilir geri sayımla
        guarded = {"shutdown": ("Kapatma", act.shutdown), "restart": ("Yeniden başlatma", act.restart),
                   "sleep": ("Uyku", act.sleep_now)}
        if tool in simple:
            simple[tool]()
        elif tool in guarded:
            desc, fn = guarded[tool]
            self.schedule(desc, fn, cd)
            self.say(f"{desc} {cd} saniye içinde. İptal için 'dur' deyin.")

    # ---------------------------------------------------------------- bağlam
    def context_line(self):
        s = self.snap
        if not s:
            return "bilinmiyor"
        busy = ", ".join(self.renders.active()) or "yok"
        return (f"saat {datetime.datetime.now():%H:%M}, öndeki uygulama {s.front}, CPU %{s.cpu:.0f}, "
                f"GPU %{s.gpu if s.gpu is not None else '?'}, bellek %{s.mem_pct:.0f}, takas {s.swap_gb:.1f} GB, "
                f"boş disk {s.disk_free_gb:.0f} GB, süren render: {busy}, profil {self.services.profile}")

    # ------------------------------------------------------------- kurgu otomasyonu
    def edit_intent(self, t):
        m = re.search(r"kaba kurgu\w*(?: yap\w*)?(.*)", t)
        if m:
            return ("rough", m.group(1).strip())
        if re.search(r"(referans|bu (fotoğraf|görsel|resim)|şu (fotoğraf|görsel)).*(gibi|renk)|renkleri eşle", t):
            return ("match", "")
        if re.search(r"\b(renk|rengi|renkleri|ton|bakış|look|grade|kontrast|doygun|sıcak|soğuk|sinematik|filmik)", t) \
                and re.search(r"yap|olsun|ver|artır|azalt|daha|sıfırla|uygula|kaldır|ekle", t):
            return ("look", t)
        if not re.search(r"yap|ekle|uygula|kes|koy|ver|at\b|temizle|olsun|getir|sansürle|geri al|oluştur|hazırla", t):
            return None
        ops = editfx.plan_from_text(t)
        if ops:
            return ("ops", ops)
        # Kurala uymayan ama kurguyla ilgili serbest istek: yerel/ bulut planlayıcıya
        if re.search(r"videoya|videoyu|klibe|klibi|kurguya|zaman çizelgesi|efekt|sese|sesi .* yap", t):
            return ("plan", t)
        return None

    def start_edit(self, job, arg):
        if self.edit_busy:
            return "Bir kurgu işi zaten sürüyor, bitince haber vereceğim."
        front = self.snap.front if self.snap else ""
        if job == "look":
            return self.do_look(arg, front)
        media = editkit.pick_media(front, editkit.VIDEO_EXT if job == "match" else editkit.VIDEO_EXT + editkit.AUDIO_EXT)
        if not media:
            return ("Hangi dosya üzerinde çalışacağımı bulamadım. Resolve'da zaman çizelgesini açın ya da "
                    "Finder'da dosyayı seçip tekrar söyleyin.")
        self.edit_busy = True
        threading.Thread(target=self._edit_job, args=(job, arg, media, front), daemon=True).start()
        names = ", ".join(os.path.basename(m) for m in media[:2])
        what = {"ops": "Üzerinde çalışıyorum", "plan": "İsteğinizi kurguya döküyorum",
                "rough": "Kaba kurguyu hazırlıyorum", "match": "Referansa göre renk eşliyorum"}[job]
        return f"{what}: {names}. Bitince haber vereceğim."

    def _load_edit(self, media, front):
        """Resolve açıksa oradaki kesimlerden, değilse Mek'in son sürümünden ya da ham dosyadan başlar."""
        if "resolve" in front.lower():
            pieces, tl_name = editkit.resolve_timeline_pieces()
            if pieces:
                e = editfx.Edit.from_media([pieces[0][0]])
                e.d["pieces"] = [{"src": p, "in": a, "out": b} for p, a, b in pieces]
                e.d["name"] = editfx._slug(tl_name or pieces[0][0])
                prev = editfx.Edit.load_for([pieces[0][0]])
                e.d["version"] = prev.d.get("version", 0)
                return e
        return editfx.Edit.load_for(media)

    def _deliver(self, edit, front):
        xml, srt = editfx.export(edit)
        where = editkit.open_in_editor(xml, front)
        if srt:
            if "resolve" in front.lower() and editkit.resolve_import_srt(srt):
                where += " Alt yazı medya havuzunda."
            elif "premiere" in front.lower():
                import subprocess
                subprocess.Popen(["open", "-a", front, srt])
                where += " Alt yazıyı da içe aktardım."
        return f"Sürüm {edit.d['version']} hazır, {edit.duration():.0f} saniye. {where}"

    def _edit_job(self, job, arg, media, front):
        T = self.title
        try:
            ui.set("thinking", "Kurgu işi sürüyor…")
            stt = self.cfg["stt_model"]
            if job in ("ops", "plan"):
                ops = arg
                if job == "plan":
                    raw = self.brain.complete(editfx.PLANNER_SYSTEM, arg, max_tokens=600)
                    ops = json.loads(raw[raw.index("{"): raw.rindex("}") + 1]).get("ops", [])
                    if not ops:
                        raise RuntimeError("isteği kurgu işlemlerine çeviremedim")
                e = self._load_edit(media, front)
                msgs = editfx.run_ops(e, ops, stt, llm=self.brain.complete,
                                      log=lambda m: ui.set("thinking", m))
                msg = f"{T}, " + "; ".join(msgs) + ". " + self._deliver(e, front)
            elif job == "rough":
                r = editkit.rough_cut(media, arg, stt, self.brain.complete)
                where = editkit.open_in_editor(r["xml"], front)
                msg = f"Kaba kurgu hazır: {r['parts']} parça, {r['after']:.0f} saniye. {r['note']} {where}"
            elif job == "match":
                ref = [p for p in editkit.finder_selection() if p.lower().endswith(editkit.IMAGE_EXT)]
                if not ref:
                    msg = "Referans görseli Finder'da seçip tekrar söyleyin."
                else:
                    paths = editkit.match_lut(media[0], ref[0])
                    msg = self._apply_lut(paths, front, "Referans renk LUT'u")
            else:
                msg = ""
            log.info("Kurgu işi bitti: %s", msg)
            act.notify(msg, "Mek kurgu")
            self.proactive(msg, urgent=True)
        except Exception as e:
            log.exception("Kurgu işi hatası")
            self.proactive(f"Kurgu işinde sorun çıktı: {e}", urgent=True)
        finally:
            self.edit_busy = False
            self.brain.maybe_unload(force=self.busy())
            if ui.current() == "thinking":
                ui.set("listening", "")

    def _apply_lut(self, paths, front, label):
        resolve_path = next((p for p in paths if "Blackmagic" in p), None)
        if "resolve" in front.lower() and resolve_path:
            n = editkit.resolve_apply_lut(resolve_path)
            if n:
                return f"{label} {n} klibe uygulandı."
        name = os.path.splitext(os.path.basename(paths[0]))[0] if paths else ""
        return (f"{label} hazır: {name}. Premiere'de Lumetri, Yaratıcı, Görünüm listesinde; "
                "Resolve'da LUT listesinde Mek klasöründe.")

    def do_look(self, text, front):
        params, hit = editkit.parse_look(text, self.look_params)
        if not hit and params:
            return "Hangi bakışı istediğinizi anlayamadım. Sıcak, soğuk, sinematik, kontrastlı, soluk gibi tarif edebilirsiniz."
        if not hit and not params:
            self.look_params = {}
            return "Renk ayarını sıfırladım."
        self.look_params = params
        paths = editkit.look_lut(params, "Mek bakis")
        return self._apply_lut(paths, front, "Yeni bakış")

    def look_screen(self, question):
        """Ekran görüntüsünü yalnızca istendiğinde, küçültülmüş olarak Gemini'ye gösterir."""
        import subprocess
        path = os.path.join(config.STATE_DIR, "screen.jpg")
        r = subprocess.run(["screencapture", "-x", "-t", "jpg", path], capture_output=True)
        if r.returncode != 0 or not os.path.exists(path):
            return "Ekran görüntüsü alamadım; Python'a Ekran Kaydı izni gerekiyor."
        subprocess.run(["sips", "-Z", "1600", path], capture_output=True)
        ui.set("thinking", "Ekrana bakıyorum…")
        try:
            return self.brain.look(question, path, self.context_line())
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def start_clean(self, announce=False):
        """Temizliği başlatır; bitince ne kadar yer açıldığını söyler."""
        if self.busy():
            return False

        def free_gb():
            st = os.statvfs("/System/Volumes/Data")
            return st.f_bavail * st.f_frsize / 1024 ** 3

        before = free_gb()
        proc = self.services.run_cleaner()
        if not proc:
            return False

        def wait():
            try:
                proc.wait(timeout=3600)
            except Exception:
                return
            freed = max(0.0, free_gb() - before)
            amount = f"{freed:.1f} gigabayt" if freed >= 0.1 else "kayda değer olmayan miktarda"
            msg = f"Sistem temizliği bitti {self.title}, {amount} yer açıldı."
            act.notify(msg, "Mek temizlik")
            if announce or freed >= 1:
                self.proactive(msg, urgent=announce)
            log.info(msg)

        threading.Thread(target=wait, daemon=True).start()
        return True

    def health(self, s):
        """Düşük maliyetli sürekli sağlık bakımı: kaçak süreçler, ısı, disk sağlığı."""
        T = self.title
        alive = set()
        for pid, name, cpu, cmd in s.hot:
            if pid == os.getpid() or re.search(r"kernel_task|WindowServer|coreaudiod|launchd", name):
                continue
            alive.add(pid)
            self.hot_seen[pid] = self.hot_seen.get(pid, 0) + 1
            # ~1 dakikadır kurgu dışı bir süreç çekirdekleri yiyorsa verimlilik çekirdeklerine al
            if self.hot_seen[pid] >= 6 and pid not in self.tamed:
                import subprocess
                subprocess.run(["/usr/sbin/taskpolicy", "-b", "-p", str(pid)], capture_output=True)
                self.tamed.add(pid)
                log.info("Kaçak süreç arka plana alındı: %s (%d) %%%d", name, pid, cpu)
                if s.pro_pids and self._once("hot:" + name, 3 * 3600):
                    self.proactive(f"{T}, {name} arka planda işlemciyi yoruyordu, "
                                   "kurgu uygulamalarınızı yavaşlatmasın diye verimlilik çekirdeklerine aldım.")
        self.hot_seen = {p: c for p, c in self.hot_seen.items() if p in alive}
        if s.thermal >= 2 and self._once("thermal", 1800):
            self.proactive(f"{T}, sistem ısısı yükseldi. Havalandırmanın önü açık mı kontrol eder misiniz?",
                           urgent=True)
        if self._once("deep_health", 6 * 3600):
            try:
                import jarvis
                issues = jarvis.check_critical_system_health()
                if issues:
                    self.proactive(" ".join(issues), urgent=True)
            except Exception:
                pass

    def status_report(self):
        s = self.snap
        if not s:
            return "Henüz ölçüm yapmadım."
        parts = [f"İşlemci yüzde {s.cpu:.0f}"]
        if s.gpu is not None:
            parts.append(f"grafik işlemci yüzde {s.gpu}")
        parts.append(f"bellek yüzde {s.mem_pct:.0f}")
        parts.append(f"diskte {s.disk_free_gb:.0f} gigabayt boş")
        busy = self.renders.active()
        if busy:
            parts.append("süren işler: " + ", ".join(busy))
        prof = "performans profili" if self.services.profile == "performance" else "normal profil"
        return f"{', '.join(parts)}. Sistem {prof}nde efendim."

    def observe(self, s):
        """Ne yaptığınıza göre öneri ve uyarılar üretir."""
        now = s.t
        T = self.title
        # Masaya dönüş / ilk oturum brifingi
        away = s.idle > self.cfg["away_threshold_s"]
        today = datetime.date.today()
        if self.was_away and not away:
            if self.greeted_date != today and datetime.datetime.now().hour >= self.cfg["quiet_end_hour"]:
                self.greeted_date = today
                open(self._greet_file, "w").write(today.isoformat())
                self.proactive(legacy_briefing(), urgent=True)
            elif self.away_events:
                self.proactive(f"Tekrar hoş geldiniz {T}. Siz yokken: " + "; ".join(self.away_events) + ".",
                               urgent=True)
            self.away_events = []
        if self.greeted_date is None and not away and datetime.datetime.now().hour >= self.cfg["quiet_end_hour"]:
            self.greeted_date = today
            open(self._greet_file, "w").write(today.isoformat())
            self.proactive(legacy_briefing(), urgent=True)
        self.was_away = away

        # Render bitişleri
        for name, dur in self.renders.update(s.render_cpu, now):
            msg = f"{name} işi bitti gibi görünüyor, yaklaşık {int(dur // 60)} dakika sürdü"
            if away:
                self.away_events.append(msg)
            else:
                self.proactive(f"{T}, {msg}.", urgent=True)
            act.notify(msg)

        # Uzun kurgu oturumu -> mola
        if s.pro_pids and not away:
            self.pro_session_start = self.pro_session_start or now
            mins = (now - self.pro_session_start) / 60
            if mins > self.cfg["break_after_min"] and now - self.last_break_hint > 3600 \
                    and not self.renders.active():
                self.last_break_hint = now
                self.proactive(f"{T}, {int(mins)} dakikadır kesintisiz kurgudasınız. "
                               "Beş dakikalık bir mola gözlerinize iyi gelir.")
        else:
            self.pro_session_start = None

        # Bellek baskısı: kurgu açıkken bellek yiyen yan uygulamaları söyle
        if s.pro_pids and s.swap_gb > 6 and self._once("swap", 1800):
            hogs = [n for _, n in s.top_mem if not re.search(
                r"Premiere|After Effects|Resolve|Topaz|Media Encoder|ffmpeg|kernel|WindowServer", n)][:2]
            hint = f" En çok belleği {' ve '.join(hogs)} kullanıyor; kapatırsam render hızlanır." if hogs else ""
            self.proactive(f"{T}, takas alanı {s.swap_gb:.0f} gigabayta çıktı.{hint}")

        # Disk
        if s.disk_free_gb < 60 and self._once("disk", 6 * 3600):
            self.proactive(f"Diskte yalnızca {s.disk_free_gb:.0f} gigabayt kaldı {T}. "
                           "Boşta olduğunuzda önbellekleri temizleyeceğim.", urgent=s.disk_free_gb < 25)

        # Boşta + kurgu yok + render yok -> bakım
        if s.idle > 900 and not s.pro_pids and not self.renders.active() and self.services.cleaner_due():
            log.info("Boşta bakım başlıyor")
            self.start_clean()
        self.health(s)

    def _once(self, key, gap):
        now = time.time()
        if now - self.warned.get(key, 0) > gap:
            self.warned[key] = now
            return True
        return False

    # ---------------------------------------------------------------- döngüler
    def monitor_loop(self):
        while self.running:
            try:
                s = ctx.snapshot()
                self.snap = s
                self.services.tick(s)
                self.observe(s)
                self.brain.maybe_unload(force=bool(self.snap and self.snap.pro_pids) and not self.edit_busy)
            except Exception as e:
                log.exception("İzleme hatası: %s", e)
            # Uyarlanır hız: başınızdayken 10 sn, uzaktayken ve iş yokken 45 sn
            idle_mode = self.snap and self.snap.idle > 600 and not self.snap.pro_pids
            time.sleep(45 if idle_mode else 10)

    def mic_watch_loop(self):
        """Başka uygulama mikrofonu açınca (kayıt, görüşme) Mek dinlemeyi bırakır."""
        prev = []
        while self.running:
            try:
                self.mic_others = ctx.other_mic_users()
            except Exception:
                self.mic_others = []
            if self.mic_others != prev:
                log.info("Mikrofonu kullananlar: %s", self.mic_others or "yok")
                ui.set("busy_mic" if self.mic_others else "listening",
                       "" if not self.mic_others else ", ".join(self.mic_others))
                prev = self.mic_others
            time.sleep(2)

    def socket_loop(self):
        """`mek "metin"` komutu ve kısayollar için yerel soket."""
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
                conn.settimeout(5)
                self._serve(conn)
            except Exception as e:
                log.warning("Soket hatası: %s", e)

    def _serve(self, conn):
        if True:
            with conn:
                data = conn.recv(4096).decode("utf-8", "ignore").strip()
                if data == "__status__":
                    conn.sendall(self.context_line().encode())
                elif data == "__toggle_listen__":
                    self.cfg["listen"] = not self.cfg.get("listen", True)
                    self.say("Dinliyorum." if self.cfg["listen"] else "Mikrofonu kapattım.")
                elif data == "__toggle_rec__":
                    self.recording_mode = not self.recording_mode
                    self.say("Kayıt modu açık." if self.recording_mode else "Kayıt modu kapandı.")
                elif data.startswith("__say__ "):
                    self.say(data[8:])
                    conn.sendall(b"ok")
                elif data:
                    self.inbox.put(("text", data))
                    conn.sendall(b"ok")

    def run(self):
        threading.Thread(target=self.speaker.warmup, daemon=True).start()
        threading.Thread(target=self.monitor_loop, daemon=True, name="monitor").start()
        threading.Thread(target=self.socket_loop, daemon=True, name="socket").start()
        threading.Thread(target=self.mic_watch_loop, daemon=True, name="micwatch").start()
        self.listener = Listener(self.cfg, self.speaker, self.inbox, self.mic_paused,
                                 has_pending=lambda: self.pending is not None)
        self.listener.start()
        self.conversing = False

        def after_speech():
            # Sohbetin içindeysek 8 sn boyunca "Mek" demeden cevap verilebilir
            if self.conversing:
                self.listener.awaiting_followup_until = time.time() + 8
                ui.set("awake", "Sizi dinliyorum…")
        self.speaker.on_done = after_speech

        def local_ok():
            import psutil
            free_gb = psutil.virtual_memory().available / 1024 ** 3
            return free_gb >= self.cfg["llm_min_free_gb"] and not self.busy()
        self.brain.can_load_local = local_ok
        ui.set("listening", "")
        log.info("Mek hazır")
        while self.running:
            try:
                src, text = self.inbox.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.handle(src, text)
            except Exception as e:
                log.exception("Komut hatası: %s", e)

    def stop(self, *_):
        self.running = False
        self.services.shutdown()
        sys.exit(0)


def act_legacy(fn):
    import jarvis
    return getattr(jarvis, fn)()


def legacy_briefing():
    import jarvis
    return jarvis.build_startup_message()


def main():
    handler = logging.handlers.RotatingFileHandler(config.LOG_FILE, maxBytes=2_000_000, backupCount=3)
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    m = Mek()
    signal.signal(signal.SIGTERM, m.stop)
    m.run()


if __name__ == "__main__":
    main()
