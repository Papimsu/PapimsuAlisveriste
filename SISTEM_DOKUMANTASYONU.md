# 🤖 Fiyat Radarı (Papimsu Alışverişte) - Tam Sistem ve Mimari Dokümantasyonu

Bu dokümantasyon, Amazon Türkiye, Trendyol ve Hepsiburada üzerinde 7/24 fiyat takibi, fiyat hatası (glitch) ve indirim tespiti yapan sıfır maliyetli ($0) otopilot sisteminin tüm mimarisini, kodlarını ve kurulum kılavuzunu içerir.

---

## 📌 1. Sistemin Genel Mimarisi

Sistem **tamamen bulut üzerinde ve sıfır maliyetle ($0)** çalışacak şekilde 3 ana bileşenden oluşur. Kullanıcının bilgisayarına (Mac/PC) hiçbir bağımlılığı yoktur.

```
+-------------------------------------------------------------+
|                     1. KULLANICI / TELEGRAM                 |
|   - Kullanıcı veya eşi gruba link atar.                     |
|   - Bot reply yaparak hedef fiyat sorar.                    |
|   - /liste veya /sil komutları çalışır.                     |
+------------------------------+------------------------------+
                               | Webhook
                               v
+-------------------------------------------------------------+
|                 2. CLOUDFLARE WORKER (Ön Büro)              |
|   - 7/24 Telegram mesajlarını dinler.                       |
|   - Gelen linkten Asin/ID temizler.                         |
|   - KV Session ile soru-cevap durumunu tutar.               |
|   - GitHub API ile `watchlist.json` dosyasına ekler/siler.  |
+------------------------------+------------------------------+
                               | Kayıt
                               v
+-------------------------------------------------------------+
|            3. GITHUB ACTIONS & PYTHON MOTORU (Arka Plan)    |
|   - Her 15 dakikada bir otomatik çalışır (Cron).            |
|   - `watchlist.json` içindeki tüm ürünleri tarar.           |
|   - Amazon, Trendyol ve Hepsiburada'dan curl_cffi ile       |
|     Chrome 124/Safari 17 parmak iziyle fiyat & satıcı çeker.|
|   - İndirim yakalarsa Telegram grubuna anında alarm atar.    |
|   - Güncellenen son fiyatları repoya commit/push eder.      |
+-------------------------------------------------------------+
```

---

## 📁 2. Dosya Yapısı ve Görevleri

* **`scraper.py`**: Amazon, Trendyol ve Hepsiburada için çok katmanlı veri kazıyıcı.
  * *Amazon*: `priceToPay`, `apexPriceToPay`, gizli inputlar ve sayfa içi JSON regex (`priceAmount`, `displayPrice`). Gerçek satıcı tespiti (`desktop-merchant-info`).
  * *Trendyol*: JSON-LD structured data, DOM indirim etiketleri (`prc-dsc`, `prc-slg`) ve ham veri (`discountedPrice`). Mağaza adı tespiti (`merchant`).
  * *Hepsiburada*: JSON-LD ve sayfa içi JSON regex (`price`, `merchantName`).
  * *Anti-Bot Koruması*: `curl_cffi` ile TLS/JA3 Chrome 124 & Safari 17 impersonation.
* **`tracker.py`**: Fiyat kontrol motoru ve karar mekanizması.
  * Eşik kontrolü (Hedef fiyat veya % indirim).
  * İlk ekleme anında sahte alarm vermeyi engelleyen koruma.
  * Sadece yeni dip fiyatlarda alarm verme (spam önleme).
  * Stok tükenmesi tespiti (`out_of_stock`).
  * Durum takibi ve kalp atışı (`status.json`).
* **`notifier.py`**: Telegram Bot API istemcisi. Bildirimleri doğrudan yapılandırılan Telegram grubuna (`<CHAT_ID>`) gönderir.
* **`.github/workflows/check_prices.yml`**: Her 15 dakikada bir (`*/15 * * * *`) çalışan GitHub Actions otomasyonu.
* **`cloudflare_worker.js`**: Telegram Webhook'unu karşılayan serverless kod.
* **`watchlist.json`**: Takipteki ürünlerin veritabanı.

---

## ⚙️ 3. Telegram Komutları

* **Ürün Ekleme:** Gruba doğrudan Amazon, Trendyol veya Hepsiburada ürün linki atın. Bot hemen yanıt vererek hedef fiyatı (örn: `18000`) veya indirim yüzdesini (örn: `%30`) sorar. Girilen rakama otomatik %5 esneklik payı (buffer) eklenir.
* **`/liste`:** Takipteki tüm ürünleri, güncel fiyatlarını, hedef fiyatlarını ve silme komutlarını kompakt metin olarak listeler.
* **`/sil <no>`:** Listeden çıkarmak istediğiniz ürünün numarasını yazarak silebilirsiniz (Örn: `/sil 1`).

---

## 🔐 4. Güvenlik ve Gizlilik Prensipleri

1. **Açık Şifre Yok:** Kodların içinde bot token'ları veya gizli anahtarlar düz metin olarak bulunmaz. Tüm anahtarlar GitHub Secrets ve Cloudflare Secrets kasasında saklanır.
2. **Public Repo Avantajı:** Repo açık (public) olduğu için GitHub Actions çalışma dakikaları **tamamen sınırsız ve $0**'dır. 50-80 ürüne kadar tek kuruş maliyet çıkmaz.
3. **Bozuk JSON & Çakışma Koruması:** Telegram'dan ürün eklenirken aynı anda GitHub cron'u fiyat güncellerse, rebase push ve JSON doğrulama mekanizması sayesinde veriler asla bozulmaz.

---

## 🚨 S4: Telegram Bot Token İptal ve Yenileme Kılavuzu

Eğer bot token'ı herhangi bir şekilde açığa çıkarsa veya şüphe oluşursa aşağıdaki 4 adımı sırasıyla uygulayın:

### 1. Telegram'da Eski Token'ı İptal Edin (@BotFather)
1. Telegram uygulamasında [@BotFather](https://t.me/BotFather) botunu açın.
2. `/revoke` komutunu gönderin.
3. İptal edilecek botunuzu listeden seçin.
4. BotFather eski token'ı derhal geçersiz kılacak ve size yeni bir token (`<YENI_BOT_TOKEN>`) üretecektir.

### 2. Cloudflare Worker Secret Değişkenlerini Güncelleyin
Cloudflare Dashboard -> **Workers & Pages** -> Worker'ınızı seçin -> **Settings** -> **Variables and Secrets**:
1. `BOT_TOKEN` değerini yeni token (`<YENI_BOT_TOKEN>`) ile güncelleyin ve kaydedip dağıtın (Deploy).
2. `WEBHOOK_SECRET` değerini güvenli rastgele bir string (örn: `openssl rand -hex 20`) ile tanımlayın.
3. `ALLOWED_CHAT_IDS` değerine yetkili chat ID'lerini virgülle ayrılmış olarak girin (örn: `12345678,-10012345678`).

### 3. GitHub Secrets Kasasını Güncelleyin
GitHub Repository -> **Settings** -> **Secrets and variables** -> **Actions**:
1. `TELEGRAM_BOT_TOKEN` secret'ını düzenleyin ve `<YENI_BOT_TOKEN>` yapıştırın.
2. `TELEGRAM_CHAT_ID` secret'ının tanımlı olduğundan emin olun.

### 4. Telegram Webhook'unu Yeni Token ve Secret Token ile Yeniden Kurun
Tarayıcınızdan veya terminalden aşağıdaki isteği göndererek webhook'u yeni token ve güvenlik başlığıyla aktifleştirin:

```bash
curl -F "url=https://<WORKER_ADRESINIZ>.workers.dev" \
     -F "secret_token=<WEBHOOK_SECRET>" \
     "https://api.telegram.org/bot<YENI_BOT_TOKEN>/setWebhook"
```

Yanıt olarak `{"ok":true,"result":true,"description":"Webhook was set"}` döndüğünde sistem tamamen yeni token ve secret token korumasıyla devreye girmiş olur.
