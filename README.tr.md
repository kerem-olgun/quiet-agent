# quiet-agent

Bir yapay zekâ ajanı Windows'ta tarayıcını kullanırken hoparlörün ve ekranın sende kalsın.

[English](README.md)

Senin yerine gezinen bir ajan er ya da geç ses çalan bir sayfa açar, başlattığı bir şey de çalıştığın ekranın üstünde bir konsol penceresi parlatır. `quiet-agent` bu durum için iki küçük araç ve bir kural sayfasıdır:

- `quiet-agent shield`, ajanın tarayıcısını (yalnız ajanın tarayıcısını) belirli bir süre susturur, sonra sesi geri verir.
- `quiet-agent watch-windows`, o pencereyi hangi sürecin açtığını kaydeder.
- [`docs/agent-rules.md`](docs/agent-rules.md), ajanın talimatlarına yapıştırılacak kısa bir kural sayfasıdır.

Windows'ta çalışır ve Core Audio oturum arayüzüne `ctypes` üzerinden erişir; derlenecek bir şey yoktur.

## Neden tarayıcıyı doğrudan susturmuyoruz?

Çünkü tarayıcıyı sen de kullanıyorsun. Sırasıyla üç sorunla karşılaştım.

**Bir tarayıcı örneği tek ses oturumudur.** Chromium bütün sekmelerin sesini tek bir ses servisi sürecinden çalar. Windows dışarıdan bakınca tarayıcının tamamı için tek oturum görür; yani tek bir sekme ya da pencere ayrı susturulamaz. İlk sürüm "tarayıcıyı" susturuyordu ve benim izlediğim videoyu da susturdu. Çözüm, ajana kendi `--user-data-dir` klasörüyle ayrı bir tarayıcı örneği vermek. O örneğin kendi ses süreci olur ve tek başına susturulabilir.

**Sayfa içinden susturmak tutmuyor.** `<video>` öğelerini susturan bir betik bir sonraki sayfa geçişinde kaybolur, otomatik oynayan önizlemeler de betik çalışmadan başlar. Windows ses oturumundaki susturma sayfanın ne yaptığına bağlı değildir.

**Ses geri verilmek zorunda.** Tarayıcıyı susturan süreç ajan oturumuyla birlikte öldürüldü ve temizlik adımını hiç çalıştıramadı. Fark edene kadar tarayıcı yaklaşık altı saat sessiz kaldı. Artık kalkan ajandan bağımsız çalışıyor, neyi susturduğunu yazıyor ve yine de öldürülürse diye bir `recover` komutu var.

## Kurulum

```
pip install git+https://github.com/kerem-olgun/quiet-agent
```

Windows 10 ya da 11, Python 3.10 ve üzeri. Tek bağımlılık `psutil`.

## Ajana kendi tarayıcı örneğini ver (bir kez)

Tarayıcını ayrı bir profil klasörüyle başlatan bir kısayol oluştur:

```
"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --user-data-dir="C:\AgentBrowser"
```

Ajanın tarayıcı eklentisini o pencereye kur ve ajanın ihtiyaç duyduğu sitelere orada giriş yap. Bundan sonra ajan orada çalışır, senin tarayıcına dokunulmaz.

## Kullanım

```
quiet-agent status --match AgentBrowser
quiet-agent shield start --match AgentBrowser --minutes 30
   ... ajan çalışır ...
quiet-agent shield stop
```

`--match`, ajanın tarayıcı örneğinin komut satırında geçen herhangi bir metindir. En kolay seçim profil klasörünün adıdır.

| Komut | Ne yapar |
|---|---|
| `status` | Eşleşen ses oturumlarını ve o anki ses düzeyini listeler. Hiçbir şeyi değiştirmez. |
| `shield start` | Çağıran terminal ya da ajan oturumu kapansa da çalışmaya devam eden bir kalkan başlatır. Kullanılacak komut budur. |
| `shield run` | Aynı kalkanı ön planda çalıştırır. |
| `shield stop` | Çalışan kalkanı erken bitirir ve sesi geri verir. |
| `recover` | Öldürülmüş bir kalkanın sessiz bıraktığı oturumları açar. Kira dosyası kayıpsa `--all` ile hedefin bütün sessiz oturumları açılır. |
| `watch-windows` | Bir süre boyunca hangi süreçlerin konsol penceresi ya da diyalog açtığını kaydeder. |

Her komut tek bir JSON nesnesi yazar; ajan sonucu okuyabilir. Ajanın tarayıcısında bir kalkan çalışırken benim makinemde `status` çıktısı:

```json
{"sessions": [{"key": "0:35540", "pid": 35540, "process": "brave.exe", "state": 0, "muted": true}], "peak": 0.0, "shields_running": []}
```

Çıkış kodları:

| Kod | Anlamı |
|---|---|
| 0 | Tamam. |
| 1 | Burada çalışamaz (Windows değil ya da `psutil` yok). |
| 2 | Reddedildi: hedef tarayıcı şu an duyulur biçimde ses çalıyor. |
| 3 | Reddedildi: tarayıcı örneği belirtilmedi. |

## Kalkanın verdiği sözler

Her satır, tersi bir kez yaşandığı için burada.

- **Her şeyi susturmaz.** `--match` yoksa 3 koduyla çıkar. `--whole-browser` seçeneği var, ama onu senin yazman gerekir.
- **Sen dinlerken başlamaz.** Önce hedefi iki saniye dinler. Ses geliyorsa 2 koduyla çıkar ve hiçbir şeye dokunmaz.
- **Yalnız kendi susturduğunu açar.** Senin elle susturduğun oturum sessiz kalır.
- **Hedefi sessiz tutar.** Saniyede yaklaşık on kez bakar; yeni açılan oturum neredeyse anında susturulur, kendi sesini açan sayfa yeniden susturulur.
- **Ajandan uzun yaşar.** `shield start`, Windows izin verdiğinde kalkanı çağıran terminalin iş nesnesinin dışında başlatır; ajan oturumunu kapatmak kalkanı öldürmez. Süre dolunca yine kendiliğinden biter.
- **Arkası toplanabilir.** Kalkan, susturduğu oturumları bir kira dosyasında tutar. Süreç öldürülürse `quiet-agent recover` tam olarak onları açar.

## Parlayan pencereyi bulmak

Saniyenin küçük bir kısmında görünüp kaybolan konsol pencerelerinin kaynağını gözle bulmak zordur. İzleyiciyi başlat, her zamanki gibi çalış, sonra kaydı oku:

```
pythonw -m quiet_agent watch-windows --minutes 60
```

Burada `pythonw` önemli, çünkü izleyicinin kendisi pencere açmamalı. Kayıt satır başına bir JSON nesnesidir. `window` olayı pencere sınıfını, başlığını, konumunu ve üst süreç zincirini taşır; zincir, parlayan şeyi kimin başlattığını gösterir. `process` olayı yeni başlayan her konsol türü program için yazılır. Son satır görülenleri sayar:

```json
{"event": "finished", "top_level_windows_shown": 1, "windows_recorded": 0, "processes_recorded": 107, "t": "11:57:59.737"}
```

Kayıt makinende kalır. İçinde pencere başlıkları ve komut satırları bulunur; paylaşmadan önce oku.

## Ajan için kurallar

Araçlar, ajan onları ne zaman kullanacağını bilirse işe yarar. [`docs/agent-rules.md`](docs/agent-rules.md) bilgisayarda ajana değil insana ait olan beş şeyi (hoparlör, ekran, odak, pano, klavye ve fare) sayar ve her biri için yerine ne yapılacağını söyler. Sayfayı `CLAUDE.md`, `AGENTS.md` ya da ajanının talimat okuduğu yere yapıştır. Kural sayfası İngilizcedir.

## Sınırlar

- Yalnız Windows.
- Kasım 2026 başından beri Windows 11'de Brave ile çalıştırdığım bir betikten doğdu. Chrome, Edge, Vivaldi ve Opera aynı ses mimarisi üzerine kurulu ve varsayılan süreç listesinde; ama onları denemedim. Denersen sonucu bir issue açarak yaz.
- Firefox desteklenmiyor.
- Aynı anda tek kalkan.
- Kalkan susturur, oynatmayı durdurmaz.
- Oturumlar `cihaz:pid` olarak hatırlanır. Kalkan çalışırken bir ses cihazı eklenir ya da çıkarılırsa oturum yeni bir anahtar alabilir. Bu durum için `quiet-agent recover --all --match <metin>` var.
- Birim testleri ses katmanının yerine sahte bir katman koyar; bu yüzden hiçbir sesi değiştirmez ve her yerde çalışır. Gerçek Core Audio çağrılarının ayrı bir denetimi var: `tests/live_roundtrip.py`. Ses cihazı olan bir Windows makinesi ister. CI yalnız birim testlerini koşar.

## Nasıl çalışır

`IMMDeviceEnumerator` etkin çıkış cihazlarını listeler. Her cihaz için `IAudioSessionManager2` oturum listesini verir. Araç her oturumun sahibi olan sürecin kimliğini okur; süreç seçilen tarayıcılardan biriyse ve kendisinin ya da üst süreçlerinden birinin komut satırında `--match` metni geçiyorsa oturumu tutar. Susturma `ISimpleAudioVolume`, ses düzeyi okuma `IAudioMeterInformation` ile yapılır. Kod [`src/quiet_agent/audio.py`](src/quiet_agent/audio.py) dosyasında, iki yüz satırın biraz üzerinde.

## Benzer araçlar

[SoundVolumeView](https://www.nirsoft.net/utils/sound_volume_view.html) ve [pycaw](https://github.com/AndreMiras/pycaw) bir sürecin sesini susturabilir; `quiet-agent` altta aynı Windows arayüzünü kullanır. Eklediği şey, ajan durumu için susturmanın çevresindeki işler: tek bir tarayıcı örneğini seçmek, sen dinlerken reddetmek ve ajan gittikten sonra sesi geri almak.

## Geliştirme

```
git clone https://github.com/kerem-olgun/quiet-agent
cd quiet-agent
python -m unittest -v
python tests/live_roundtrip.py
```

İkinci komut canlı denetimdir. Sessiz (genliği sıfır) ses çalan bir yardımcı süreç başlatır, yalnız o sürece karşı gerçek bir kalkan çalıştırır ve oturumun önce susturulduğunu, sonra açıldığını doğrular. Hoparlörden ses çıkmaz, hiçbir tarayıcıya dokunulmaz.

## Lisans

MIT
