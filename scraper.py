import re
import json
import time
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from curl_cffi import requests

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

def clean_url(url: str) -> str:
    """URL'deki takip parametrelerini temizler."""
    if "amazon" in url:
        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if asin_match:
            return f"https://www.amazon.com.tr/dp/{asin_match.group(1)}"
    elif "trendyol" in url:
        return url.split("?")[0].replace("/en/", "/")
    elif "hepsiburada" in url:
        return url.split("?")[0]
    return url

def parse_price(price_text) -> Optional[float]:
    """
    Para metinlerini float sayıya çevirir. Türkçe ve İngilizce biçimleri ayırt eder:
      '17.416,00 TL' -> 17416.0   (TR: nokta binlik, virgül ondalık)
      '17.416'       -> 17416.0   (TR: tek nokta + 3 hane = binlik)
      '17,416.00'    -> 17416.0   (EN: virgül binlik, nokta ondalık)
      '17416.00'     -> 17416.0   (JSON: nokta ondalık)
      '199,90 TL'    -> 199.9
    Geçersiz veya 0 değerler için None döner.
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
                s = s.replace(",", "")                      # 1,299 / 1,299,000 -> binlik
            else:
                s = s.replace(",", ".")                     # 199,90 -> ondalık
        elif "." in s:
            parts = s.split(".")
            if len(parts) > 2 or len(parts[-1]) == 3:
                s = s.replace(".", "")                      # 17.416 / 1.234.567 -> binlik
        value = float(s)
        return value if value > 0 else None
    except Exception:
        return None

# Amazon'da SADECE ana ürünün fiyat kutuları (öneri/sponsorlu ürünlerin fiyatı yanlışlıkla okunmasın)
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
    title_tag = soup.find(id="productTitle")
    title = title_tag.get_text(strip=True) if title_tag else None

    containers = [c for c in (soup.find(id=cid) for cid in AMAZON_PRICE_CONTAINERS) if c]
    price_found = None

    # 1. Ana kutulardaki "ödenecek fiyat" (priceToPay)
    for c in containers:
        el = c.find("span", class_="priceToPay") or c.find("span", class_="apexPriceToPay")
        if el:
            price_found = _amazon_price_from_span(el)
            if price_found:
                break

    # 2. Ana kutulardaki diğer fiyatlar (üstü çizili liste fiyatı hariç)
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

    # 3. Eski Amazon şablonları
    if price_found is None:
        for pid in ("priceblock_ourprice", "priceblock_dealprice"):
            el = soup.find("span", id=pid)
            if el:
                price_found = parse_price(el.get_text(strip=True))
                if price_found:
                    break

    # 4. Sepete ekle formundaki gizli fiyat alanı (ana ürüne aittir)
    if price_found is None:
        m = re.search(r'customerVisiblePrice\]\[amount\]"\s+value="([0-9.]+)"', raw_html)
        if m:
            price_found = parse_price(m.group(1))

    # 5. Gömülü veri - sadece sayfada TEK bir fiyat varsa (başka ürünle karışmasın)
    if price_found is None:
        amounts = set(re.findall(r'"priceAmount":\s*([0-9\.]+)', raw_html))
        if len(amounts) == 1:
            price_found = parse_price(amounts.pop())

    # 6. Güçlü Genel Yedek: Sayfadaki priceToPay veya apexPriceToPay (Amazon mobil/dinamik şablon uyumu)
    if price_found is None:
        gen_el = soup.find("span", class_="priceToPay") or soup.find("span", class_="apexPriceToPay")
        if gen_el:
            price_found = _amazon_price_from_span(gen_el)

    if price_found is None:
        disp_match = re.search(r'"displayPrice":\s*"([^"]+)"', raw_html)
        if disp_match:
            price_found = parse_price(disp_match.group(1))

    # Stok durumu
    avail = soup.find(id="availability")
    avail_text = avail.get_text(" ", strip=True).lower() if avail else ""
    out_of_stock = bool(soup.find(id="outOfStock")) or any(m in avail_text for m in AMAZON_OOS_MARKERS)
    if out_of_stock:
        price_found = None  # Stokta yokken görünen fiyat ana ürünün satış fiyatı değildir

    # Satıcı tespiti: SADECE "Satıcı" alanına bakılır ("Gönderici: Amazon" resmi satıcı demek değildir)
    seller_name = None
    for el in soup.find_all(attrs={"offer-display-feature-name": "desktop-merchant-info"}):
        if "offer-display-feature-text" in (el.get("class") or []):
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
        is_official = seller_name.strip().lower().startswith("amazon.com.tr")
    else:
        seller_name = "Satıcı tespit edilemedi"
        is_official = False

    return {
        "title": title, "price": price_found, "seller_name": seller_name,
        "is_official_seller": is_official, "platform": "Amazon", "out_of_stock": out_of_stock,
    }

def fetch_trendyol(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    title = None
    price_found = None
    seller_name = "Trendyol Satıcısı"
    is_official = False
    out_of_stock = False

    # JSON-LD (ana ürünün yapılandırılmış verisi)
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string)
        except Exception:
            continue
        for d in (data if isinstance(data, list) else [data]):
            if not isinstance(d, dict) or "offers" not in d:
                continue
            offers = d["offers"]
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price_found = parse_price(offers.get("price"))
            title = d.get("name")
            availability = str(offers.get("availability", ""))
            if "OutOfStock" in availability or "SoldOut" in availability:
                out_of_stock = True
            seller = offers.get("seller")
            if isinstance(seller, dict) and seller.get("name"):
                seller_name = seller["name"]
            break
        if title:
            break

    if not title:
        title_elem = soup.find("h1", class_="pr-new-br") or soup.find("h1")
        title = title_elem.get_text(" ", strip=True) if title_elem else None

    # 2. JSON-LD'den fiyat gelmediyse Trendyol DOM veya sayfa içi JSON verisi
    if price_found is None:
        p_elem = soup.find("span", class_="prc-dsc") or soup.find("span", class_="prc-slg") or soup.find("div", class_="product-price-container")
        if p_elem:
            price_found = parse_price(p_elem.get_text(strip=True))

    if price_found is None:
        ty_p_match = re.search(r'"discountedPrice":\{"value":([0-9\.]+)', raw_html) or re.search(r'"originalPrice":\{"value":([0-9\.]+)', raw_html) or re.search(r'"sellingPrice":\{"value":([0-9\.]+)', raw_html)
        if ty_p_match:
            price_found = parse_price(ty_p_match.group(1))

    # Satıcı (JSON-LD'de yoksa sayfa içi veriden)
    if seller_name == "Trendyol Satıcısı":
        match = re.search(r'"merchant":\{"id":\d+,"name":"([^"]+)"', raw_html)
        if match:
            seller_name = match.group(1)
    if "trendyol" in seller_name.lower() and seller_name != "Trendyol Satıcısı":
        is_official = True
        seller_name = "Trendyol"

    if out_of_stock:
        price_found = None

    return {
        "title": title, "price": price_found, "seller_name": seller_name,
        "is_official_seller": is_official, "platform": "Trendyol", "out_of_stock": out_of_stock,
    }

def fetch_hepsiburada(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    title = None
    price_found = None
    seller_name = "Hepsiburada Satıcısı"
    is_official = False

    # 1. Başlık
    title_elem = soup.find("h1", id="product-name") or soup.find("h1")
    if title_elem:
        title = title_elem.get_text(strip=True)

    # 2. JSON-LD Kontrolü
    scripts = soup.find_all("script", type="application/ld+json")
    for s in scripts:
        try:
            data = json.loads(s.string)
            if "offers" in data:
                price_found = parse_price(str(data["offers"].get("price")))
                if not title:
                    title = data.get("name")
                break
        except Exception:
            pass

    # 3. Hepsiburada Sayfa İçi JSON Verisi (Kesin Fiyat ve Satıcı)
    if price_found is None:
        p_match = re.search(r'"price":\s*"([\d\.]+)"', raw_html)
        if p_match:
            price_found = parse_price(p_match.group(1))

    # Satıcı Adı Tespiti
    m_match = re.search(r'"merchantName":\s*"([^"]+)"', raw_html)
    if m_match:
        seller_name = m_match.group(1)
        if "hepsiburada" in seller_name.lower():
            is_official = True
            seller_name = "Hepsiburada"
    else:
        seller_elem = soup.find("a", class_="merchant-name") or soup.find("span", class_="merchant-name")
        if seller_elem:
            seller_name = seller_elem.get_text(strip=True)
            if "hepsiburada" in seller_name.lower():
                is_official = True
                seller_name = "Hepsiburada"

    return {"title": title, "price": price_found, "seller_name": seller_name, "is_official_seller": is_official, "platform": "Hepsiburada", "out_of_stock": False}

def fetch_product_data(url: str) -> Dict[str, Any]:
    cleaned = clean_url(url)
    result = {
        "url": cleaned,
        "title": "Ürün",
        "price": None,
        "seller_name": "Satıcı Bilgisi Yok",
        "is_official_seller": False,
        "platform": "Bilinmeyen",
        "out_of_stock": False,
        "success": False,
        "error": None
    }

    # Anlık bloklanmalara karşı 2 farklı tarayıcı parmak izi ile deneme
    profiles = ["chrome124", "safari17_0"]
    for attempt, browser in enumerate(profiles):
        try:
            resp = requests.get(
                cleaned,
                headers=DEFAULT_HEADERS,
                impersonate=browser,
                timeout=25,
                allow_redirects=True
            )

            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}"
                time.sleep(2.0)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            final_url = str(resp.url)

            if "amazon" in final_url:
                parsed = fetch_amazon(soup, resp.text)
            elif "trendyol" in final_url:
                parsed = fetch_trendyol(soup, resp.text)
            elif "hepsiburada" in final_url:
                parsed = fetch_hepsiburada(soup, resp.text)
            else:
                result["error"] = "Desteklenmeyen platform"
                return result

            result["title"] = parsed.get("title") or "Ürün"
            result["seller_name"] = parsed.get("seller_name", "Satıcı")
            result["is_official_seller"] = parsed.get("is_official_seller", False)
            result["platform"] = parsed.get("platform", "Pazar Yeri")

            # Stokta yok: site çalışıyor, sorun bizde değil -> tekrar denemeye gerek yok
            if parsed.get("out_of_stock"):
                result["out_of_stock"] = True
                result["error"] = "Stokta yok"
                return result

            # Fiyat başarıyla bulunduysa sonucu döndür
            if parsed.get("price") is not None:
                result["price"] = parsed.get("price")
                result["success"] = True
                result["error"] = None
                return result
            else:
                # Fiyat okunamadıysa kısa bir mola verip 2. tarayıcı profiliyle dene
                result["error"] = "Element veya fiyat bulunamadı"
                time.sleep(2.0)

        except Exception as e:
            result["error"] = str(e)
            time.sleep(2.0)

    return result

# Geriye dönük uyumluluk
fetch_amazon_product = fetch_product_data
clean_amazon_url = clean_url
