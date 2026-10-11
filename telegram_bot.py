import os
import re
import uuid
import httpx
from datetime import datetime
from scraper import fetch_amazon_product, clean_amazon_url
from tracker import load_watchlist, save_watchlist

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8975854875:AAEYoCLeZ9gdzvcXNxoSqHpJ465J_aX3lJ4")
ALLOWED_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "8958109267"))

def send_msg(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    httpx.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False})

def handle_message(chat_id: str, text: str):
    # Sadece senin mesajlarını kabul et
    if str(chat_id) != ALLOWED_CHAT_ID:
        send_msg(chat_id, "⛔ Üzgünüm, bu özel bir bottur.")
        return

    text = text.strip()

    # /start veya /yardim
    if text.startswith("/start") or text.startswith("/yardim"):
        msg = (
            "🤖 *Amazon Fiyat Radarı Botuna Hoş Geldin!*\n\n"
            "Nasıl ürün eklenir?\n"
            "1️⃣ *Sadece Link:* `https://amazon.com.tr/dp/...` (Varsayılan: %40 indirim alarmı)\n"
            "2️⃣ *Hedef Fiyat:* `https://amazon.com.tr/dp/... 5000` (5.000 TL altına düşünce)\n"
            "3️⃣ *Yüzde İndirim:* `https://amazon.com.tr/dp/... %50` (%50 düşünce)\n\n"
            "Komutlar:\n"
            "📋 `/liste` - Takip edilen ürünleri listeler\n"
            "🗑️ `/sil <no>` - Ürünü takipten çıkarır"
        )
        send_msg(chat_id, msg)
        return

    # /liste
    if text == "/liste":
        items = load_watchlist()
        if not items:
            send_msg(chat_id, "📋 Takip listenizde şu anda ürün yok.")
            return

        lines = ["📋 *Takipteki Ürünler:*"]
        for idx, item in enumerate(items, 1):
            price_info = f"{item.get('last_checked_price'):,.2f} TL" if item.get('last_checked_price') else "Taranıyor..."
            rule_info = f"Eşik: <= {item['target_price']:,.2f} TL" if item.get("target_price") else f"Eşik: %{item.get('discount_threshold_percent', 40)} İndirim"
            lines.append(f"{idx}. {item['name'][:30]}...\n   💰 Fiyat: {price_info} | {rule_info}")
        
        send_msg(chat_id, "\n\n".join(lines))
        return

    # /sil <no>
    if text.startswith("/sil"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            send_msg(chat_id, "Kullanım: `/sil 1` (Listeden silmek istediğin ürünün numarasını yaz)")
            return
        idx = int(parts[1]) - 1
        items = load_watchlist()
        if 0 <= idx < len(items):
            removed = items.pop(idx)
            save_watchlist(items)
            send_msg(chat_id, f"🗑️ '{removed['name'][:30]}' başarıyla takipten çıkarıldı.")
        else:
            send_msg(chat_id, "❌ Geçersiz ürün numarası.")
        return

    # Link Ekleme Kontrolü
    amazon_match = re.search(r"https?://[^\s]*amazon\.[^\s]+", text)
    if amazon_match:
        raw_url = amazon_match.group(0)
        clean_url = clean_amazon_url(raw_url)
        
        # Kullanıcı ekstra hedef fiyat veya yüzde yazmış mı?
        rest_text = text.replace(raw_url, "").strip()
        target_price = None
        discount_percent = 40.0 # varsayılan

        if "%" in rest_text:
            pct_match = re.search(r"%?(\d+)", rest_text)
            if pct_match:
                discount_percent = float(pct_match.group(1))
        elif rest_text:
            price_match = re.search(r"(\d+(?:[\.,]\d+)?)", rest_text)
            if price_match:
                try:
                    target_price = float(price_match.group(1).replace(".", "").replace(",", "."))
                except Exception:
                    pass

        send_msg(chat_id, "🔍 Ürün bilgileri Amazon'dan çekiliyor, lütfen bekle...")
        data = fetch_amazon_product(clean_url)

        items = load_watchlist()
        # Zaten var mı?
        if any(i["url"] == clean_url for i in items):
            send_msg(chat_id, "⚠️ Bu ürün zaten takip listenizde ekli!")
            return

        title = data.get("title") or "Amazon Ürünü"
        current_price = data.get("price") or 0.0

        new_item = {
            "id": str(uuid.uuid4())[:8],
            "name": title,
            "url": clean_url,
            "normal_price": current_price,
            "target_price": target_price,
            "discount_threshold_percent": discount_percent if target_price is None else None,
            "last_checked_price": current_price,
            "last_alert_sent_price": None,
            "active": True
        }

        items.append(new_item)
        save_watchlist(items)

        rule_desc = f"Hedef: {target_price:,.2f} TL altına düşünce" if target_price else f"Hedef: %{discount_percent} indirim yakalanınca"
        reply = (
            f"✅ *Ürün Takibe Alındı!*\n\n"
            f"📦 *{title[:50]}*\n"
            f"💰 Güncel Fiyat: {current_price:,.2f} TL\n"
            f"🎯 Kural: {rule_desc}\n\n"
            f"Fiyat düştüğünde anında buraya alarm göndereceğim!"
        )
        send_msg(chat_id, reply)
        return

    send_msg(chat_id, "❓ Anlayamadım. Bir Amazon linki yapıştırabilir veya `/yardim` yazabilirsin.")

def run_bot():
    """Telegram Long Polling Dinleyicisi"""
    print("🤖 Telegram Bot Dinleyicisi Başlatıldı...")
    offset = None
    with httpx.Client(timeout=30.0) as client:
        while True:
            try:
                params = {"timeout": 20}
                if offset:
                    params["offset"] = offset

                url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
                resp = client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        message = update.get("message")
                        if message and "text" in message:
                            chat_id = message["chat"]["id"]
                            text = message["text"]
                            handle_message(chat_id, text)
            except Exception as e:
                print(f"Hata: {e}")

if __name__ == "__main__":
    run_bot()
