import os
import pytest
from scraper import parse_price, clean_url, fetch_product_data

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

def read_fixture(filename: str) -> str:
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def test_parse_price_formats():
    assert parse_price("17.416,00 TL") == 17416.0
    assert parse_price("17.416 TL") == 17416.0
    assert parse_price("17.416") == 17416.0
    assert parse_price("199,90 TL") == 199.9
    assert parse_price("17,416.00") == 17416.0
    assert parse_price("1250.50") == 1250.5
    assert parse_price("0") is None
    assert parse_price(None) is None
    assert parse_price("Geçersiz") is None

def test_clean_url():
    amz = clean_url("https://www.amazon.com.tr/dp/B0BK25SY9G?ref=xyz&tag=123")
    assert amz == "https://www.amazon.com.tr/dp/B0BK25SY9G"

    ty = clean_url("https://www.trendyol.com/pioneer/ddj-flx4-p-471697117?boutiqueId=1")
    assert ty == "https://www.trendyol.com/pioneer/ddj-flx4-p-471697117"

    hb = clean_url("https://www.hepsiburada.com/pioneer-dj-ddj-flx4-pm-HBC00003MXQS9?magaza=abc")
    assert hb == "https://www.hepsiburada.com/pioneer-dj-ddj-flx4-pm-HBC00003MXQS9"

def test_amazon_scraper_sample():
    html = read_fixture("amazon_sample.html")
    res = fetch_product_data("https://www.amazon.com.tr/dp/B0BK25SY9G", html_override=html)
    assert res["success"] is True
    assert res["price"] == 17416.0
    assert "Pioneer" in res["title"]
    assert res["is_official_seller"] is True
    assert res["out_of_stock"] is False

def test_amazon_scraper_blocked():
    html = read_fixture("amazon_blocked.html")
    res = fetch_product_data("https://www.amazon.com.tr/dp/B0BK25SY9G", html_override=html)
    assert res["success"] is False
    assert res["error_type"] == "BLOCKED"

def test_amazon_scraper_out_of_stock():
    html = read_fixture("amazon_oos.html")
    res = fetch_product_data("https://www.amazon.com.tr/dp/B0BK25SY9G", html_override=html)
    assert res["success"] is True
    assert res["out_of_stock"] is True
    assert res["price"] is None

def test_trendyol_scraper_sample():
    html = read_fixture("trendyol_sample.html")
    res = fetch_product_data("https://www.trendyol.com/p/123", html_override=html)
    assert res["success"] is True
    assert res["price"] == 17416.0
    assert "Pioneer" in res["title"]
    assert res["out_of_stock"] is False

def test_trendyol_scraper_out_of_stock():
    html = read_fixture("trendyol_oos.html")
    res = fetch_product_data("https://www.trendyol.com/p/123", html_override=html)
    assert res["success"] is True
    assert res["out_of_stock"] is True

def test_hepsiburada_scraper_sample():
    html = read_fixture("hepsiburada_sample.html")
    res = fetch_product_data("https://www.hepsiburada.com/p/123", html_override=html)
    assert res["success"] is True
    assert res["price"] == 4103.05
    assert "Philips Hue" in res["title"]
    assert res["is_official_seller"] is True
    assert res["out_of_stock"] is False

def test_hepsiburada_scraper_aggregate_offer():
    html = read_fixture("hepsiburada_aggregate_offer.html")
    res = fetch_product_data("https://www.hepsiburada.com/p/123", html_override=html)
    assert res["success"] is True
    assert res["price"] == 3999.0
    assert res["out_of_stock"] is False

def test_hepsiburada_scraper_out_of_stock():
    html = read_fixture("hepsiburada_oos.html")
    res = fetch_product_data("https://www.hepsiburada.com/p/123", html_override=html)
    assert res["success"] is True
    assert res["out_of_stock"] is True
