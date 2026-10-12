import json
import time
import os
import random
from datetime import datetime, timezone, timedelta
from scraper import fetch_amazon_product
from notifier import send_telegram

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")
STATUS_PATH = os.path.join(BASE_DIR, "status.json")

CHECK_INTERVAL_MIN = 15          # GitHub Actions cron aralığı (check_prices.yml ile aynı olmalı)
FAILURE_ALERT_THRESHOLD = 4      # Üst üste kaç başarısız turda sistem uyarısı atılsın (4 tur = 1 saat)
BIG_DROP_RATIO = 0.60            # Normal fiyatın %60'ının altı -> alarmdan önce 2. kez çekip teyit et
TR_TZ = timezone(timedelta(hours=3))

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    """Atomik yazım: yarım kalmış/bozuk dosya oluşmasını engeller."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_watchlist():
    return load_json(WATCHLIST_PATH, [])

def save_watchlist(items):
    save_json(WATCHLIST_PATH, items)

def write_status(status, **fields):
    now = datetime.now(timezone.utc)
    status.update(fields)
    status["last_run_utc"] = now.isoformat(timespec="seconds")
    status["last_run_tr"] = now.astimezone(TR_TZ).strftime("%d.%m.%Y %H:%M")
    save_json(STATUS_PATH, status)

def seller_line(data):
    name = data.get("seller_name", "Satıcı")
    if data.get("is_official_seller"):
        return f"✅ Satıcı: {name} (Resmi Satıcı)"
    return f"🏪 Satıcı: {name} (Pazaryeri Satıcısı)"

def process_item(item, stats):
    """Tek bir ürünü kontrol eder. Watchlist'te değişiklik olduysa True döner."""
    url = item["url"]
    name = item.get("name") or "Ürün"
    normal_price = float(item.get("normal_price") or 0.0)
    target_price = float(item.get("target_price") or 0.0) if item.get("target_price") else None
    discount_percent_target = float(item.get("discount_threshold_percent") or 0.0) if item.get("discount_threshold_percent") else None

    print(f"\n🔎 İnceleniyor: {name}")
    data = fetch_amazon_product(url)

    # --- 1) Stokta yok: site çalışıyor, bu bir sistem arızası DEĞİL ---
    if data.get("out_of_stock"):
        stats["out_of_stock"] += 1
        print("📭 Ürün stokta yok.")
        item["consecutive_failures"] = 0
        item["last_error"] = None
        item["error_alert_sent"] = False
        item["last_checked_at"] = datetime.now().isoformat()
        if not item.get("out_of_stock"):
            msg = (
                f"📭 ÜRÜN STOKTA KALMADI\n\n"
                f"📦 Ürün: {data.get('title') or name}\n"
                f"🌐 Platform: {data.get('platform', '')}\n\n"
                f"Takip devam ediyor; tekrar stoğa girince haber vereceğim.\n{url}"
            )
            if send_telegram(msg):
                item["out_of_stock"] = True
        return True

    # --- 2) Fiyat okunamadı ---
    if not data["success"] or data["price"] is None:
        err_msg = data.get("error") or "Element veya fiyat bulunamadı"
        print(f"⚠️ Fiyat okunamadı: {err_msg}")
        stats["failed"] += 1
        fail_count = item.get("consecutive_failures", 0) + 1
        item["consecutive_failures"] = fail_count
        item["last_error"] = err_msg

        # ">=" sayesinde Telegram o an ulaşılamazsa bir sonraki turda tekrar denenir
        if fail_count >= FAILURE_ALERT_THRESHOLD and not item.get("error_alert_sent", False):
            minutes = fail_count * CHECK_INTERVAL_MIN
            system_alert = (
                f"⚠️ SİSTEM UYARISI: Ürün Fiyatı Okunamıyor!\n\n"
                f"📦 Ürün: {name}\n"
                f"🌐 Platform: {data.get('platform', 'Bilinmeyen')}\n"
                f"❌ Hata: {err_msg}\n"
                f"🔗 Link:\n{url}\n\n"
                f"ℹ️ {fail_count} kontrol turudur (~{minutes} dk) veri alınamıyor. "
                f"Olası sebepler: site geçici olarak engelledi, sayfa yapısı değişti veya ürün kaldırıldı. "
                f"Sorun sürerken tekrar mesaj atılmayacak, düzelince haber vereceğim."
            )
            if send_telegram(system_alert):
                item["error_alert_sent"] = True
        return True

    # --- 3) Büyük düşüşte teyit (yanlış okumaya karşı sahte alarm koruması) ---
    current_price = data["price"]
    if normal_price > 0 and current_price < normal_price * BIG_DROP_RATIO:
        print(f"🧐 Büyük düşüş ({current_price:,.2f} TL) - teyit için tekrar çekiliyor...")
        time.sleep(3)
        confirm = fetch_amazon_product(url)
        if (not confirm.get("success") or confirm.get("price") is None
                or abs(confirm["price"] - current_price) > max(1.0, current_price * 0.01)):
            print("⚠️ Teyit edilemedi, bu tur alarm atılmıyor.")
            item["last_checked_at"] = datetime.now().isoformat()
            return True
        data = confirm
        current_price = confirm["price"]

    stats["ok"] += 1

    # --- 4) Arıza sonrası iyileşme bildirimi ---
    if item.get("error_alert_sent", False):
        recovery_alert = (
            f"✅ SİSTEM NORMALE DÖNDÜ!\n\n"
            f"📦 Ürün: {data.get('title') or name}\n"
            f"💰 Güncel Fiyat: {current_price:,.2f} TL başarıyla okundu.\n"
            f"🛡️ Takip sorunsuz şekilde devam ediyor."
        )
        if send_telegram(recovery_alert):
            item["error_alert_sent"] = False

    # --- 5) Tekrar stoğa girdi bildirimi ---
    if item.get("out_of_stock"):
        back_msg = (
            f"📦 TEKRAR STOKTA!\n\n"
            f"📦 Ürün: {data.get('title') or name}\n"
            f"🏷️ Fiyat: {current_price:,.2f} TL\n"
            f"{seller_line(data)}\n\n{url}"
        )
        if send_telegram(back_msg):
            item["out_of_stock"] = False

    item["consecutive_failures"] = 0
    item["last_error"] = None
    item["last_checked_price"] = current_price
    item["last_checked_at"] = datetime.now().isoformat()

    # Gerçek ürün adı / normal fiyat henüz kaydedilmediyse otomatik doldur
    if data.get("title") and (not item.get("name") or "Amazon" in item.get("name") or "Ürün" in item.get("name") or "Urun" in item.get("name")):
        item["name"] = data["title"]
    if not item.get("normal_price") or float(item.get("normal_price", 0)) == 0:
        item["normal_price"] = current_price
        normal_price = current_price

    print(f"💰 Güncel Fiyat: {current_price:,.2f} TL (Normal: {normal_price:,.2f} TL)")

    calc_discount = 0.0
    if normal_price > 0:
        calc_discount = ((normal_price - current_price) / normal_price) * 100.0

    trigger_alert = False
    reasons = []

    # Kural 1: Hedef fiyat (%5 bufferlı effective_target)
    threshold = item.get("effective_target") or target_price
    if threshold and current_price <= float(threshold):
        trigger_alert = True
        reasons.append(f"Hedef fiyat eşiği olan {int(threshold):,} TL altına indi!")

    # Kural 2: İndirim yüzdesi
    if discount_percent_target and normal_price > 0 and calc_discount >= discount_percent_target:
        trigger_alert = True
        reasons.append(f"%{calc_discount:.1f} indirim yakalandı! (Eşik: %{discount_percent_target})")

    last_alerted = item.get("last_alert_sent_price")
    if trigger_alert:
        # Sadece YENİ bir dip fiyatta haber ver (eşik altında fiyat oynadıkça spam olmasın)
        if last_alerted is not None and current_price >= float(last_alerted):
            print(f"ℹ️ {float(last_alerted):,.2f} TL için zaten alarm gönderilmişti, tekrar atılmıyor.")
        else:
            print("🚨🚨 ALARM ŞARTI SAĞLANDI! 🚨🚨")
            alert_message = (
                f"🚨 FİYAT HATASI / İNDİRİM ALARMI! 🚨\n\n"
                f"📦 Ürün: {data.get('title') or name}\n"
                f"🏷️ Güncel Fiyat: {current_price:,.2f} TL\n"
                f"📉 Normal Fiyat: {normal_price:,.2f} TL\n"
                f"🔥 Durum: {chr(10).join(reasons)}\n"
                f"{seller_line(data)}\n\n"
                f"🛒 Hemen İncele:\n{url}"
            )
            # Sadece Telegram mesajı GERÇEKTEN gittiyse kaydet; gitmezse bir sonraki turda tekrar denenir
            if send_telegram(alert_message):
                item["last_alert_sent_price"] = current_price
            else:
                print("❌ Alarm gönderilemedi, bir sonraki turda tekrar denenecek.")
    elif last_alerted is not None:
        # Fiyat eşiğin üstüne çıktı -> hafızayı sıfırla, bir sonraki düşüşte (aynı fiyat olsa bile) tekrar haber ver
        print("↩️ Fiyat eşiğin üstüne çıktı, alarm hafızası sıfırlandı.")
        item["last_alert_sent_price"] = None

    return True

def check_all_products():
    """Tüm aktif ürünleri kontrol eder ve şartlar sağlanırsa alarm verir."""
    status = load_json(STATUS_PATH, {}) if os.path.exists(STATUS_PATH) else {}
    if not isinstance(status, dict):
        status = {}

    # watchlist.json bozuksa: sessizce çökmek yerine TEK SEFER haber ver
    try:
        items = load_watchlist()
        if not isinstance(items, list):
            raise ValueError("watchlist.json bir liste değil")
    except Exception as e:
        print(f"❌ watchlist.json okunamadı: {e}")
        if not status.get("watchlist_error_alert_sent"):
            if send_telegram(f"🔴 SİSTEM HATASI: Takip listesi (watchlist.json) okunamıyor!\n\nHata: {e}\n\nFiyat kontrolleri durdu. Lütfen kontrol edin."):
                status["watchlist_error_alert_sent"] = True
        write_status(status, state="watchlist_error", error=str(e))
        return

    if status.get("watchlist_error_alert_sent"):
        send_telegram("✅ Takip listesi tekrar okunabiliyor, fiyat kontrolleri devam ediyor.")
        status["watchlist_error_alert_sent"] = False

    stats = {"ok": 0, "failed": 0, "out_of_stock": 0}
    active = [i for i in items if i.get("active", True)]
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {len(active)} ürün kontrol ediliyor...")

    for item in active:
        try:
            process_item(item, stats)
        except Exception as e:
            # Tek bir üründeki beklenmedik hata tüm turu öldürmesin
            print(f"❌ Beklenmedik hata ({item.get('url')}): {e}")
            stats["failed"] += 1
            item["consecutive_failures"] = item.get("consecutive_failures", 0) + 1
            item["last_error"] = f"Beklenmedik hata: {e}"
        time.sleep(random.uniform(2.0, 4.0))  # Ban koruması

    save_watchlist(items)
    write_status(status, state="ok", error=None, items_checked=len(active), **stats)
    print(f"\n✅ Kontrol turu tamamlandı. Başarılı: {stats['ok']}, Hatalı: {stats['failed']}, Stokta yok: {stats['out_of_stock']}")

if __name__ == "__main__":
    check_all_products()
