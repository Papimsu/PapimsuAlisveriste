# 🤖 Fiyat Radarı (Papimsu Alışverişte) - Tam Sistem ve Mimari Dokümantasyonu

Bu dokümantasyon; Amazon Türkiye, Trendyol ve Hepsiburada üzerinde 7/24 fiyat takibi, fiyat hatası (glitch) ve indirim tespiti yapan sıfır maliyetli ($0) otopilot sisteminin tüm mimarisini, dosya yapısını, veri ayrımını ve kurulum kılavuzunu içerir.

---

## 📌 1. Sistemin Genel Mimarisi

Sistem **tamamen bulut üzerinde ve sıfır maliyetle ($0)** çalışacak şekilde 3 ana bileşenden oluşur. Kullanıcının bilgisayarına hiçbir bağımlılığı yoktur.

```
+-------------------------------------------------------------+
|                     1. KULLANICI / TELEGRAM                 |
|   - Kullanıcı veya aile üyesi gruba ürün linki atar.        |
|   - Bot reply yaparak hedef fiyat veya % indirim sorar.     |
|   - /liste veya /sil komutları çalışır.                     |
+------------------------------+------------------------------+
                               | Webhook (X-Telegram-Bot-Api-Secret-Token)
                               v
+-------------------------------------------------------------+
|                 2. CLOUDFLARE WORKER (Ön Büro)              |
|   - 7/24 Telegram mesajlarını dinler (Daima HTTP 200 döner).|
|   - Desteklenen URL'leri doğrular (Amazon TR, TY, HB).      |
|   - KV Session ile soru-cevap durumunu tutar (pending_...). |
|   - GitHub Contents API ile `watchlist.json`'a ekler/siler. |
|   - Dead Man's Switch cron kontrolü (Her 30 dk).            |
+------------------------------+------------------------------+
                               | Config (watchlist.json)
                               v
+-------------------------------------------------------------+
|            3. GITHUB ACTIONS & PYTHON MOTORU (Arka Plan)    |
|   - Her 15 dakikada bir otomatik çalışır (Cron */15).       |
|   - `watchlist.json` içindeki aktif ürünleri tarar.         |
|   - curl_cffi ile Chrome 124 / Safari 17 TLS parmak izi.    |
|   - Circuit Breaker: 2 ardışık BLOCKED sonrası domain atlar.|
|   - Time Budget: 330s bütçe ile zaman aşımını engeller.     |
|   - İndirim yakalarsa Telegram'a zengin alarm atar.         |
|   - Makine durumunu `state.json` ve `status.json`'a yazar.  |
+-------------------------------------------------------------+
```

---

## 📁 2. Dosya Yapısı ve Görev Ayrımı (R6 Config vs State)

Sistemde **konfigürasyon** ile **makine durumu** kesin olarak birbirinden ayrılmıştır:

* **`watchlist.json` (CONFIG - Yalnızca Cloudflare Worker yazar):**
  * Kullanıcının takip etmek istediği ürünleri ve belirlediği hedefleri içerir.
  * Alanlar: `id`, `url`, `name`, `target_price`, `discount_threshold_percent`, `active`, `created_at`.
  * GitHub Actions bu dosyayı **asla değiştirmez veya commit etmez**, böylece eşzamanlı çakışmalar (merge conflict) engellenir.
* **`state.json` (STATE - Yalnızca GitHub Actions yazar):**
  * Kazıma motorunun her ürün için tuttuğu makine durumunu ve geçmişini saklar.
  * Alanlar: `id`, `title`, `last_price`, `last_checked_at`, `last_alert_sent_price`, `consecutive_failures`, `error_alert_sent`, `out_of_stock`, `price_history`.
  * `price_history`: Son 60 günün günlük minimum fiyatlarını tutar.
* **`status.json` (DURUM - Yalnızca GitHub Actions yazar):**
  * Son çalışma turunun özetini saklar: `state` (`ok` veya `degraded`), `items_checked`, `ok`, `failed`, `out_of_stock`, `skipped_due_to_budget`, `circuit_breaker_tripped`, `last_run_utc`, `last_run_tr`.
* **`scraper.py`**:
  * Amazon TR, Trendyol ve Hepsiburada için çok katmanlı veri kazıyıcı.
  * Hata sınıflandırması (`BLOCKED`, `TIMEOUT`, `HTTP_xxx`, `PARSE_FAIL`, `NO_BUYBOX`).
  * Domain başına en az 3 saniye rate limiting.
  * Hepsiburada JSON-LD ve AggregateOffer parsing (A3).
  * Amazon buybox ve ana fiyat konteyneri doğrulaması (A4).
* **`tracker.py`**:
  * Fiyat kontrol motoru ve karar mekanizması.
  * Zaman bütçesi yönetimi (C3 - Varsayılan 330 saniye) ve en eski taranan ürünleri önceliklendirme.
  * Her ürün sonrası diske anında atomik kaydetme (Incremental Save).
  * Domain bazlı Circuit Breaker (R1).
  * Toplu hata bildirimi (R2 - Spam engelleme).
  * Hedef fiyat (`price <= target`) ve %5 yaklaşma (`NEAR_TARGET`) ayrımı (A1).
  * Spam önleme: En az %1 düşüş şartı (A2).
  * 30 günlük medyan referans fiyat hesaplama (A5).
  * UTC zaman damgası saklama, TR saati ile bildirim (A6).
* **`notifier.py`**:
  * Telegram Bot API istemcisi. Bildirimleri yapılandırılan Telegram sohbetine (`TELEGRAM_CHAT_ID`) HTML formatında gönderir.
* **`cloudflare_worker.js`**:
  * Telegram Webhook'unu karşılayan Cloudflare Worker kodu.
  * Webhook secret token kontrolü (`X-Telegram-Bot-Api-Secret-Token` vs `WEBHOOK_SECRET`) (S1).
  * Dinamik yetkili sohbet listesi (`ALLOWED_CHAT_IDS`) (S2).
  * Strict regex ve Türkçe sayı ayrıştırma, `pending_${chatId}_${fromId}` KV oturumu (C1).
  * GitHub API 409/422 durumunda 3 kereye kadar otomatik retry (C4).
  * Mesaj parçalama (R4 - 3800 karakter sınırı) ve HTML-escape.
  * Daima HTTP 200 dönme garantisi (R5).
  * Dead man's switch: GitHub Actions 75 dakikadan uzun süre çalışmazsa uyarı (R3).
  * URL doğrulama, Amazon Türkiye zorunluluğu, deduplication ve `/sil` komutu (A7).

---

## ⚙️ 3. Telegram Komutları ve Kullanımı

* **Ürün Ekleme:**
  * Gruba doğrudan Amazon TR, Trendyol veya Hepsiburada ürün linki atın.
  * Bot reply yaparak hedef fiyat veya indirim yüzdesi ister.
  * **Hedef Fiyat:** Sadece rakam yazın (Örn: `18000` veya `1.250,50`).
  * **İndirim Yüzdesi:** Yüzde yazın (Örn: `%30`).
  * Normal sohbet mesajları (örn: *"50 dakika sonra gelirim"*) kural olarak algılanmaz ve yok sayılır.
* **`/liste`:**
  * Takipteki tüm ürünleri, ürün ID'lerini, hedef fiyatlarını ve son okunan fiyatlarını listeler.
  * Çok sayıda ürün olduğunda Telegram mesaj sınırına takılmadan güvenli parçalar halinde gönderir.
* **`/sil <no veya id>`:**
  * Ürünü sıra numarasına göre (Örn: `/sil 1`) veya ID'sine göre (Örn: `/sil d8wk9m6x`) silebilirsiniz.
  * Silinen ürünün adını belirterek onay mesajı verir.

---

## 🔔 4. Alarm ve Bildirim Mantığı (A1 & A2)

1. **Hedef Fiyata Ulaşıldı (🚨):** Fiyat kullanıcının girdiği hedef fiyata eşit veya altına indiğinde (`fiyat <= hedef_fiyat`) tetiklenir.
2. **Hedefe Çok Yaklaştı (🔔):** Fiyat hedef fiyatın hemen üzerinde ancak %5 pay içindeyse (`hedef < fiyat <= hedef * 1.05`), tek seferlik "Hedefe yaklaştı" bildirimi gönderilir.
3. **İndirim Eşiği Aşıldı (📉):** 30 günlük medyan referans fiyata göre belirlenen yüzde indirim yakalandığında tetiklenir.
4. **Spam Önleme (Anti-Spam):** Bir fiyat seviyesi için alarm gittikten sonra, yeni bir alarm ancak fiyat **en az %1 daha düştüğünde** verilir. Fiyat hedefin üstüne çıkarsa alarm hafızası sıfırlanır.

---

## 🔐 5. Güvenlik ve Kurulum Kılavuzu

### Gerekli Ortam Değişkenleri (Secrets)

#### Cloudflare Worker Settings -> Variables and Secrets:
* `BOT_TOKEN`: Telegram bot token'ı (`<TELEGRAM_BOT_TOKEN>`).
* `WEBHOOK_SECRET`: Webhook güvenliği için rastgele bir anahtar (örn: `openssl rand -hex 20`).
* `ALLOWED_CHAT_IDS`: Yetkili chat ID'leri virgülle ayrılmış (örn: `12345678,-10012345678`).
* `GITHUB_TOKEN`: Repoya yazma izni olan GitHub Personal Access Token (repo scope).
* `GITHUB_REPO`: `kullanici_adi/repo_adi`.
* KV Namespace Bağlantısı: `SESSIONS` -> Cloudflare KV oluşturulup Worker'a bağlanmalıdır.

#### GitHub Repository Settings -> Secrets and variables -> Actions:
* `TELEGRAM_BOT_TOKEN`: Telegram bot token'ı.
* `TELEGRAM_CHAT_ID`: Alarmların gönderileceği sohbet veya grup ID'si.

---

## 🚨 6. S4: Telegram Bot Token İptal ve Yenileme Kılavuzu

Eğer bot token'ı herhangi bir şekilde açığa çıkarsa veya şüphe oluşursa aşağıdaki 4 adımı sırasıyla uygulayın:

1. **Telegram'da Eski Token'ı İptal Edin (@BotFather):**
   * Telegram'da [@BotFather](https://t.me/BotFather) botuna gidin.
   * `/revoke` komutunu gönderin ve botunuzu seçin. Yeni bir token (`<YENI_BOT_TOKEN>`) alacaksınız.
2. **Cloudflare Worker Secret'larını Güncelleyin:**
   * Cloudflare Dashboard -> Workers & Pages -> Worker -> Settings -> Variables.
   * `BOT_TOKEN` değerini `<YENI_BOT_TOKEN>` yapın ve Deploy edin.
3. **GitHub Secrets Kasasını Güncelleyin:**
   * GitHub Repo -> Settings -> Secrets and variables -> Actions -> `TELEGRAM_BOT_TOKEN` değerini güncelleyin.
4. **Webhook'u Secret Token ile Yeniden Kaydedin:**
   ```bash
   curl -F "url=https://<WORKER_ADRESINIZ>.workers.dev" \
        -F "secret_token=<WEBHOOK_SECRET>" \
        "https://api.telegram.org/bot<YENI_BOT_TOKEN>/setWebhook"
   ```

---

## 🛡️ 7. C5: GitHub IP Engeli ve Tanılama Kılavuzu

GitHub Actions sunucuları Microsoft Azure veri merkezlerinde çalışır. E-ticaret siteleri zaman zaman veri merkezi IP bloklarına geçici bot engeli (HTTP 403 / Cloudflare Challenge / Robot Check) uygulayabilir.

* **Sistem Koruması:** Sistemimiz böyle bir engelleme algıladığında **Circuit Breaker** devreye sokar ve o domaini o tur için pas geçer; gereksiz istek atarak IP'yi kalıcı olarak kara listeye sokmaz.
* **Manuel Tanılama (Diagnose):** GitHub Actions sekmesinden **"Teşhis - GitHub IP'sinden 3 Site Testi"** (`diagnose.yml`) iş akışını günün farklı saatlerinde elle tetikleyerek (Run workflow) GitHub sunucularının sitelere anlık erişim durumunu test edebilirsiniz.
