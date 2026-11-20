#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mek — sizi dinlemeyen, gün içinde duruma göre kendisi konuşan ve arka planda sistemi
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
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apple
import activity_monitor
import adblock
import config
import context as ctx
import disk_health
import foundation_llm
import updater
import power_guard as power
from events import EventBus
from network_optimizer import NetworkOptimizer
from agent import Agent
from security import Security
import services
from services import ServiceManager
from memory_budget import MemoryBudget
from questions import QuestionBox, Question, Option, ai_classifier
from llm_ram import LLMRamCoordinator, is_llm_process
import proc as proc_mod


log = logging.getLogger("mek")
PY = os.path.join(config.MEK_HOME, "venv/bin/python")
SPEAK = os.path.join(config.MEK_HOME, "jarvis.py")

# jarvis.py (ses/hava durumu yardımcıları) jarvisd paketinin kardeşidir.
# Eskiden ~/.jarvis doğrudan sys.path'e ekleniyordu; bu, depodan çalışan
# testlerin ve araçların kurulu (eski) kopyayı yüklemesine yol açıyordu.
# Artık modül yalnızca ihtiyaç anında, açık dosya yolundan yüklenir.
_LEGACY_PATHS = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jarvis.py"),
    os.path.join(config.MEK_HOME, "jarvis.py"),
)
_legacy_cache = []


def legacy_module():
    """jarvis.py'yi sys.path'i kirletmeden yükler; bulunamazsa None döner."""
    if _legacy_cache:
        return _legacy_cache[0]
    import importlib.util
    for path in _LEGACY_PATHS:
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("mek_legacy", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            log.warning("Eski jarvis.py yüklenemedi (%s): %s", path, e)
            continue
        _legacy_cache.append(mod)
        return mod
    _legacy_cache.append(None)
    return None
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")


def ai_phrase(facts, style="kısa ve doğal", timeout=20):
    """Cihaz üzerindeki Apple Foundation Models ile bilgileri Mek üslubunda cümleye döker."""
    instr = ("Sen Mek'sin: Alptuğ Bey'in Mac'inde yaşayan, zeki, kibar, hafif esprili bir asistan. "
             f"Verilen bilgileri sesli okunmak üzere {style} Türkçeyle aktar. Bilgileri değiştirme, "
             "sayıları aynen koru, yeni bilgi uydurma. Emoji, madde işareti, başlık kullanma.")
    return foundation_llm.generate_text(facts, instructions=instr, timeout=timeout)


class Mek:
    def __init__(self):
        self.cfg = config.load()
        self.T = self.cfg["user_title"]
        self.events = EventBus()
        self.agent = Agent(self)
        self.events.subscribe("app.crashed", self._on_crash_event)
        self.events.subscribe("network.state_changed", self._on_network_state_event)
        self.events.subscribe("security.alert", self._on_security_event)
        self.events.subscribe("performance.profile_changed", self._on_profile_event)
        self.events.subscribe("activity.snapshot", self._on_activity_event)
        self.events.subscribe("system.snapshot", self._on_system_snapshot)
        self.events.subscribe("power.state_changed", self._on_power_state_event)
        self.events.subscribe("power.ups_critical", self._on_power_critical_event)
        self.network_state = "unknown"
        self.last_activity_snapshot = None
        self.services = ServiceManager(self.cfg, events=self.events)
        self.security = Security(self.notify, events=self.events)
        self.llm_ram = LLMRamCoordinator()
        self._last_llm_gb = 0.0
        self.memory_budget = MemoryBudget(self.cfg)
        self.questions = QuestionBox(
            notify=self.notify,
            speak=self.speak,
            classify=ai_classifier(config.AI_BIN, subprocess.run),
            state_path=os.path.join(config.STATE_DIR, "pending_question.json")
        )
        self.crash_monitor = activity_monitor.CrashMonitor(
            state_path=os.path.join(config.STATE_DIR, "crash_reports.json"), events=self.events)
        self.power_guard = power.PowerGuard(
            events=self.events,
            critical_percent=min(15, int(self.cfg["ups_shutdown_percent"])),
            critical_minutes=min(5, int(self.cfg["ups_shutdown_minutes"])))
        self.network = NetworkOptimizer(events=self.events)
        self.next_network_check = 0.0
        self.updating = False
        self.last_update_summary = "Henüz otomatik güncelleme kontrolü yapılmadı."
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
        self.speech_queue = queue.Queue()
        self.speech_worker = None
        self.speech_worker_lock = threading.Lock()
        self.running = True

    @staticmethod
    def _parse_crash_app_name(filename):
        """macOS .ips/.crash/.hang dosya adından gerçek uygulama adını ayıklar."""
        stem = re.sub(r"\.(ips|crash|hang)$", "", filename or "", flags=re.I)
        stem = re.sub(r"-\d{4}-\d{2}-\d{2}-\d{6}(?:\.\d+)?$", "", stem)
        if stem.startswith("ExcUserFault_"):
            return stem.split("_", 1)[1] or stem, True
        return stem, False

    def _on_crash_event(self, event, payload):
        report = payload["report"]
        name, is_user_fault = self._parse_crash_app_name(report["name"])
        if is_user_fault:
            log.debug("Sistem ExcUserFault raporu yoksayıldı: %s (%s)", name, report["name"])
            return
        log.warning("Uygulama çökme raporu: %s (%s)", name, report["name"])
        # Çöken raporları sesli okumak yerine arka planda sessizce gerekeni yap:
        self._handle_crash_silently(name, report)

    def _handle_crash_silently(self, name, report):
        import glob
        crashes = self.__dict__.setdefault("_crash_counts", {})
        now = time.time()
        counts = [t for t in crashes.get(name, []) if now - t < 86400] + [now]
        crashes[name] = counts
        # Çökme sonrası asılı kalabilecek Adobe/kurgu kilit ve yetim süreçlerini sessizce temizle
        if hasattr(self, "services") and self.services:
            self.services.cleanup_orphans(getattr(self, "snap", None))
        for pat in [f"/tmp/*{name}*.lock", f"/private/tmp/*{name}*.lock"]:
            for f in glob.glob(pat):
                try:
                    if os.path.isfile(f):
                        os.unlink(f)
                except OSError:
                    pass
        if len(counts) >= 3:
            log.warning("Süreç %s son 24 saatte %d kez çöktü; geçici önbellek ve kilit dosyaları temizleniyor.",
                        name, len(counts))
            for pat in [f"/tmp/*{name}*", f"/private/tmp/*{name}*"]:
                for f in glob.glob(pat):
                    try:
                        if os.path.isfile(f):
                            os.unlink(f)
                    except OSError:
                        pass

    def _on_power_state_event(self, event, payload):
        state = payload["state"]
        previous = payload.get("previous")
        if not state.get("available"):
            return
        if previous is None:
            return
        was_on_battery = bool(previous and previous[1])
        if state["on_battery"] and not was_on_battery:
            if state["is_ups"]:
                percent = f" UPS pili yüzde {state['percent']}." if state["percent"] is not None else ""
                time_left = (f" Tahmini {state['remaining_minutes']} dakika kaldı."
                             if state["remaining_minutes"] is not None else "")
                self.notify("Şebeke elektriği kesildi; UPS devrede." + percent + time_left,
                            title="Güç kesintisi", urgent=True)
            else:
                self.notify("AC güç kesildi; cihaz pil gücünde çalışıyor.",
                            title="Güç kesintisi", urgent=True)
        elif was_on_battery and not state["on_battery"]:
            if self.cfg.get("ups_auto_shutdown_enabled", False):
                subprocess.run(["sudo", "-n", config.PRIV_HELPER, "ups-cancel"],
                               capture_output=True, timeout=15)
            self.notify("AC güç geri geldi.", title="Güç durumu")

    def _on_power_critical_event(self, event, payload):
        state = payload["state"]
        percent = state.get("percent")
        text = (f"UPS pili kritik seviyede, yüzde {percent}." if percent is not None else
                "UPS tahmini çalışma süresi kritik seviyede.")
        if not self.cfg.get("ups_auto_shutdown_enabled", False):
            self.notify(text + " Otomatik kapatma kapalı; verilerinizi kaydedin.",
                        title="UPS kritik", urgent=True)
            return
        result = subprocess.run(["sudo", "-n", config.PRIV_HELPER, "ups-shutdown"],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            self.notify(text + " Verileri kaydetmeniz için bir dakika sonra güvenli kapatma planlandı.",
                        title="UPS kritik", urgent=True)
        else:
            log.error("UPS güvenli kapatması başlatılamadı: %s", result.stderr.strip())
            self.notify(text + " Güvenli kapatma başlatılamadı; hemen dosyalarınızı kaydedin.",
                        title="UPS kritik", urgent=True)

    def _on_network_state_event(self, event, payload):
        self.network_state = payload["state"]

    def _on_security_event(self, event, payload):
        log.warning("Güvenlik olayı: %s", payload["text"])

    def _on_profile_event(self, event, payload):
        log.info("Performans profili olayı: %s", payload["profile"])

    def _on_activity_event(self, event, payload):
        self.last_activity_snapshot = payload["snapshot"]

    def _on_system_snapshot(self, event, payload):
        self.snap = payload["snapshot"]

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
        cfg = getattr(self, "cfg", None) or {}
        q_start = cfg.get("quiet_start_hour", 0)
        q_end = cfg.get("quiet_end_hour", 11)
        h = dt.datetime.now().hour
        return q_start <= h < q_end

    def speak(self, text, force=False):
        """Speak requests in FIFO order so startup greetings cannot be overtaken."""
        if not text or not text.strip():
            return
        if not force and self._quiet_hours():
            log.info("Sessiz saatte konuşma engellendi (saat %02d): %s", dt.datetime.now().hour, text[:60])
            return
        text = re.sub(r"\bApple\b", "Epıl", text)
        text = re.sub(r"\bapple\b", "epıl", text)
        text = re.sub(r"\bAPPLE\b", "EPIL", text)
        with self.speech_worker_lock:
            if not self.speech_worker or not self.speech_worker.is_alive():
                self.speech_worker = threading.Thread(target=self._speech_loop, daemon=True)
                self.speech_worker.start()
        self.speech_queue.put((text, True) if force else text)

    def _speech_loop(self):
        while self.running or not self.speech_queue.empty():
            try:
                item = self.speech_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if isinstance(item, tuple):
                text, force = item[0], bool(item[1])
            else:
                text, force = item, False
            try:
                self.events.publish("speech.started", text=text)
                log.info("SAY%s: %s", " (force)" if force else "", text)
                cmd = [PY, SPEAK] + (["--force"] if force else []) + [text]
                result = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, text=True,
                                        timeout=90,
                                        preexec_fn=lambda: os.nice(5))
                if result.returncode:
                    log.error("TTS başarısız (kod %d): %s", result.returncode, result.stderr.strip())
            except subprocess.TimeoutExpired:
                log.error("TTS zaman aşımına uğradı (90 sn)")
            except Exception:
                log.exception("TTS çalıştırılamadı")
            finally:
                self.events.publish("speech.finished", text=text)
                self.speech_queue.task_done()

    def notify(self, text, urgent=False, title="Mek", spoken_text=None):
        """Bildirim her zaman; sesli söyleme yalnızca uygunsa (sessiz saat, görüşme, aralık)."""
        self.events.publish("notification.created", text=text, urgent=urgent, title=title)
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(text)} with title {json.dumps(title)}'],
                       capture_output=True)
        to_say = spoken_text or text
        # Sessiz saatlerde (00:00 - 11:00) sesli konuşma yapılmaz
        if self._quiet_hours():
            if self.snap and self.snap.idle > self.cfg["away_threshold_s"]:
                self.away_events.append(to_say)
            return
        if self._speech_blocked() and not urgent:
            if self.snap and self.snap.idle > self.cfg["away_threshold_s"]:
                self.away_events.append(to_say)
            return
        if not urgent and time.time() - self.last_spoke < self.cfg["proactive_min_gap_s"]:
            return
        self.last_spoke = time.time()
        self.speak(to_say)

    def _once(self, key, gap):
        now = time.time()
        if now - self.warned.get(key, 0) > gap:
            self.warned[key] = now
            return True
        return False

    # ------------------------------------------------------------------ brifing
    def briefing(self):
        now = dt.datetime.now()
        facts = [f"Saat {now:%H:%M}, {now.day} {apple.MONTHS[now.month - 1]} "
                 f"{apple.DAYS[now.weekday()]}."]
        try:
            legacy = legacy_module()
            w = legacy.get_weather(timeout=4) if legacy else None
            if w:
                facts.append(w)
        except Exception:
            log.debug("Hava durumu alınamadı", exc_info=True)
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
        issues = self.health_summary()
        facts.append(issues or "Sistem sağlıklı, güvenlik denetimleri temiz.")
        text = " ".join(facts)
        return ai_phrase(text, "kısa ve doğal bir sistem, hava ve takvim özeti") or text

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

    def check_disk_smart(self):
        result = disk_health.inspect_disk()
        self.events.publish("disk.smart.checked", result=result)
        if result["warning"]:
            log.warning("Disk sağlığı: %s", result["warning"])
            self.notify(result["warning"], urgent=True, title="Disk sağlığı")
        elif result["smart"] == "Unavailable":
            log.warning("Disk SMART denetimi yapılamadı: %s", result["warning"])

    def verify_disk_volume(self):
        result = disk_health.verify_volume()
        self.events.publish("disk.volume.verified", result=result)
        if result["ok"]:
            log.info("Başlangıç birimi doğrulandı: %s", result["message"])
        else:
            log.warning("Dosya sistemi denetimi: %s", result["message"])
            self.notify("Disk dosya sistemi doğrulaması hata bildirdi. Mek onarım yapmadı; Disk İzlencesi'nde "
                        "Yardımcı İlk Yardım'ı çalıştırmanız önerilir.", urgent=True, title="Disk sağlığı")

    def sample_network(self, announce=True):
        result = self.network.sample()
        self.network_state = result["state"]
        self.next_network_check = time.monotonic() + self.network.recommended_interval(
            result["state"], performance=bool(self.snap and self.snap.pro_pids))
        if announce and result["state"] in ("offline", "degraded", "busy"):
            if self._once("network:" + result["state"], 1800):
                if result["state"] == "degraded":
                    text = "Ağda gecikme veya paket kaybı ölçtüm. Mek DNS ya da yönlendirmeyi değiştirmedi; Wi-Fi sinyalini, modemi ve ağ yükünü kontrol edin."
                elif result["state"] == "busy":
                    text = (f"Ağ bağlantısının yaklaşık yüzde {result['utilization_pct']:.0f}'ı kullanılıyor. "
                            "Bulut eşitleme veya indirmeler etkiliyor olabilir; hiçbir süreci otomatik durdurmadım.")
                else:
                    text = "Varsayılan ağ arayüzü çevrimdışı görünüyor. Wi-Fi veya Ethernet bağlantısını kontrol edin."
                self.notify(text, title="Ağ durumu", urgent=(result["state"] == "offline"))
        return result

    def start_update_cycle(self):
        if self.updating:
            return False
        self.updating = True
        open(os.path.join(config.STATE_DIR, "clean_automatic_updates"), "w").write(str(time.time()))

        def run():
            installed, failures, restart_required, deferred = [], [], [], []
            try:
                apple = updater.check_updates()
                if not apple["ok"]:
                    failures.append("Apple güncelleme kontrolü: " + apple["message"])
                else:
                    for item in apple["updates"]:
                        if not self.update_install_allowed():
                            deferred.append(item["title"])
                            continue
                        result = updater.install_update(item["label"])
                        if result["ok"]:
                            installed.append(item["title"])
                            if item["restart_required"]:
                                restart_required.append(item["title"])
                        else:
                            failures.append(item["title"] + ": " + result["message"])

                third_party = updater.update_third_party(
                    running_apps=ctx.running_app_names(), install=True, refresh=True,
                    allow_install=self.update_install_allowed)
                installed.extend(third_party["installed"])
                failures.extend(third_party["errors"])
                devices = updater.attached_devices()
                self.events.publish("hardware.inventory", devices=devices)
                device_summary = ", ".join(
                    f"{item['vendor']} {item['name']} ({item['transport']}, firmware: {item['firmware']})"
                    for item in devices[:12])
                log.info("Güncelleme kontrolü: apple=%d third_party=%d devices=%s deferred=%s",
                         len(apple.get("updates", [])), len(third_party["checked"]), device_summary,
                         third_party["deferred"])

                if installed:
                    self.last_update_summary = "Kuruldu: " + ", ".join(installed[:8])
                    self.notify("Boşta bakım sırasında güncellemeler kuruldu: " + ", ".join(installed[:5]) + ".",
                                title="Güncellemeler")
                elif apple["ok"] and not third_party["checked"]:
                    self.last_update_summary = "Desteklenen kanallarda güncelleme bulunamadı."
                else:
                    self.last_update_summary = f"Kontrol tamamlandı; {len(third_party['checked']) + len(apple.get('updates', []))} güncelleme bulundu."
                if restart_required:
                    self.last_update_summary += " Yeniden başlatma gerekebilir; otomatik yeniden başlatma yapılmadı."
                if deferred:
                    self.last_update_summary += " Güvenli pencere nedeniyle ertelendi: " + ", ".join(deferred[:5]) + "."
                if third_party["deferred"]:
                    self.last_update_summary += " Uygulama açık veya güvenli pencere kapalı olduğu için ertelendi: " + ", ".join(third_party["deferred"][:5]) + "."
                if failures:
                    self.last_update_summary += " Hata: " + "; ".join(failures[:3])
                    log.warning("Güncelleme sorunları: %s", failures)
                    self.notify("Bazı güncellemeler tamamlanamadı. Ayrıntılar mek log çıktısında.",
                                title="Güncellemeler")
                self.last_update_summary += (f" Takılı USB/Thunderbolt aygıtı: {len(devices)}. "
                                             "Üçüncü taraf firmware için üretici aracı gerekebilir.")
            except Exception as exc:
                log.exception("Otomatik güncelleme başarısız")
                self.last_update_summary = f"Güncelleme kontrolü başarısız: {exc}"
            finally:
                self.events.publish("updates.finished", summary=self.last_update_summary,
                                    installed=installed, failures=failures, deferred=deferred)
                self.updating = False

        threading.Thread(target=run, daemon=True).start()
        return True

    def hardware_report(self):
        devices = updater.attached_devices()
        self.events.publish("hardware.inventory", devices=devices)
        if not devices:
            return "USB veya Thunderbolt aygıtı bulunamadı ya da donanım envanteri okunamadı."
        listed = "; ".join(
            f"{item['vendor']} {item['name']} ({item['transport']}, firmware: {item['firmware']})"
            for item in devices[:20])
        return (f"Takılı aygıtlar: {listed}. Apple firmware güncellemeleri macOS güncellemeleriyle kontrol edilir; "
                "üçüncü taraf firmware için üretici güncelleme aracı gerekir.")

    def update_install_allowed(self):
        s = self.snap
        if not s:
            return False
        busy = bool(s.pro_pids) or bool(self.renders.active())
        idle = s.idle > 900
        return updater.safe_install_window(idle, busy, updater.is_on_ac_power(), s.disk_free_gb,
                                           s.thermal, self.network_state)

    def check_for_updates(self):
        if self.updating:
            return "Bir güncelleme işlemi zaten çalışıyor."
        self.updating = True
        try:
            apple = updater.check_updates()
            third_party = updater.update_third_party(running_apps=ctx.running_app_names(),
                                                     install=False, refresh=True)
            if not apple["ok"]:
                self.last_update_summary = "Apple güncelleme kontrolü başarısız: " + apple["message"]
            else:
                labels = [item["title"] for item in apple["updates"]] + third_party["checked"]
                self.last_update_summary = ("Güncelleme yok." if not labels else
                                            "Bulunanlar: " + ", ".join(labels[:12]))
                if third_party["errors"]:
                    self.last_update_summary += " Kontrol hatası: " + "; ".join(third_party["errors"][:3])
            self.events.publish("updates.checked", summary=self.last_update_summary,
                                apple=apple.get("updates", []), third_party=third_party["checked"])
            return self.last_update_summary
        finally:
            self.updating = False

    def network_report(self):
        result = self.sample_network(announce=False)
        interface = result["interface"] or "yok"
        if result["state"] == "offline":
            return f"Ağ çevrimdışı; varsayılan arayüz yok. {result['reason']}"
        if result["state"] == "unknown":
            return f"Arayüz {interface} bağlı; ping ölçümü belirsiz. Bu, ICMP engelinden kaynaklanabilir."
        rx = f"{result['rx_mbps']:.1f}" if result["rx_mbps"] is not None else "ölçülüyor"
        tx = f"{result['tx_mbps']:.1f}" if result["tx_mbps"] is not None else "ölçülüyor"
        latency = result["internet"].get("latency_ms") if result["internet"] else None
        loss = result["internet"].get("loss_pct") if result["internet"] else None
        quality = (f"gecikme {latency:.0f} ms, kayıp yüzde {loss:.0f}" if latency is not None and loss is not None
                   else "internet ping ölçümü yok")
        usage = f", link kullanımı yüzde {result['utilization_pct']:.0f}" if result["utilization_pct"] is not None else ""
        return (f"{interface}: {result['state']}; indirme {rx} Mbps, yükleme {tx} Mbps{usage}; "
                f"{quality}. {result['reason']}")

    def adblock_command(self, command):
        parts = command.lower().split()
        action = parts[1] if len(parts) > 1 else "durum"
        if action in ("durum", "status"):
            result = adblock.status()
            if result["error"]:
                return f"Hosts dosyası okunamadı veya Mek işaretleri bozuk: {result['error']}"
            return (f"Reklam engelleme etkin; {result['count']} alan adı hosts dosyasında engelli."
                    if result["enabled"] else "Sistem genelinde hosts reklam engelleme kapalı.")
        if action in ("aç", "ac", "etkinleştir", "etkinlestir", "güncelle", "guncelle"):
            change = adblock.change("enable", config.PRIV_HELPER)
        elif action in ("kapat", "disable"):
            change = adblock.change("disable", config.PRIV_HELPER)
        else:
            return "Kullanım: mek reklam [durum|aç|güncelle|kapat]"
        if not change["ok"]:
            return (f"Reklam engelleme işlemi başarısız: {change['message']} "
                    "Yetkili yardımcı kurulu değilse sudo bash ~/.jarvis/jarvisd/install_privileged.sh çalıştırın.")
        return change["message"]

    # ------------------------------------------------------------------ olaylar
    def observe(self, s):
        if isinstance(s.t, dt.datetime):
            now = s.t
            now_s = s.t.timestamp()
        elif isinstance(s.t, (int, float)):
            now_s = float(s.t)
            now = dt.datetime.fromtimestamp(now_s)
        else:
            now = dt.datetime.now()
            now_s = now.timestamp()
        today = dt.date.today()
        away = s.idle > self.cfg["away_threshold_s"]

        # Günün ilk selamı: Alptuğ Bey masaya oturduğunda (away değilken)
        # yalnızca sessiz saatler bittikten (11:00) sonra söylenir
        greeted = open(self.greet_file).read().strip() if os.path.exists(self.greet_file) else ""
        min_hour = max(self.cfg.get("morning_greeting_from_hour", 11), self.cfg.get("quiet_end_hour", 11))
        is_morning = now.hour >= min_hour
        can_greet = not away and not self._quiet_hours() and is_morning
        if can_greet and greeted != today.isoformat():
            with open(self.greet_file, "w") as fh:
                fh.write(today.isoformat())
            greet = "Günaydın" if now.hour < 12 else "Tünaydın" if now.hour < 18 else "İyi akşamlar"
            self.speak(f"{greet} {self.T}. Mek hazır.")

            def send_briefing():
                try:
                    self.notify(self.briefing(), urgent=True)
                except Exception:
                    log.exception("İlk brifing hazırlanamadı")

            threading.Thread(target=send_briefing, daemon=True).start()
        elif self.was_away and not away and self.away_events:
            summary = "; ".join(self.away_events[-5:])
            self.away_events = []
            self.notify(ai_phrase(f"Kullanıcı masaya döndü. Yokken olanlar: {summary}", "2-3 cümleyle, "
                                  "'tekrar hoş geldiniz' diyerek") or f"Tekrar hoş geldiniz {self.T}. {summary}.",
                        urgent=True)
        self.was_away = away

        for name, dur in self.renders.update(s.render_cpu, now_s):
            self.notify(f"{self.T}, {name} işi bitti, yaklaşık {int(dur // 60)} dakika sürdü.", urgent=True)

        if s.pro_pids and not away:
            self.pro_session_start = self.pro_session_start or now_s
            mins = (now_s - self.pro_session_start) / 60
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
        lineage = services._lineage_pids(getattr(s, "pro_pids", ()), getattr(s, "ppids", None))
        for pid, name, cpu, cmd in s.hot:
            target = cmd or name or ""
            if (pid == os.getpid()
                    or pid in lineage
                    or re.search(r"kernel_task|WindowServer|coreaudiod|launchd|siri|campo|assistantd", name, re.I)
                    or is_llm_process(target)
                    or services.is_creative_or_plugin(target)):
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
                self.events.publish("calendar.reminder", title=e["title"], minutes=mins,
                                    location=e["location"], start=e["start"])
                self.notify(f"{self.T}, {mins} dakika sonra {e['title']}{loc}.", urgent=True, title="Takvim")
        except Exception as e:
            log.warning("Takvim kontrolü: %s", e)

    def market_tick(self):
        """Only report threshold-crossing changes for explicitly watched symbols."""
        now = dt.datetime.now()
        watchlist = self.cfg.get("market_watchlist", [])
        if now.weekday() >= 5 or not watchlist:
            return
        th = self.cfg["market_alert_pct"]
        syms = list(dict.fromkeys(watchlist))
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
                self.events.publish("market.alert", reports=moves)
                self.notify(ai_phrase(facts, "1-2 kısa cümleyle, yatırım tavsiyesi vermeden") or facts,
                            title="Piyasa")

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
                        report = f"Saat {h:02d} civarında yağmur bekleniyor, yüzde {slot['chanceofrain']} ihtimalle. Çıkacaksanız şemsiye alın."
                        self.events.publish("weather.alert", hour=h, chance=int(slot["chanceofrain"]))
                        self.notify(report, title="Hava")
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
                out, timed_out = proc_mod.capture_background(
                    [PY, os.path.join(os.path.dirname(__file__), "cleaner.py"), "--tier", tier],
                    timeout=3600)
                if timed_out or not out.strip():
                    log.warning("Temizlik tamamlanamadı (tier=%s)", tier)
                    return
                r = json.loads(out.strip().splitlines()[-1])
                self.events.publish("maintenance.cleaner.completed", tier=tier, result=r)
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

    def check_memory_relief(self, s):
        if getattr(self, "llm_ram", None):
            rel = self.llm_ram.maybe_relieve(s, self.cfg, pro_active=bool(getattr(s, "pro_pids", ())))
            if rel.get("unloaded") and getattr(s, "pressure", 1) < 4:
                return
        if getattr(s, "pressure", 1) >= 4:
            services.priv("purge")
        if not self.cfg.get("memory_relief", True) or s.idle > self.cfg["away_threshold_s"]:
            return
        working_set = set(self.services.working_pids(s))
        heavy_bg = []
        for pid, exe, cpu, rss in getattr(s, "pro_procs", ()):
            name = services.app_of(exe)
            if pid not in working_set and rss > 4 * 1024 ** 3:
                heavy_bg.append((name, pid, rss / 1024 ** 3))
        if heavy_bg:
            app_name, pid, gb = heavy_bg[0]
            qid = f"relief_{app_name.lower()}"

            def do_quit():
                proc_mod.run(["osascript", "-e", f'tell application "{app_name}" to quit'],
                         capture_output=True, timeout=20)
                return f"{app_name} uygulamasına kapanma isteği iletildi; bellek boşalacak."

            q = Question(
                qid=qid,
                text=f"Bellek baskısı yükseldi. Arka planda boşta duran {app_name} {gb:.1f} GB RAM tutuyor. Kapatmamı ister misiniz?",
                options=[
                    Option("kapat", f"{app_name}'ı kapat", run=do_quit, words=["kapat", "sonlandır", "evet", "onay"], polarity="yes"),
                    Option("dokunma", "Açık kalsın", run=lambda: "Dokunulmadı, açık tutuluyor.", words=["dokunma", "kalsın", "hayır", "vazgeç"], polarity="no")
                ],
                default="dokunma",
                ttl=600,
                speak_text=f"Bellek baskısı yükseldi {self.T}. Arka planda boşta duran {app_name} {gb:.0f} gigabayt bellek tutuyor. Kapatmamı ister misiniz? Kapat ya da Dokunma diyebilirsiniz."
            )
            self.questions.ask(q, repeat_after=3600)

    def _safe_step(self, label, fn):
        try:
            return fn()
        except Exception as e:
            log.exception("Döngü adımı (%s) hatası: %s", label, e)
            return None

    # ------------------------------------------------------------------ döngüler
    def loop(self):
        while self.running:
            t0 = time.time()
            try:
                s = ctx.snapshot(self.events)
                self.snap = s
            except Exception as e:
                log.exception("Anlık sistem durumu (snapshot) okunamadı: %s", e)
                time.sleep(5)
                continue

            def _refresh_llm():
                if not getattr(self, "llm_ram", None):
                    return 0.0
                check_api = self.security._due("llm_ram", 20)
                if check_api or self.llm_ram.last_summary is None:
                    info = self.llm_ram.inspect_snapshot(s, check_ollama=check_api)
                else:
                    info = self.llm_ram.inspect_snapshot(s, check_ollama=False)
                self._last_llm_gb = float(info.get("total_gb") or 0.0)
                return self._last_llm_gb

            s.llm_gb = self._safe_step("llm_ram", _refresh_llm) or 0.0
            self._safe_step("services", lambda: self.services.tick(s, rendering=bool(self.renders.active())))
            self._safe_step("miners", lambda: self.security.check_miners(s.hot))
            self._safe_step("stealers", lambda: self.security.check_stealers(getattr(s, "procs", ())))
            self._safe_step("tame_runaways", lambda: self.tame_runaways(s))
            self._safe_step("observe", lambda: self.observe(s))
            self._safe_step("questions", self.questions.expire)

            if getattr(s, "exes", None) and self.security._due("memory_budget", 30):
                def _budget_tick():
                    w_pids = set(self.services.working_pids(s))
                    active_exes = [exe for pid, exe, _, _ in getattr(s, "pro_procs", ()) if pid in w_pids]
                    us = getattr(self.services, "ultra_scratch", None)
                    scratch_gb = (us.used_bytes() / (1024 ** 3)) if us else 0.0
                    extra = round(float(self._last_llm_gb or 0.0) + scratch_gb, 2)
                    self.memory_budget.tick(s.exes, active_exes=active_exes, extra_reserve_gb=extra)
                self._safe_step("memory_budget", _budget_tick)

            if (getattr(s, "pressure", 1) >= 2 or getattr(s, "mem_pct", 0.0) >= 75.0) and self.security._due("llm_relief", 60):
                self._safe_step("llm_relief", lambda: self.llm_ram.maybe_relieve(s, self.cfg, pro_active=bool(s.pro_pids)))

            if getattr(s, "pressure", 1) >= 2 and self.security._due("memory_relief", 120):
                self._safe_step("memory_relief", lambda: self.check_memory_relief(s))

            # Cihaz içi Apple Foundation Models otonom stres danışmanı:
            # Normal çalışmada hiç uyanmaz (0 CPU/RAM); yalnızca gerçek baskı/ısı stresinde
            # en fazla 30 dakikada bir arka planda değerlendirme yapar.
            tf_tel = getattr(getattr(self.services, "thermal_fan", None), "last_telemetry", None) or {}
            ssd_hot = tf_tel.get("ssd_c") is not None and float(tf_tel["ssd_c"]) >= 58.0
            stressed = (
                getattr(s, "pressure", 1) >= 2
                or float(getattr(s, "swap_gb", 0.0) or 0.0) >= 4.0
                or int(getattr(s, "thermal", 0) or 0) >= 2
                or ssd_hot
            )
            if stressed and self.security._due("fm_advisor", 1800):
                threading.Thread(target=self._run_autonomous_fm_advisor, daemon=True).start()

            busy = bool(s.pro_pids) or bool(self.renders.active())
            idle = s.idle > 900
            if time.monotonic() >= self.next_network_check:
                self._safe_step("network", self.sample_network)
            sec = self.security
            if sec._due("power_state", 30):
                self._safe_step("power", self.power_guard.sample)
            if sec._due("cal", 60):
                self._safe_step("calendar", self.calendar_tick)
            if sec._due("pool", 120 if not busy else 300):
                self._safe_step("pool", sec.check_pool_connections)
            if sec._due("market", 1800):
                threading.Thread(target=self.market_tick, daemon=True).start()
            if sec._due("weather", 3600):
                threading.Thread(target=self.weather_tick, daemon=True).start()
            if not busy and sec._due("persist", 900):
                self._safe_step("persist", sec.audit_persistence)
            if not busy and sec._due("download_gate", 60):
                threading.Thread(target=sec.scan_recent_downloads, daemon=True).start()
            if sec._due("crash_reports", 60):
                self._safe_step("crash_reports", self.crash_monitor.scan)
            if not busy and sec._due("posture", 6 * 3600):
                def _posture_check():
                    issues = sec.posture()
                    if issues and self._once("posture", 24 * 3600):
                        self.notify("Güvenlik ayarlarında dikkat: " + ", ".join(issues) +
                                    ". Sistem Ayarları'ndan açmanızı öneririm.", title="Güvenlik")
                self._safe_step("posture", _posture_check)
            if idle and not busy:
                if sec._due("update_window", 300):
                    def _update_check():
                        safe = updater.safe_install_window(
                            idle, busy, updater.is_on_ac_power(), s.disk_free_gb, s.thermal, self.network_state)
                        if safe and self._since("automatic_updates") >= 24 * 3600 and \
                                sec._due("automatic_updates", 24 * 3600):
                            self.start_update_cycle()
                    self._safe_step("updater", _update_check)
                if sec._due("disk_smart", 6 * 3600):
                    self._safe_step("disk_smart", self.check_disk_smart)
                if sec._due("disk_verify", 7 * 86400):
                    threading.Thread(target=self.verify_disk_volume, daemon=True).start()
            # Ağır işler yalnızca siz yokken ve kurgu/render yokken
            # Disk kritik: kurgu yoksa hemen (siz başındayken bile, en düşük öncelikte)
            if s.disk_free_gb < 60 and not busy and self._since("acil") > 2 * 3600:
                self._safe_step("clean_acil", lambda: self.start_clean("acil"))
            if idle and not busy:
                if self._since("haftalik") > 7 * 86400:
                    self._safe_step("clean_haftalik", lambda: self.start_clean("haftalik"))
                elif self._since("gunluk") > 20 * 3600 and self._since("haftalik") > 20 * 3600:
                    self._safe_step("clean_gunluk", lambda: self.start_clean("gunluk"))
                if sec._due("freshclam", 24 * 3600):
                    threading.Thread(target=sec.update_av, daemon=True).start()
                if (getattr(s, "pressure", 1) == 1 and getattr(s, "mem_pct", 0.0) < 70.0
                        and float(getattr(s, "llm_gb", 0.0) or 0.0) < 4.0 and sec._due("av", 3 * 3600)):
                    threading.Thread(target=sec.av_scan, daemon=True).start()
            # Uyarlanır hız: kurgu sırasında 15 sn (yalnızca hafif işler), başınızdayken 10, uzakta 45
            s = self.snap
            gap = 45 if (s and s.idle > 600 and not s.pro_pids) else 15 if (s and s.pro_pids) else 10
            time.sleep(max(1, gap - (time.time() - t0)))

    def socket_loop(self):
        """Terminal, menü çubuğu ve Siri (App Intents) komutlarını işler."""
        try:
            os.unlink(config.SOCKET_PATH)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(config.SOCKET_PATH)
            os.chmod(config.SOCKET_PATH, 0o600)
            srv.listen(16)
            # accept() üzerinde zaman aşımı olmadan `while self.running`
            # koşulu hiç yeniden değerlendirilemiyordu; servis düzgün
            # kapanamıyordu.
            srv.settimeout(1.0)
            while self.running:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError as e:
                    if self.running:
                        log.warning("Soket kabul edilemedi: %s", e)
                    continue
                try:
                    conn.settimeout(180)
                    with conn:
                        cmd = self._read_command(conn)
                        if not cmd:
                            continue
                        try:
                            reply = self.command(cmd)
                        except Exception:
                            # Eskiden istisna dış döngüye düşüyor, istemciye
                            # hiç yanıt gitmiyor ve `mek ...` 180 sn donuyordu.
                            log.exception("Komut başarısız: %s", cmd[:120])
                            reply = ("Komutu işlerken bir hata oluştu; ayrıntı için "
                                     "`mek log` çıktısına bakın.")
                        if not cmd.lower().startswith("odak "):
                            conn.sendall((reply or "ok").encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError, socket.timeout):
                    pass
                except Exception as e:
                    log.warning("Soket: %s", e)
        finally:
            srv.close()
            try:
                os.unlink(config.SOCKET_PATH)
            except OSError:
                pass

    @staticmethod
    def _read_command(conn, limit=64 * 1024):
        """Komutu satır sonuna ya da bağlantı kapanana kadar okur.

        Tek bir ``recv(1024)`` çağrısı uzun komutları (ör. `kurgu kaba <uzun
        dosya yolu>`) sessizce kesiyor ve akışın parçalı gelmesi hâlinde
        eksik komut işliyordu.
        """
        chunks = []
        total = 0
        while total < limit:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if b"\n" in chunk:
                break
        return b"".join(chunks).decode("utf-8", "ignore").strip()

    # Not: Eski `siri_http_loop` (127.0.0.1:45728) kaldırıldı.
    #
    # Yalnızca Kestirmeler tabanlı Siri köprüsü için vardı ve kimlik
    # doğrulaması olmayan bir GET isteğiyle İSTEDİĞİ Mek komutunu
    # çalıştırıyordu; üstelik "Access-Control-Allow-Origin: *" gönderdiği
    # için ziyaret edilen herhangi bir web sayfası bile bu uç noktayı
    # çağırıp yanıtı okuyabiliyordu. Siri artık App Intents üzerinden
    # doğrudan Unix soketine bağlanıyor (bkz. ui/MekMenuBar.swift).
    def _collect_advisor_telemetry(self):
        s = self.snap
        tf = getattr(self.services, "thermal_fan", None) if hasattr(self, "services") else None
        tel = (tf.last_telemetry or tf.telemetry()) if tf else {}
        us = getattr(self.services, "ultra_scratch", None) if hasattr(self, "services") else None
        us_st = us.status_dict() if us else {}
        headroom = disk_health.apfs_headroom()
        top_ram = [f"{n} {rss / 1024**3:.1f}GB" for rss, n in (getattr(s, "top_mem", ()) or ())[:4]]
        return {
            "profile": getattr(self.services, "profile", "normal") if hasattr(self, "services") else "normal",
            "front_app": getattr(s, "front", "") if s else "",
            "cpu_usage_pct": round(float(getattr(s, "cpu", 0.0) or 0.0), 1),
            "gpu_usage_pct": getattr(s, "gpu", None) if s else None,
            "ram_usage_pct": round(float(getattr(s, "mem_pct", 0.0) or 0.0), 1),
            "swap_gb": round(float(getattr(s, "swap_gb", 0.0) or 0.0), 1),
            "memory_pressure_level": int(getattr(s, "pressure", 1) or 1),
            "cpu_temp_c": tel.get("cpu_c"),
            "gpu_temp_c": tel.get("gpu_c"),
            "peak_temp_c": tel.get("peak_c"),
            "nvme_ssd_temp_c": tel.get("ssd_c"),
            "psu_temp_c": tel.get("psu_c"),
            "fan0_rpm": tel.get("fan0_rpm"),
            "fan1_rpm": tel.get("fan1_rpm"),
            "fan_mode": tel.get("mode", "auto_silent"),
            "disk_free_gb": headroom["free_gb"],
            "disk_free_pct": headroom["free_pct"],
            "ultra_scratch_used_mb": us_st.get("used_mb", 0.0),
            "local_llm_ram_gb": round(float(getattr(self, "_last_llm_gb", 0.0) or 0.0), 2),
            "top_ram_apps": top_ram,
            # İç doğrulama korkulukları için uyumluluk alanları:
            "cpu": round(float(getattr(s, "cpu", 0.0) or 0.0), 1),
            "mem_pct": round(float(getattr(s, "mem_pct", 0.0) or 0.0), 1),
            "pressure": int(getattr(s, "pressure", 1) or 1),
            "peak_c": tel.get("peak_c"),
            "ssd_c": tel.get("ssd_c"),
            "llm_gb": round(float(getattr(self, "_last_llm_gb", 0.0) or 0.0), 2),
        }

    def _apply_safe_advisor_action(self, action):
        if action == "optimize" and hasattr(self, "services") and self.services:
            return self.services.optimize_now(self.snap)
        if action == "fan_boost" and hasattr(self, "services") and getattr(self.services, "thermal_fan", None):
            return self.services.thermal_fan.set_mode("performans")
        if action == "fan_silent" and hasattr(self, "services") and getattr(self.services, "thermal_fan", None):
            return self.services.thermal_fan.set_mode("sessiz")
        if action == "unload_llm" and getattr(self, "llm_ram", None):
            info = self.llm_ram.inspect_snapshot(self.snap, check_ollama=True)
            unloaded = [
                name for m in info["ollama_models"]
                if (name := (m.get("name") or m.get("model"))) and self.llm_ram._unloader(name)
            ]
            return f"Ollama indirildi: {', '.join(unloaded)}." if unloaded else ""
        if action == "clean_cache" and not (self.snap and self.snap.pro_pids):
            self.start_clean("gunluk")
            return "Günlük önbellek temizliği başlatıldı."
        return ""

    def analyze_with_foundation_models(self, apply_action=False):
        """Apple Foundation Models ile canlı performans ve donanım ömrü analizi yapar."""
        telemetry = self._collect_advisor_telemetry()
        advice = foundation_llm.advise_system(telemetry)
        applied_note = ""
        if apply_action and advice["recommended_action"] != "none":
            applied_note = self._apply_safe_advisor_action(advice["recommended_action"])
        return foundation_llm.format_advice(advice, applied_note=applied_note)

    def _run_autonomous_fm_advisor(self):
        try:
            telemetry = self._collect_advisor_telemetry()
            advice = foundation_llm.advise_system(telemetry)
            action = advice.get("recommended_action", "none")
            applied = self._apply_safe_advisor_action(action) if action != "none" else ""
            log.info(
                "Apple Foundation Models stres analizi: puan=%d eylem=%s özet=%s",
                advice["health_score"], action, advice["summary"],
            )
            if applied:
                self.notify(f"Apple AI koruma: {advice['summary']} {applied}", title="Mek Performans")
        except Exception as e:
            log.debug("Otonom Apple Foundation Models analizi atlandı: %s", e)

    def hardware_longevity_report(self):
        """Bilgisayarın ve uygulamaların anlık performans + donanım ömrü özetini döndürür."""
        s = self.snap
        tf = getattr(self.services, "thermal_fan", None)
        tel = tf.telemetry() if tf else {}
        us = getattr(self.services, "ultra_scratch", None)
        us_st = us.status_dict() if us else {}
        disk = disk_health.inspect_disk()
        headroom = disk_health.apfs_headroom()
        wear = disk_health.nvme_wear_stats()
        fm = foundation_llm.status()

        lines = []
        if s:
            lines.append(
                f"Performans Profili: {self.services.profile} | CPU %{s.cpu:.0f} | GPU %{s.gpu} | "
                f"Bellek %{s.mem_pct:.0f} | Takas {s.swap_gb:.1f} GB | LLM {float(getattr(self, '_last_llm_gb', 0.0)):.1f} GB"
            )
        if tel:
            ssd_txt = f", NVMe SSD {tel['ssd_c']}°C" if tel.get("ssd_c") is not None else ""
            psu_txt = f", PSU/Kasa {tel['psu_c']}°C" if tel.get("psu_c") is not None else ""
            lines.append(
                f"Termal & Fan: CPU {tel.get('cpu_c')}°C, GPU {tel.get('gpu_c')}°C{ssd_txt}{psu_txt} | "
                f"Fanlar {tel.get('fan0_rpm')}/{tel.get('fan1_rpm')} RPM ({tel.get('applied')})"
            )
        if us_st.get("mounted"):
            lines.append(
                f"Ultra-Scratch (SSD Aşınma Kalkanı): {us_st['used_mb']:.1f} MB / {us_st['size_gb']} GB "
                f"(bağlar: {'sağlam' if us_st.get('symlinks_ok') else 'onarılıyor'})"
            )
        wear_txt = ""
        if wear:
            parts = []
            if wear.get("percentage_used") is not None:
                parts.append(f"aşınma %{wear['percentage_used']}")
            if wear.get("tbw") is not None:
                parts.append(f"yazılan {wear['tbw']} TB")
            if wear.get("available_spare") is not None:
                parts.append(f"yedek blok %{wear['available_spare']}")
            if parts:
                wear_txt = " | NVMe: " + ", ".join(parts)
        headroom_status = "sağlıklı" if headroom["healthy_headroom"] else "düşük (temizlik önerilir)"
        lines.append(
            f"Disk & Ömür: SMART {disk.get('smart')} | Boş Alan {headroom['free_gb']:.0f} GB "
            f"(%{headroom['free_pct']:.0f}, {headroom_status}){wear_txt}"
        )
        lines.append(
            f"Cihaz İçi Zeka: {fm['model']} — {'Hazır (0 ek RAM)' if fm['available'] else 'Kapalı (' + fm['reason'] + ')'}"
        )
        return "\n".join(lines)

    def command(self, cmd):
        c = cmd.lower().strip()
        if c in ("odak zorla", "performans", "optimize", "hızlandır", "hizlandir", "performans modu"):
            if hasattr(self, "services") and self.services and hasattr(self.services, "optimize_now"):
                return self.services.optimize_now(getattr(self, "snap", None))
            return "Performans yöneticisi etkin değil."
        if c.startswith("odak "):
            parts = cmd.split(None, 2)
            if len(parts) >= 2 and parts[1].isdigit():
                pid = int(parts[1])
                app_name = parts[2] if len(parts) > 2 else ""
                snap = getattr(self, "snap", None)
                if hasattr(self, "services") and self.services:
                    self.services.on_focus_change(pid, app_name, snap)
                if snap is not None and getattr(snap, "exes", None) and getattr(self, "memory_budget", None):
                    w_pids = set(self.services.working_pids(snap))
                    active_exes = [exe for ppid, exe, _, _ in getattr(snap, "pro_procs", ()) if ppid in w_pids]
                    if active_exes:
                        us = getattr(self.services, "ultra_scratch", None)
                        scratch_gb = (us.used_bytes() / (1024 ** 3)) if us else 0.0
                        extra = round(float(getattr(self, "_last_llm_gb", 0.0) or 0.0) + scratch_gb, 2)
                        self.memory_budget.tick(snap.exes, active_exes=active_exes, extra_reserve_gb=extra)
            return "ok"

        if c in ("sağlık", "saglik", "ömür", "omur", "donanım", "donanim", "health"):
            return self.hardware_longevity_report()

        if c in ("analiz", "akıl", "akil", "ai", "ai analiz", "sistem analizi") or c.startswith(("analiz ", "ai ")):
            if "durum" in c or "status" in c:
                fm = foundation_llm.status(force=True)
                return f"{fm['model']}: {'Aktif ve hazır (0 ek RAM).' if fm['available'] else 'Kullanılamıyor (' + fm['reason'] + ').'}"
            apply_now = any(k in c for k in ("uygula", "düzelt", "duzelt", "auto", "otomatik"))
            return self.analyze_with_foundation_models(apply_action=apply_now)

        if c == "status_json":
            s = getattr(self, "snap", None)
            tf = getattr(self.services, "thermal_fan", None) if hasattr(self, "services") else None
            tel = (tf.last_telemetry or tf.telemetry()) if tf else {}
            us = getattr(self.services, "ultra_scratch", None) if hasattr(self, "services") else None
            us_st = us.status_dict() if us and hasattr(us, "status_dict") else {}
            fm = foundation_llm.status()
            payload = {
                "profile": getattr(self.services, "profile", "normal") if hasattr(self, "services") else "normal",
                "front": getattr(s, "front", "") if s else "",
                "cpu": round(float(getattr(s, "cpu", 0.0) or 0.0), 1),
                "gpu": getattr(s, "gpu", None) if s else None,
                "mem_pct": round(float(getattr(s, "mem_pct", 0.0) or 0.0), 1),
                "swap_gb": round(float(getattr(s, "swap_gb", 0.0) or 0.0), 1),
                "disk_free_gb": round(float(getattr(s, "disk_free_gb", 0.0) or 0.0), 1),
                "llm_gb": round(float(getattr(self, "_last_llm_gb", 0.0) or 0.0), 2),
                "pro_count": len(getattr(s, "pro_pids", ())) if s else 0,
                "renders": self.renders.active() if hasattr(self, "renders") else [],
                "cpu_c": tel.get("cpu_c"),
                "gpu_c": tel.get("gpu_c"),
                "ssd_c": tel.get("ssd_c"),
                "psu_c": tel.get("psu_c"),
                "peak_c": tel.get("peak_c"),
                "fan0_rpm": tel.get("fan0_rpm"),
                "fan1_rpm": tel.get("fan1_rpm"),
                "fan_mode": tel.get("mode", "auto_silent"),
                "scratch_mounted": bool(us_st.get("mounted")),
                "scratch_used_mb": us_st.get("used_mb", 0.0),
                "scratch_size_gb": us_st.get("size_gb", 12),
                "fm_available": bool(fm.get("available")),
            }
            return json.dumps(payload, ensure_ascii=False)

        qbox = getattr(self, "questions", None)
        # Bekleyen bir soru varsa ve kullanıcı yanıt verdiyse:
        if qbox and qbox.pending():
            c_clean = c.removeprefix("cevap ").strip()
            handled, reply = qbox.answer(c_clean)
            if handled:
                self.speak(reply, force=True)
                return reply

        if c.startswith("cevap ") or c in ("soru", "sorular"):
            if not qbox:
                return "Soru yönetim kutusu etkin değil."
            if c in ("soru", "sorular"):
                return qbox.summary()
            handled, reply = qbox.answer(cmd.split(None, 1)[1])
            if handled:
                self.speak(reply, force=True)
            return reply

        if c in ("bellek", "ram", "memory"):
            mb = getattr(self, "memory_budget", None)
            base = mb.report() if mb else "Bellek yöneticisi etkin değil."
            if getattr(self, "llm_ram", None):
                return base + "\n" + self.llm_ram.format_report(self.snap)
            return base

        if c in ("llm", "llm durum") or c.startswith("llm "):
            lr = getattr(self, "llm_ram", None)
            if not lr:
                return "LLM bellek koordinatörü etkin değil."
            arg = c.split(None, 1)[1].strip() if " " in c else ""
            if arg in ("boşalt", "bosalt", "temizle", "unload", "indir"):
                info = lr.inspect_snapshot(self.snap, check_ollama=True)
                unloaded = []
                for m in info["ollama_models"]:
                    name = m.get("name") or m.get("model")
                    if name and lr._unloader(name):
                        unloaded.append(name)
                if unloaded:
                    return f"Ollama bellekten indirildi: {', '.join(unloaded)}."
                return "Bellekte yüklü boşaltılacak Ollama modeli yok."
            return lr.format_report(self.snap)

        if c in ("ses", "ses durum", "audio") or c.startswith("ses "):
            arg = c.split(None, 1)[1].strip() if " " in c else ""
            if arg in ("sistem", "system", "kulaklık", "kulaklik", "varsayılan", "varsayilan"):
                self.cfg = config.set_values(speech_device="system")
                return "Ses çıkışı sistem varsayılan aygıtına (kulaklık/aktif çıkış) ayarlandı."
            if arg in ("hoparlör", "hoparlor", "mac studio", "macstudio"):
                self.cfg = config.set_values(speech_device="mac studio")
                return "Ses çıkışı Mac Studio dahili hoparlörüne ayarlandı."
            if arg.startswith(("seviye ", "volume ")):
                nums = re.findall(r"\d+(?:\.\d+)?", arg)
                if nums:
                    val = float(nums[0])
                    vol = round(max(0.05, min(1.0, val / 100.0 if val > 1.0 else val)), 2)
                    self.cfg = config.set_values(speech_volume=vol)
                    return f"Konuşma ses düzeyi %{int(vol * 100)} olarak ayarlandı."
            if arg == "test":
                self.speak("Mek ses çıkışı testi başarılı.", force=True)
                return f"Ses testi kuyruğa eklendi (aygıt: {self.cfg.get('speech_device')}, seviye: %{int(float(self.cfg.get('speech_volume', 0.3)) * 100)})."
            return (f"Konuşma çıkışı: {self.cfg.get('speech_device', 'mac studio')} | "
                    f"Ses düzeyi: %{int(float(self.cfg.get('speech_volume', 0.3)) * 100)} | "
                    "Sessiz saatler: "
                    f"{self.cfg.get('quiet_start_hour', 0):02d}:00-{self.cfg.get('quiet_end_hour', 11):02d}:00. "
                    "Değiştirmek için: `mek ses sistem` | `mek ses hoparlör` | `mek ses seviye <10-100>` | `mek ses test`.")

        if c in ("ramdisk", "scratch", "ultrascratch", "ultra-scratch"):
            us = getattr(self.services, "ultra_scratch", None)
            if not us:
                return "Ultra-Scratch yöneticisi etkin değil."
            us.ensure_mounted()
            return us.report()

        if c in ("fan", "termal", "thermal", "soğutma", "sogutma") or c.startswith(("fan ", "termal ")):
            tf = getattr(self.services, "thermal_fan", None)
            if not tf:
                return "Termal & Fan yöneticisi etkin değil."
            parts = c.split(None, 1)
            if len(parts) > 1 and parts[1] not in ("durum", "status"):
                return tf.set_mode(parts[1])
            return tf.report()

        if c in ("yardım", "yardim", "help"):
            return ("Komutlar: brifing, durum, sağlık (donanım & ömür), analiz [uygula] (Apple Foundation Models), "
                    "performans (optimize), bellek, llm [durum|boşalt], ramdisk, fan [durum|sessiz|otomatik|performans], "
                    "ses [durum|sistem|hoparlör|seviye <N>|test], izleme [ekle|çıkar <sembol>], piyasa <sembol>, "
                    "temizle [derin], disk, ağ [test], güç, güvenlik, etkinlik [aç|sesli], soru, cevap <seçenek>, "
                    "güncelle [kontrol|durum|şimdi], karantina [geri <no>], günlük [adet], sor <serbest soru> (ya da doğrudan yazın), onay, iptal, reklam [durum|aç|güncelle|kapat], sus <dakika>. "
                    "Yalnızca tanımlı ve güvenli eylemler yürütülür; Mek keyfî terminal komutu çalıştırmaz.")
        if c in ("güç", "guc", "power", "güç durumu", "guc durumu", "power status"):
            state = power.read_power_status()
            if not state["available"]:
                return "Güç telemetrisi okunamadı. Masaüstü bilgisayarda kesintiye karşı uygun bir UPS gerekir."
            if state["is_ups"]:
                percent = f", pil yüzde {state['percent']}" if state["percent"] is not None else ""
                remaining = (f", yaklaşık {state['remaining_minutes']} dakika" if
                             state["remaining_minutes"] is not None else "")
                return f"UPS: {state['source']}{percent}{remaining}; {'şebeke kesik' if state['on_battery'] else 'şebeke mevcut'}."
            return (f"Güç kaynağı: {state['source']}. Harici UPS telemetrisi algılanmadı; "
                    "yazılım fiziksel güç kesintisini tek başına önleyemez.")
        if c in ("güç koruma", "guc koruma", "power protection"):
            enabled = self.cfg.get("ups_auto_shutdown_enabled", False)
            return ("UPS kritik seviyede bir dakikalık güvenli kapatma etkin."
                    if enabled else "UPS kritik seviyede otomatik kapatma kapalı; `~/.jarvis/jarvis.json` içinde "
                    "ups_auto_shutdown_enabled değerini true yaparak açabilirsiniz.")
        if c in ("etkinlik", "activity", "etkinlik sesli"):
            report = activity_monitor.format_snapshot(activity_monitor.process_snapshot(events=self.events))
            if c == "etkinlik sesli":
                self.speak(report, force=True)
            return report
        if c in ("etkinlik aç", "etkinlik ac", "activity open"):
            return activity_monitor.open_activity_monitor()["message"]
        if c in ("çökmeler", "cokmeler", "crashes"):
            reports = self.crash_monitor.recent()
            if not reports:
                return "Son uygulama çökme raporu bulunamadı."
            return "Son çökme raporları: " + "; ".join(report["name"] for report in reports[:8])
        if c in ("güncelle kontrol", "guncelle kontrol", "update check"):
            threading.Thread(target=self.check_for_updates, daemon=True).start()
            return "Apple, Homebrew ve varsa Mac App Store güncellemeleri kontrol ediliyor."
        if c in ("güncelle şimdi", "guncelle simdi", "update now"):
            if not self.update_install_allowed():
                return "Güvenli güncelleme koşulları sağlanmıyor; kurulum yapılmadı. AC, boşta olma, render, disk, ısı ve ağ durumunu kontrol edin."
            return ("Güncelleme işlemi başladı." if self.start_update_cycle() else
                    "Bir güncelleme işlemi zaten çalışıyor.")
        if c in ("güncelle", "güncelle durum", "guncelle", "guncelle durum", "update", "update status"):
            return self.last_update_summary
        if c in ("güncelle donanım", "guncelle donanim", "update hardware"):
            return self.hardware_report()
        if c in ("ağ test", "ag test", "network test"):
            return self.network.active_quality_test()["message"]
        if c in ("ağ", "ag", "network"):
            return self.network_report()
        if c.startswith("reklam") or c.startswith("adblock"):
            return self.adblock_command(cmd)
        if c == "disk" or c.startswith("disk "):
            result = disk_health.inspect_disk()
            if result["warning"]:
                return f"{result['warning']} SMART: {result['smart']}"
            return (f"Disk {result['device'] or 'başlangıç birimi'}; "
                    f"SMART: {result['smart']}. Dosya sistemi denetimi boşta haftalık yapılır; "
                    "onarım otomatik çalıştırılmaz.")
        if c.startswith("brifing"):
            text = self.briefing()
            self.speak(text, force=True)
            return text
        if c == "izleme" or c.startswith("izleme ") or c == "watchlist" or c.startswith("watchlist "):
            return self.watchlist_command(cmd)
        if c.startswith("piyasa") or c.startswith("borsa"):
            text = apple.market_report(cmd.split(" ", 1)[1] if " " in cmd else "genel", self.cfg["market_watchlist"])
            self.speak(text, force=True)
            return text
        if c.startswith("temizle"):
            tier = "haftalik" if any(k in c for k in ("derin", "haftalik", "kalıntı", "kalinti")) else "gunluk"
            has_pro = bool(self.snap and self.snap.pro_pids)
            self.start_clean(tier)
            if has_pro:
                return f"{'Derin' if tier == 'haftalik' else 'Günlük'} temizlik başladı. Kurgu programı açık olduğu için aktif proje önbellekleri korunarak sistem, uygulama ve kalıntılar temizleniyor."
            return f"{'Derin' if tier == 'haftalik' else 'Günlük'} temizlik başladı, bitince haber vereceğim."
        if c.startswith("sus"):
            mins = int(re.findall(r"\d+", c)[0]) if re.findall(r"\d+", c) else 60
            self.muted_until = time.time() + mins * 60
            return f"{mins} dakika sessizim."
        if c in ("onay", "evet", "onayla"):
            return self.agent.confirm()
        if c in ("iptal", "vazgeç", "vazgec"):
            return self.agent.cancel()
        if "karantina" in c or "karantına" in c or "neleri karantinaya" in c:
            if c == "karantina_json":
                rows = self.security.quarantine_list()
                items = []
                for i, ts, orig, dest, there in rows:
                    items.append({
                        "no": i,
                        "date": ts,
                        "original": orig,
                        "name": os.path.basename(orig),
                        "dest": dest,
                        "exists": there
                    })
                return json.dumps({"count": len(items), "items": items})
            arg = c.split()
            if len(arg) >= 3 and arg[1] in ("geri", "restore") and arg[2].isdigit():
                return self.security.quarantine_restore(int(arg[2]))
            if len(arg) >= 2 and arg[1] in ("boşalt", "temizle", "sil"):
                return self.security.quarantine_clear()
            rows = self.security.quarantine_list()
            if not rows:
                return "Karantina tertemiz, hiçbir zararlı veya izole edilmiş öğe yok."
            lines = [f"{i}) {ts}  {os.path.basename(orig)} ({orig}) [{'karantinada' if there else 'silinmiş'}]"
                     for i, ts, orig, _, there in rows]
            return f"Karantinada {len(rows)} öğe var:\n" + "\n".join(lines) + \
                "\nGeri almak için: mek karantina geri <no>"
        if c.startswith(("sor ", "ask ", "derin ")):
            return self.agent.ask(cmd.split(None, 1)[1] if not c.startswith("derin") else cmd)
        if c in ("günlük", "gunluk", "log") or c.startswith(("günlük ", "gunluk ")):
            n = int(re.findall(r"\d+", c)[0]) if re.findall(r"\d+", c) else 20
            keys = ("Asistan", "SAY:", "Arka plana", "park edildi", "tam performansa", "purge", "yetim",
                    "Bellek sınırı", "Güvenlik", "karantin", "Karantin", "madenci", "temizlik", "güncell")
            try:
                with open(config.LOG_FILE, encoding="utf-8", errors="ignore") as f:
                    lines = [l.strip()[:300] for l in f.readlines()[-3000:] if any(k in l for k in keys)]
            except OSError:
                return "Günlük okunamadı."
            return "\n".join(lines[-n:]) or "Kayda değer olay yok."
        if c.startswith(("güvenlik", "guvenlik")):
            issues = self.security.posture()
            shell_issues = self.security.audit_shell_and_cron()
            dl_extra = ""
            if "tara" in c or "scan" in c:
                self.security.audit_persistence()
                dl = self.security.scan_recent_downloads()
                dl_extra = (f" İndirilenler tarandı ({dl['checked']} kurulum öğesi, "
                            f"karantinaya alınan: {len(dl['quarantined'])}).")
            q = len(os.listdir(config.QUARANTINE)) - (1 if os.path.exists(os.path.join(config.QUARANTINE, "kayit.txt")) else 0)
            shell_txt = (", kabuk/cron uyarısı: " + ", ".join(shell_issues)) if shell_issues else ", kabuk/cron temiz"
            return ("Güvenlik ayarları: " + (", ".join(issues) if issues else "hepsi açık") +
                    f"{shell_txt}. Karantinada {max(0, q)} öğe var.{dl_extra}")
        if c.startswith("söyle "):
            self.speak(cmd[6:], force=True)
            return "ok"
        s = self.snap
        if c in ("durum", "status"):
            if not s:
                return "Henüz ölçüm yok."
            parked = len(getattr(self.services, "parked_pro", ()))
            clamped = len(getattr(self.services, "clamped", ())) + len(getattr(self.services, "bg_clamped", ()))
            return (f"Öndeki uygulama {s.front}, CPU %{s.cpu:.0f}, GPU %{s.gpu}, bellek %{s.mem_pct:.0f}, "
                    f"takas {s.swap_gb:.1f} GB, boş disk {s.disk_free_gb:.0f} GB, "
                    f"profil {self.services.profile} (E-çekirdekte: {parked} boşta kurgu, {clamped} yardımcı süreç), "
                    f"süren render: {', '.join(self.renders.active()) or 'yok'}.")
        if len(cmd.split()) >= 2 or "?" in cmd:  # tanımlı komut değilse serbest soru say
            return self.agent.ask(cmd)
        return "Komutu tanıyamadım. Desteklenen komutlar için `mek yardım` yazın."

    def watchlist_command(self, command):
        parts = command.split(maxsplit=2)
        action = parts[1].lower() if len(parts) > 1 else "liste"
        watchlist = list(self.cfg.get("market_watchlist", []))
        if action in ("liste", "list", "durum"):
            if not watchlist:
                return "Piyasa izleme listeniz boş; yalnızca kritik değişimleri duymak için `mek izleme ekle THYAO.IS` kullanın."
            thresholds = self.cfg["market_alert_pct"]
            return "İzleme listeniz: " + "; ".join(
                f"{symbol} (kritik eşik %{thresholds.get(symbol, thresholds['default']):g})"
                for symbol in watchlist)
        if action not in ("ekle", "add", "çıkar", "cikar", "sil", "remove") or len(parts) < 3:
            return "Kullanım: mek izleme [liste|ekle <sembol...>|çıkar <sembol...>]"
        symbols = [symbol.upper() for symbol in re.split(r"[,\s]+", parts[2]) if symbol]
        if not symbols or any(not re.fullmatch(r"[A-Z0-9.^=-]{1,24}", symbol) for symbol in symbols):
            return "Geçersiz sembol. Örnek: `mek izleme ekle THYAO.IS AAPL BTC-USD`"
        candidate = dict(self.cfg)
        updated = watchlist.copy()
        if action in ("ekle", "add"):
            updated.extend(symbol for symbol in symbols if symbol not in updated)
        else:
            missing = [symbol for symbol in symbols if symbol not in updated]
            if missing:
                return "İzleme listesinde bulunmuyor: " + ", ".join(missing)
            updated = [symbol for symbol in updated if symbol not in symbols]
        candidate["market_watchlist"] = updated
        try:
            config.save(candidate)
        except (OSError, TypeError, ValueError) as exc:
            log.exception("Piyasa izleme listesi kaydedilemedi")
            return f"İzleme listesi kaydedilemedi: {exc}"
        self.cfg.clear()
        self.cfg.update(candidate)
        return ("İzleme listesine eklendi: " if action in ("ekle", "add") else
                "İzleme listesinden çıkarıldı: ") + ", ".join(symbols)

    def _boot_greeting(self):
        """Bilgisayar açılır açılmaz / oturum başlar başlamaz gecikmesiz selamlama."""
        try:
            time.sleep(1.2)  # Ses ve donanım sürücülerinin hazır olması için kısa tolerans
            now = dt.datetime.now()
            today = dt.date.today()

            # Sessiz saatlerde (00:00 - 11:00) açılış selamı kesinlikle verilmez.
            # Selam yalnızca 11:00 - 00:00 saatleri arasında verilebilir.
            if self._quiet_hours():
                log.info("Açılış selamı sessiz saatte atlandı (saat %02d).", now.hour)
                return

            greeted = ""
            if os.path.exists(self.greet_file):
                with open(self.greet_file) as fh:
                    greeted = fh.read().strip()
            if greeted != today.isoformat():
                # Damga yalnızca gerçekten selam verildiğinde yazılır; aksi hâlde
                # sessiz saat bittiğinde ana döngü selamı veremezdi.
                with open(self.greet_file, "w") as fh:
                    fh.write(today.isoformat())
                greet = "Günaydın" if now.hour < 12 else "Tünaydın" if now.hour < 18 else "İyi akşamlar"
                msg = f"{greet} {self.T}. Mek hazır."
                log.info("Açılış anında selamlama yapılıyor: %s", msg)
                self.speak(msg)

                def send_briefing():
                    try:
                        # urgent=False: açılış brifingi acil bir güvenlik uyarısı
                        # değil, bu yüzden sessiz saat ve konuşma aralığı
                        # kurallarına uymalı. (urgent=True sessiz saati bilerek
                        # aşar; o yalnızca güvenlik uyarıları içindir.)
                        self.notify(self.briefing())
                    except Exception:
                        log.exception("Açılış brifingi hazırlanamadı")

                threading.Thread(target=send_briefing, daemon=True).start()
        except Exception:
            log.exception("Açılış selamlaması hatası")

    def run(self):
        threading.Thread(target=self.socket_loop, daemon=True).start()
        # Bilgisayar açılır açılmaz anında selamlama (gecikmesiz):
        threading.Thread(target=self._boot_greeting, daemon=True).start()
        log.info("Mek hazır")
        self.loop()

    def stop(self, *_):
        self.running = False
        self.services.shutdown()
        sys.exit(0)


def main():
    h = logging.handlers.RotatingFileHandler(config.LOG_FILE, maxBytes=2_000_000, backupCount=2)
    logging.basicConfig(level=logging.INFO, handlers=[h], format="%(asctime)s %(name)s %(levelname)s %(message)s")
    j = Mek()
    signal.signal(signal.SIGTERM, j.stop)
    j.run()


if __name__ == "__main__":
    main()
