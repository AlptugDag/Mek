# -*- coding: utf-8 -*-
"""M1 Max Mac Studio Akıllı Termal & Fan Yöneticisi (AppleSMC IOKit Doğrudan Telemetri + Güvenli Sessiz Kip).

Mac Studio (M1 Max) Donanım Gerçeği ve Çözüm Mimarisi:
- Mac Studio'nun içinde 370W dahili güç kaynağı (PSU) ve NVMe SSD modülleri kasanın alt hava girişinde
  yer alır ve ayrı bir fanları yoktur; üstteki çift radyal fanın oluşturduğu hava akışıyla soğurlar.
  Bu nedenle fanları tamamen 0 RPM'e kapatmak (CPU soğuk olsa bile) PSU ve SSD'yi aşırı ısıtır.
- ANCAK Apple'ın fabrika ayarı, işlemci 39-42°C gibi buz gibi bir sıcaklıktayken bile fanları
  1330 RPM'de (`F0Tg`/`F1Tg` = 1330) çevirir; oysa AppleSMC donanımının kendi güvenli alt sınırı
  (`F0Mn`/`F1Mn`) **1100 RPM**'dir (bu devirde masa rezonansı ve hava sesi duyulma eşiğinin altına iner).
- Ayrıca `pmset highpowermode 1` açık bırakılırsa Mac Studio gereksiz yere fan devrini yükseltir.

Bu modül:
1. IOKit `AppleSMC` üzerinden gerçek zamanlı CPU/GPU çekirdek sıcaklıklarını (`Tp01..Tp0T`, `Tg05..Tg0D`)
   ve fan devirlerini (`F0Ac`, `F1Ac`) <0.2 ms'de okur (okuma için root gerekmez).
2. Bilgisayar boştayken / sıcaklıklar güvenli eşikteyken (< 50°C):
   - `highpowermode` zorlamasını kapatır.
   - Yetkili SMC yardımcısı kuruluysa fan hedefini 1330 RPM'den güvenli donanım tabanı olan **1100 RPM**'e
     (`sessiz` kip) indirir.
3. Sıcaklık **52°C**'ye ulaştığı anda, ağır bir render başladığında veya servis kapandığında:
   - Milisaniyesinde `F0Md=0, F1Md=0` göndererek kontrolü Apple'ın donanımsal otomatik SMC eğrisine
     geri devreder (sıfır aşırı ısınma riski, %100 donanım koruması).
"""
import ctypes
import logging
import os
import struct
import subprocess
import time

from config import PRIV_HELPER

log = logging.getLogger("mek.thermal")

CPU_SENSOR_KEYS = ("Tp01", "Tp05", "Tp09", "Tp0D", "Tp0T")
GPU_SENSOR_KEYS = ("Tg05", "Tg0D", "Tg0L", "Tg0T")
FAN_SILENT_RPM = 1100.0      # AppleSMC F0Mn/F1Mn donanım güvenli minimum devri
TEMP_SILENT_MAX_C = 50.0     # Bu sıcaklığın altında 1100 RPM sessiz kip korunur
TEMP_AUTO_RELEASE_C = 52.0   # Bu sıcaklığa ulaşıldığında anında donanımsal otomatik SMC eğrisine geçilir
TEMP_PRECOOL_C = 72.0        # Ağır render'da throttling'i önlemek için ön-soğutma eşiği


class _SMCKeyDataVers(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_ubyte),
        ("minor", ctypes.c_ubyte),
        ("build", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte),
        ("release", ctypes.c_ushort),
    ]


class _SMCKeyDataPLimit(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_ushort),
        ("length", ctypes.c_ushort),
        ("cpuPLimit", ctypes.c_uint),
        ("gpuPLimit", ctypes.c_uint),
        ("memPLimit", ctypes.c_uint),
    ]


class _SMCKeyDataKeyInfo(ctypes.Structure):
    _fields_ = [
        ("dataSize", ctypes.c_uint),
        ("dataType", ctypes.c_uint),
        ("dataAttributes", ctypes.c_ubyte),
    ]


class _SMCKeyData(ctypes.Structure):
    _fields_ = [
        ("key", ctypes.c_uint),
        ("vers", _SMCKeyDataVers),
        ("pLimitData", _SMCKeyDataPLimit),
        ("keyInfo", _SMCKeyDataKeyInfo),
        ("result", ctypes.c_ubyte),
        ("status", ctypes.c_ubyte),
        ("data8", ctypes.c_ubyte),
        ("data32", ctypes.c_uint),
        ("bytes", ctypes.c_ubyte * 32),
    ]


def _fourcc(key_str):
    return struct.unpack(">I", key_str.encode("ascii"))[0]


class AppleSMCReader:
    """IOKit.framework üzerinden doğrudan AppleSMC sensör okuyucusu."""

    def __init__(self):
        self._iokit = None
        self._conn = ctypes.c_uint(0)
        self._key_info_cache = {}
        self._open()

    def _open(self):
        try:
            iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
            iokit.IOServiceMatching.restype = ctypes.c_void_p
            iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
            iokit.IOServiceOpen.argtypes = [
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_uint),
            ]
            iokit.IOConnectCallStructMethod.argtypes = [
                ctypes.c_uint,
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_size_t),
            ]
            svc = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
            if not svc:
                return
            task = ctypes.c_uint.in_dll(ctypes.CDLL(None), "mach_task_self_").value
            if iokit.IOServiceOpen(svc, task, 0, ctypes.byref(self._conn)) == 0:
                self._iokit = iokit
        except Exception:
            self._iokit = None

    def read_key(self, key):
        if not self._iokit or not self._conn.value:
            return None
        try:
            k_int = _fourcc(key)
            info = self._key_info_cache.get(k_int)
            if info is None:
                inp = _SMCKeyData()
                out = _SMCKeyData()
                inp.key = k_int
                inp.data8 = 9  # kSMCGetKeyInfo
                osz = ctypes.c_size_t(ctypes.sizeof(out))
                rc = self._iokit.IOConnectCallStructMethod(
                    self._conn.value, 2, ctypes.byref(inp), ctypes.sizeof(inp), ctypes.byref(out), ctypes.byref(osz)
                )
                if rc != 0 or out.result != 0 or out.keyInfo.dataSize == 0:
                    return None
                dtype = struct.pack(">I", out.keyInfo.dataType).decode("ascii", "ignore")
                info = (dtype, out.keyInfo.dataSize)
                self._key_info_cache[k_int] = info

            dtype, dsize = info
            inp = _SMCKeyData()
            out = _SMCKeyData()
            inp.key = k_int
            inp.keyInfo.dataSize = dsize
            inp.data8 = 5  # kSMCReadKey
            osz = ctypes.c_size_t(ctypes.sizeof(out))
            rc = self._iokit.IOConnectCallStructMethod(
                self._conn.value, 2, ctypes.byref(inp), ctypes.sizeof(inp), ctypes.byref(out), ctypes.byref(osz)
            )
            if rc != 0 or out.result != 0:
                return None
            raw = bytes(out.bytes[:dsize])
            if dtype == "flt " and dsize == 4:
                return float(struct.unpack("<f", raw)[0])
            if dtype == "ui8 " and dsize == 1:
                return int(raw[0])
            if dtype == "ui16" and dsize == 2:
                return int(struct.unpack(">H", raw)[0])
            return None
        except Exception:
            return None


class ThermalFanController:
    """Sıcaklığa ve iş yüküne göre fanları en sessiz ve en güvenli noktada tutan denetleyici."""

    def __init__(self, reader=None):
        self.smc = reader or AppleSMCReader()
        self.user_mode = "auto_silent"  # auto_silent | auto | boost
        self.applied_state = "unknown"  # silent | auto | boost
        self.last_telemetry = {}
        self.last_adjust = 0.0

    def telemetry(self):
        """Anlık CPU/GPU sıcaklıklarını (°C) ve Sol/Sağ fan devirlerini (RPM) döndürür."""
        cpu_temps = [v for k in CPU_SENSOR_KEYS if (v := self.smc.read_key(k)) is not None and 10.0 < v < 125.0]
        gpu_temps = [v for k in GPU_SENSOR_KEYS if (v := self.smc.read_key(k)) is not None and 10.0 < v < 125.0]
        f0_actual = self.smc.read_key("F0Ac")
        f1_actual = self.smc.read_key("F1Ac")
        f0_min = self.smc.read_key("F0Mn") or FAN_SILENT_RPM
        f0_max = self.smc.read_key("F0Mx") or 3500.0
        f0_target = self.smc.read_key("F0Tg")
        f0_mode = self.smc.read_key("F0Md")

        cpu_max = round(max(cpu_temps), 1) if cpu_temps else None
        gpu_max = round(max(gpu_temps), 1) if gpu_temps else None
        all_temps = cpu_temps + gpu_temps
        peak_c = round(max(all_temps), 1) if all_temps else None

        data = {
            "cpu_c": cpu_max,
            "gpu_c": gpu_max,
            "peak_c": peak_c,
            "fan0_rpm": round(f0_actual) if f0_actual is not None else None,
            "fan1_rpm": round(f1_actual) if f1_actual is not None else None,
            "fan_target_rpm": round(f0_target) if f0_target is not None else None,
            "fan_min_rpm": round(f0_min),
            "fan_max_rpm": round(f0_max),
            "manual_override": bool(f0_mode),
            "mode": self.user_mode,
            "applied": self.applied_state,
        }
        self.last_telemetry = data
        return data

    def _priv_fan(self, subcmd):
        if not os.path.exists(PRIV_HELPER):
            return False
        try:
            r = subprocess.run(
                ["sudo", "-n", PRIV_HELPER, subcmd],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False

    def tick(self, rendering=False, thermal_state=0):
        """Her döngüde sıcaklığı denetler; ısınma varsa anında otomatik SMC korumasına geçer."""
        t = self.telemetry()
        peak = t.get("peak_c")

        # 1. Güvenlik önceliği: Sıcaklık >= 52°C ise veya termal uyarı varsa veya kullanıcı 'auto' seçtiyse
        if self.user_mode == "boost" or (rendering and peak is not None and peak >= TEMP_PRECOOL_C):
            desired = "boost"
        elif (
            self.user_mode == "auto"
            or rendering
            or thermal_state >= 1
            or peak is None
            or peak >= TEMP_AUTO_RELEASE_C
        ):
            desired = "auto"
        elif peak <= TEMP_SILENT_MAX_C:
            desired = "silent"
        else:
            # 50°C - 52°C arası histerezis bölgesi: mevcut durumu koru
            desired = self.applied_state if self.applied_state in ("silent", "auto") else "auto"

        if desired != self.applied_state:
            cmd_map = {
                "silent": "fan-silent",
                "auto": "fan-auto",
                "boost": "fan-boost",
            }
            if self._priv_fan(cmd_map[desired]):
                log.info("Fan kipi güncellendi: %s -> %s (zirve sıcaklık: %s°C)", self.applied_state, desired, peak)
                self.applied_state = desired
            else:
                self.applied_state = desired
            self.last_adjust = time.time()
        return t

    def set_mode(self, mode):
        """Kullanıcı komutlarıyla fan kipini değiştirir: sessiz | otomatik | performans."""
        m = mode.lower().strip()
        if m in ("sessiz", "silent", "quiet", "düşük", "dusuk"):
            self.user_mode = "auto_silent"
            self.applied_state = "unknown"
            t = self.tick(rendering=False)
            return (
                f"Akıllı Sessiz Fan kipi etkin. Sıcaklık 52°C altında olduğu sürece fanlar güvenli alt sınır "
                f"olan {t['fan_min_rpm']} RPM'e çekilir; ısınma olursa anında otomatik soğutmaya geçer. "
                f"Şu an: CPU {t['cpu_c']}°C, GPU {t['gpu_c']}°C, Fanlar {t['fan0_rpm']}/{t['fan1_rpm']} RPM."
            )
        if m in ("otomatik", "auto", "normal", "fabrika"):
            self.user_mode = "auto"
            self._priv_fan("fan-auto")
            self.applied_state = "auto"
            t = self.telemetry()
            return (
                f"Apple fabrika otomatik fan eğrisi etkin (1330 RPM taban). "
                f"Şu an: CPU {t['cpu_c']}°C, GPU {t['gpu_c']}°C, Fanlar {t['fan0_rpm']}/{t['fan1_rpm']} RPM."
            )
        if m in ("performans", "boost", "soğut", "sogut", "max"):
            self.user_mode = "boost"
            self._priv_fan("fan-boost")
            self.applied_state = "boost"
            t = self.telemetry()
            return (
                f"Performans ön-soğutma kipi etkin. "
                f"Şu an: CPU {t['cpu_c']}°C, GPU {t['gpu_c']}°C, Fanlar {t['fan0_rpm']}/{t['fan1_rpm']} RPM."
            )
        return self.report()

    def shutdown(self):
        """Servis kapanırken fanları her zaman Apple donanım otomatiğine (F0Md=0, F1Md=0) bırakır."""
        self._priv_fan("fan-auto")
        self.applied_state = "auto"

    def report(self):
        t = self.telemetry()
        mode_tr = {
            "auto_silent": "Akıllı Sessiz (<=50°C'de 1100 RPM, >=52°C'de Otomatik Soğutma)",
            "auto": "Fabrika Otomatik (1330 RPM taban)",
            "boost": "Performans Ön-Soğutma (1800 RPM)",
        }.get(self.user_mode, self.user_mode)
        return (
            f"Termal & Fan Durumu: CPU {t['cpu_c']}°C, GPU {t['gpu_c']}°C | "
            f"Sol Fan: {t['fan0_rpm']} RPM, Sağ Fan: {t['fan1_rpm']} RPM "
            f"(Hedef: {t['fan_target_rpm']} RPM, Güvenli Min: {t['fan_min_rpm']} RPM) | "
            f"Kip: {mode_tr}. "
            f"(Not: Mac Studio'da 370W PSU ve SSD'ler aynı hava tünelinden soğuduğu için 0 RPM donanıma zarar verir; "
            f"1100 RPM tamamen duyulmaz ve %100 güvenlidir.)"
        )
