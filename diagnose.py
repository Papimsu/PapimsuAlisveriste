"""GitHub Actions sunucusunun (veri merkezi IP'si) 3 siteye erişebildiğini doğrular. Telegram'a mesaj atmaz."""
import json, os, time
from datetime import datetime, timezone
from scraper import fetch_product_data

TEST_URLS = {
    "Amazon": "https://www.amazon.com.tr/dp/B0BK25SY9G",
    "Trendyol": "https://www.trendyol.com/pioneer/ddj-flx4-tasinabilir-2-kanalli-dj-controller-p-471697117",
    "Hepsiburada": "https://www.hepsiburada.com/pioneer-dj-ddj-flx4-2-kanal-dj-controller-akilli-fader-ozelligi-ile-puruzsuz-gecisler-pm-HBC00003MXQS9",
}

report = {"run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": {}}
for name, url in TEST_URLS.items():
    t0 = time.time()
    d = fetch_product_data(url)
    report["results"][name] = {
        "success": d["success"], "price": d["price"], "seller": d["seller_name"],
        "official": d["is_official_seller"], "out_of_stock": d["out_of_stock"],
        "error": d["error"], "title": (d["title"] or "")[:60], "seconds": round(time.time() - t0, 1),
    }
    print(name, report["results"][name])
    time.sleep(3)

os.makedirs("diagnostics", exist_ok=True)
with open("diagnostics/github_ip_test.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
