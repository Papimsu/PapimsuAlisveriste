import re
from typing import Optional, Dict, Any
from bs4 import BeautifulSoup
from curl_cffi import requests

# Amazon'un robot korumasına takılmamak için gerçek tarayıcı başlıkları
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

def clean_amazon_url(url: str) -> str:
    """URL'deki gereksiz referans ve takip parametrelerini temizler."""
    asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
    if asin_match:
        asin = asin_match.group(1)
        return f"https://www.amazon.com.tr/dp/{asin}"
    return url

def extract_asin(url: str) -> Optional[str]:
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
    return match.group(1) if match else None

def parse_price(price_text: str) -> Optional[float]:
    """'5.499,00 TL' veya '24.999 TL' metnini float sayıya çevirir."""
    if not price_text:
        return None
    try:
        clean = re.sub(r"[^\d,\.]", "", price_text).strip()
        if "," in clean and "." in clean:
            clean = clean.replace(".", "").replace(",", ".")
        elif "," in clean:
            clean = clean.replace(",", ".")
        return float(clean)
    except Exception:
        return None

def fetch_amazon_product(url: str) -> Dict[str, Any]:
    """Amazon ürün sayfasından başlık, güncel fiyat ve satıcı bilgisini çeker."""
    clean_url = clean_amazon_url(url)
    result = {
        "url": clean_url,
        "title": "Bilinmeyen Ürün",
        "price": None,
        "is_amazon_seller": False,
        "success": False,
        "error": None
    }

    try:
        # Chrome 124 TLS ve tarayıcı parmak izini taklit eder (Amazon robot korumasını aşar)
        resp = requests.get(
            clean_url,
            headers=DEFAULT_HEADERS,
            impersonate="chrome124",
            timeout=20
        )
        
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. Başlık Çıkarımı
        title_tag = soup.find(id="productTitle")
        if title_tag:
            result["title"] = title_tag.get_text(strip=True)

        # 2. Fiyat Çıkarımı
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

        result["price"] = price_found

        # 3. Satıcı Kontrolü
        merchant_info = soup.find(id="merchant-info")
        if merchant_info:
            text = merchant_info.get_text(strip=True).lower()
            if "amazon" in text:
                result["is_amazon_seller"] = True

        result["success"] = (price_found is not None)
        return result

    except Exception as e:
        result["error"] = str(e)
        return result
