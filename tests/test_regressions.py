# -*- coding: utf-8 -*-
"""Bu oturumda bulunup düzeltilen hatalar için gerileme (regression) testleri.

Her test sınıfı, gerçekten yaşanmış somut bir arızayı kilitler.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "jarvisd"))

import config  # noqa: E402
import proc  # noqa: E402
import security  # noqa: E402


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
