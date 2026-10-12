# 🎯 Fiyat Radarı (Amazon TR, Trendyol, Hepsiburada)

Amazon Türkiye, Trendyol ve Hepsiburada üzerindeki ürünleri 7/24 izleyen, indirimleri ve fiyat hatalarını tespit edip Telegram grubuna anında alarm gönderen serverless fiyat takip sistemi.

## 🚀 Temel Özellikler
- **Telegram Bot Entegrasyonu:** Sohbet grubuna ürün linki atıldığında Cloudflare Worker üzerinden otomatik yakalanır.
- **Çift Alarm Mantığı:** İster hedef fiyat (TL), ister yüzde indirim oranı (%) ile kural belirleme.
- **Anti-Bot Dayanıklılığı:** `curl_cffi` ile modern TLS/JA3 parmak izi impersonation.
- **7/24 Otopilot:** GitHub Actions cron döngüsü ile periyodik kontrol (`*/15 * * * *`).
- **Telegram Alarmları:** Fiyat hedefe ulaştığında veya hedefe yaklaştığında anında zengin bildirim.

## 📁 Mimari
1. **Cloudflare Worker (`cloudflare_worker.js`):** Telegram webhook'unu karşılar, link ve kuralları doğrular, `watchlist.json` dosyasına işler.
2. **GitHub Actions (`check_prices.yml`):** `tracker.py` motorunu çalıştırır.
3. **Scraper & Tracker (`scraper.py`, `tracker.py`):** Ürünleri tarar, doğrular, alarmları `notifier.py` ile Telegram'a iletir, makine durumunu `state.json` ve `status.json`'a kaydeder.

Detaylı mimari ve kurulum kılavuzu için [SISTEM_DOKUMANTASYONU.md](file:///Users/Devrim/.gemini/antigravity/scratch/amazon-tracker/SISTEM_DOKUMANTASYONU.md) dosyasını inceleyin.
