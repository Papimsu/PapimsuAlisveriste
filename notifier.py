import os
import httpx

# Öncelikli bildirim hedefi: Ortak Aile Grubu (-1004357390519)
FALLBACK_BOT_TOKEN = "8975854875:AAEYoCLeZ9gdzvcXNxoSqHpJ465J_aX3lJ4"
FALLBACK_CHAT_ID = "-1004357390519"

def send_telegram(message: str, bot_token: str = None, chat_id: str = None) -> bool:
    """
    Telegram Resmi Bot API kullanarak mesaj gönderir.
    Varsayılan olarak ortak Telegram grubuna (-1004357390519) atar.
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN") or FALLBACK_BOT_TOKEN
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or FALLBACK_CHAT_ID

    if not token or not chat:
        print("[UYARI] Telegram ayarları tanımlı değil.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": str(chat),
        "text": message,
        "disable_web_page_preview": False
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                print(f"✅ Telegram bildirimi başarıyla gönderildi! (Hedef: {chat})")
                return True
            else:
                print(f"⚠️ Telegram API hatası ({resp.status_code}): {resp.text}")
                return False
    except Exception as e:
        print(f"❌ Telegram bildirim hatası: {e}")
        return False

if __name__ == "__main__":
    print("Telegram modülü hazır.")
