<h1 align="center">Mek</h1>

<p align="center">
  Kurgucular ve seslendirme sanatçıları için Apple Silicon Mac'te sürekli çalışan, sesle yönetilen akıllı asistan:
  sistem bakımı, performans profilleri, Premiere Pro ve DaVinci Resolve için kurgu otomasyonu.
  <br><br>
  <img src="https://img.shields.io/badge/macOS-14%2B-black" alt="macOS 14+">
  <img src="https://img.shields.io/badge/Apple%20Silicon-M1%E2%80%93M5-blue" alt="Apple Silicon">
  <img src="https://img.shields.io/badge/version-0.0.0.2-orange" alt="v0.0.0.2">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

> **v0.0.0.2:** Bu sürüm, önceki tek betiklik `mac-cleaner`'ın yerini alır. Temizleyici artık Mek'in bir parçası
> (`bin/smart_system_cleaner.sh`) ve Mek tarafından uygun zamanda kendiliğinden çalıştırılır.
> Eski sürüm git geçmişinde duruyor.

*English summary: Mek is an always-on, voice-controlled Turkish assistant daemon for video editors on Apple Silicon.
It replaces separate cron-style scripts with one event-driven service: dynamic performance profiles, idle-time
cleaning, process health, and editing automation (silence removal, retake cleanup, hooks, audio/video effects,
captions, LUT grading) that exports FCP7 XML for both Premiere Pro and DaVinci Resolve.*

---

## Ne yapar?

### Sesle kontrol
- **"Mek"** dediğinizde uyanır. "Mek" kelimesi, sizin sesinizden çıkarılan kişisel şablonlarla (MFCC + DTW) algılanır.
  Bu kontrol 2 ms sürer, kısa kelimelerde Whisper'ın uydurduğu "Altyazı M.K." gibi cümlelere de takılmaz.
  "Mek" ile başlamayan konuşmalar yazıya dökülmez.
- Komutlar yerelde, mlx-whisper (4-bit large-v3-turbo) ile yazıya dökülür.
- Başka bir uygulama mikrofonu açınca (kayıt, FaceTime, Zoom, Discord) Mek kendiliğinden susar.
- Menü çubuğunda bir durum simgesi gösterir. Duyduğu ve söylediği cümleler ekranın üstünde Siri benzeri bir balonda görünür.

### Sohbet ve hafıza
- Sohbet geçmişi ve sizinle ilgili öğrendikleri (`state/history.json`, `state/memory.json`) yeniden başlatmada kaybolmaz.
- Cevap verdikten sonraki 8 saniye boyunca "Mek" demeden konuşmaya devam edebilirsiniz.
- Beyin: yerel Qwen3-4B (yalnızca gerektiğinde yüklenir, 90 saniye kullanılmazsa bellekten atılır; kurgu programı
  açıkken hiç yüklenmez). Gemini ise yalnızca internet araması ve "ekrana bak" için kullanılır, günlük kotayla sınırlıdır.

### Komut örnekleri
| Söyleyin | Ne olur |
|---|---|
| Mek, Safari'yi aç / Topaz'ı kapat | Uygulamayı açar ya da kapatır |
| Mek, 15 dk sonra bilgisayarı kapat | İptal edilebilir zamanlayıcı kurar ("Mek iptal") |
| Mek, kapan | 20 saniyelik geri sayımla kapatır ("dur" diyerek iptal edilir) |
| Mek, babamı ara | Rehberde bulur, iPhone üzerinden arar |
| Mek, bilgisayarı temizle | Temizliği başlatır, bitince kaç GB açıldığını söyler |
| Mek, yarım saat sonra su iç | Hatırlatma kurar |
| Mek, dolar bugün kaç lira? | Google'da arar (Gemini) |
| Mek, ekrana bak, bu efekti nasıl yaparım? | Ekran görüntüsünü yorumlar |
| Mek, kayıt modu | Kayıt bitene kadar susar |

### Sistem sağlığı ve performans
- **Performans profili:** Premiere, After Effects, Resolve, Topaz ya da Media Encoder açıldığında devreye girer.
  - Kurgu uygulamalarını en yüksek önceliğe (QoS) alır.
  - Bulut eşitleme, güncelleyici ve indeksleme gibi arka plan süreçlerini verimlilik çekirdeklerine alır.
  - Gereksiz ajanları geçici olarak durdurur ve uykuyu engeller.
  - Uygulamalar kapanınca her şeyi eski haline döndürür.
- **Kaçak süreçler:** Kurgu programı olmayan bir süreç işlemciyi uzun süre yorarsa onu verimlilik çekirdeklerine alır ve size söyler.
- **Takip:** ısı, disk doluluğu, disk sağlığı (SMART) ve takas alanı.
- **Bakım:** Temizliği yalnızca siz bilgisayar başında değilken ve render sürmüyorken yapar.
- **Kendi yükü:** boşta yaklaşık 0,3–1 GB bellek kullanır. Siz bilgisayar başında değilken kontrolleri seyrekleştirir.

### Kurgu otomasyonu (Premiere Pro ve DaVinci Resolve)
Komutlar üst üste eklenir. Her komut `~/Movies/Mek/<ad>/vN.xml` olarak yeni bir sürüm üretir. "Kurguyu geri al" bir önceki sürüme döner.

```
Mek, boşlukları kes, girişe reverb ile gir, "proxy dosyalar" dediğim yeri robot yap ve altyazı ekle
Mek, bu videoya iyi bir hook yap
Mek, ııı'ları ve tekrar alınan cümleleri temizle
Mek, kaba kurgu yap, 30 saniyelik reels
Mek, renkleri sinematik yap … biraz daha sıcak … rengi sıfırla
Mek, bu fotoğraftaki gibi renk ver        (Finder'da referans görsel seçili)
Mek, "lanet" kelimesini sansürle
```

- **Ses efektleri:** reverb, reverb ile giriş, yankı, robot, telefon, radyo, megafon, su altı, kalın ses, ince ses,
  şeytan sesi, ters çalma, yayın kalitesinde temizlik (−14 LUFS), normalize, susturma
- **Görüntü efektleri:** punch-in, zoom, ağır çekim, hızlandırma, siyah-beyaz, flaş, titreme, glitch, vinyet, kare dondurma
- **Efekt sesleri:** whoosh, riser, impact, bip. Hepsi matematiksel olarak üretilir, telif sorunu yoktur.
- **Hedefleme:** "girişe", "son cümleye", "ilk 3 saniyeye", "'X' dediğim yere", "tamamına"
- **Hook:** transkripti, ses enerjisini ve sahne değişimlerini analiz eder. En çarpıcı cümleyi başa koyar, efektleri seçer ve "HOOK" işaretçisi ekler.
- **Renk:** 33'lük `.cube` LUT üretir.
  - Premiere'de: Lumetri › Yaratıcı › Görünüm
  - Resolve'da: kliplere kendiliğinden uygulanır

**İki programda da aynı sonuç nasıl alınıyor?** Premiere ve Resolve birbirinin efekt ayarlarını XML'den okuyamaz.
Bu yüzden efekt uygulanan parçalar, Apple Silicon'un donanımsal ProRes kodlayıcısıyla ayrı klip olarak işlenip
zaman çizelgesine konur. Orijinal dosyalara hiç dokunulmaz.
- **Resolve:** Harici betik ayarı açıksa (Tercihler › Sistem › Genel › Harici betik kullanımı: Yerel) Mek, mevcut
  zaman çizelgesindeki V1 kesimlerini okur ve sonucu yeni bir zaman çizelgesi olarak ekler.
- **Premiere:** Programın dışından zaman çizelgesi okunamaz. Mek ham dosyadan ya da kendi son sürümünden çalışır ve
  sonucu yeni bir sekans olarak içe aktarır.

**Kurgu yardımı (API harcamadan):** Premiere, Resolve, After Effects ya da Photoshop öndeyken "kes", "render al",
"işaretçi ekle", "katmanı kopyala" gibi komutları doğrudan o programın kısayoluna çevirir.

---

## Kurulum

```bash
brew install ffmpeg
git clone https://github.com/AlptugDag/Mac-Cleaner.git && cd Mac-Cleaner
./install.sh
```

İsteğe bağlı adımlar:
```bash
mek-anahtar                                                  # Gemini anahtarı (gösterilmeden kaydedilir)
~/.jarvis/venv/bin/python ~/.jarvis/mek/wakeword.py ekle ~/Desktop/mek.wav   # 8-10 kez "Mek" dediğiniz kayıt
sudo bash ~/.jarvis/mek/install_privileged.sh                # güç profili + sistem servisleri (yalnızca sabit komutlar)
```

**Gereken macOS izinleri** (Sistem Ayarları › Gizlilik ve Güvenlik):
- **Mikrofon**
- **Erişilebilirlik:** kısayol göndermek için
- **Ekran Kaydı:** "ekrana bak" için
- **Kişiler:** "… ara" için

**Terminal komutları:**
- `mek "metin"`: komut gönderir
- `mek durum`: anlık durumu gösterir
- `mek log`: kayıtları izler
- `mek restart`: Mek'i yeniden başlatır

Kaldırmak için `./uninstall.sh` çalıştırın. Mek durur, eski ajanlar geri yüklenir. Hiçbir dosyanız silinmez.

## Yapı
```
mek/mekd.py        ana servis: komutlar, bağlam, proaktif öneriler, sağlık
mek/voice.py       mikrofon, VAD, Whisper, EMA Lightning TTS
mek/wakeword.py    kişisel "Mek" algılayıcı (MFCC + DTW)
mek/brain.py       yerel/Gemini/Claude beyni, hafıza, önbellek, günlük kota
mek/context.py     ön plandaki uygulama, boşta kalma süresi, GPU, render takibi, mikrofon kullanımı, ısı
mek/services.py    performans profilleri, QoS, ajan yönetimi, temizlik
mek/editkit.py     medya analizi, transkript, FCP7 XML, LUT, Resolve API
mek/editfx.py      kurgu durumu, efektler, hook, alt yazı, komut ayrıştırıcı
mek/apphelp.py     kurgu uygulaması kısayolları
mek/menubar.py     menü çubuğu simgesi + ekran balonu
bin/               mek CLI, anahtar kaydı, temizleyici, iş istasyonu ayarları
```

## Gizlilik
- Ses yalnızca bilgisayarınızda işlenir.
- Gemini'ye yalnızca açıkça istediğiniz aramalar, ekran görüntüleri ve (kota varsa) kurgu planları gider.
- API anahtarı, sohbet geçmişi, hafıza ve ses şablonlarınız `~/.jarvis` altında kalır ve bu depoya girmez.

## Lisans
MIT
