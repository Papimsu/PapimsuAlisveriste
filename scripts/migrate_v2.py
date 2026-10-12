#!/usr/bin/env python3
"""
scripts/migrate_v2.py
Migrates legacy watchlist.json into:
1. watchlist.json (Config - written only by Cloudflare Worker)
2. state.json (Machine state - written only by GitHub Actions)
"""
import json
import os
import sys
from datetime import datetime, timezone

def migrate(watchlist_path="watchlist.json", state_path="state.json"):
    if not os.path.exists(watchlist_path):
        print(f"Hata: {watchlist_path} bulunamadı.")
        return False

    with open(watchlist_path, "r", encoding="utf-8") as f:
        legacy_data = json.load(f)

    if not isinstance(legacy_data, list):
        print("Hata: watchlist.json liste biçiminde değil.")
        return False

    clean_watchlist = []
    clean_state = {}

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for item in legacy_data:
        item_id = item.get("id") or os.urandom(4).hex()
        
        # Config fields (only user configuration)
        target_price = None
        if item.get("target_price") is not None:
            try:
                target_price = float(item["target_price"])
            except (ValueError, TypeError):
                target_price = None

        discount_pct = None
        if item.get("discount_threshold_percent") is not None:
            try:
                discount_pct = float(item["discount_threshold_percent"])
            except (ValueError, TypeError):
                discount_pct = None

        config_item = {
            "id": item_id,
            "url": item.get("url", ""),
            "name": item.get("name") or "Ürün",
            "target_price": target_price,
            "discount_threshold_percent": discount_pct,
            "active": bool(item.get("active", True)),
            "created_at": item.get("created_at") or now_iso
        }
        clean_watchlist.append(config_item)

        # State fields (machine state and tracking history)
        last_price = None
        if item.get("last_checked_price") is not None:
            try:
                last_price = float(item["last_checked_price"])
            except (ValueError, TypeError):
                last_price = None
        elif item.get("normal_price") is not None:
            try:
                last_price = float(item["normal_price"])
            except (ValueError, TypeError):
                last_price = None

        last_alert = None
        if item.get("last_alert_sent_price") is not None:
            try:
                last_alert = float(item["last_alert_sent_price"])
            except (ValueError, TypeError):
                last_alert = None

        price_history = item.get("price_history", [])
        if not price_history and last_price:
            price_history = [{
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "price": last_price
            }]

        clean_state[item_id] = {
            "id": item_id,
            "title": item.get("name") or "Ürün",
            "last_price": last_price,
            "last_checked_at": item.get("last_checked_at"),
            "last_alert_sent_price": last_alert,
            "consecutive_failures": int(item.get("consecutive_failures", 0)),
            "error_alert_sent": bool(item.get("error_alert_sent", False)),
            "out_of_stock": bool(item.get("out_of_stock", False)),
            "price_history": price_history
        }

    # Atomically write migrated files
    tmp_watchlist = watchlist_path + ".tmp"
    with open(tmp_watchlist, "w", encoding="utf-8") as f:
        json.dump(clean_watchlist, f, ensure_ascii=False, indent=2)
    os.replace(tmp_watchlist, watchlist_path)

    tmp_state = state_path + ".tmp"
    with open(tmp_state, "w", encoding="utf-8") as f:
        json.dump(clean_state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_state, state_path)

    print(f"✅ Başarılı: {len(clean_watchlist)} ürün ayrıştırıldı -> {watchlist_path} ve {state_path}")
    return True

if __name__ == "__main__":
    w_path = sys.argv[1] if len(sys.argv) > 1 else "watchlist.json"
    s_path = sys.argv[2] if len(sys.argv) > 2 else "state.json"
    migrate(w_path, s_path)
