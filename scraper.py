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
    """URL'deki takip parametrelerini temizler ve evrensel formata getirir."""
    if "amazon" in url:
        asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
        if asin_match:
            return f"https://www.amazon.com.tr/dp/{asin_match.group(1)}"
    elif "trendyol" in url:
        # /en/ gibi dil kodlarını kaldır ve query parametrelerini at
        clean = url.split("?")[0].replace("/en/", "/")
        return clean
    elif "hepsiburada" in url:
        clean = url.split("?")[0]
        return clean
    return url

def parse_price(price_text: str) -> Optional[float]:
    """Her türlü para formatını float sayıya çevirir."""
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

def fetch_amazon(soup: BeautifulSoup) -> Dict[str, Any]:
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

    is_amazon_seller = False
    merchant_info = soup.find(id="merchant-info")
    if merchant_info and "amazon" in merchant_info.get_text(strip=True).lower():
        is_amazon_seller = True

    return {"title": title, "price": price_found, "is_official_seller": is_amazon_seller, "platform": "Amazon"}

def fetch_trendyol(soup: BeautifulSoup) -> Dict[str, Any]:
    title = None
    price_found = None

    # 1. Öncelik: JSON-LD Yapısal Verisi (En Sağlam & Değişmeyen Yol)
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

    # 2. Öncelik: HTML DOM Seçicileri (Yedek)
    if not title:
        title_elem = soup.find("h1", class_="pr-new-br") or soup.find("h1")
        title = title_elem.get_text(" ", strip=True) if title_elem else None

    if price_found is None:
        price_elem = soup.find("span", class_="prc-dsc") or \
                     soup.find("span", class_="prc-slg") or \
                     soup.find("div", class_="product-price-container")
        if price_elem:
            price_found = parse_price(price_elem.get_text(strip=True))

    is_official = False
    seller_elem = soup.find("a", class_="merchant-text")
    if seller_elem and "trendyol" in seller_elem.get_text(strip=True).lower():
        is_official = True

    return {"title": title, "price": price_found, "is_official_seller": is_official, "platform": "Trendyol"}

def fetch_hepsiburada(soup: BeautifulSoup) -> Dict[str, Any]:
    title = None
    price_found = None

    # JSON-LD Kontrolü
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
        title_elem = soup.find("h1", id="product-name") or soup.find("h1")
        title = title_elem.get_text(strip=True) if title_elem else None

    if price_found is None:
        price_elem = soup.find("span", {"data-test-id": "price-current-price"}) or \
                     soup.find("span", class_="price") or \
                     soup.find("div", class_="price-area")
        if price_elem:
            price_found = parse_price(price_elem.get_text(strip=True))

    is_official = False
    seller_elem = soup.find("a", class_="merchant-name") or soup.find("span", class_="merchant-name")
    if seller_elem and "hepsiburada" in seller_elem.get_text(strip=True).lower():
        is_official = True

    return {"title": title, "price": price_found, "is_official_seller": is_official, "platform": "Hepsiburada"}

def fetch_product_data(url: str) -> Dict[str, Any]:
    cleaned = clean_url(url)
    result = {
        "url": cleaned,
        "title": "Ürün",
        "price": None,
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
            parsed = fetch_amazon(soup)
        elif "trendyol" in final_url:
            parsed = fetch_trendyol(soup)
        elif "hepsiburada" in final_url:
            parsed = fetch_hepsiburada(soup)
        else:
            result["error"] = "Desteklenmeyen platform"
            return result

        result["title"] = parsed.get("title") or "Ürün"
        result["price"] = parsed.get("price")
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
