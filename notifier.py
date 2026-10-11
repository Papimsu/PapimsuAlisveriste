import os
import httpx

def send_telegram(message: str, bot_token: str = None, chat_id: str = None) -> bool:
    """
    Telegram Resmi Bot API kullanarak mesaj gönderir.
    bot_token: @BotFather'dan alınan token
    chat_id: Mesajın gideceği kullanıcı, grup veya kanal ID'si
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat:
        print("[UYARI] Telegram ayarları (TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID) tanımlı değil.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": message,
        "disable_web_page_preview": False
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                print("✅ Telegram bildirimi başarıyla gönderildi!")
                return True
            else:
                print(f"⚠️ Telegram API hatası ({resp.status_code}): {resp.text}")
                return False
    except Exception as e:
        print(f"❌ Telegram bildirim hatası: {e}")
        return False

if __name__ == "__main__":
    print("Telegram modülü hazır.")
