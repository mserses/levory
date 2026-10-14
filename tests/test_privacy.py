"""privacy.mask_pii testleri."""

import pytest

from serviszeka.privacy import is_valid_tckn, luhn_valid, mask_pii

VALID_TCKN = "10000000146"


def test_tckn_checksum():
    assert is_valid_tckn(VALID_TCKN)
    assert not is_valid_tckn("10000000147")
    assert not is_valid_tckn("01234567890")
    assert not is_valid_tckn("1234")


@pytest.mark.parametrize(
    "text,expected",
    [
        (f"TCKN: {VALID_TCKN}", "TCKN: [TCKN]"),
        (f"kimlik={VALID_TCKN}.", "kimlik=[TCKN]."),
        ("mail: ali.veli@ornek.com.tr", "mail: [EPOSTA]"),
        ("Yaz: Ayse_K+it@sirket.io lütfen", "Yaz: [EPOSTA] lütfen"),
        ("Tel: +90 532 123 45 67", "Tel: [TELEFON]"),
        ("Tel: +905321234567", "Tel: [TELEFON]"),
        ("Tel: 0532 123 45 67", "Tel: [TELEFON]"),
        ("Tel: 05321234567", "Tel: [TELEFON]"),
        ("Tel: 0 (532) 123-45-67", "Tel: [TELEFON]"),
        ("Tel: (0532) 123 45 67", "Tel: [TELEFON]"),
        ("Tel: 0090 532 123 45 67", "Tel: [TELEFON]"),
        ("Tel: 0532.123.45.67", "Tel: [TELEFON]"),
        ("IBAN TR33 0006 1005 1978 6457 8413 26 hesabı", "IBAN [IBAN] hesabı"),
        ("IBAN: TR330006100519786457841326", "IBAN: [IBAN]"),
        ("iban tr330006100519786457841326", "iban [IBAN]"),
        ("Kart 4111111111111111 ile", "Kart [KART] ile"),
        ("Kart 4111 1111 1111 1111 ile", "Kart [KART] ile"),
        ("Kart 4111-1111-1111-1111", "Kart [KART]"),
        ("Amex 3782 822463 10005", "Amex [KART]"),
    ],
)
def test_masks(text, expected):
    assert mask_pii(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "INC0000042 kaydı açıldı",
        "Talep REQ0012345 ve INC12345678901",
        "Numara 10000000147 geçersiz TCKN",
        "01234567890 sıfırla başlar",
        "Kart 4111111111111112 Luhn geçersiz",
        "Sunucu 10.0.0.12 port 8443",
        "Tarih 2024-01-15 saat 14:30",
        "Sipariş no 123456",
        "TR33 0006 1005 kısa iban",
        "Sabit hat 0212 123 45 67",
        "",
    ],
)
def test_does_not_mask_ordinary_numbers(text):
    assert mask_pii(text) == text


def test_multiple_pii_in_one_text():
    text = (
        f"Kullanıcı {VALID_TCKN} (ali@ornek.com, 0532 123 45 67) "
        "TR330006100519786457841326 hesabına 4111111111111111 kartıyla ödeme yaptı. INC0000042"
    )
    masked = mask_pii(text)
    for tag in ("[TCKN]", "[EPOSTA]", "[TELEFON]", "[IBAN]", "[KART]"):
        assert tag in masked
    assert VALID_TCKN not in masked
    assert "ali@ornek.com" not in masked
    assert "INC0000042" in masked


def test_idempotent():
    once = mask_pii(f"{VALID_TCKN} ali@ornek.com")
    assert mask_pii(once) == once


def test_luhn():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    assert not luhn_valid("abc")
