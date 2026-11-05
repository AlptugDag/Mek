# -*- coding: utf-8 -*-
"""Kurgu ve Render Motorları İçin Sürümden Bağımsız İç Yapılandırma İyileştiricisi (App Tuner).

Premiere Pro, After Effects, Media Encoder, Audition, Photoshop, DynamicLink, DaVinci Resolve ve
Topaz Video AI uygulamalarının kendi iç motor parametrelerini (Debug Database.txt, XML tercihleri,
Multi-Frame Rendering ve Metal GPU havuz ayarlarını) M1 Max (10 CPU çekirdeği, 32 GPU çekirdeği,
64 GB Birleşik Bellek ve Çift ProRes Motoru) mimarisi için en yüksek hıza ayarlar.

Sürümden bağımsızdır: 2025, 2026, 2030 veya gelecekte kurulacak tüm sürüm klasörlerini otomatik
tarar ve yalnızca ilgili uygulama kapalıyken güvenli biçimde günceller.
"""
import glob
import logging
import os
import re
import tempfile

import psutil

log = logging.getLogger("mek.app_tuner")
HOME = os.path.expanduser("~")

# Adobe Debug Database.txt içinde M1 Max birleşik bellek ve Metal GPU mimarisi için
# en yüksek kurgu, önizleme, decode ve render hızını sağlayan anahtar-değer çiftleri.
ADOBE_DEBUG_DB_OVERRIDES = {
    # After Effects / DynamicLink / MOGRT render'larını tek kareye (1) kilitleyen darboğazı kaldır:
    "ExporterCalcMaxRequests.OverrideMaxRequests": "",
    # Apple Silicon Birleşik Bellek + Metal Heap + Sıfır Kopya (Zero-Copy) GPU Havuzu:
    "GF.DisableMemoryPooling": "false",
    "GF.UseMetalHeap": "true",
    "GF.Metal": "true",
    "GF.EnableZeroCopy": "true",
    "Afterburner.EnableZeroCopy": "true",
    "Lumetri.Metal": "true",
    "EnableGPUDebayer": "true",
    "GF.Metal.EnableFlushlessEvent": "true",
    "GF.Metal.Catalina16BitWorkaround": "false",
    "GF.DisableAcceleratedRenderer": "false",
    "GF.DisableAcceleratedDisplay": "false",
    "GF.DisableGPUFrameCache": "false",
    # M1 Max 64 GB RAM + Çift Medya Motoru için Decode, Önizleme ve Asenkron Kare Kuyrukları:
    "CodecSupport.MCDecoderPoolSize": "12",
    "MediaCoreAsyncFrameCount3": "32",
    "PlayerMain.MaxOutstandingPendingAheadOfPlayback": "24",
    "DecodeQueue.IOThreadsPerQueue": "16",
    "GF.DeviceAssetCachePercent": "45",
    "GF.DeviceSoftPoolFactor": "75",
    "GF.HostPoolFactor": "30",
    "DVACompute.ShouldUseObjectSelectionAsync": "true",
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


def _running_adobe_families():
    """Şu anda açık olan Adobe uygulama ailelerini döndürür (açık uygulamanın dosyasına dokunulmaz)."""
    open_fams = set()
    for p in psutil.process_iter(["name", "exe"]):
        try:
            target = f"{p.info.get('name') or ''} {p.info.get('exe') or ''}".lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
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
    return None


def _atomic_write(path, content, encoding="utf-8"):
    d = os.path.dirname(path)
    st = os.stat(path) if os.path.exists(path) else None
    fd, tmp = tempfile.mkstemp(prefix=".mek_tune_", dir=d)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
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
            # ExporterCalcMaxRequests.OverrideMaxRequests gibi varsayılanı boş olan bayraklarda
            # varsayılan sütunu varsa onu koru, birinci değer sütununu desired yap:
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
    """Kurulu en güncel Adobe Premiere Pro .app yolunu bulur (2026, 2030 vb. sürümden bağımsız)."""
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
    """Adobe After Effects Prefs.txt içinde Multi-Frame Rendering ve telemetri ayarlarını iyileştirir."""
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
    )
    for pattern, repl in replacements:
        new_text, count = re.subn(pattern, repl, updated)
        if new_text != updated:
            updated = new_text
            changes += count

    if changes > 0 and updated != raw:
        _atomic_write(path, updated)
    return changes


def tune_all(home=HOME, apps_dir="/Applications", open_families=None):
    """Tüm kurgu uygulamalarının (tüm mevcut ve gelecek sürümlerinin) iç motor ayarlarını optimize eder."""
    if open_families is None:
        open_families = _running_adobe_families()

    summary = {"debug_db_files": 0, "debug_db_keys": 0, "xml_files": 0, "xml_keys": 0, "ae_files": 0}
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

    if any(summary.values()):
        log.info("Kurgu motoru iç parametreleri optimize edildi: %s", summary)
    return summary
