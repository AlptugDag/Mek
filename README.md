<h1 align="center">Mek</h1>

<p align="center">
  Apple Silicon Mac'i kendi kendine en hızlı, en sağlıklı, en temiz ve en güvenli hâlde tutan; gün içinde
  takvimden, piyasalardan ve havadan haber veren arka plan asistanı. Apple Intelligence ile çalışır.
  <br><br>
  <img src="https://img.shields.io/badge/macOS-26%2B-black" alt="macOS 26+">
  <img src="https://img.shields.io/badge/Apple%20Silicon-M1%E2%80%93M5-blue" alt="Apple Silicon">
  <img src="https://img.shields.io/badge/version-0.0.0.3-orange" alt="v0.0.0.3">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

> **v0.0.0.3:** Bu sürümde Mek sizi dinlemez, yalnızca kendisi konuşur. Mikrofon hiç açılmaz.
> Sesle yönetilen asistan ve kurgu otomasyonu içeren önceki Mek sürümü [`v0.0.0.2`](https://github.com/AlptugDag/Mek/tree/v0.0.0.2)
> etiketinde duruyor.

*English summary: an always-on background daemon for Apple Silicon Macs. It keeps performance, health, storage
and security at their best with no user input, and proactively speaks Turkish updates (calendar, markets,
weather, system). Text is phrased on-device by Apple Intelligence (FoundationModels). It never listens.*

---

## Konuştukları
Mek aşağıdaki durumlarda kendiliğinden konuşur:
- **Günün ilk brifingi:** masaya oturduğunuzda saat, hava, takvim, anımsatıcılar, piyasa özeti ve sistem durumu
- **Takvim:** etkinliklerden 10 dakika önce hatırlatma
- **Piyasalar (hafta içi):** BIST 100, dolar, euro, gram altın, Bitcoin ve izleme listenizde belirgin bir hareket olduğunda haber verir, 18:15'te kapanış özeti okur. Alım-satım yapmaz, tavsiye vermez.
- **Hava:** önümüzdeki 3 saat için yağmur uyarısı
- **Sistem:** biten renderlar, temizlik sonuçları, disk, ısı ve takas alanı uyarıları, uzun kurgu sonrası mola önerisi
- **Bilgisayar başında değilken:** olanları biriktirir, döndüğünüzde özetler

Şu durumlarda susar (acil güvenlik uyarıları hariç):
- 00:00–11:00 arası sessiz saatler
- Bir uygulama mikrofonu kullanıyorken (görüşme ya da kayıt)
- Siz bilgisayar başında değilken

Söyleyeceği cümleleri **Apple Intelligence**, cihaz üzerinde doğal Türkçeye çevirir; internet ya da API anahtarı gerekmez.
Ses [EMA Lightning](https://huggingface.co/canberkkkkkk/ema-lightning) ile ayrı bir süreçte üretilir, bu yüzden konuşma bitince bellek tamamen boşalır.

## Performans
- **Kurgu sırasında:** Premiere, After Effects, Resolve, Topaz veya Media Encoder açıldığında devreye girer.
  - Bu uygulamaları en yüksek önceliğe alır.
  - Bulut eşitleme, güncelleyici ve indeksleme gibi işleri verimlilik çekirdeklerine iter.
  - Gereksiz servisleri geçici olarak durdurur, uykuyu ve Power Nap'i engeller.
  - Uygulamalar kapanınca her şeyi eski hâline döndürür.
- **Kaçak süreçler:** Kurgu programı olmayan bir süreç işlemciyi uzun süre yorarsa onu verimlilik çekirdeklerine alır.
- **Kendi yükü:** yaklaşık 70–90 MB bellek. Kontrol sıklığı duruma göre değişir: kurgu sırasında 15 saniye, siz bilgisayar başındayken 10 saniye, uzaktayken 45 saniye.

## Depolama temizliği
| Katman | Ne zaman | Kapsam |
|---|---|---|
| Günlük | Siz bilgisayar başında değilken ve render yokken | Adobe, DaVinci, Topaz, Lightroom, Capture One önbellekleri; bütün uygulama önbellekleri (App Store uygulamaları ve Electron tabanlılar dahil); tarayıcılar; kayıtlar, çökme raporları, geçici dosyalar |
| Haftalık | Haftada bir | Günlük katman + Homebrew, npm, pip, Xcode, Docker + sistem önbellekleri ve kayıtları + rapor |
| Acil | Boş alan 60 GB'ın altına düşünce | Yaş eşikleri düşer, Time Machine yerel anlık görüntüleri inceltilir |

**Asla silinmeyenler:**
- DaVinci renk stilleri ve proje yedekleri
- Son 30 günün otomatik kayıtları
- Yapay zekâ modelleri
- Fotoğraflar, Mail ve Mesajlar verisi

Eski kurulum dosyaları ve aylardır açılmamış büyük dosyalar silinmez, yalnızca size raporlanır.

## Güvenlik
- **Kripto madenciler:** İmza, komut satırı ya da havuz bağlantısıyla tespit edilir. Süreç durdurulur, dosyası ve onu başlatan ajan karantinaya alınır.
- **Başlangıç öğeleri:** Yeni eklenenler 15 dakikada bir denetlenir. İmzasız ve şüpheli konumdan çalışan bir öğe devre dışı bırakılır.
- **Dosya taraması:** ClamAV, siz bilgisayar başında değilken yeni indirmeleri en düşük öncelikte tarar; virüs veritabanı günde bir kez güncellenir.
- **Güvenlik ayarları:** SIP, Gatekeeper, FileVault, güvenlik duvarı ve XProtect izlenir. Mek bu ayarları değiştirmez, kapanırsa size söyler.
- Hiçbir dosya silinmez. Şüpheli dosyalar `~/.jarvis/karantina` klasörüne taşınır.

## Kurulum

Terminal komutu ve kurulum klasörü teknik nedenlerle `jarvis` / `~/.jarvis` adını taşır.

```bash
git clone https://github.com/AlptugDag/Mek.git && cd Mek
./install.sh
sudo bash ~/.jarvis/jarvisd/install_privileged.sh   # güç profilleri + sistem temizliği (sabit komutlar)
```
İlk çalıştırmada macOS, Takvim ve Anımsatıcılar için izin ister.

**Terminal komutları:**
- `jarvis brifing`: brifingi hemen okur
- `jarvis durum`: anlık sistem durumu
- `jarvis piyasa thy`: belirli bir hissenin fiyatı
- `jarvis güvenlik`: güvenlik özeti
- `jarvis temizle [derin]`: temizliği elle başlatır
- `jarvis sus 60`: 60 dakika sessiz kalır
- `jarvis log`: kayıtları izler

**Siri ile kullanım:** Kestirmeler'de "Kabuk Betiği Çalıştır" adımına `jarvis brifing` yazın, Siri'ye "Mek brifing" deyin.

## Yapı
```
jarvisd/jarvisd.py    ana servis: olaylar, brifing, bildirim, zamanlama
jarvisd/services.py   performans profilleri, QoS, servis yönetimi
jarvisd/context.py    ön plandaki uygulama, boşta kalma süresi, GPU, render takibi, ısı
jarvisd/apple.py      Takvim/Anımsatıcılar (EventKit), piyasalar, Türkçe tarih ayrıştırma
jarvisd/security.py   madenci, kalıcılık, ClamAV, güvenlik ayarları, karantina
jarvisd/cleaner.py    katmanlı depolama temizliği
ai/jarvis-ai.swift    Apple Intelligence (FoundationModels) köprüsü
jarvis.py             ses (EMA Lightning + Mac Studio hoparlör EQ), hava durumu
```

## Lisans
MIT
