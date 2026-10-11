import os
import re
import uuid
import httpx
from datetime import datetime
from scraper import fetch_amazon_product, clean_amazon_url
from tracker import load_watchlist, save_watchlist

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8975854875:AAEYoCLeZ9gdzvcXNxoSqHpJ465J_aX3lJ4")
ALLOWED_CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "8958109267"))

# Kullanıcının beklemedeki durumları (State Management)
# {chat_id: {"step": "waiting_rule", "url": "...", "title": "...", "current_price": 20000.0}}
USER_SESSIONS = {}

def send_msg(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        httpx.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False}, timeout=10.0)
    except Exception as e:
        print(f"Telegram mesaj gönderme hatası: {e}")

def handle_message(chat_id: str, text: str):
    chat_str = str(chat_id)
    if chat_str != ALLOWED_CHAT_ID:
        send_msg(chat_str, "⛔ Bu bot özel kullanım içindir.")
        return

    text = text.strip()

    # /start veya /yardim
    if text.startswith("/start") or text.startswith("/yardim"):
        msg = (
            "🤖 *Amazon Fiyat Hatası & İndirim Radarı*\n\n"
            "📌 *Nasıl kullanılır?*\n"
            "Amazon'dan beğendiğin ürünün linkini doğrudan buraya yapıştır!\n"
            "Bot ürünü inceleyip sana hedef fiyatı mı yoksa indirim yüzdesi mi istediğini soracaktır.\n\n"
            "📋 `/liste` - Takipteki tüm ürünleri gösterir\n"
            "🗑️ `/sil <no>` - Ürünü takipten kaldırır (Örn: `/sil 1`)"
        )
        send_msg(chat_str, msg)
        return

    # /liste komutu
    if text == "/liste":
        items = load_watchlist()
        if not items:
            send_msg(chat_str, "📋 Takip listenizde şu anda ürün yok.")
            return

        lines = ["📋 *Takipteki Ürünler:*"]
        for idx, item in enumerate(items, 1):
            price_info = f"{int(item.get('last_checked_price', 0)):,} TL" if item.get('last_checked_price') else "Taranıyor..."
            
            if item.get("target_price"):
                rule_info = f"Hedef: <= {int(item['target_price']):,} TL (Buffer: {int(item.get('effective_target', item['target_price'])):,} TL)"
            else:
                rule_info = f"Eşik: %{int(item.get('discount_threshold_percent', 40))} İndirim"

            lines.append(f"{idx}. *{item['name'][:35]}*\n   💰 Fiyat: {price_info}\n   🎯 {rule_info}")
        
        send_msg(chat_str, "\n\n".join(lines))
        return

    # /sil <no> komutu
    if text.startswith("/sil"):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            send_msg(chat_str, "Kullanım: `/sil 1` (Listeden silmek istediğin ürünün numarasını yaz)")
            return
        idx = int(parts[1]) - 1
        items = load_watchlist()
        if 0 <= idx < len(items):
            removed = items.pop(idx)
            save_watchlist(items)
            send_msg(chat_str, f"🗑️ '{removed['name'][:30]}' başarıyla takipten çıkarıldı.")
        else:
            send_msg(chat_str, "❌ Geçersiz ürün numarası.")
        return

    # 1. ADIM: Beklemede olan bir soru var mı? (Hedef fiyat veya % cevabı)
    session = USER_SESSIONS.get(chat_str)
    if session and session.get("step") == "waiting_rule":
        product_info = session["product"]
        raw_ans = text.replace(" ", "").replace(".", "").replace(",", "")

        target_price = None
        effective_target = None
        discount_percent = None
        rule_desc = ""

        # Yüzde indirim mi yazdı? (Örn: %30 veya 30%)
        if "%" in text:
            pct_num = re.sub(r"[^\d]", "", text)
            if pct_num:
                discount_percent = float(pct_num)
                rule_desc = f"Normal fiyattan %{int(discount_percent)} veya daha fazla indirim olduğunda"
            else:
                send_msg(chat_str, "❌ Geçersiz yüzde formatı. Örn: `%30` yazabilirsin.")
                return
        # Rakam mı yazdı? (Örn: 18000)
        elif raw_ans.isdigit():
            entered_price = float(raw_ans)
            # %5 buffer / tolerans ekle (örn: 18.000 TL yazdıysa 18.900 TL altına düşünce de yakalasın)
            effective_target = entered_price * 1.05
            target_price = entered_price
            rule_desc = f"{int(entered_price):,} TL altına indiğinde (%5 buffer ile {int(effective_target):,} TL eşiği)"
        else:
            send_msg(chat_str, "❓ Anlayamadım. Lütfen sadece rakam (örn: `18000`) veya yüzde (örn: `%30`) yaz.")
            return

        # Ürünü listeye kaydet
        items = load_watchlist()
        new_item = {
            "id": str(uuid.uuid4())[:8],
            "name": product_info["title"],
            "url": product_info["url"],
            "normal_price": product_info["price"] or 0.0,
            "target_price": target_price,
            "effective_target": effective_target, # Bufferlı hedef
            "discount_threshold_percent": discount_percent,
            "last_checked_price": product_info["price"],
            "last_alert_sent_price": None,
            "active": True
        }

        items.append(new_item)
        save_watchlist(items)
        del USER_SESSIONS[chat_str]

        confirm_msg = (
            f"✅ *Ürün Başarıyla Takibe Alındı!*\n\n"
            f"📦 *{product_info['title'][:50]}*\n"
            f"💰 Normal / Güncel Fiyat: {int(product_info['price']):,} TL\n"
            f"🎯 Kural: {rule_desc}\n\n"
            f"Fiyat bu seviyeye indiğinde anında sana mesaj atacağım! 🚀"
        )
        send_msg(chat_str, confirm_msg)
        return

    # 2. ADIM: Yeni bir Amazon Linki mi geldi?
    amazon_match = re.search(r"https?://[^\s]*amazon\.[^\s]+", text)
    if amazon_match:
        raw_url = amazon_match.group(0)
        clean_url = clean_amazon_url(raw_url)

        items = load_watchlist()
        if any(i["url"] == clean_url for i in items):
            send_msg(chat_str, "⚠️ Bu ürün zaten takip listenizde mevcut!")
            return

        send_msg(chat_str, "🔍 Ürün bilgileri Amazon'dan kontrol ediliyor, lütfen bekle...")
        data = fetch_amazon_product(clean_url)

        if not data.get("success") or data.get("price") is None:
            send_msg(chat_str, "⚠️ Ürünün fiyat bilgisi Amazon'dan okunamadı veya ürün şu anda stokta yok.")
            return

        curr_p = int(data["price"])
        title = data.get("title", "Amazon Ürünü")

        # Oturuma kaydet ve kullanıcıya sor
        USER_SESSIONS[chat_str] = {
            "step": "waiting_rule",
            "product": {
                "title": title,
                "url": clean_url,
                "price": data["price"]
            }
        }

        ask_msg = (
            f"📦 *{title[:50]}*\n"
            f"💰 *Güncel Fiyat:* {curr_p:,} TL\n\n"
            f"Nasıl bir alarm kuralı koymak istersin?\n\n"
            f"🔹 *Hedef Fiyat için:* Sadece rakam yaz (Örn: `{int(curr_p * 0.85)}`)\n"
            f"🔹 *Yüzde İndirim için:* Yüzde yaz (Örn: `%30`)\n\n"
            f"*(Hedef fiyata otomatik %5 buffer eklenir, küsürat girmenize gerek yoktur)*"
        )
        send_msg(chat_str, ask_msg)
        return

    send_msg(chat_str, "❓ Bir Amazon ürün linki yapıştırabilir veya `/yardim` yazabilirsin.")

def run_bot():
    print("🤖 Telegram Etkileşimli Bot Dinleyicisi Başlatıldı...")
    offset = None
    with httpx.Client(timeout=35.0) as client:
        while True:
            try:
                params = {"timeout": 25}
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
