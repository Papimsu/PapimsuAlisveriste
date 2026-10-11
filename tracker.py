import json
import time
import os
import random
from datetime import datetime
from scraper import fetch_amazon_product
from notifier import send_telegram

WATCHLIST_PATH = os.path.join(os.path.dirname(__file__), "watchlist.json")

def load_watchlist():
    if not os.path.exists(WATCHLIST_PATH):
        return []
    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_watchlist(items):
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def check_all_products():
    """Tüm aktif ürünleri kontrol eder ve şartlar sağlanırsa alarm verir."""
    items = load_watchlist()
    if not items:
        print("Takip listesinde ürün bulunamadı.")
        return

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {len(items)} ürün kontrol ediliyor...")

    updated = False
    for item in items:
        if not item.get("active", True):
            continue

        url = item["url"]
        name = item.get("name") or "Amazon Ürünü"
        normal_price = float(item.get("normal_price") or 0.0)
        target_price = float(item.get("target_price") or 0.0) if item.get("target_price") else None
        discount_percent_target = float(item.get("discount_threshold_percent") or 0.0) if item.get("discount_threshold_percent") else None
        
        print(f"\n🔎 İnceleniyor: {name}")
        data = fetch_amazon_product(url)

        if not data["success"] or data["price"] is None:
            print(f"⚠️ Fiyat okunamadı: {data.get('error') or 'Element bulunamadı'}")
            time.sleep(random.uniform(2.0, 4.0))
            continue

        current_price = data["price"]
        item["last_checked_price"] = current_price
        item["last_checked_at"] = datetime.now().isoformat()
        
        # Eğer ürünün gerçek adı veya normal fiyatı henüz kaydedilmediyse otomatik güncelle
        if data.get("title") and (not item.get("name") or "Amazon" in item.get("name") or "Ürün" in item.get("name") or "Urun" in item.get("name")):
            item["name"] = data["title"]
        if not item.get("normal_price") or float(item.get("normal_price", 0)) == 0:
            item["normal_price"] = current_price
            normal_price = current_price
            
        updated = True

        print(f"💰 Güncel Fiyat: {current_price:,.2f} TL (Normal: {normal_price:,.2f} TL)")

        # İndirim oranını hesapla (normal fiyata göre)
        calc_discount = 0.0
        if normal_price > 0:
            calc_discount = ((normal_price - current_price) / normal_price) * 100.0

        trigger_alert = False
        reasons = []

        # Kural 1: Hedef Fiyatın Altına Düştü mü? (Varsa %5 bufferlı effective_target kullanılır)
        threshold = item.get("effective_target") or target_price
        if threshold and current_price <= float(threshold):
            trigger_alert = True
            reasons.append(f"Hedef fiyat eşiği olan {int(threshold):,} TL altına indi!")

        # Kural 2: Belirlenen İndirim Yüzdesi Aşıldı mı?
        if discount_percent_target and normal_price > 0 and calc_discount >= discount_percent_target:
            trigger_alert = True
            reasons.append(f"%{calc_discount:.1f} indirim yakalandı! (Eşik: %{discount_percent_target})")

        # Daha önce aynı fiyattan alarm gönderildiyse tekrar spam yapma
        if trigger_alert:
            last_alerted = item.get("last_alert_sent_price")
            if last_alerted == current_price:
                print("ℹ️ Bu fiyat için zaten alarm gönderilmişti, tekrar atılmıyor.")
            else:
                print("🚨🚨 ALARM ŞARTI SAĞLANDI! 🚨🚨")
                reason_text = "\n".join(reasons)
                seller_note = "✅ Satıcı: Amazon.com.tr" if data.get("is_amazon_seller") else "ℹ️ Satıcı: Üçüncü Taraf"
                
                real_title = data.get("title") or name
                alert_message = (
                    f"🚨 FİYAT HATASI / İNDİRİM ALARMI! 🚨\n\n"
                    f"📦 Ürün: {real_title}\n"
                    f"🏷️ Güncel Fiyat: {current_price:,.2f} TL\n"
                    f"📉 Normal Fiyat: {normal_price:,.2f} TL\n"
                    f"🔥 Durum: {reason_text}\n"
                    f"{seller_note}\n\n"
                    f"🛒 Hemen İncele:\n{url}"
                )
                
                send_telegram(alert_message)
                item["last_alert_sent_price"] = current_price

        # Amazon ban koruması için 2-4 saniye bekle
        time.sleep(random.uniform(2.0, 4.0))

    if updated:
        save_watchlist(items)
    print("\n✅ Kontrol turu tamamlandı.")

if __name__ == "__main__":
    check_all_products()
