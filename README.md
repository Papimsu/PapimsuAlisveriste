# 🎯 Fiyat Radarı (Amazon TR, Trendyol, Hepsiburada)

Amazon Türkiye, Trendyol ve Hepsiburada üzerindeki ürünleri 7/24 izleyen, indirimleri ve fiyat hatalarını tespit edip Telegram grubuna anında alarm gönderen sıfır maliyetli ($0) serverless fiyat takip sistemi.

---

## 🚀 Temel Özellikler

* **Telegram Bot Entegrasyonu:** Sohbet grubuna ürün linki atıldığında Cloudflare Worker üzerinden otomatik yakalanır.
* **Akıllı Kural Tanımlama:** İster hedef fiyat (TL), ister yüzde indirim oranı (%) ile kural belirleme.
* **Çift Seviyeli Bildirim:** Fiyat hedefe indiğinde birincil alarm (🚨); %5 yakınına geldiğinde tek seferlik bilgilendirme (🔔).
* **Anti-Spam Koruması:** Tekrar eden gereksiz bildirimleri engellemek için en az %1 yeni düşüş şartı.
* **Anti-Bot Dayanıklılığı:** `curl_cffi` ile modern TLS/JA3 parmak izi taklidi (Chrome 124 / Safari 17).
* **Circuit Breaker:** E-ticaret siteleri geçici bot engeli koyduğunda o domaini pas geçer, IP'yi yakmaz.
* **7/24 Otopilot:** GitHub Actions cron döngüsü ile periyodik kontrol (`*/15 * * * *`).
* **Veri Güvenliği (Config vs State):** `watchlist.json` (kullanıcı listesi) ile `state.json` (makine durumu) ayrı dosyalarda tutularak yazma çakışmaları tamamen önlenir.

---

## 📁 Mimari Bileşenler

1. **Cloudflare Worker (`cloudflare_worker.js`):** Telegram webhook'unu karşılar, link ve kuralları doğrular, `watchlist.json` dosyasına işler.
2. **GitHub Actions (`.github/workflows/check_prices.yml`):** Her 15 dakikada bir `tracker.py` motorunu çalıştırır.
3. **Scraper & Tracker (`scraper.py`, `tracker.py`):** Ürünleri tarar, doğrular, alarmları `notifier.py` ile Telegram'a iletir, makine durumunu `state.json` ve `status.json`'a kaydeder.

---

## 🛠️ Kurulum Adımları

### 1. Telegram Botu Oluşturma
1. Telegram'da [@BotFather](https://t.me/BotFather) ile yeni bir bot oluşturun ve token'ınızı alın (`<TELEGRAM_BOT_TOKEN>`).
2. Botu takip yapmak istediğiniz aile grubuna ekleyin ve gruba `/start` yazın.
3. Grubun `chat_id` değerini öğrenin (örn: `-10012345678`).

### 2. Cloudflare Worker Ayarları
Cloudflare Dashboard -> **Workers & Pages** -> Worker'ınızı seçin -> **Settings** -> **Variables and Secrets**:
* `BOT_TOKEN`: `<TELEGRAM_BOT_TOKEN>`
* `WEBHOOK_SECRET`: Güvenli rastgele bir string (örn: `openssl rand -hex 20`)
* `ALLOWED_CHAT_IDS`: Yetkili sohbet ID'leri virgülle ayrılmış (örn: `12345678,-10012345678`)
* `GITHUB_TOKEN`: GitHub Personal Access Token (repo scope)
* `GITHUB_REPO`: `kullanici_adi/repo_adi`
* KV Namespace: `SESSIONS` adıyla oluşturulup Worker'a bağlanmalıdır.

### 3. Telegram Webhook Kaydı
Terminalden webhook'u secret token korumasıyla kaydedin:
```bash
curl -F "url=https://<WORKER_ADRESINIZ>.workers.dev" \
     -F "secret_token=<WEBHOOK_SECRET>" \
     "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook"
```

### 4. GitHub Secrets Ayarları
GitHub Repository -> **Settings** -> **Secrets and variables** -> **Actions**:
* `TELEGRAM_BOT_TOKEN`: `<TELEGRAM_BOT_TOKEN>`
* `TELEGRAM_CHAT_ID`: `<CHAT_ID>`

---

## 🧪 Testleri Çalıştırma

Projeyi yerel ortamda test etmek için:

```bash
# Python testleri (16 test - yerel HTML fixture'ları ile sıfır canlı istek)
PYTHONPATH=. pytest -v tests/

# Worker birim testleri (7 test)
node --test tests/worker.test.js

# Manuel canlı site duman testi (Smoke Test)
python3 scripts/smoke_test.py
```

Detaylı teknik mimari ve kılavuzlar için [SISTEM_DOKUMANTASYONU.md](SISTEM_DOKUMANTASYONU.md) ve alternatif mimari seçenekleri için [docs/ARCHITECTURE_OPTIONS.md](docs/ARCHITECTURE_OPTIONS.md) dosyalarını inceleyebilirsiniz.
