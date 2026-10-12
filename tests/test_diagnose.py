def test_diagnose_importable():
    """C2: diagnose.py scraper import hatası olmadan başarıyla import edilebilmeli."""
    import diagnose
    assert hasattr(diagnose, "TEST_URLS")
    assert "Amazon" in diagnose.TEST_URLS
