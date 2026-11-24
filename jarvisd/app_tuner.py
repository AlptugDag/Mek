# -*- coding: utf-8 -*-
"""Kurgu ve Render Motorları İçin Sürümden Bağımsız İç Yapılandırma İyileştiricisi (App Tuner).

Premiere Pro, After Effects, Media Encoder, Audition, Photoshop, DynamicLink, DaVinci Resolve ve
Topaz Video uygulamalarının iç motor parametrelerini (Debug Database.txt, XML tercihleri,
Multi-Frame Rendering, Apple Silicon Birleşik Bellek Metal GPU havuzları ve donanım decode/encode
ayarlarını) M1 Max (10 CPU çekirdeği, 32 GPU çekirdeği, 64 GB Birleşik Bellek ve Çift Medya
Motoru) mimarisi için en yüksek hız ve kararlılığa ayarlar.

Sürümden bağımsızdır: 2025, 2026, 2027, 2030 veya gelecekte kurulacak tüm sürüm klasörlerini
otomatik tarar ve yalnızca ilgili uygulama kapalıyken atomik biçimde günceller.
"""
import glob
import logging
import os
import re
import subprocess
import tempfile

import psutil

log = logging.getLogger("mek.app_tuner")
HOME = os.path.expanduser("~")

# Adobe Debug Database.txt içinde M1 Max 64 GB birleşik bellek ve Metal GPU mimarisi için
# çift havuzlamayı (OOM/çökme) önleyen, kararlı ve en yüksek hızlı parametreler.
ADOBE_DEBUG_DB_OVERRIDES = {
    # After Effects / DynamicLink / MOGRT render'larını tek kareye (1) kilitleyen darboğazı kaldır:
    "ExporterCalcMaxRequests.OverrideMaxRequests": "",
    # Apple Silicon Birleşik Bellek + Metal (çift havuzlamayı önleyerek RAM taşması ve çökmeyi keser):
    "GF.DisableMemoryPooling": "true",
    "GF.UseMetalHeap": "false",
    "GF.Metal": "true",
    "GF.ForceCPU": "false",
    "GF.Lockless": "true",
    "GF.EnableZeroCopy": "true",
    "Afterburner.EnableZeroCopy": "false",
    "Lumetri.Metal": "true",
    "EnableGPUDebayer": "true",
    "ImmersiveVideo.UseMetal": "true",
    "GF.Metal.EnableFlushlessEvent": "true",
    "GF.Metal.Catalina16BitWorkaround": "false",
    "GF.DisableAcceleratedRenderer": "false",
    "GF.DisableAcceleratedDisplay": "false",
    "GF.DisableGPUFrameCache": "false",
    "GF.MaxTextureMemPercentage": "80",
    "ExecuteKernel Async": "true",
    "DisplaySurface.PresentAsync": "true",
    "DS.UseGPUSync": "false",
    # M1 Max Çift Medya Motoru için Dengeli Decode, Scrubbing ve Asenkron Kare Kuyrukları:
    "CodecSupport.MCDecoderPoolSize": "6",
    "MediaCoreAsyncFrameCount3": "16",
    "PlayerMain.MaxOutstandingPendingAheadOfPlayback": "16",
    "LowLevel Timecode hardware queue depth": "16",
    "Async Read Buffer Allocation Size": "16777216",
    "DecodeQueue.IOThreadsPerQueue": "8",
    "ImporterMPEG.UseAsyncDecode": "true",
    "Multiple Decodes Experimental": "true",
    "UseAsyncExport": "true",
    "GF.DeviceAssetCachePercent": "20",
    "GF.DeviceSoftPoolFactor": "50",
    "GF.HostPoolFactor": "10",
    "DVACompute.ShouldUseObjectSelectionAsync": "true",
    # CoreML ve ONNX Runtime AI Efektlerini 1. Nesil Yavaş ANE Yerine 32-Core Metal GPU'da Çalıştır:
    "dvaml.CoreML.ComputeUnitOverride": "1",
    "dvaml.CoreML.AllowLowPrecisionAccumulationOnGPU": "true",
    "dvaml.EnableORTGPUBufferDispatch": "true",
    "dvaml.OverwriteORTThreadPool": "8",
    "dvaai.UseGPU": "true",
    "dvaaccelerate.UseMetal": "true",
    # Uygulama Açılışı ve Eklenti/Ses Tarama Hızlandırıcıları:
    "ML.UseKnownPluginsCache": "true",
    "ML.LoadTestingPluginsAtStartup": "false",
    "dvaaudiofilterhost.EnableQuickEffectScan": "true",
    "dvaaudiofilterhost.EnableWavesVST2ShellSkipping": "true",
    "EA.MediaFinder.EnableSyncDirectoryScanForFastMediaSearch": "true",
    # Kurgu ve Render Sırasında Zaman Çizelgesini Yavaşlatan İç İzleme/Telemetri Kayıtlarını Kapat:
    "BeePostedCmdTraceMode (0:None,1:Posted,2:PostedDelta,3:PostedDeltaAndDecode)": "0",
    "CrashReporterSentry.EnableSymbolicatedStackTrace": "false",
    "CrashReporterSentry.EnableSentryStackTrace": "false",
    "GPUAnalytics.EnableSDMLogging": "false",
    "CutlistCreationInstrumentation.Enable": "false",
    "CreateLogFilesThatDoNotExist": "false",
    "GF.ProfileKernels": "false",
    "GF.LockTimings": "false",
}

# Adobe Premiere Pro ve Media Encoder XML tercih dosyalarında uygulanacak performans etiketleri:
PREMIERE_XML_OVERRIDES = {
    "BE.Prefs.General.RenderOptimization": "0",  # 0 = Performans için optimize et
    "Project.Prefs.EnablehHWAcceleratedDecode": "true",
    "Project.Prefs.EnablehHWAcceleratedEncode": "true",
    "Project.Prefs.EnablehHWAcceleratedProResEncode": "true",
    "BE.Prefs.Audio.MultithreadedPeakfileGeneration": "true",
    "BE.Prefs.Undo.MaxLevels": "100",            # 1000 yerine 100 (her kesmede sekans kopyalama yükünü 10x azaltır)
    "BE.Prefs.AutoSave.Interval": "15",          # 5 dk yerine 15 dk (ana iş parçacığı duraksamasını azaltır)
    "BE.Prefs.AutoSave.DoSaveToCloud": "false",
    "BE.Prefs.Media.CRValidateOnImport": "false",
    "BE.Prefs.Media.ScanCROnImport": "false",
    "BE.Prefs.Media.ScanFilesForCROnImport": "false",
    "BE.Prefs.Transcription.RunBackgroundTranscription": "false",
    "BE.Prefs.MediaIntelligence.AnalyzeImportedMediaForMISO": "false",
    "BE.Prefs.MediaIntelligence.AnalyzeImportedMediaForFace": "false",
}

# DaVinci Resolve config.user.xml performans etiketleri:
RESOLVE_USER_XML_OVERRIDES = {
    "IsStopRenderOnError": "false",
    "PerfModeOptDecode": "true",
    "FusionMemoryCacheMode": "FUSION_MEMORY_CACHE_MODE_AUTO",
    "IsOptimizeUiUpdatesForAudioPlugins": "true",
    "IsCopyRenderCacheWhenDupTimelines": "false",
    "AutoCacheDeleteMode": "1",
}

# Topaz Video performans defaults anahtarları:
TOPAZ_DEFAULTS_OVERRIDES = {
    "gpuDeviceID": 0,
    "hardwareDecodingEnabled": 1,
    "maxProcesses": 1,
    "powerSaverMode": 0,
    "liveExportEnabled": 0,
    "exportWithLivePreviewEnabled": 0,
    "fileLoggingEnabled": 0,
    "allowAnonymousDataCollection": 0,
}


def _running_adobe_families():
    """Şu anda açık olan kurgu uygulama ailelerini döndürür (açık uygulamanın dosyasına dokunulmaz)."""
    open_fams = set()
    _ignore_helpers = ("crashpad_handler", "resolvepython", "logtransport", "adobe crash processor")
    for p in psutil.process_iter(["name", "exe"]):
        try:
            name = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(h in name or h in exe for h in _ignore_helpers):
            continue
        target = f"{name} {exe}"
        if "premiere" in target or "pproheadless" in target:
            open_fams.add("premiere")
        if "after effects" in target or "aerender" in target:
            open_fams.add("aftereffects")
        if "media encoder" in target:
            open_fams.add("ame")
        if "audition" in target:
            open_fams.add("audition")
        if "photoshop" in target:
            open_fams.add("photoshop")
        if "/contents/macos/resolve" in exe or name in ("resolve", "davinci resolve"):
            open_fams.add("resolve")
        if "topaz video" in target or ("topaz" in target and "veai" in target):
            open_fams.add("topaz")
    return open_fams


def _family_of_path(path):
    low = path.lower()
    if "premiere pro" in low:
        return "premiere"
    if "after effects" in low:
        return "aftereffects"
    if "media encoder" in low:
        return "ame"
    if "audition" in low:
        return "audition"
    if "/ps/" in low or "photoshop" in low:
        return "photoshop"
    if "davinci resolve" in low:
        return "resolve"
    return None


def _atomic_write(path, content, encoding="utf-8"):
    d = os.path.dirname(path) or "."
    st = os.stat(path) if os.path.exists(path) else None
    fd, tmp = tempfile.mkstemp(prefix=".mek_tune_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if st is not None:
            os.chmod(tmp, st.st_mode & 0o777)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def tune_debug_database_file(path, overrides=None):
    """Tek bir Adobe Debug Database.txt dosyasını güvenli biçimde günceller."""
    overrides = overrides or ADOBE_DEBUG_DB_OVERRIDES
    try:
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
            raw = f.read()
    except OSError:
        return 0

    line_sep = "\r\n" if "\r\n" in raw else ("\r" if "\r" in raw and "\n" not in raw else "\n")
    lines = raw.splitlines()
    changed = 0
    new_lines = []

    for line in lines:
        if "\t" not in line:
            new_lines.append(line)
            continue
        parts = line.split("\t")
        key = parts[0].strip()
        if key in overrides and len(parts) >= 2:
            desired = overrides[key]
            if parts[1] != desired:
                parts[1] = desired
                changed += 1
                line = "\t".join(parts)
        new_lines.append(line)

    if changed > 0:
        out = line_sep.join(new_lines)
        if raw.endswith(("\n", "\r")):
            out += line_sep
        _atomic_write(path, out)
    return changed


def _latest_installed_premiere_app(apps_dir="/Applications"):
    """Kurulu en güncel Adobe Premiere Pro .app yolunu bulur (2026, 2027, 2030 vb. sürümden bağımsız)."""
    candidates = sorted(
        glob.glob(os.path.join(apps_dir, "Adobe Premiere Pro*", "Adobe Premiere Pro*.app"))
        + glob.glob(os.path.join(apps_dir, "Adobe Premiere Pro*.app"))
    )
    return candidates[-1] if candidates else None


def tune_premiere_prefs_file(path, latest_app=None):
    """Adobe Premiere Pro Prefs veya Adobe Media Encoder Prefs XML dosyasını optimize eder."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
            raw = f.read()
    except OSError:
        return 0

    updated = raw
    changes = 0
    for tag, val in PREMIERE_XML_OVERRIDES.items():
        pat = re.compile(rf"(<{re.escape(tag)}>)(.*?)(</{re.escape(tag)}>)", re.S)
        new_text, count = pat.subn(
            lambda m: m.group(0) if m.group(2).strip() == val else f"{m.group(1)}{val}{m.group(3)}",
            updated,
        )
        if new_text != updated:
            updated = new_text
            changes += count

    # CoreAudio kesintisiz oynatma (dropout/cızırtı önleyici blok boyutu ve çıkış kipi):
    new_text, n_blk = re.subn(
        r'("abe\.io\.audio\.coreaudio\.ioblocksize":\s*)(?!1024\b)\d+',
        r"\g<1>1024",
        updated,
    )
    if n_blk:
        updated = new_text
        changes += n_blk

    new_text, n_out = re.subn(
        r'("abe\.io\.audio\.outputonly":\s*)false\b',
        r"\g<1>true",
        updated,
    )
    if n_out:
        updated = new_text
        changes += n_out

    # Eski sürümden (2023/2024 vb.) kalmış kırık uygulama içi dosya yollarını güncel sürüme onar:
    if latest_app is None:
        latest_app = _latest_installed_premiere_app()
    if latest_app and os.path.exists(latest_app):
        def _fix_old_app_path(match):
            nonlocal changes
            old_app = match.group(0)
            if old_app != latest_app and not os.path.exists(old_app):
                changes += 1
                return latest_app
            return old_app

        updated = re.sub(
            r"/Applications/Adobe Premiere Pro [^/<]+/Adobe Premiere Pro [^/<]+\.app",
            _fix_old_app_path,
            updated,
        )

    if changes > 0 and updated != raw:
        _atomic_write(path, updated)
    return changes


def tune_after_effects_prefs_file(path):
    """Adobe After Effects Prefs.txt içinde Multi-Frame Rendering, 3D çoklu işlem ve telemetriyi iyileştirir."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
            raw = f.read()
    except OSError:
        return 0

    updated = raw
    changes = 0
    replacements = (
        (r'("Enable Concurrent Frame Renders"\s*=\s*)00', r"\g<1>01"),
        (r'("Log BEE Commands Into AdobePIP"\s*=\s*)01', r"\g<1>00"),
        (r'("Enable Multiprocessing"\s*=\s*)"0"', r'\g<1>"1"'),
        # 10 çekirdekli M1 Max'te arayüz/WindowServer/CoreAudio ve paralel kurgu uygulamaları donmadan en yüksek MFR verimi (%20 = 2 çekirdek UI rezervi):
        (r'("Reserved CPU Power"\s*=\s*)"(?:0|10|2[5-9]|[3-9]\d)"', r'\g<1>"20"'),
    )
    for pattern, repl in replacements:
        new_text, count = re.subn(pattern, repl, updated)
        if new_text != updated:
            updated = new_text
            changes += count

    if changes > 0 and updated != raw:
        _atomic_write(path, updated)
    return changes


def tune_resolve_prefs(home=HOME):
    """DaVinci Resolve config.user.xml ve config.dat performans parametrelerini iyileştirir."""
    pref_dir = os.path.join(home, "Library", "Preferences", "Blackmagic Design", "DaVinci Resolve")
    if not os.path.isdir(pref_dir):
        return 0
    changes = 0
    user_xml = os.path.join(pref_dir, "config.user.xml")
    if os.path.isfile(user_xml):
        try:
            with open(user_xml, "r", encoding="utf-8", errors="ignore", newline="") as f:
                raw = f.read()
            updated = raw
            for tag, val in RESOLVE_USER_XML_OVERRIDES.items():
                pat = re.compile(rf"(<{re.escape(tag)}>)(.*?)(</{re.escape(tag)}>)", re.S)
                new_text, count = pat.subn(
                    lambda m: m.group(0) if m.group(2).strip() == val else f"{m.group(1)}{val}{m.group(3)}",
                    updated,
                )
                if new_text != updated:
                    updated = new_text
                    changes += count
            if updated != raw:
                _atomic_write(user_xml, updated)
        except OSError:
            pass

    resolve_cfg_rules = {
        "config.dat": {
            "Bg.Tasks.EnableBgTranscription": "0",
            "Bg.Tasks.EnableBackgroundRenders": "0",
            "Bg.Tasks.PauseBGRenderOnFGActivity": "1",
            # Local.DNE.Mode kullanıcının Resolve arayüzünden seçtiği (ör. GPU / Auto) değerde bırakılır
            "Local.MPSGraph.Enable": "1",
            "Local.Resource.ResolveMemoryPercentage": "85",
            "Local.Resource.FusionMemoryPercentage": "15",
            # Buffers.1: memory_budget.py dinamik yönetir (sabit 47349 onunla çakışıyordu)
        },
        ".config.data": {
            "Bg.Tasks.EnableBgTranscription": "0",
            "Bg.Tasks.EnableBackgroundRenders": "0",
            "Bg.Tasks.PauseBGRenderOnFGActivity": "1",
            # DaVinciNeuralEngine kullanıcının Resolve arayüzünden seçtiği değerde bırakılır
            "ResolveMemoryUsageInPercentage": "85",
            "FusionMemoryUsageInPercentage": "15",
            # Memory: memory_budget.py dinamik yönetir
        },
    }
    for cfg_name, rules in resolve_cfg_rules.items():
        cfg_path = os.path.join(pref_dir, cfg_name)
        if not os.path.isfile(cfg_path):
            continue
        try:
            with open(cfg_path, "r", encoding="latin-1", newline="") as f:
                raw = f.read()
            new_text = raw
            file_changes = 0
            for k, v in rules.items():
                pat = re.compile(rf"^{re.escape(k)}[ \t]*=[ \t]*(.*)$", flags=re.M)
                def _repl(m, key=k, val=v):
                    nonlocal file_changes
                    if m.group(1).strip() == val and m.group(0) == f"{key} = {val}":
                        return m.group(0)
                    file_changes += 1
                    return f"{key} = {val}"
                new_text = pat.sub(_repl, new_text)
            if file_changes and new_text != raw:
                _atomic_write(cfg_path, new_text, encoding="latin-1")
                changes += file_changes
        except OSError:
            pass
    return changes


def tune_topaz_prefs(runner=subprocess.run):
    """Topaz Video donanım çözme ve güç verimliliği ayarlarını (uygulama kapalıyken) optimize eder."""
    domain = "com.topazlabs.Topaz Video"
    try:
        probe = runner(["/usr/bin/defaults", "read", domain, "gpuDeviceID"],
                       capture_output=True, text=True, timeout=5)
        if probe.returncode != 0:
            return 0
    except Exception:
        return 0

    changed = 0
    for key, val in TOPAZ_DEFAULTS_OVERRIDES.items():
        try:
            cur = runner(["/usr/bin/defaults", "read", domain, key],
                         capture_output=True, text=True, timeout=5)
            if cur.returncode == 0 and cur.stdout.strip() == str(val):
                continue
            w = runner(["/usr/bin/defaults", "write", domain, key, "-int", str(val)],
                       capture_output=True, text=True, timeout=5)
            if w.returncode == 0:
                changed += 1
        except Exception:
            continue
    return changed


def tune_all(home=HOME, apps_dir="/Applications", open_families=None):
    """Tüm kurgu uygulamalarının (tüm mevcut ve gelecek sürümlerinin) iç motor ayarlarını optimize eder."""
    if open_families is None:
        open_families = _running_adobe_families()

    summary = {
        "debug_db_files": 0,
        "debug_db_keys": 0,
        "xml_files": 0,
        "xml_keys": 0,
        "ae_files": 0,
        "resolve_keys": 0,
        "topaz_keys": 0,
    }
    adobe_prefs_root = os.path.join(home, "Library", "Preferences", "Adobe")
    adobe_docs_root = os.path.join(home, "Documents", "Adobe")

    # 1. Tüm Adobe Debug Database.txt dosyaları (Premiere, AE, Media Encoder, Audition, PS, DynamicLink)
    for db_path in glob.glob(os.path.join(adobe_prefs_root, "*", "*", "Debug Database.txt")):
        fam = _family_of_path(db_path)
        if fam and fam in open_families:
            continue
        n = tune_debug_database_file(db_path)
        if n > 0:
            summary["debug_db_files"] += 1
            summary["debug_db_keys"] += n

    # 2. Premiere Pro ve Media Encoder XML tercihleri (tüm sürümler ve profiller)
    latest_pr = _latest_installed_premiere_app(apps_dir=apps_dir)
    xml_patterns = (
        os.path.join(adobe_docs_root, "Premiere Pro", "*", "Profile-*", "Adobe Premiere Pro Prefs"),
        os.path.join(adobe_docs_root, "Adobe Media Encoder", "*", "Adobe Media Encoder Prefs"),
    )
    for pat in xml_patterns:
        for xml_path in glob.glob(pat):
            fam = _family_of_path(xml_path)
            if fam and fam in open_families:
                continue
            n = tune_premiere_prefs_file(xml_path, latest_app=latest_pr)
            if n > 0:
                summary["xml_files"] += 1
                summary["xml_keys"] += n

    # 3. After Effects Prefs.txt dosyaları (tüm sürümler)
    if "aftereffects" not in open_families:
        for ae_path in glob.glob(os.path.join(adobe_prefs_root, "After Effects", "*", "Adobe After Effects * Prefs.txt")):
            if tune_after_effects_prefs_file(ae_path) > 0:
                summary["ae_files"] += 1

    # 4. DaVinci Resolve tercihleri + BorisFX Continuum OFX 4K GPU tamponu
    if "resolve" not in open_families:
        summary["resolve_keys"] = tune_resolve_prefs(home=home)
        for bcc_xml in glob.glob(os.path.join(home, "Library", "Preferences", "com.borisfx.Continuum*.filter.ofx.xml")):
            try:
                raw_bcc = open(bcc_xml, "r", encoding="utf-8", errors="ignore", newline="").read()
                new_bcc, n_bcc = re.subn(
                    r'(Pref_Name="Use 4k Gpu Buffers"[^>]*Pref_Value=")0(")',
                    r"\g<1>1\g<2>",
                    raw_bcc,
                )
                if n_bcc and new_bcc != raw_bcc:
                    _atomic_write(bcc_xml, new_bcc)
                    summary["resolve_keys"] += n_bcc
            except OSError:
                pass

    # 5. Topaz Video tercihleri (yalnızca gerçek sistem dizininde çalışırken)
    if home == HOME and "topaz" not in open_families:
        summary["topaz_keys"] = tune_topaz_prefs()

    if any(summary.values()):
        log.info("Kurgu motoru iç parametreleri optimize edildi: %s", summary)
    return summary
