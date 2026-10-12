import json
import os
import tempfile
from scripts.migrate_v2 import migrate

def test_migrate_v2_preserves_all_data_and_is_idempotent():
    legacy_watchlist = [
        {
            "id": "item1",
            "name": "Pioneer DJ Controller",
            "url": "https://www.amazon.com.tr/dp/B0BK25SY9G",
            "normal_price": 17416.0,
            "target_price": 17000.0,
            "effective_target": 17850.0,
            "discount_threshold_percent": None,
            "last_checked_price": 17416.0,
            "last_alert_sent_price": 17416.0,
            "active": True,
            "last_checked_at": "2026-10-12T12:00:00Z",
            "consecutive_failures": 0,
            "last_error": None,
            "error_alert_sent": False,
            "out_of_stock": False
        },
        {
            "id": "item2",
            "name": "Philips Hue Kit (Tükendi)",
            "url": "https://www.amazon.com.tr/dp/B07FYDGD7R",
            "normal_price": 4000.0,
            "target_price": 3800.0,
            "discount_threshold_percent": 15.0,
            "last_checked_price": 3900.0,
            "last_alert_sent_price": None,
            "active": False,
            "last_checked_at": "2026-10-12T11:00:00Z",
            "consecutive_failures": 2,
            "last_error": "Stok yok",
            "error_alert_sent": True,
            "out_of_stock": True
        }
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        w_path = os.path.join(tmpdir, "watchlist.json")
        s_path = os.path.join(tmpdir, "state.json")

        with open(w_path, "w", encoding="utf-8") as f:
            json.dump(legacy_watchlist, f)

        # 1. İlk Çalıştırma
        success1 = migrate(w_path, s_path)
        assert success1 is True

        with open(w_path, "r", encoding="utf-8") as f:
            w1 = json.load(f)
        with open(s_path, "r", encoding="utf-8") as f:
            s1 = json.load(f)

        # Ürün sayısı tam olmalı
        assert len(w1) == 2
        assert len(s1) == 2

        # Hedef fiyat ve indirim korundu mu?
        assert w1[0]["target_price"] == 17000.0
        assert w1[1]["discount_threshold_percent"] == 15.0
        assert w1[1]["active"] is False

        # State bilgileri (last_alert_sent_price ve out_of_stock) korundu mu?
        assert s1["item1"]["last_alert_sent_price"] == 17416.0
        assert s1["item1"]["out_of_stock"] is False
        assert s1["item2"]["out_of_stock"] is True
        assert s1["item2"]["last_price"] == 3900.0

        # 2. İkinci Çalıştırma (Idempotency testi)
        success2 = migrate(w_path, s_path)
        assert success2 is True

        with open(w_path, "r", encoding="utf-8") as f:
            w2 = json.load(f)
        with open(s_path, "r", encoding="utf-8") as f:
            s2 = json.load(f)

        # İki çalıştırma sonrası dosyalar birebir aynı kalmalı, veri kaybı olmamalı
        assert w1 == w2
        assert s1 == s2
