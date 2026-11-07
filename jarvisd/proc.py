# -*- coding: utf-8 -*-
"""Mek için güvenli alt süreç yardımcıları.

Mek bir arka plan servisidir: zaman aşımı olmayan tek bir `subprocess.run`
çağrısı, o işi yapan iş parçacığını kalıcı olarak kilitler. En sık karşılaşılan
örnek, kaydedilmemiş değişikliği olan Premiere Pro'ya gönderilen
``tell application "..." to quit`` çağrısıdır: uygulama kaydetme penceresi
açar ve ``osascript`` yanıt gelene kadar hiç dönmez.

Bu modül her çağrıya varsayılan bir zaman aşımı koyar ve zaman aşımını bir
istisna yerine ``returncode = 124`` olan normal bir sonuç nesnesine çevirir
(``timeout`` komutunun davranışı), böylece çağıran taraf akışı bozulmadan
devam eder.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger("mek.proc")

#: Komut belirtmezse kullanılan zaman aşımı (saniye).
DEFAULT_TIMEOUT = 20
#: Zaman aşımına uğrayan komutun döndürdüğü kod (``timeout(1)`` ile aynı).
TIMEOUT_RETURNCODE = 124


def run(cmd, timeout=DEFAULT_TIMEOUT, check=False, **kwargs):
    """``subprocess.run`` sarmalayıcısı: zaman aşımı zorunlu, istisna atmaz.

    Zaman aşımında veya komut bulunamadığında ``returncode`` sıfırdan farklı
    olan bir ``CompletedProcess`` döner; ``stdout``/``stderr`` istenmişse boş
    metin olur. Böylece çağıran kod her zaman aynı biçimde sonuç okuyabilir.
    """
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    text = kwargs.get("text", False)
    empty = "" if text else b""
    try:
        return subprocess.run(cmd, timeout=timeout, check=check, **kwargs)
    except subprocess.TimeoutExpired as exc:
        log.warning("Komut %s sn içinde yanıt vermedi, iptal edildi: %s",
                    timeout, _describe(cmd))
        return subprocess.CompletedProcess(
            cmd, TIMEOUT_RETURNCODE,
            _as(exc.stdout, empty), _as(exc.stderr, empty))
    except FileNotFoundError:
        log.warning("Komut bulunamadı: %s", _describe(cmd))
        return subprocess.CompletedProcess(cmd, 127, empty, empty)
    except PermissionError:
        log.warning("Komut çalıştırma izni yok: %s", _describe(cmd))
        return subprocess.CompletedProcess(cmd, 126, empty, empty)
    except subprocess.CalledProcessError:
        raise
    except OSError as exc:
        log.warning("Komut çalıştırılamadı (%s): %s", exc, _describe(cmd))
        return subprocess.CompletedProcess(cmd, 1, empty, empty)


def quiet(cmd, timeout=DEFAULT_TIMEOUT, **kwargs):
    """Çıktısı ilgilenmediğimiz komutlar için ``run`` kısayolu."""
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    return run(cmd, timeout=timeout, **kwargs)


def ok(cmd, timeout=DEFAULT_TIMEOUT, **kwargs):
    """Komut başarıyla (0 ile) bitti mi?"""
    return quiet(cmd, timeout=timeout, **kwargs).returncode == 0


def have(tool):
    """Komut PATH üzerinde var mı? (``shutil.which`` kısayolu)"""
    return shutil.which(tool) is not None


def temp_wav(prefix):
    """Benzersiz, yalnızca kullanıcının okuyabildiği geçici bir .wav yolu üretir.

    Eskiden ``/tmp/mek_vh_<hash(path) % 1000000>.wav`` kullanılıyordu. Bu üç
    açıdan hatalıydı: ``hash()`` süreçler arasında değişir (PYTHONHASHSEED),
    bir milyonluk alan eşzamanlı işlerde çakışır ve /tmp'de tahmin edilebilir
    bir ad sembolik bağ saldırısına açıktır.
    """
    fd, path = tempfile.mkstemp(prefix=f"mek_{prefix}_", suffix=".wav")
    os.close(fd)
    return path


def ffmpeg_to_wav(src, dst, timeout=300):
    """Sesi 16 kHz tek kanallı WAV'a çevirir. Başarılıysa True döner."""
    if not have("ffmpeg"):
        log.warning("ffmpeg kurulu değil; ses dönüştürülemedi: %s", src)
        return False
    r = quiet(["ffmpeg", "-nostdin", "-y", "-i", src,
               "-ac", "1", "-ar", "16000", "-vn", dst], timeout=timeout)
    if r.returncode != 0:
        log.warning("ffmpeg dönüşümü başarısız (kod %s): %s", r.returncode, src)
        return False
    return os.path.isfile(dst) and os.path.getsize(dst) > 0


def capture_with_input(cmd, text_input, timeout):
    """Komuta stdin'den metin verip çıktısını toplar; zaman aşımında öldürür.

    Dönüş: (stdout, stderr, returncode). Süreç başlatılamazsa ya da zaman
    aşımına uğrarsa returncode sıfırdan farklıdır.
    """
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    except (OSError, ValueError) as exc:
        log.warning("Süreç başlatılamadı (%s): %s", exc, _describe(cmd))
        return "", str(exc), 127
    try:
        out, err = p.communicate(input=text_input, timeout=timeout)
        return out or "", err or "", p.returncode
    except subprocess.TimeoutExpired:
        log.warning("Süreç %s sn içinde yanıt vermedi, durduruluyor: %s",
                    timeout, _describe(cmd))
        p.kill()
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return "", "timeout", TIMEOUT_RETURNCODE
    finally:
        if p.poll() is None:
            p.kill()
        for stream in (p.stdin, p.stdout, p.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


def capture_background(cmd, timeout, low_priority=True):
    """Uzun süren bir taramayı E-çekirdeklerde çalıştırıp çıktısını toplar.

    ``Popen.communicate(timeout=...)`` zaman aşımına uğradığında alt süreci
    ÖLDÜRMEZ; çağıran taraf bunu yapmazsa clamscan gibi ağır bir tarama
    arka planda süresiz çalışmaya devam eder ve boru hattı sızar. Bu yardımcı
    her durumda süreci sonlandırır.

    Dönüş: (çıktı_metni, zaman_aşımına_uğradı_mı)
    """
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL, text=True)
    except (OSError, ValueError) as exc:
        log.warning("Tarama başlatılamadı (%s): %s", exc, _describe(cmd))
        return "", False
    if low_priority:
        quiet(["/usr/sbin/taskpolicy", "-b", "-p", str(p.pid)], timeout=10)
    try:
        out, _ = p.communicate(timeout=timeout)
        return out or "", False
    except subprocess.TimeoutExpired:
        log.warning("Tarama %s sn içinde bitmedi, durduruluyor: %s", timeout, _describe(cmd))
        p.kill()
        try:
            out, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = ""
        return out or "", True
    finally:
        if p.poll() is None:
            p.kill()
        for stream in (p.stdout, p.stderr, p.stdin):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


def _as(value, empty):
    return value if value is not None else empty


def _describe(cmd):
    if isinstance(cmd, (list, tuple)):
        return " ".join(str(c) for c in cmd)[:200]
    return str(cmd)[:200]
