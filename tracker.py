import json
import time
import os
import random
import statistics
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from scraper import fetch_product_data, get_domain
from notifier import send_telegram

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
STATUS_PATH = os.path.join(BASE_DIR, "status.json")

# Varsayılan ayarlar
CHECK_INTERVAL_MIN = 15
FAILURE_ALERT_THRESHOLD = 4      # Üst üste 4 başarısız tur (~1 saat) sonra uyarı
DEFAULT_TIME_BUDGET_SEC = 330    # GitHub Actions 8dk sınırı için 330s (~5.5 dk)
BIG_DROP_RATIO = 0.60            # Fiyat normalin %60'ının altına inerse teyit çekimi
CIRCUIT_BREAKER_FAILURES = 2     # Üst üste 2 BLOCKED sonrası o domaini bu tur atla

TR_TZ = timezone(timedelta(hours=3))

def load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ JSON okuma hatası ({path}): {e}")
        return default

def save_json(path: str, data: Any):
    """Atomik dosya yazımı: veri kaybını ve yarım yazılmış dosyaları önler."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def load_watchlist() -> List[Dict[str, Any]]:
    """R6: watchlist.json yalnızca konfigürasyon içerir."""
    data = load_json(WATCHLIST_PATH, [])
    return data if isinstance(data, list) else []

def load_state() -> Dict[str, Dict[str, Any]]:
    """R6: state.json ürün ID'sine göre makine durumunu tutar."""
    raw = load_json(STATE_PATH, {})
    if isinstance(raw, list):
        return {item["id"]: item for item in raw if "id" in item}
    elif isinstance(raw, dict):
        return raw
    return {}

def save_state(state: Dict[str, Dict[str, Any]]):
    save_json(STATE_PATH, state)

def write_status(status: Dict[str, Any], **fields):
    now = datetime.now(timezone.utc)
    status.update(fields)
    status["last_run_utc"] = now.isoformat(timespec="seconds")
    status["last_run_tr"] = now.astimezone(TR_TZ).strftime("%d.%m.%Y %H:%M")
    save_json(STATUS_PATH, status)

def seller_line(data: Dict[str, Any]) -> str:
    name = data.get("seller_name", "Satıcı")
    if data.get("is_official_seller"):
        return f"✅ Satıcı: {name} (Resmi Satıcı)"
    return f"🏪 Satıcı: {name} (Pazaryeri Satıcısı)"

# ==============================================================================
# A5: REFERANS FİYAT VE FİYAT GEÇMİŞİ (60 Günlük Medyan)
# ==============================================================================
def update_price_history(item_state: Dict[str, Any], current_price: float):
    history: List[Dict[str, Any]] = item_state.get("price_history", [])
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    found = False
    for entry in history:
        if entry.get("date") == today_str:
            entry["price"] = min(float(entry.get("price", current_price)), current_price)
            found = True
            break

    if not found:
        history.append({"date": today_str, "price": current_price})

    # Maksimum 60 günlük geçmiş sakla
    if len(history) > 60:
        history = history[-60:]

    item_state["price_history"] = history

def get_reference_price(item_state: Dict[str, Any]) -> Optional[float]:
    """
    A5: İndirim yüzdesi için referans fiyat:
    >= 7 günlük veri varsa son 30 günün medyanı; aksi takdirde ilk görülen fiyat.
    """
    history = item_state.get("price_history", [])
    if not history:
        return item_state.get("last_price")

    prices = [float(h["price"]) for h in history if h.get("price") and float(h["price"]) > 0]
    if not prices:
        return item_state.get("last_price")

    if len(prices) >= 7:
        recent = prices[-30:]
        return statistics.median(recent)
    return prices[0]

# ==============================================================================
# A1 & A2: HEDEF FİYAT VE SPAM ÖNLEME MANTIĞI
# ==============================================================================
def evaluate_alert_conditions(
    item_config: Dict[str, Any],
    item_state: Dict[str, Any],
    current_price: float
) -> Tuple[Optional[str], Optional[str]]:
    """
    A1: Hedef fiyat semantiği ve buffer ayrımı.
    A2: Spam önleme (en az %1 düşüş şartı).
    Döner: (alert_type, reason_description)
    """
    target_price = item_config.get("target_price")
    discount_pct = item_config.get("discount_threshold_percent")
    last_alert_price = item_state.get("last_alert_sent_price")
    near_alert_sent = item_state.get("near_target_alert_sent", False)

    ref_price = get_reference_price(item_state) or current_price

    # 1. Kural: Hedef Fiyat (price <= target_price)
    if target_price and target_price > 0:
        target = float(target_price)
        if current_price <= target:
            # A2 Spam Kontrolü: Daha önce alarm gönderildiyse en az %1 daha düşük olmalı
            if last_alert_price is not None and current_price > float(last_alert_price) * 0.99:
                return None, None
            reason = f"🎯 Hedef fiyat olan {target:,.2f} TL seviyesine / altına indi!"
            return "TARGET_REACHED", reason

        # A1 Buffer: Hedefe yaklaştı (%5 pay içinde)
        buffer_threshold = target * 1.05
        if target < current_price <= buffer_threshold:
            if not near_alert_sent and last_alert_price is None:
                diff = current_price - target
                reason = f"🔔 Hedef fiyata çok yaklaştı! (Hedef: {target:,.2f} TL, Fark: +{diff:,.2f} TL)"
                return "NEAR_TARGET", reason

        # Fiyat hedefin ve buffer'ın üstüne çıktıysa hafızayı sıfırla
        if current_price > buffer_threshold:
            item_state["last_alert_sent_price"] = None
            item_state["near_target_alert_sent"] = False

    # 2. Kural: Yüzde İndirim Oranı
    if discount_pct and discount_pct > 0 and ref_price > 0:
        threshold = float(discount_pct)
        calc_discount = ((ref_price - current_price) / ref_price) * 100.0
        if calc_discount >= threshold:
            if last_alert_price is not None and current_price > float(last_alert_price) * 0.99:
                return None, None
            reason = f"📉 %{calc_discount:.1f} indirim yakalandı! (Referans: {ref_price:,.2f} TL, Eşik: %{threshold:.0f})"
            return "DISCOUNT_REACHED", reason

        if current_price >= ref_price:
            item_state["last_alert_sent_price"] = None

    return None, None

# ==============================================================================
# TEK ÜRÜN İŞLEME DÖNGÜSÜ (C3, R1, A1, A2, A5, A6)
# ==============================================================================
def process_single_item(
    item_config: Dict[str, Any],
    item_state: Dict[str, Any],
    stats: Dict[str, Any],
    domain_blocked: Dict[str, int],
    circuit_breakers: Dict[str, bool],
    collected_failures: List[Dict[str, Any]]
) -> bool:
    url = item_config["url"]
    name = item_config.get("name") or item_state.get("title") or "Ürün"
    domain = get_domain(url)

    # R1: Circuit Breaker Kontrolü
    if circuit_breakers.get(domain):
        print(f"🛑 [Circuit Breaker Aktif] {domain} domaini için ürün atlanıyor: {name}")
        stats["skipped_due_circuit_breaker"] = stats.get("skipped_due_circuit_breaker", 0) + 1
        return False

    print(f"\n🔎 İnceleniyor: {name} ({domain})")
    data = fetch_product_data(url)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- 1. Stokta Yok ---
    if data.get("out_of_stock"):
        stats["out_of_stock"] = stats.get("out_of_stock", 0) + 1
        print("📭 Ürün stokta yok.")
        item_state["consecutive_failures"] = 0
        item_state["last_error"] = None
        item_state["last_checked_at"] = now_iso

        if not item_state.get("out_of_stock"):
            msg = (
                f"📭 <b>ÜRÜN STOKTA KALMADI</b>\n\n"
                f"📦 Ürün: {data.get('title') or name}\n"
                f"🌐 Platform: {data.get('platform', '')}\n\n"
                f"Takip devam ediyor; tekrar stoğa girince haber vereceğim.\n{url}"
            )
            if send_telegram(msg, parse_mode="HTML"):
                item_state["out_of_stock"] = True
        domain_blocked[domain] = 0
        return True

    # --- 2. Başarısızlık / Hata Durumu ---
    if not data["success"] or data["price"] is None:
        err_type = data.get("error_type") or "PARSE_FAIL"
        err_msg = data.get("error") or err_type
        print(f"⚠️ Hata [{err_type}]: {err_msg}")
        stats["failed"] = stats.get("failed", 0) + 1

        fail_count = int(item_state.get("consecutive_failures", 0)) + 1
        item_state["consecutive_failures"] = fail_count
        item_state["last_error"] = f"{err_type}: {err_msg}"
        item_state["last_checked_at"] = now_iso

        # R1: Circuit breaker tetikleme kontrolü
        if err_type == "BLOCKED":
            domain_blocked[domain] = domain_blocked.get(domain, 0) + 1
            if domain_blocked[domain] >= CIRCUIT_BREAKER_FAILURES:
                circuit_breakers[domain] = True
                print(f"🚨 CIRCUIT BREAKER DEVREYE GİRDİ: {domain} üst üste {CIRCUIT_BREAKER_FAILURES} kez engellendi!")
        else:
            domain_blocked[domain] = 0

        # R2 için hata kaydı topla
        collected_failures.append({
            "name": name,
            "domain": domain,
            "error_type": err_type,
            "error_msg": err_msg,
            "consecutive_failures": fail_count,
            "url": url,
            "item_state": item_state
        })
        return False

    # --- 3. Başarılı Fiyat Okundu ---
    domain_blocked[domain] = 0
    current_price = float(data["price"])

    # Büyük düşüş teyidi (sahte alarm önleme)
    ref_price = get_reference_price(item_state)
    if ref_price and ref_price > 0 and current_price < ref_price * BIG_DROP_RATIO:
        print(f"🧐 Büyük düşüş ({current_price:,.2f} TL vs ref {ref_price:,.2f} TL) - teyit için yeniden çekiliyor...")
        time.sleep(3.0)
        confirm = fetch_product_data(url)
        if (not confirm.get("success") or confirm.get("price") is None
                or abs(confirm["price"] - current_price) > max(1.0, current_price * 0.01)):
            print("⚠️ Fiyat teyit edilemedi, bu tur alarm atlanıyor.")
            item_state["last_checked_at"] = now_iso
            return True
        current_price = float(confirm["price"])
        data = confirm

    stats["ok"] = stats.get("ok", 0) + 1

    # Arıza sonrası iyileşme bildirimi
    if item_state.get("error_alert_sent", False):
        rec_msg = (
            f"✅ <b>SİSTEM NORMALE DÖNDÜ</b>\n\n"
            f"📦 Ürün: {data.get('title') or name}\n"
            f"💰 Güncel Fiyat: {current_price:,.2f} TL başarıyla okundu.\n"
            f"🛡️ Takip sorunsuz devam ediyor."
        )
        if send_telegram(rec_msg, parse_mode="HTML"):
            item_state["error_alert_sent"] = False

    # Tekrar stoğa girdi bildirimi
    if item_state.get("out_of_stock"):
        back_msg = (
            f"📦 <b>TEKRAR STOKTA!</b>\n\n"
            f"📦 Ürün: {data.get('title') or name}\n"
            f"🏷️ Fiyat: {current_price:,.2f} TL\n"
            f"{seller_line(data)}\n\n{url}"
        )
        if send_telegram(back_msg, parse_mode="HTML"):
            item_state["out_of_stock"] = False

    # State güncellemesi
    item_state["consecutive_failures"] = 0
    item_state["last_error"] = None
    item_state["last_price"] = current_price
    item_state["last_checked_at"] = now_iso
    if data.get("title"):
        item_state["title"] = data["title"]

    update_price_history(item_state, current_price)
    ref_price = get_reference_price(item_state) or current_price

    print(f"💰 Güncel Fiyat: {current_price:,.2f} TL (Referans: {ref_price:,.2f} TL)")

    # Alarm kurallarını değerlendir (A1 & A2)
    alert_type, reason = evaluate_alert_conditions(item_config, item_state, current_price)
    if alert_type:
        header = "🚨 FİYAT HATASI / İNDİRİM ALARMI! 🚨" if alert_type != "NEAR_TARGET" else "🔔 HEDEFE YAKLAŞTI! 🔔"
        alert_msg = (
            f"{header}\n\n"
            f"📦 Ürün: {item_state.get('title') or name}\n"
            f"🏷️ Güncel Fiyat: <b>{current_price:,.2f} TL</b>\n"
            f"📊 Referans Fiyat: {ref_price:,.2f} TL\n"
            f"🔥 Durum: {reason}\n"
            f"{seller_line(data)}\n\n"
            f"🛒 Hemen İncele:\n{url}"
        )
        if send_telegram(alert_msg, parse_mode="HTML"):
            if alert_type in ("TARGET_REACHED", "DISCOUNT_REACHED"):
                item_state["last_alert_sent_price"] = current_price
            elif alert_type == "NEAR_TARGET":
                item_state["near_target_alert_sent"] = True
        else:
            print("❌ Telegram alarmı gönderilemedi.")

    return True

# ==============================================================================
# R2: TOPLU HATA BİLDİRİMİ (AGGREGATED SYSTEM ALERTS)
# ==============================================================================
def handle_aggregated_alerts(
    collected_failures: List[Dict[str, Any]],
    circuit_breakers: Dict[str, bool]
):
    """
    R2: Her hata için ayrı mesaj atmak yerine tek bir özet alarm gönderir.
    Sadece FAILURE_ALERT_THRESHOLD (~1 saat) aşanlar veya Circuit Breaker devreye girdiğinde atar.
    """
    critical_failures = [
        f for f in collected_failures
        if f["consecutive_failures"] >= FAILURE_ALERT_THRESHOLD and not f["item_state"].get("error_alert_sent", False)
    ]

    has_tripped_cb = any(circuit_breakers.values())
    if not critical_failures and not has_tripped_cb:
        return

    lines = ["⚠️ <b>SİSTEM UYARISI: Fiyat Kontrollerinde Aksaklık</b>\n"]

    if has_tripped_cb:
        tripped_domains = [d for d, tripped in circuit_breakers.items() if tripped]
        lines.append(f"🛑 <b>Circuit Breaker Devrede:</b> {', '.join(tripped_domains)} domainleri bot koruması (BLOCKED) nedeniyle bu tur durduruldu.\n")

    if critical_failures:
        lines.append(f"❌ <b>Sürekli Hata Veren Ürünler (≥ {FAILURE_ALERT_THRESHOLD} turdur ulaşılamıyor):</b>")
        for f in critical_failures:
            lines.append(f"• <b>{f['name']}</b> ({f['domain']}) -> <i>{f['error_type']}: {f['error_msg']}</i>")
            f["item_state"]["error_alert_sent"] = True

    lines.append("\nℹ️ Bir sonraki turda erişim tekrar denenecektir.")
    summary_message = "\n".join(lines)
    send_telegram(summary_message, parse_mode="HTML")

# ==============================================================================
# ANA ÇALIŞTIRMA GÖREVİ (C3 Time Budget & Sıralama)
# ==============================================================================
def check_all_products(time_budget_sec: Optional[int] = None):
    start_time = time.time()
    budget = int(time_budget_sec or os.environ.get("TIME_BUDGET_SEC", DEFAULT_TIME_BUDGET_SEC))

    status = load_json(STATUS_PATH, {})
    if not isinstance(status, dict):
        status = {}

    watchlist = load_watchlist()
    if not watchlist:
        print("ℹ️ Takip listesi (watchlist.json) boş veya bulunamadı.")
        write_status(status, state="ok", items_checked=0, ok=0, failed=0, out_of_stock=0)
        return

    state = load_state()

    # Aktif ürünleri belirle
    active_items = [item for item in watchlist if item.get("active", True)]

    # C3: Sıralama - last_checked_at değeri en eski olanlar (veya hiç taranmamışlar) en önce!
    def sort_key(item):
        item_id = item["id"]
        last_checked = state.get(item_id, {}).get("last_checked_at")
        return last_checked or ""

    active_items.sort(key=sort_key)

    stats = {
        "ok": 0,
        "failed": 0,
        "out_of_stock": 0,
        "skipped_due_to_budget": 0,
        "skipped_due_circuit_breaker": 0
    }
    domain_blocked: Dict[str, int] = {}
    circuit_breakers: Dict[str, bool] = {}
    collected_failures: List[Dict[str, Any]] = []

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {len(active_items)} aktif ürün taranacak (Bütçe: {budget}s)...")

    for idx, item_config in enumerate(active_items):
        item_id = item_config["id"]
        if item_id not in state:
            state[item_id] = {
                "id": item_id,
                "title": item_config.get("name") or "Ürün",
                "last_price": None,
                "last_checked_at": None,
                "last_alert_sent_price": None,
                "consecutive_failures": 0,
                "error_alert_sent": False,
                "out_of_stock": False,
                "price_history": []
            }

        item_state = state[item_id]

        # C3 Time Budget Kontrolü: 30s güvenlik payı
        elapsed = time.time() - start_time
        if elapsed > (budget - 30):
            remaining = len(active_items) - idx
            stats["skipped_due_to_budget"] += remaining
            print(f"\n⏳ Zaman bütçesi doldu ({elapsed:.1f}s / {budget}s). Kalan {remaining} ürün bir sonraki tura aktarılıyor.")
            break

        try:
            process_single_item(item_config, item_state, stats, domain_blocked, circuit_breakers, collected_failures)
        except Exception as e:
            print(f"❌ Beklenmedik hata ({item_config.get('url')}): {e}")
            stats["failed"] += 1
            item_state["consecutive_failures"] = item_state.get("consecutive_failures", 0) + 1
            item_state["last_error"] = f"CRASH: {e}"

        # C3 Incremental Save: Her ürün sonrası anında diske kaydet!
        save_state(state)

    # R2: Toplu hata ve circuit breaker bildirimleri
    handle_aggregated_alerts(collected_failures, circuit_breakers)

    # Nihai state kaydı
    save_state(state)

    # R3 Durum Tespiti: Başarısızlık oranı > %50 ise degraded ata
    total_processed = stats["ok"] + stats["failed"] + stats["out_of_stock"]
    system_state = "ok"
    if total_processed > 0 and (stats["failed"] / total_processed) > 0.5:
        system_state = "degraded"

    cb_tripped_map = {d: True for d, t in circuit_breakers.items() if t}
    write_status(
        status,
        state=system_state,
        error=None,
        items_checked=total_processed,
        circuit_breaker_tripped=cb_tripped_map,
        **stats
    )

    print(f"\n✅ Tur Tamamlandı: {system_state.upper()} | Başarılı: {stats['ok']}, Hatalı: {stats['failed']}, Stok Yok: {stats['out_of_stock']}, Bütçe Nedeniyle Atlanan: {stats['skipped_due_to_budget']}")

if __name__ == "__main__":
    check_all_products()
