# 🏗️ Fiyat Radarı: Mimari Seçenekler ve Yasal Değerlendirme Raporu

> **⚠️ ÖNEMLİ NOT:** Bu doküman yalnızca analiz ve karar desteği amacıyla hazırlanmıştır. Kullanıcıdan açık onay alınmadan sistem mimarisinde herhangi bir altyapı değişikliği yapılmayacaktır.

---

## 📊 1. Mimari Seçeneklerin Karşılaştırma Matrisi

| Kriter | Seçenek 1: GitHub Actions (Mevcut) | Seçenek 2: Cloudflare Cron Triggers | Seçenek 3: Bağımsız VPS (Hetzner / DO) | Seçenek 4: Serverless (Fly.io / Lambda) |
| :--- | :--- | :--- | :--- | :--- |
| **Aylık Maliyet** | **0 TL ($0)** (Public repo) | **0 TL ($0)** (Ücretsiz plan) | **~150 - 250 TL ($4 - $6)** | **0 - 150 TL ($0 - $4)** |
| **IP Bloklanma Riski** | **Orta - Yüksek** (Azure/GitHub IP havuzu) | **Çok Yüksek** (Cloudflare IP'leri hemen tanınır) | **Düşük - Orta** (Sabit veya değiştirilebilir IP) | **Orta** (AWS/GCP havuzları) |
| **TLS/JA3 İmpersonation Desteği** | **Tam Destek** (`curl_cffi` Linux binary) | **Desteklemez** (V8 JS runtime kısıtlı) | **Tam Destek** (Python, Playwright, Chrome) | **Tam Destek** (Docker imajı ile) |
| **Çalışma Zaman Sınırı** | 6 saat / job (Biz 5.5 dk kullanıyoruz) | 30 saniye CPU süresi | Sınırsız | 15 dakika (Lambda) / Sınırsız (Fly) |
| **Bakım Kolaylığı** | **Çok Yüksek** (Sunucu yönetimi yok) | **Çok Yüksek** (Serverless) | **Orta** (Linux güncelleme, systemd, SSH) | **Orta - Yüksek** (Docker & CLI) |
| **Durum Saklama (State)** | Git Repo (JSON commit) | KV / D1 / R2 | SQLite / PostgreSQL / JSON | S3 / DynamoDB / Fly Volumes |

---

## 🔍 2. Seçeneklerin Detaylı Analizi

### Seçenek 1: GitHub Actions (Mevcut Mimari)
* **Nasıl Çalışır:** Her 15 dakikada bir GitHub cron tetiklenir, Ubuntu runner ayağa kalkar, `tracker.py` çalışır, güncel durumu `state.json` olarak repoya işler.
* **Avantajları:**
  - Tamamen ücretsiz ($0).
  - Sunucu bakımı, yama veya güvenlik duvarı derdi yoktur.
  - Kod ve çalışma geçmişi şeffaftır.
* **Dezavantajları ve Riskleri:**
  - GitHub Actions IP adresleri Microsoft Azure veri merkezlerine aittir. E-ticaret siteleri bu IP bloklarını bildiği için ara sıra CAPTCHA veya HTTP 403 engelleyebilir.
  - Cron tetiklemeleri yoğun saatlerde 3-5 dakika gecikebilir.

### Seçenek 2: Cloudflare Cron Triggers + Worker
* **Nasıl Çalışır:** Mevcut Worker'a periyodik `scheduled()` eklenerek doğrudan kazıma işlemi Worker içine taşınır.
* **Neden Önerilmez:**
  - Cloudflare Worker ortamında C kütüphanesi derlenemez; dolayısıyla TLS parmak izini taklit eden `curl_cffi` **çalıştırılamaz**.
  - E-ticaret siteleri Cloudflare Worker IP'lerinden gelen standart `fetch` isteklerini anında engeller.

### Seçenek 3: Bağımsız Küçük VPS (Hetzner Cloud / DigitalOcean)
* **Nasıl Çalışır:** Aylık ~4-5 € bedelle küçük bir Linux sanal sunucu kiralanır. `systemctl` veya cron ile `tracker.py` çalıştırılır.
* **Avantajları:**
  - Temiz bir IP adresi atanır; veri merkezi engellemelerine takılma olasılığı GitHub IP'lerine kıyasla çok daha düşüktür.
  - İstenirse Puppeteer veya Headless Chrome kolayca çalıştırılabilir.
* **Dezavantajları:**
  - Aylık sabit maliyet gerektirir.
  - Sunucu güvenliği (SSH anahtarları, firewall, Linux güncellemeleri) kullanıcıya aittir.

### Seçenek 4: Fly.io / AWS Lambda (Konteyner Mimarisi)
* **Nasıl Çalışır:** `Dockerfile` hazırlanarak uygulama Fly.io veya Lambda üzerinde zamanlanmış görev olarak çalıştırılır.
* **Avantajları:**
  - Ücretsiz kota içinde kalabilir.
  - Bağımsız Docker ortamı sağlar.
* **Dezavantajları:**
  - Kurulum karmaşıktır; GitHub Secrets + Cloudflare + AWS/Fly üçlü entegrasyonu gerektirir.

---

## ⚖️ 3. Yasal ve Hizmet Şartları (Terms of Service) Değerlendirmesi

1. **Kamuya Açık Repo (Public Repository) Durumu:**
   - Repoda hiçbir gizli anahtar (token, şifre, chat ID) bırakılmamıştır (S1, S2, S3 düzeltmeleriyle tamamlanmıştır).
   - Ancak kamuya açık bir depoda kazıma (scraping) kodlarının bulunması, hedeflenen platformların otomatik tarayıcıları tarafından tespit edilebilir.
   - **Tavsiye:** Repoyu GitHub üzerinde **Private** (Gizli) yapmak, sistemin dış gözlerden korunması için en sağlıklı adımdır.

2. **E-Ticaret Platformlarının Hizmet Şartları:**
   - Amazon, Trendyol ve Hepsiburada kullanım şartlarında otomatik veri çekilmesini (scraping) sınırlayan maddeler yer alır.
   - **Mevcut Hafifletici Önlemler:**
     - Sistemimiz ticari veya toplu bir kazıma yapmamaktadır; yalnızca aile kullanımı için 2-30 ürünü 15 dakikada bir kontrol eder.
     - Domain başına en az **3 saniye bekleme süresi** (Rate Limiting) uygulanmıştır.
     - Circuit Breaker ile engel alındığında o domain sorgulanmaktan derhal vazgeçilir; sitelerin sunucularına gereksiz yük bindirilmez.
     - Hiçbir CAPTCHA kırma veya sahte IP rotasyonu hilesi kullanılmamaktadır.

---

## 💡 4. Kıdemli Mühendis Tavsiyesi

1. **Aşama 1 (Şu An):** Mevcut **GitHub Actions + Cloudflare Worker** mimarisiyle devam edin. Eklenen **Circuit Breaker (R1)**, **Time Budget (C3)**, **Config/State Ayrımı (R6)** ve **Rate Limiting** sistemin stabilitesini fazlasıyla sağlamaktadır.
2. **Aşama 2 (Gerekirse):** Eğer ileride GitHub Actions IP'leri siteler tarafından kalıcı olarak engellenirse, aylık 4€ karşılığında Hetzner VPS seçeneğine geçiş tek komutla yapılabilir.
