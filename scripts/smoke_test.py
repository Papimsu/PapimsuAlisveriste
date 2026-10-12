#!/usr/bin/env python3
"""
scripts/smoke_test.py
Manuel canlı site duman testi (Smoke Test).
3 platformdan (Amazon TR, Trendyol, Hepsiburada) 1'er gerçek link dener.
Anti-bot engellerini, CAPTCHA durumunu ve fiyat yakalamayı test eder.
Rate-limit: Her domain arasında en az 3 saniye mola verir.
"""
import time
import sys
from scraper import fetch_product_data

TEST_URLS = {
    "Amazon TR": "https://www.amazon.com.tr/dp/B0BK25SY9G",
    "Trendyol": "https://www.trendyol.com/pioneer/ddj-flx4-tasinabilir-2-kanalli-dj-controller-p-471697117",
    "Hepsiburada": "https://www.hepsiburada.com/pioneer-dj-ddj-flx4-2-kanal-dj-controller-akilli-fader-ozelligi-ile-puruzsuz-gecisler-pm-HBC00003MXQS9"
}

def run_smoke_test():
    print("=" * 60)
    print("🚀 FİYAT RADARI - MANUEL CANLI SİTE SMOKE TESTİ")
    print("=" * 60)

    results = {}
    for name, url in TEST_URLS.items():
        print(f"\n📡 Test ediliyor: {name}")
        print(f"🔗 Link: {url}")
        t0 = time.time()
        res = fetch_product_data(url)
        elapsed = round(time.time() - t0, 2)

        results[name] = res
        if res["success"]:
            status_symbol = "✅ BAŞARILI"
            details = f"Fiyat: {res['price']:,.2f} TL | Satıcı: {res['seller_name']} ({'Resmi' if res['is_official_seller'] else 'Pazaryeri'})"
        elif res.get("out_of_stock"):
            status_symbol = "📭 STOKTA YOK"
            details = "Ürün mevcut değil veya tükendi olarak algılandı."
        elif res.get("error_type") == "BLOCKED":
            status_symbol = "🛡️ ENGELLENDİ (BLOCKED)"
            details = f"Site bot koruması (Robot check / Captcha) devreye girdi: {res.get('error')}"
        else:
            status_symbol = f"⚠️ HATA [{res.get('error_type') or 'FAIL'}]"
            details = f"Hata mesajı: {res.get('error')}"

        print(f"Sonuç: {status_symbol} ({elapsed} sn)")
        print(f"Detay: {details}")
        print("-" * 50)
        time.sleep(3.5)

    print("\n" + "=" * 60)
    print("📊 ÖZET TABLOSU:")
    print("=" * 60)
    for name, res in results.items():
        status = "OK" if res["success"] else (res.get("error_type") or "FAIL")
        price = f"{res['price']:,.2f} TL" if res.get("price") else "-"
        print(f"{name:15} | Durum: {status:10} | Fiyat: {price}")
    print("=" * 60)

if __name__ == "__main__":
    run_smoke_test()
