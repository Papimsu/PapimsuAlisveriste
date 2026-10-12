import re
import json
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

def parse_price(price_text: str) -> Optional[float]:
    """Para metinlerini float sayıya çevirir."""
    if not price_text:
        return None
    try:
        clean = re.sub(r"[^\d,\.]", "", str(price_text)).strip()
        if "," in clean and "." in clean:
            clean = clean.replace(".", "").replace(",", ".")
        elif "," in clean:
            clean = clean.replace(",", ".")
        return float(clean)
    except Exception:
        return None

def fetch_amazon(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    title_tag = soup.find(id="productTitle")
    title = title_tag.get_text(strip=True) if title_tag else None

    price_found = None
    price_elem = soup.find("span", class_="priceToPay") or \
                 soup.find("span", class_="apexPriceToPay") or \
                 soup.find("span", id="priceblock_ourprice") or \
                 soup.find("span", id="priceblock_dealprice")

    if price_elem:
        offscreen = price_elem.find("span", class_="a-offscreen")
        price_text = offscreen.get_text(strip=True) if offscreen else price_elem.get_text(strip=True)
        price_found = parse_price(price_text)

    if price_found is None:
        offscreen = soup.find("span", class_="a-offscreen")
        if offscreen:
            price_found = parse_price(offscreen.get_text(strip=True))

    if price_found is None:
        whole = soup.find("span", class_="a-price-whole")
        fraction = soup.find("span", class_="a-price-fraction")
        if whole:
            fraction_text = fraction.get_text(strip=True) if fraction else "00"
            combined = f"{whole.get_text(strip=True)}.{fraction_text}"
            price_found = parse_price(combined)

    # 3. Aşama: Sayfa içi gömülü veri ve Regex yedekleri (DOM değişse bile asla ıskalamaz)
    if price_found is None:
        amount_match = re.search(r'"priceAmount":\s*([0-9\.]+)', raw_html)
        if amount_match:
            try:
                price_found = float(amount_match.group(1))
            except Exception:
                pass

    if price_found is None:
        display_match = re.search(r'"displayPrice":\s*"([^"]+)"', raw_html)
        if display_match:
            price_found = parse_price(display_match.group(1))

    # Satıcı Tespiti
    seller_name = "Üçüncü Taraf Satıcı"
    is_official = False

    buybox = soup.find(id="desktop_buybox") or soup.find(id="buybox")
    if buybox:
        bb_text = buybox.get_text(" ", strip=True)
        if "Amazon.com.tr" in bb_text:
            seller_name = "Amazon.com.tr"
            is_official = True
        else:
            seller_link = buybox.find(id="sellerProfileTriggerId") or buybox.find("a", href=re.compile(r"/seller/"))
            if seller_link:
                seller_name = seller_link.get_text(strip=True)

    if not is_official:
        m_info = soup.find(id="merchant-info")
        if m_info and "amazon" in m_info.get_text(strip=True).lower():
            seller_name = "Amazon.com.tr"
            is_official = True

    return {"title": title, "price": price_found, "seller_name": seller_name, "is_official_seller": is_official, "platform": "Amazon"}

def fetch_trendyol(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    title = None
    price_found = None
    seller_name = "Trendyol Satıcısı"
    is_official = False

    # JSON-LD
    scripts = soup.find_all("script", type="application/ld+json")
    for s in scripts:
        try:
            data = json.loads(s.string)
            if "offers" in data:
                price_found = parse_price(str(data["offers"].get("price")))
                title = data.get("name")
                break
        except Exception:
            pass

    if not title:
        title_elem = soup.find("h1", class_="pr-new-br") or soup.find("h1")
        title = title_elem.get_text(" ", strip=True) if title_elem else None

    # Satıcı
    match = re.search(r'"merchant":\{"id":\d+,"name":"([^"]+)"', raw_html)
    if match:
        seller_name = match.group(1)
        if "trendyol" in seller_name.lower():
            is_official = True
            seller_name = "Trendyol"

    return {"title": title, "price": price_found, "seller_name": seller_name, "is_official_seller": is_official, "platform": "Trendyol"}

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

    return {"title": title, "price": price_found, "seller_name": seller_name, "is_official_seller": is_official, "platform": "Hepsiburada"}

def fetch_product_data(url: str) -> Dict[str, Any]:
    cleaned = clean_url(url)
    result = {
        "url": cleaned,
        "title": "Ürün",
        "price": None,
        "seller_name": "Satıcı Bilgisi Yok",
        "is_official_seller": False,
        "platform": "Bilinmeyen",
        "success": False,
        "error": None
    }

    try:
        resp = requests.get(
            cleaned,
            headers=DEFAULT_HEADERS,
            impersonate="chrome124",
            timeout=25,
            allow_redirects=True
        )

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

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
        result["price"] = parsed.get("price")
        result["seller_name"] = parsed.get("seller_name", "Satıcı")
        result["is_official_seller"] = parsed.get("is_official_seller", False)
        result["platform"] = parsed.get("platform", "Pazar Yeri")
        result["success"] = (result["price"] is not None)
        return result

    except Exception as e:
        result["error"] = str(e)
        return result

# Geriye dönük uyumluluk
fetch_amazon_product = fetch_product_data
clean_amazon_url = clean_url
