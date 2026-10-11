# Amazon Fiyat Hatası & Takip Botu

Amazon Türkiye üzerindeki seçtiğiniz ürünleri 7/24 izleyen, fiyat hatalarını ve gizli indirimleri tespit edip WhatsApp'ınıza bildiren sıfır maliyetli sistem.

## 🚀 Özellikler
- **Web Arayüzü:** Link yapıştırın, ürün başlığını ve fiyatını otomatik çeksin.
- **Çift Alarm Mantığı:** İster `X TL altına inerse`, ister `%Y oranında düşerse` şeklinde hedef belirleyin.
- **Bot Koruması Aşımı:** `curl_cffi` ile Chrome TLS parmak izi taklidi (Amazon engellerine takılmaz).
- **7/24 Sıfır Maliyet:** Bilgisayar kapalıyken GitHub Actions cron ile her 15 dakikada bir otomatik çalışır.
- **WhatsApp Bildirimi:** CallMeBot ücretsiz gateway üzerinden doğrudan cebinize mesaj atar.

---

## 📱 WhatsApp Bildirim Kurulumu (1 Dakika - %100 Ücretsiz)
1. Telefonunuzdan WhatsApp'ı açın.
2. Kişilere **+34 644 51 95 23** (veya +34 644 20 44 47) numarasını ekleyin.
3. Bu numaraya WhatsApp'tan şu mesajı gönderin:
   ```text
   I allow callmebot to send me messages
   ```
4. Bot size 10 saniye içinde özel bir `apikey` gönderecektir (Örn: `apikey: 123456`).

---

## 🌐 Web Arayüzünü Kendi Bilgisayarınızda Çalıştırma
Yeni ürün linkleri eklemek veya listeyi yönetmek için:
```bash
cd /Users/Devrim/.gemini/antigravity/scratch/amazon-tracker
python3 app.py
```
Tarayıcınızda açın: **http://127.0.0.1:5001**

---

## ☁️ 7/24 GitHub Actions Kurulumu (Bilgisayar Kapalıyken Çalışması İçin)
1. GitHub'da **Private** (Gizli) yeni bir repo oluşturun (Örn: `amazon-price-radar`).
2. Bu klasördeki tüm dosyaları o depoya push edin:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/KULLANICI_ADINIZ/amazon-price-radar.git
   git push -u origin main
   ```
3. GitHub reponuzda **Settings -> Secrets and variables -> Actions** kısmına gidin:
   - `New repository secret` butonuna tıklayın:
     - İsim: `WHATSAPP_PHONE` | Değer: `905xxxxxxxxx` (Başında + olmadan)
     - İsim: `WHATSAPP_API_KEY` | Değer: CallMeBot'tan aldığınız apikey
4. Bitti! GitHub sunucuları her 15 dakikada bir depodaki `watchlist.json`'ı otomatik tarayacak ve şartlar sağlandığında telefonunuza WhatsApp mesajı atacaktır.
