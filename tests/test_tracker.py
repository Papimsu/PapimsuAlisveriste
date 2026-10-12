import statistics
from tracker import (
    evaluate_alert_conditions,
    update_price_history,
    get_reference_price,
    CIRCUIT_BREAKER_FAILURES
)

def test_target_price_reached():
    item_config = {"target_price": 4000.0, "discount_threshold_percent": None}
    item_state = {"last_alert_sent_price": None, "near_target_alert_sent": False}

    # Fiyat hedefin altında -> TARGET_REACHED
    alert_type, reason = evaluate_alert_conditions(item_config, item_state, 3950.0)
    assert alert_type == "TARGET_REACHED"
    assert "4,000.00 TL" in reason

def test_target_price_spam_prevention():
    item_config = {"target_price": 4000.0, "discount_threshold_percent": None}
    item_state = {"last_alert_sent_price": 3950.0, "near_target_alert_sent": False}

    # Aynı fiyat veya önemsiz dalgalanma (< %1 düşüş) -> Spam engelleme (None)
    alert_type, reason = evaluate_alert_conditions(item_config, item_state, 3950.0)
    assert alert_type is None

    alert_type, reason = evaluate_alert_conditions(item_config, item_state, 3940.0) # Sadece ~%0.25 düşüş
    assert alert_type is None

    # %1'den fazla düşüş (3950 * 0.99 = 3910.5) -> Yeni alarm tetiklenir
    alert_type, reason = evaluate_alert_conditions(item_config, item_state, 3900.0)
    assert alert_type == "TARGET_REACHED"

def test_near_target_buffer_alert():
    item_config = {"target_price": 4000.0, "discount_threshold_percent": None}
    item_state = {"last_alert_sent_price": None, "near_target_alert_sent": False}

    # %5 buffer içinde (4000 < fiyat <= 4200) -> NEAR_TARGET
    alert_type, reason = evaluate_alert_conditions(item_config, item_state, 4150.0)
    assert alert_type == "NEAR_TARGET"
    assert "Hedef fiyata çok yaklaştı" in reason

    # Zaten haber verildiyse tekrar atılmaz
    item_state["near_target_alert_sent"] = True
    alert_type, _ = evaluate_alert_conditions(item_config, item_state, 4150.0)
    assert alert_type is None

def test_price_climb_resets_memory():
    item_config = {"target_price": 4000.0, "discount_threshold_percent": None}
    item_state = {"last_alert_sent_price": 3800.0, "near_target_alert_sent": True}

    # Fiyat hedefin ve buffer'ın üstüne çıktı (> 4200 TL) -> Hafıza sıfırlanır
    alert_type, _ = evaluate_alert_conditions(item_config, item_state, 4500.0)
    assert alert_type is None
    assert item_state["last_alert_sent_price"] is None
    assert item_state["near_target_alert_sent"] is False

def test_discount_threshold_percent():
    item_config = {"target_price": None, "discount_threshold_percent": 20.0}
    item_state = {
        "last_price": 10000.0,
        "last_alert_sent_price": None,
        "price_history": [{"date": "2026-10-01", "price": 10000.0}]
    }

    # %25 indirim -> DISCOUNT_REACHED
    alert_type, reason = evaluate_alert_conditions(item_config, item_state, 7500.0)
    assert alert_type == "DISCOUNT_REACHED"
    assert "%25.0 indirim" in reason

def test_price_history_and_median_reference():
    item_state = {"price_history": []}

    # 1. Günlük eklemeler
    update_price_history(item_state, 5000.0)
    assert len(item_state["price_history"]) == 1

    # Aynı gün daha düşük fiyat gelirse güncellenir
    update_price_history(item_state, 4800.0)
    assert len(item_state["price_history"]) == 1
    assert item_state["price_history"][0]["price"] == 4800.0

    # 2. 7 günden az veri varken ilk fiyat referanstır
    assert get_reference_price(item_state) == 4800.0

    # 3. >= 7 gün olunca medyan hesaplanır
    history = []
    prices = [100.0, 110.0, 105.0, 120.0, 115.0, 130.0, 125.0, 140.0]
    for i, p in enumerate(prices):
        history.append({"date": f"2026-10-{i+1:02d}", "price": p})
    item_state["price_history"] = history

    expected_median = statistics.median(prices)
    assert get_reference_price(item_state) == expected_median

def test_circuit_breaker_tripping_and_skipping():
    """R1: Aynı domainde 2 ardışık BLOCKED sonrası devre kesici devreye girer."""
    domain_blocked = {}
    circuit_breakers = {}
    stats = {}
    collected_failures = []

    item1 = {"url": "https://www.amazon.com.tr/dp/B01", "name": "Item 1"}
    item2 = {"url": "https://www.amazon.com.tr/dp/B02", "name": "Item 2"}
    item3 = {"url": "https://www.amazon.com.tr/dp/B03", "name": "Item 3"}

    # Mock fetch returning BLOCKED
    from tracker import process_single_item
    import tracker

    original_fetch = tracker.fetch_product_data
    try:
        tracker.fetch_product_data = lambda url: {
            "success": False, "price": None, "error_type": "BLOCKED", "error": "Bot checkpoint"
        }

        # 1. İstek BLOCKED (sayac = 1)
        state1 = {}
        process_single_item(item1, state1, stats, domain_blocked, circuit_breakers, collected_failures)
        assert circuit_breakers.get("www.amazon.com.tr") is not True

        # 2. İstek BLOCKED (sayac = 2 -> Devre kesici tetiklenmeli)
        state2 = {}
        process_single_item(item2, state2, stats, domain_blocked, circuit_breakers, collected_failures)
        assert circuit_breakers.get("www.amazon.com.tr") is True

        # 3. İstek: Devre kesici aktif olduğundan kazıma çağrılmadan doğrudan atlanmalı
        state3 = {}
        skipped = not process_single_item(item3, state3, stats, domain_blocked, circuit_breakers, collected_failures)
        assert skipped is True
        assert stats.get("skipped_due_circuit_breaker") == 1
    finally:
        tracker.fetch_product_data = original_fetch

def test_aggregated_alerts_five_failures_single_message():
    """R2: 5 hatalı üründe her biri için ayrı spam atılmaz; tek bir toplu özet mesajı gönderilir."""
    from tracker import handle_aggregated_alerts
    import tracker

    collected_failures = []
    for i in range(5):
        collected_failures.append({
            "name": f"Hatalı Ürün {i+1}",
            "domain": "www.amazon.com.tr",
            "error_type": "PARSE_FAIL",
            "error_msg": "Element bulunamadı",
            "consecutive_failures": 4, # FAILURE_ALERT_THRESHOLD eşiğinde
            "url": f"https://www.amazon.com.tr/dp/{i}",
            "item_state": {"error_alert_sent": False}
        })

    sent_alerts = []
    original_send = tracker.send_telegram
    try:
        tracker.send_telegram = lambda msg, **kw: (sent_alerts.append(msg) or True)
        handle_aggregated_alerts(collected_failures, {})

        # Tam olarak 1 adet toplu uyarı gitmeli (5 ayrı mesaj değil!)
        assert len(sent_alerts) == 1
        assert "SİSTEM UYARISI" in sent_alerts[0]
        for i in range(5):
            assert f"Hatalı Ürün {i+1}" in sent_alerts[0]
            assert collected_failures[i]["item_state"]["error_alert_sent"] is True
    finally:
        tracker.send_telegram = original_send

def test_time_budget_incremental_save_and_no_repeat_alert():
    """C3: fetch'i 30 sn bekleten mock ile bütçe aşılmıyor, işlenen ürünlerin state'i diskte, ikinci çalıştırmada aynı alarm tekrar gitmiyor."""
    import tempfile
    import json
    import os
    import tracker

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_watchlist = os.path.join(tmpdir, "watchlist.json")
        tmp_state = os.path.join(tmpdir, "state.json")
        tmp_status = os.path.join(tmpdir, "status.json")

        items = [
            {"id": "p1", "url": "https://www.amazon.com.tr/dp/P1", "name": "Ürün 1", "target_price": 4000.0, "active": True},
            {"id": "p2", "url": "https://www.amazon.com.tr/dp/P2", "name": "Ürün 2", "target_price": 4000.0, "active": True},
            {"id": "p3", "url": "https://www.amazon.com.tr/dp/P3", "name": "Ürün 3", "target_price": 4000.0, "active": True}
        ]

        with open(tmp_watchlist, "w", encoding="utf-8") as f:
            json.dump(items, f)

        orig_w = tracker.WATCHLIST_PATH
        orig_s = tracker.STATE_PATH
        orig_stat = tracker.STATUS_PATH
        orig_fetch = tracker.fetch_product_data
        orig_send = tracker.send_telegram
        orig_time = tracker.time.time

        simulated_time = [1000.0]
        sent_messages = []

        def mock_time():
            return simulated_time[0]

        def mock_fetch(url):
            # Her fetch çağrısı 30 saniye sürer
            simulated_time[0] += 30.0
            return {
                "success": True,
                "price": 3500.0,
                "title": "Ürün",
                "seller_name": "Amazon",
                "is_official_seller": True,
                "platform": "Amazon",
                "out_of_stock": False
            }

        try:
            tracker.WATCHLIST_PATH = tmp_watchlist
            tracker.STATE_PATH = tmp_state
            tracker.STATUS_PATH = tmp_status
            tracker.fetch_product_data = mock_fetch
            tracker.send_telegram = lambda msg, **kw: (sent_messages.append(msg) or True)
            tracker.time.time = mock_time

            # 1. Tur: 50s bütçe ile çalıştır
            tracker.check_all_products(time_budget_sec=50)

            # Bütçe aşılmadı ve kalan 2 ürün atlandı
            with open(tmp_status, "r", encoding="utf-8") as f:
                stat = json.load(f)
            assert stat["skipped_due_to_budget"] == 2
            assert stat["ok"] == 1

            # İşlenen ürünün state'i diskte anında mevcut
            with open(tmp_state, "r", encoding="utf-8") as f:
                disk_state = json.load(f)
            assert "p1" in disk_state
            assert disk_state["p1"]["last_price"] == 3500.0
            assert disk_state["p1"]["last_alert_sent_price"] == 3500.0
            assert len(sent_messages) == 1

            # 2. Tur: İşlenen ürün (p1) tekrar kontrol edildiğinde (aynı fiyat 3500 TL için) alarm TEKRAR gitmemeli
            with open(tmp_watchlist, "w", encoding="utf-8") as f:
                json.dump([items[0]], f)
            tracker.check_all_products(time_budget_sec=50)
            assert len(sent_messages) == 1  # p1 için ikinci alarm gitmedi!
        finally:
            tracker.WATCHLIST_PATH = orig_w
            tracker.STATE_PATH = orig_s
            tracker.STATUS_PATH = orig_stat
            tracker.fetch_product_data = orig_fetch
            tracker.send_telegram = orig_send
            tracker.time.time = orig_time
