import re
import json
import time
from typing import Optional, Dict, Any
from urllib.parse import urlparse
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
except ImportError:
    import httpx as requests

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

# Domain bazlı rate limiting için son istek zamanları
LAST_REQUEST_TIMES: Dict[str, float] = {}
MIN_DOMAIN_DELAY_SEC = 3.0
REQUEST_TIMEOUT_SEC = 12.0

def clean_url(url: str) -> str:
    """URL'deki takip parametrelerini temizler ve standart formata getirir."""
    if not url:
        return ""
    if "amazon" in url:
        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if asin_match:
            return f"https://www.amazon.com.tr/dp/{asin_match.group(1)}"
        return url.split("?")[0]
    elif "trendyol" in url:
        return url.split("?")[0].replace("/en/", "/")
    elif "hepsiburada" in url:
        return url.split("?")[0]
    return url

def get_domain(url: str) -> str:
    """URL'den domain adını ayıklar."""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower() or "unknown"
    except Exception:
        return "unknown"

def wait_rate_limit(domain: str, min_delay: float = MIN_DOMAIN_DELAY_SEC):
    """Aynı domaine art arda istek atmadan önce en az min_delay kadar bekler."""
    now = time.time()
    last = LAST_REQUEST_TIMES.get(domain, 0.0)
    elapsed = now - last
    if elapsed < min_delay:
        time.sleep(min_delay - elapsed)
    LAST_REQUEST_TIMES[domain] = time.time()

def parse_price(price_text) -> Optional[float]:
    """
    Para metinlerini float sayıya çevirir. Türkçe ve uluslararası biçimleri doğru ayrıştırır.
      '17.416,00 TL' -> 17416.0
      '17.416'       -> 17416.0
      '17,416.00'    -> 17416.0
      '199,90 TL'    -> 199.9
    """
    if price_text is None:
        return None
    if isinstance(price_text, (int, float)):
        return float(price_text) if price_text > 0 else None
    s = re.sub(r"[^\d,\.]", "", str(price_text)).strip(".,")
    if not s:
        return None
    try:
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")   # 17.416,00
            else:
                s = s.replace(",", "")                      # 17,416.00
        elif "," in s:
            parts = s.split(",")
            if len(parts) > 2 or len(parts[-1]) == 3:
                s = s.replace(",", "")                      # 1,299 / 1,299,000
            else:
                s = s.replace(",", ".")                     # 199,90
        elif "." in s:
            parts = s.split(".")
            if len(parts) > 2 or len(parts[-1]) == 3:
                s = s.replace(".", "")                      # 17.416 / 1.234.567
        value = float(s)
        return value if value > 0 else None
    except Exception:
        return None

# ==============================================================================
# AMAZON PARSER
# ==============================================================================
AMAZON_PRICE_CONTAINERS = [
    "corePriceDisplay_desktop_feature_div",
    "corePrice_feature_div",
    "apex_desktop",
    "corePrice_desktop",
    "desktop_buybox",
    "buybox",
]

AMAZON_OOS_MARKERS = [
    "mevcut değil", "stokta yok", "stokta bulunmamaktadır",
    "currently unavailable", "temporarily out of stock",
]

def _amazon_price_from_span(span) -> Optional[float]:
    if not span:
        return None
    off = span.find("span", class_="a-offscreen")
    if off and off.get_text(strip=True):
        return parse_price(off.get_text(strip=True))
    whole = span.find("span", class_="a-price-whole")
    if whole:
        frac = span.find("span", class_="a-price-fraction")
        w = re.sub(r"[^\d]", "", whole.get_text())
        f = re.sub(r"[^\d]", "", frac.get_text()) if frac else "00"
        if w:
            return parse_price(f"{w}.{f or '00'}")
    return None

def fetch_amazon(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    # 1. Bot engeli tespiti
    if any(m in raw_html for m in ["Robot Check", "validateCaptcha", "Type the characters you see in this image"]):
        return {
            "title": "Amazon Ürünü",
            "price": None,
            "seller_name": "Bilinmiyor",
            "is_official_seller": False,
            "platform": "Amazon",
            "out_of_stock": False,
            "error_type": "BLOCKED",
            "confidence": "low",
        }

    title_tag = soup.find(id="productTitle")
    title = title_tag.get_text(strip=True) if title_tag else None

    # Stok durumu
    avail = soup.find(id="availability")
    avail_text = avail.get_text(" ", strip=True).lower() if avail else ""
    out_of_stock = bool(soup.find(id="outOfStock")) or any(m in avail_text for m in AMAZON_OOS_MARKERS)

    if out_of_stock:
        return {
            "title": title,
            "price": None,
            "seller_name": "Satıcı Yok",
            "is_official_seller": False,
            "platform": "Amazon",
            "out_of_stock": True,
            "error_type": None,
            "confidence": "high",
        }

    price_found = None
    confidence = "high"

    # Fiyat: Ana konteynerler
    containers = [c for c in (soup.find(id=cid) for cid in AMAZON_PRICE_CONTAINERS) if c]
    for c in containers:
        el = c.find("span", class_="priceToPay") or c.find("span", class_="apexPriceToPay")
        if el:
            price_found = _amazon_price_from_span(el)
            if price_found:
                break

    if price_found is None:
        for c in containers:
            for p in c.find_all("span", class_="a-price"):
                if "a-text-price" in (p.get("class") or []):
                    continue
                price_found = _amazon_price_from_span(p)
                if price_found:
                    break
            if price_found:
                break

    if price_found is None:
        for pid in ("priceblock_ourprice", "priceblock_dealprice"):
            el = soup.find("span", id=pid)
            if el:
                price_found = parse_price(el.get_text(strip=True))
                if price_found:
                    break

    # Genel yedek (low confidence)
    if price_found is None:
        gen_el = soup.find("span", class_="priceToPay") or soup.find("span", class_="apexPriceToPay")
        if gen_el:
            price_found = _amazon_price_from_span(gen_el)
            confidence = "low"

    # Buybox kontrolü
    has_buybox = bool(soup.find(id="desktop_buybox") or soup.find(id="buybox") or soup.find(id="add-to-cart-button"))
    error_type = None
    if price_found is None:
        if not has_buybox:
            error_type = "NO_BUYBOX"
        else:
            error_type = "PARSE_FAIL"

    # Satıcı tespiti
    seller_name = None
    for el in soup.find_all(attrs={"offer-display-feature-name": "desktop-merchant-info"}):
        msg = el.find(class_="offer-display-feature-text-message")
        txt = msg.get_text(" ", strip=True) if msg else ""
        if not txt:
            first = el.find(["a", "span"], string=True)
            txt = first.get_text(" ", strip=True) if first else ""
        if txt:
            seller_name = txt
            break

    if not seller_name:
        link = soup.find(id="sellerProfileTriggerId")
        if link and link.get_text(strip=True):
            seller_name = link.get_text(strip=True)

    if seller_name:
        clean_seller = re.sub(r"^satıcı:\s*", "", seller_name, flags=re.IGNORECASE).strip()
        seller_name = clean_seller
        is_official = clean_seller.lower().startswith("amazon.com.tr") or clean_seller.lower() == "amazon"
    else:
        seller_name = "Satıcı tespit edilemedi"
        is_official = False

    return {
        "title": title,
        "price": price_found,
        "seller_name": seller_name,
        "is_official_seller": is_official,
        "platform": "Amazon",
        "out_of_stock": False,
        "error_type": error_type,
        "confidence": confidence,
    }

# ==============================================================================
# TRENDYOL PARSER
# ==============================================================================
def fetch_trendyol(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    # Bot engeli tespiti
    if any(m in raw_html for m in ["ShieldSquare", "validateCaptcha", "cf-browser-verification", "ddg-captcha"]):
        return {
            "title": "Trendyol Ürünü",
            "price": None,
            "seller_name": "Bilinmiyor",
            "is_official_seller": False,
            "platform": "Trendyol",
            "out_of_stock": False,
            "error_type": "BLOCKED",
            "confidence": "low",
        }

    title = None
    price_found = None
    seller_name = "Trendyol Satıcısı"
    is_official = False
    out_of_stock = False

    # 1. JSON-LD structured data
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if not isinstance(d, dict) or ("offers" not in d and "@type" not in d):
                continue
            if d.get("@type") == "Product" or "offers" in d:
                title = title or d.get("name")
                offers = d.get("offers")
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    avail = str(offers.get("availability", ""))
                    if "OutOfStock" in avail or "SoldOut" in avail:
                        out_of_stock = True
                    price_found = parse_price(offers.get("price"))
                    seller = offers.get("seller")
                    if isinstance(seller, dict) and seller.get("name"):
                        seller_name = seller["name"]
                break
        if title and price_found is not None:
            break

    if not title:
        title_elem = soup.find("h1", class_="pr-new-br") or soup.find("h1")
        title = title_elem.get_text(" ", strip=True) if title_elem else None

    # DOM fiyatı (JSON-LD'de yoksa)
    if price_found is None:
        p_elem = soup.find("span", class_="prc-dsc") or soup.find("span", class_="prc-slg") or soup.find("div", class_="product-price-container")
        if p_elem:
            price_found = parse_price(p_elem.get_text(strip=True))

    # Sayfa içi JSON verisi fallback
    if price_found is None:
        ty_p_match = re.search(r'"discountedPrice":\{"value":([0-9\.]+)', raw_html) or re.search(r'"originalPrice":\{"value":([0-9\.]+)', raw_html) or re.search(r'"sellingPrice":\{"value":([0-9\.]+)', raw_html)
        if ty_p_match:
            price_found = parse_price(ty_p_match.group(1))

    # Satıcı adı
    if seller_name == "Trendyol Satıcısı":
        m_match = re.search(r'"merchant":\{"id":\d+,"name":"([^"]+)"', raw_html)
        if m_match:
            seller_name = m_match.group(1)

    if "trendyol" in seller_name.lower() and seller_name != "Trendyol Satıcısı":
        is_official = True
        seller_name = "Trendyol"

    if "tükendi" in raw_html.lower() and not price_found:
        out_of_stock = True

    error_type = None
    if out_of_stock:
        price_found = None
    elif price_found is None:
        error_type = "PARSE_FAIL"

    return {
        "title": title,
        "price": price_found,
        "seller_name": seller_name,
        "is_official_seller": is_official,
        "platform": "Trendyol",
        "out_of_stock": out_of_stock,
        "error_type": error_type,
        "confidence": "high" if price_found else "low",
    }

# ==============================================================================
# HEPSİBURADA PARSER (A3: JSON-LD, AggregateOffer, OOS)
# ==============================================================================
def fetch_hepsiburada(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    # Bot engeli tespiti
    if any(m in raw_html for m in ["ShieldSquare", "validateCaptcha", "cf-browser-verification", "ddg-captcha"]):
        return {
            "title": "Hepsiburada Ürünü",
            "price": None,
            "seller_name": "Bilinmiyor",
            "is_official_seller": False,
            "platform": "Hepsiburada",
            "out_of_stock": False,
            "error_type": "BLOCKED",
            "confidence": "low",
        }

    title = None
    price_found = None
    seller_name = "Hepsiburada Satıcısı"
    is_official = False
    out_of_stock = False

    # 1. Başlık
    title_elem = soup.find("h1", id="product-name") or soup.find("h1")
    if title_elem:
        title = title_elem.get_text(strip=True)

    # 2. JSON-LD (A3: Product, offers list, AggregateOffer, availability)
    scripts = soup.find_all("script", type="application/ld+json")
    for s in scripts:
        try:
            data = json.loads(s.string)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if not isinstance(d, dict):
                continue
            if d.get("@type") == "Product" or "offers" in d:
                title = title or d.get("name")
                offers_data = d.get("offers")

                # offers Array veya tekil obje olabilir
                offer_list = []
                if isinstance(offers_data, list):
                    offer_list = offers_data
                elif isinstance(offers_data, dict):
                    if offers_data.get("@type") == "AggregateOffer":
                        # AggregateOffer: lowPrice veya offers listesi
                        low_p = offers_data.get("lowPrice") or offers_data.get("price")
                        if low_p:
                            price_found = parse_price(low_p)
                        sub_offers = offers_data.get("offers", [])
                        if isinstance(sub_offers, list):
                            offer_list = sub_offers
                        elif isinstance(sub_offers, dict):
                            offer_list = [sub_offers]
                    else:
                        offer_list = [offers_data]

                for off in offer_list:
                    if not isinstance(off, dict):
                        continue
                    avail = str(off.get("availability", ""))
                    if "OutOfStock" in avail or "SoldOut" in avail:
                        out_of_stock = True
                    if price_found is None and off.get("price") is not None:
                        price_found = parse_price(off.get("price"))
                    seller = off.get("seller")
                    if isinstance(seller, dict) and seller.get("name"):
                        seller_name = seller.get("name")
                    if price_found is not None:
                        break
            if title and price_found is not None:
                break
        if title and price_found is not None:
            break

    # 3. DOM ve Sayfa İçi Fiyat - A3 Kuralı: Rastgele tüm sayfa yerine ürün fiyat kutusuyla sınırla!
    if price_found is None:
        price_box = (
            soup.find(attrs={"data-test-id": "price-current-price"})
            or soup.find("div", class_="product-price-container")
            or soup.find("div", class_="product-price")
            or soup.find("div", class_="price-wrapper")
        )
        if price_box:
            price_found = parse_price(price_box.get_text(strip=True))

    # Sayfa içi JSON verisi fallback (kesin ürün price alanı)
    if price_found is None:
        p_match = re.search(r'"price":\s*"([0-9\.]+)"', raw_html)
        if p_match:
            price_found = parse_price(p_match.group(1))

    # Stok durumu kontrolü
    oos_text = any(m in raw_html.lower() for m in ["tükendi", "stokta yok", "bu ürün geçici olarak temin edilememektedir"])
    if oos_text and not price_found:
        out_of_stock = True

    # Satıcı adı tespiti
    m_match = re.search(r'"merchantName":\s*"([^"]+)"', raw_html)
    if m_match:
        seller_name = m_match.group(1)
    else:
        seller_elem = soup.find("a", class_="merchant-name") or soup.find("span", class_="merchant-name")
        if seller_elem:
            seller_name = seller_elem.get_text(strip=True)

    if "hepsiburada" in seller_name.lower():
        is_official = True
        seller_name = "Hepsiburada"

    error_type = None
    if out_of_stock:
        price_found = None
    elif price_found is None:
        error_type = "PARSE_FAIL"

    return {
        "title": title,
        "price": price_found,
        "seller_name": seller_name,
        "is_official_seller": is_official,
        "platform": "Hepsiburada",
        "out_of_stock": out_of_stock,
        "error_type": error_type,
        "confidence": "high" if price_found else "low",
    }

# ==============================================================================
# ANA TARAMA FONKSİYONU (R1 Hata Sınıflandırması)
# ==============================================================================
def fetch_product_data(url: str, html_override: Optional[str] = None) -> Dict[str, Any]:
    """
    Belirtilen URL için ürün fiyatını, satıcısını ve durumunu çeker.
    R1: Hataları net olarak sınıflandırır (BLOCKED, TIMEOUT, HTTP_xxx, PARSE_FAIL, NO_BUYBOX).
    html_override verilirse HTTP isteği atmadan direkt bu HTML'i ayrıştırır (Test için).
    """
    cleaned = clean_url(url)
    domain = get_domain(cleaned)

    result = {
        "url": cleaned,
        "title": "Ürün",
        "price": None,
        "seller_name": "Satıcı Bilgisi Yok",
        "is_official_seller": False,
        "platform": "Bilinmeyen",
        "out_of_stock": False,
        "success": False,
        "error_type": None,
        "error": None,
        "confidence": "high"
    }

    # Platform tespiti
    if "amazon" in domain:
        platform = "Amazon"
    elif "trendyol" in domain:
        platform = "Trendyol"
    elif "hepsiburada" in domain:
        platform = "Hepsiburada"
    else:
        result["error_type"] = "UNSUPPORTED"
        result["error"] = "Desteklenmeyen platform"
        return result

    result["platform"] = platform

    # 1. Test veya Override HTML'i varsa direkt parse et
    if html_override is not None:
        soup = BeautifulSoup(html_override, "html.parser")
        if platform == "Amazon":
            parsed = fetch_amazon(soup, html_override)
        elif platform == "Trendyol":
            parsed = fetch_trendyol(soup, html_override)
        else:
            parsed = fetch_hepsiburada(soup, html_override)

        result.update(parsed)
        if result["out_of_stock"]:
            result["success"] = True
            result["error_type"] = None
            result["error"] = None
        elif result["price"] is not None:
            result["success"] = True
            result["error_type"] = None
            result["error"] = None
        else:
            result["success"] = False
            result["error"] = result.get("error_type") or "Fiyat okunamadı"
        return result

    # 2. Canlı İstek (Rate limiting + 12s timeout)
    wait_rate_limit(domain, MIN_DOMAIN_DELAY_SEC)

    profiles = ["chrome124", "safari17_0"]
    for attempt, browser in enumerate(profiles):
        try:
            # curl_cffi veya httpx isteği
            kwargs = {
                "headers": DEFAULT_HEADERS,
                "timeout": REQUEST_TIMEOUT_SEC,
                "allow_redirects": True
            }
            # curl_cffi varsa impersonate kullan
            try:
                resp = requests.get(cleaned, impersonate=browser, **kwargs)
            except TypeError:
                resp = requests.get(cleaned, **kwargs)

            # HTTP Durum Kontrolü
            if resp.status_code == 403 or resp.status_code == 429:
                result["error_type"] = "BLOCKED"
                result["error"] = f"Erişim engellendi (HTTP {resp.status_code})"
                return result

            if resp.status_code != 200:
                result["error_type"] = f"HTTP_{resp.status_code}"
                result["error"] = f"Sunucu hatası (HTTP {resp.status_code})"
                if resp.status_code in (404, 410):
                    return result  # Sayfa yoksa tekrar deneme
                time.sleep(2.0)
                continue

            html_text = resp.text
            soup = BeautifulSoup(html_text, "html.parser")

            if platform == "Amazon":
                parsed = fetch_amazon(soup, html_text)
            elif platform == "Trendyol":
                parsed = fetch_trendyol(soup, html_text)
            else:
                parsed = fetch_hepsiburada(soup, html_text)

            result.update(parsed)

            if result.get("error_type") == "BLOCKED":
                result["success"] = False
                result["error"] = "Bot koruması (Robot Check / Captcha)"
                return result

            if result["out_of_stock"]:
                result["success"] = True
                result["error_type"] = None
                result["error"] = None
                return result

            if result["price"] is not None:
                result["success"] = True
                result["error_type"] = None
                result["error"] = None
                return result
            else:
                # Fiyat bulunamadı
                result["error"] = result.get("error_type") or "Element veya fiyat bulunamadı"
                time.sleep(2.0)

        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str:
                result["error_type"] = "TIMEOUT"
                result["error"] = "Zaman aşımı (12s)"
            else:
                result["error_type"] = "CONNECTION_ERROR"
                result["error"] = str(e)
            time.sleep(2.0)

    return result

# Geriye dönük uyumluluk
fetch_amazon_product = fetch_product_data
clean_amazon_url = clean_url
