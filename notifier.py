import os
import re
import urllib.parse
import httpx

def send_whatsapp(message: str, phone: str = None, api_key: str = None) -> bool:
    """
    CallMeBot Free WhatsApp Gateway kullanarak mesaj gönderir.
    phone: Uluslararası format (örn: +905551234567 veya 905551234567)
    api_key: CallMeBot'tan alınan ücretsiz apikey
    """
    phone = phone or os.environ.get("WHATSAPP_PHONE")
    api_key = api_key or os.environ.get("WHATSAPP_API_KEY")

    if not phone or not api_key:
        print("[UYARI] WhatsApp ayarları (WHATSAPP_PHONE veya WHATSAPP_API_KEY) tanımlı değil.")
        return False

    # Telefon numarasından + işaretini ve boşlukları temizle
    phone_clean = re.sub(r"[^\d]", "", phone)
    encoded_msg = urllib.parse.quote_plus(message)
    
    url = f"https://api.callmebot.com/whatsapp.php?phone={phone_clean}&text={encoded_msg}&apikey={api_key}"

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            if resp.status_code == 200 and "Message queued" in resp.text:
                print("✅ WhatsApp bildirimi başarıyla gönderildi!")
                return True
            else:
                print(f"⚠️ WhatsApp API yanıtı: {resp.text}")
                return False
    except Exception as e:
        print(f"❌ WhatsApp bildirim hatası: {e}")
        return False

if __name__ == "__main__":
    # Test amaçlı
    test_msg = "🚨 FİYAT HATASI / İNDİRİM TEST MESAJI 🚨\nSistem başarıyla kuruldu!"
    # send_whatsapp(test_msg, "905xxxxxxxxx", "your_apikey")
    print("WhatsApp modülü hazır.")
