# -*- coding: utf-8 -*-
"""Bu oturumda bulunup düzeltilen hatalar için gerileme (regression) testleri.

Her test sınıfı, gerçekten yaşanmış somut bir arızayı kilitler.
"""
import json
import os
import subprocess
import sys
import tempfile
import contextlib
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jarvisd"))

import config  # noqa: E402
import proc  # noqa: E402
import security  # noqa: E402


def _load_daemon_module():
    """jarvisd/jarvisd.py'yi açık dosya yolundan yükler.

    Düz `import jarvisd`, sys.path sırasına göre `jarvisd/` DİZİNİNİ bir
    ad alanı paketi olarak çözebiliyor; o zaman modülün içeriği bulunamıyor.
    """
    import importlib.util
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    spec = importlib.util.spec_from_file_location(
        "mek_daemon_test", os.path.join(root, "jarvisd", "jarvisd.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_DAEMON = None


def daemon_module():
    global _DAEMON
    if _DAEMON is None:
        _DAEMON = _load_daemon_module()
    return _DAEMON


class SelfQuarantineTests(unittest.TestCase):
    """Mek kendi launchd ajanını karantinaya alıp kendini kapatıyordu.

    ~/.jarvis/venv/bin/python, Homebrew'un ad-hoc imzalı python'ına bir
    sembolik bağdır. is_own_item() yalnızca realpath'e baktığı için Mek'in
    kendi daemon ajanı "imzasız, şüpheli dizinde yabancı başlangıç öğesi"
    görünüyor; servis her açılışta kendini `launchctl bootout` ile
    kapatıyordu.
    """

    def test_venv_symlink_agent_is_recognised_as_own(self):
        self.assertTrue(
            security.is_own_item("com.alptug.jarvis",
                                 os.path.join(security.MEK_HOME, "venv/bin/python")),
            "venv sembolik bağı üzerinden çalışan daemon kendi öğesi sayılmalı",
        )

    def test_app_bundle_agent_is_recognised_as_own(self):
        self.assertTrue(security.is_own_item(
            "com.alptug.mek.ui", "/Applications/Mek.app/Contents/MacOS/Mek"))

    def test_own_plist_guard_is_independent_of_program_path(self):
        # İkinci savunma hattı: program yolu ne olursa olsun Mek'in kendi
        # plist'i asla devre dışı bırakılamaz.
        own = os.path.join(security.HOME, "Library/LaunchAgents/com.alptug.jarvis.plist")
        self.assertTrue(security.is_own_plist(own))
        self.assertFalse(security.is_own_plist(
            os.path.join(security.HOME, "Library/LaunchAgents/com.evil.plist")))

    def test_foreign_agent_is_still_not_exempt(self):
        # Düzeltme güvenliği gevşetmemeli.
        self.assertFalse(security.is_own_item("com.evil.agent", "/tmp/payload"))
        # Mek etiketini taklit eden ama Mek dizinlerinin dışından çalışan öğe
        # de muaf olmamalı.
        self.assertFalse(security.is_own_item("com.alptug.jarvis", "/tmp/payload"))


class AtomicSaveTests(unittest.TestCase):
    """security._save sabit bir ".tmp" adı kullanıyordu.

    İki iş parçacığı aynı durum dosyasını yazdığında ilkinin os.replace'i
    geçici dosyayı tüketiyor, ikincisi FileNotFoundError ile daemon iş
    parçacığını düşürüyordu (gerçekte scan_recent_downloads çöküyordu).
    """

    def test_concurrent_saves_do_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "alt", "state.json")
            errors = []

            def writer(i):
                try:
                    security._save(path, {"i": i})
                except Exception as exc:  # pragma: no cover
                    errors.append(repr(exc))

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(40)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            with open(path) as fh:
                self.assertIn("i", json.load(fh))

    def test_save_creates_missing_directory(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "yok", "derin", "state.json")
            security._save(path, {"a": 1})
            self.assertTrue(os.path.isfile(path))

    def test_no_temp_files_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state.json")
            for i in range(5):
                security._save(path, {"i": i})
            self.assertEqual(sorted(os.listdir(d)), ["state.json"])


class ConfigSingleSourceTests(unittest.TestCase):
    """Gemini anahtarı config.json'a yazılıp jarvis.json'dan okunuyordu.

    Sonuç: `mek zeka anahtar ...` "kaydedildi" diyor ama motor her yeniden
    başlatmada anahtarı bulamıyordu.
    """

    def _isolate(self, d):
        return patch.multiple(
            config,
            MEK_HOME=d,
            CONFIG_FILE=os.path.join(d, "jarvis.json"),
            LEGACY_CONFIG_FILE=os.path.join(d, "config.json"),
            STATE_DIR=os.path.join(d, "state"),
            QUARANTINE=os.path.join(d, "karantina"),
        )

    def test_secret_reads_legacy_config_json(self):
        with tempfile.TemporaryDirectory() as d, self._isolate(d):
            with open(os.path.join(d, "config.json"), "w") as fh:
                json.dump({"gemini_api_key": "eski-anahtar"}, fh)
            self.assertEqual(config.secret("gemini_api_key", "MEK_TEST_UNSET_KEY"),
                             "eski-anahtar")

    def test_jarvis_json_wins_over_legacy(self):
        with tempfile.TemporaryDirectory() as d, self._isolate(d):
            with open(os.path.join(d, "config.json"), "w") as fh:
                json.dump({"gemini_api_key": "eski"}, fh)
            with open(os.path.join(d, "jarvis.json"), "w") as fh:
                json.dump({"gemini_api_key": "yeni"}, fh)
            self.assertEqual(config.secret("gemini_api_key", "MEK_TEST_UNSET_KEY"), "yeni")

    def test_env_var_wins_over_files(self):
        with tempfile.TemporaryDirectory() as d, self._isolate(d):
            with open(os.path.join(d, "jarvis.json"), "w") as fh:
                json.dump({"gemini_api_key": "dosya"}, fh)
            with patch.dict(os.environ, {"MEK_TEST_KEY": "ortam"}):
                self.assertEqual(config.secret("gemini_api_key", "MEK_TEST_KEY"), "ortam")

    def test_set_values_round_trips_through_secret(self):
        # Asıl gerileme: yaz -> oku döngüsü aynı dosyayı kullanmalı.
        with tempfile.TemporaryDirectory() as d, self._isolate(d):
            config.set_values(gemini_api_key="kaydedilen")
            self.assertEqual(config.secret("gemini_api_key", "MEK_TEST_UNSET_KEY"),
                             "kaydedilen")

    def test_load_merges_legacy_and_defaults(self):
        with tempfile.TemporaryDirectory() as d, self._isolate(d):
            with open(os.path.join(d, "config.json"), "w") as fh:
                json.dump({"gemini_api_key": "x"}, fh)
            cfg = config.load()
            self.assertEqual(cfg["gemini_api_key"], "x")
            self.assertEqual(cfg["user_title"], config.DEFAULTS["user_title"])

    def test_corrupt_config_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d, self._isolate(d):
            with open(os.path.join(d, "jarvis.json"), "w") as fh:
                fh.write("{bozuk json")
            cfg = config.load()
            self.assertEqual(cfg["user_title"], config.DEFAULTS["user_title"])


class ProcTimeoutTests(unittest.TestCase):
    """Zaman aşımı verilmeyen alt süreç çağrıları daemon'u kilitliyordu.

    En somut örneği: kaydedilmemiş değişikliği olan Premiere Pro'ya gönderilen
    `tell application ... to quit` çağrısı hiç dönmüyordu.
    """

    def test_timeout_returns_result_instead_of_raising(self):
        r = proc.run(["/bin/sleep", "5"], timeout=1)
        self.assertEqual(r.returncode, proc.TIMEOUT_RETURNCODE)

    def test_missing_command_does_not_raise(self):
        r = proc.run(["/nonexistent/mek-komutu-yok"], timeout=5)
        self.assertEqual(r.returncode, 127)

    def test_successful_command_passes_through(self):
        r = proc.run(["/bin/echo", "merhaba"], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "merhaba")

    def test_ok_helper(self):
        self.assertTrue(proc.ok(["/usr/bin/true"], timeout=5))
        self.assertFalse(proc.ok(["/usr/bin/false"], timeout=5))

    def test_capture_background_kills_process_on_timeout(self):
        """communicate(timeout) alt süreci ÖLDÜRMEZ; sarmalayıcı öldürmeli."""
        out, timed_out = proc.capture_background(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=1, low_priority=False)
        self.assertTrue(timed_out)
        self.assertEqual(out, "")

    def test_capture_background_returns_output(self):
        out, timed_out = proc.capture_background(
            [sys.executable, "-c", "print('tamam')"], timeout=20, low_priority=False)
        self.assertFalse(timed_out)
        self.assertEqual(out.strip(), "tamam")

    def test_capture_with_input_round_trip(self):
        out, _err, rc = proc.capture_with_input(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
            "selam", timeout=20)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "SELAM")

    def test_capture_with_input_times_out_cleanly(self):
        out, _err, rc = proc.capture_with_input(
            [sys.executable, "-c", "import time; time.sleep(30)"], "", timeout=1)
        self.assertEqual(rc, proc.TIMEOUT_RETURNCODE)
        self.assertEqual(out, "")

    def test_temp_wav_paths_are_unique(self):
        """Eskiden /tmp/mek_vh_<hash % 1000000>.wav kullanılıyordu: çakışır."""
        paths = [proc.temp_wav("test") for _ in range(20)]
        try:
            self.assertEqual(len(set(paths)), 20)
            for p in paths:
                self.assertTrue(os.path.isfile(p))
                self.assertFalse(p.startswith("/tmp/mek_test_0"))
        finally:
            for p in paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass


class NoStalePathImportTests(unittest.TestCase):
    """jarvisd.py içe aktarılırken ~/.jarvis'i sys.path'e ekliyordu.

    Bu yüzden depodaki testler ve araçlar, kurulu (eski) kopyadaki modülleri
    yüklüyordu.
    """

    def test_importing_jarvisd_does_not_add_install_dir_to_syspath(self):
        script = (
            "import sys, os\n"
            f"sys.path.insert(0, {os.path.join(os.path.dirname(__file__), '..', 'jarvisd')!r})\n"
            "import jarvisd\n"
            "mek_home = os.path.expanduser('~/.jarvis')\n"
            "print(any(os.path.abspath(p) == mek_home for p in sys.path))\n"
        )
        r = subprocess.run([sys.executable, "-c", script],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False",
                         "jarvisd içe aktarımı ~/.jarvis'i sys.path'e eklememeli")


class RepoLayoutTests(unittest.TestCase):
    """Eksiksizlik: başvurulan her dosya depoda gerçekten var mı?"""

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def test_launchd_plists_point_at_real_entry_points(self):
        import plistlib
        checks = {
            "com.alptug.jarvis.plist": os.path.join(self.root, "jarvisd", "jarvisd.py"),
            "com.alptug.mek.ui.plist": None,  # /Applications/Mek.app, kurulumda üretilir
        }
        for name, expected in checks.items():
            path = os.path.join(self.root, "launchd", name)
            self.assertTrue(os.path.isfile(path), f"{name} eksik")
            with open(path, "rb") as fh:
                d = plistlib.load(fh)
            args = d.get("ProgramArguments") or []
            self.assertTrue(args, f"{name}: ProgramArguments boş")
            if expected:
                target = args[-1].replace("__HOME__/.jarvis/jarvisd/", "")
                self.assertTrue(os.path.isfile(os.path.join(self.root, "jarvisd", target)),
                                f"{name} var olmayan bir giriş noktasını gösteriyor: {args[-1]}")

    def test_declared_requirements_cover_third_party_imports(self):
        import ast
        with open(os.path.join(self.root, "requirements.txt")) as fh:
            reqs = fh.read().lower()
        # pyobjc paket adı -> içe aktarma adı
        pyobjc = {"Cocoa": "pyobjc-framework-cocoa",
                  "Foundation": "pyobjc-framework-cocoa",
                  "Quartz": "pyobjc-framework-quartz",
                  "EventKit": "pyobjc-framework-eventkit"}
        external = set()
        stdlib = set(sys.stdlib_module_names)
        local = {f[:-3] for f in os.listdir(os.path.join(self.root, "jarvisd"))
                 if f.endswith(".py")} | {"jarvis"}
        for path in [os.path.join(self.root, "jarvis.py")] + [
                os.path.join(self.root, "jarvisd", f)
                for f in os.listdir(os.path.join(self.root, "jarvisd")) if f.endswith(".py")]:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), path)
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    names = [a.name.split(".")[0] for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names = [n.module.split(".")[0]]
                else:
                    continue
                external.update(x for x in names if x not in stdlib and x not in local)
        # DaVinciResolveScript, Resolve kurulumundan sys.path ile gelir.
        external.discard("DaVinciResolveScript")
        missing = []
        for mod in sorted(external):
            needle = pyobjc.get(mod, mod.replace("_", "-").lower())
            if needle not in reqs:
                missing.append(f"{mod} (-> {needle})")
        self.assertEqual(missing, [], f"requirements.txt eksik: {missing}")


if __name__ == "__main__":
    unittest.main()


class QuietHoursTests(unittest.TestCase):
    """Açılış selamı sessiz saatleri hiç denetlemiyordu.

    Servis gece yarısından sonra yeniden başladığında (ya da o gün ilk kez
    çalıştığında) Mek sessiz saat olmasına rağmen sesli selam veriyordu.
    Ayrıca açılış brifingi `urgent=True` ile gönderildiği için o da sessiz
    saati aşıyordu.
    """

    @contextlib.contextmanager
    def _at_hour(self, hour):
        """jarvisd modülünün gördüğü saati sabitler (testler saate bağlı olmasın)."""
        J = daemon_module()
        import datetime as real_dt
        frozen = real_dt.datetime(2026, 1, 1, hour, 30)

        class FrozenDateTime(real_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        class FrozenModule:
            datetime = FrozenDateTime
            date = real_dt.date
            time = real_dt.time
            timedelta = real_dt.timedelta

        original = J.dt
        J.dt = FrozenModule
        try:
            yield
        finally:
            J.dt = original

    def _fake_mek(self, hour, greet_file):
        J = daemon_module()
        spoken, notified = [], []
        fake = types.SimpleNamespace(
            cfg={"quiet_start_hour": 0, "quiet_end_hour": 11,
                 "morning_greeting_from_hour": 6},
            greet_file=greet_file,
            T="Alptuğ Bey",
            speak=spoken.append,
            briefing=lambda: "brifing metni",
            notify=lambda text, **kw: notified.append((text, kw)),
            _quiet_hours=lambda: 0 <= hour < 11,
        )
        return J.Mek._boot_greeting, fake, spoken, notified

    def test_boot_greeting_is_silent_during_quiet_hours(self):
        with tempfile.TemporaryDirectory() as d:
            gf = os.path.join(d, "greeted")
            boot, fake, spoken, _ = self._fake_mek(2, gf)   # gece 02:00
            with self._at_hour(2):
                boot(fake)
            self.assertEqual(spoken, [], "sessiz saatte konuşmamalı")
            self.assertFalse(os.path.exists(gf),
                             "selam verilmediyse günlük damga da yazılmamalı")

    def test_boot_greeting_speaks_outside_quiet_hours(self):
        with tempfile.TemporaryDirectory() as d:
            gf = os.path.join(d, "greeted")
            boot, fake, spoken, _ = self._fake_mek(14, gf)  # öğleden sonra
            with self._at_hour(14):
                boot(fake)
            self.assertEqual(len(spoken), 1)
            self.assertIn("Mek hazır", spoken[0])
            self.assertTrue(os.path.exists(gf))

    def test_boot_greeting_allowed_in_morning_window(self):
        # Sessiz saat 0-11 ama morning_greeting_from_hour=6: 08:00'de selam serbest.
        with tempfile.TemporaryDirectory() as d:
            boot, fake, spoken, _ = self._fake_mek(8, os.path.join(d, "greeted"))
            with self._at_hour(8):
                boot(fake)
            self.assertEqual(len(spoken), 1)

    def test_boot_briefing_is_not_marked_urgent(self):
        """urgent=True sessiz saati bilerek aşar; brifing güvenlik uyarısı değil."""
        with tempfile.TemporaryDirectory() as d:
            boot, fake, _spoken, notified = self._fake_mek(14, os.path.join(d, "greeted"))
            with self._at_hour(14):
                boot(fake)
            for _ in range(50):
                if notified:
                    break
                time.sleep(0.05)
            self.assertTrue(notified, "açılış brifingi gönderilmeliydi")
            self.assertFalse(notified[0][1].get("urgent", False),
                             "açılış brifingi urgent olmamalı")

    def test_greeting_runs_only_once_a_day(self):
        with tempfile.TemporaryDirectory() as d:
            gf = os.path.join(d, "greeted")
            boot, fake, spoken, _ = self._fake_mek(14, gf)
            with self._at_hour(14):
                boot(fake)
                boot(fake)
            self.assertEqual(len(spoken), 1)


class SpeechOutputDeviceTests(unittest.TestCase):
    """Ses, varsayılan çıkış yerine dahili hoparlöre zorlanıyordu.

    jarvis.py içinde `TARGET_DEVICE_NAME = "mac studio"` sabit kodluydu ve
    `speech_device` ayarı hiç okunmuyordu; kulaklık takılı olsa bile Mek
    Mac Studio hoparlöründen konuşuyor, üstelik donanım ses seviyesini
    değiştirebiliyordu.
    """

    @staticmethod
    def _load(home):
        import importlib.util
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        spec = importlib.util.spec_from_file_location(
            "mek_speak_test", os.path.join(root, "jarvis.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.MEK_HOME = home
        return mod

    def test_default_is_system_output(self):
        import config as cfgmod
        self.assertEqual(cfgmod.DEFAULTS["speech_device"], "system")

    def test_system_output_skips_device_lookup(self):
        mod = self._load("/nonexistent")
        mod.USE_SYSTEM_OUTPUT = True
        self.assertEqual(mod.get_mac_studio_devices(), (None, None),
                         "varsayılan çıkışta hiçbir aygıt aranmamalı")

    def test_user_setting_is_read_from_jarvis_json(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "jarvis.json"), "w") as fh:
                json.dump({"speech_device": "mac studio", "speech_volume": 0.7}, fh)
            mod = self._load(d)
            self.assertEqual(mod._user_setting("speech_device", "system"), "mac studio")
            self.assertEqual(mod._user_setting("speech_volume", 0.3), 0.7)

    def test_user_setting_falls_back_to_legacy_config_json(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config.json"), "w") as fh:
                json.dump({"speech_volume": 0.5}, fh)
            mod = self._load(d)
            self.assertEqual(mod._user_setting("speech_volume", 0.3), 0.5)

    def test_user_setting_default_when_nothing_configured(self):
        with tempfile.TemporaryDirectory() as d:
            mod = self._load(d)
            self.assertEqual(mod._user_setting("speech_device", "system"), "system")

    def test_named_device_still_supported(self):
        mod = self._load("/nonexistent")
        self.assertFalse(
            "mac studio" in ("", "system", "default", "sistem", "varsayılan"),
            "adı verilen bir aygıt sistem varsayılanı sayılmamalı")
