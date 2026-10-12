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
