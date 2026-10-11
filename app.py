import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, flash
from tracker import load_watchlist, save_watchlist, check_all_products
from scraper import fetch_amazon_product, clean_amazon_url

app = Flask(__name__)
app.secret_key = "amazon-tracker-super-secret-key"

@app.route("/")
def index():
    items = load_watchlist()
    return render_template("index.html", items=items)

@app.route("/add", methods=["POST"])
def add_product():
    url = request.form.get("url", "").strip()
    name = request.form.get("name", "").strip()
    target_price_raw = request.form.get("target_price", "").strip()
    discount_percent_raw = request.form.get("discount_percent", "").strip()

    if not url:
        flash("Lütfen geçerli bir Amazon ürün linki girin.", "error")
        return redirect(url_for("index"))

    clean_url = clean_amazon_url(url)
    items = load_watchlist()

    # Link zaten var mı kontrol et
    if any(item["url"] == clean_url for item in items):
        flash("Bu ürün zaten takip listenizde mevcut!", "error")
        return redirect(url_for("index"))

    # Otomatik ürün bilgilerini ve ilk fiyatını çek
    flash("Ürün bilgileri Amazon'dan çekiliyor...", "info")
    product_data = fetch_amazon_product(clean_url)

    final_name = name or product_data.get("title") or "Amazon Ürünü"
    current_price = product_data.get("price")

    target_price = float(target_price_raw) if target_price_raw else None
    discount_percent = float(discount_percent_raw) if discount_percent_raw else None

    # Eğer hedef veya yüzde verilmediyse varsayılan olarak %40 indirim alarmı koy
    if target_price is None and discount_percent is None:
        discount_percent = 40.0

    new_item = {
        "id": str(uuid.uuid4())[:8],
        "name": final_name,
        "url": clean_url,
        "normal_price": current_price or 0.0,
        "target_price": target_price,
        "discount_threshold_percent": discount_percent,
        "last_checked_price": current_price,
        "last_alert_sent_price": None,
        "active": True
    }

    items.append(new_item)
    save_watchlist(items)

    flash(f"'{final_name[:30]}...' başarıyla takibe alındı!", "success")
    return redirect(url_for("index"))

@app.route("/delete/<item_id>")
def delete_product(item_id):
    items = load_watchlist()
    items = [item for item in items if item["id"] != item_id]
    save_watchlist(items)
    flash("Ürün takipten kaldırıldı.", "success")
    return redirect(url_for("index"))

@app.route("/run-check")
def run_check():
    check_all_products()
    flash("Tüm ürünler başarıyla kontrol edildi!", "success")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
