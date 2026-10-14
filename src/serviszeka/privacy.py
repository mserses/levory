"""Kişisel veri (PII) maskeleme: LLM'e giden her metin buradan geçer (KVKK)."""

from __future__ import annotations

import re

# IBAN: TR + 24 hane, boşluklu ya da bitişik.
_IBAN_RE = re.compile(r"(?<![\w])TR(?:[ ]?\d){24}(?!\w)", re.IGNORECASE)

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])"
)

# Telefon: +90 / 0090 / 0 önekli ya da öneksiz 5xx cep numaraları; boşluk, nokta, tire, parantez.
_SEP = r"[ .-]?"
_PHONE_RE = re.compile(
    r"(?<![\w+])"
    # Önek isteğe bağlı: "532 123 45 67" / "(532) 123-45-67" gibi yazımlar da maskelenir
    r"(?:(?:(?:\+|00)90" + _SEP + r"\(?0?|\(?0" + _SEP + r"\(?)" + _SEP + r"|\()?"
    r"5\d{2}\)?" + _SEP + r"\d{3}" + _SEP + r"\d{2}" + _SEP + r"\d{2}"
    r"(?!\w)"
)

# TCKN: 11 hane, ilk hane sıfır değil (sağlama ayrıca doğrulanır).
_TCKN_RE = re.compile(r"(?<![\w])[1-9]\d{10}(?!\w)")

# Kart: bitişik 13-19 hane ya da 4'lü gruplar / Amex (4-6-5) biçimi (Luhn ayrıca doğrulanır).
_CARD_RE = re.compile(
    r"(?<![\w])(?:\d{13,19}"
    r"|\d{4}(?:[ -]\d{4}){2}[ -]\d{1,7}"
    r"|\d{4}[ -]\d{6}[ -]\d{5})(?!\w)"
)


def is_valid_tckn(value: str) -> bool:
    """T.C. kimlik numarası sağlama algoritması."""
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    d = [int(c) for c in value]
    odd = d[0] + d[2] + d[4] + d[6] + d[8]
    even = d[1] + d[3] + d[5] + d[7]
    if (odd * 7 - even) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def luhn_valid(digits: str) -> bool:
    """Luhn (mod 10) kontrolü."""
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _mask_tckn(m: re.Match[str]) -> str:
    return "[TCKN]" if is_valid_tckn(m.group(0)) else m.group(0)


def _mask_card(m: re.Match[str]) -> str:
    digits = re.sub(r"[ -]", "", m.group(0))
    if 13 <= len(digits) <= 19 and luhn_valid(digits):
        return "[KART]"
    return m.group(0)


def mask_pii(text: str) -> str:
    """Metindeki TCKN, e-posta, telefon, IBAN ve kart numaralarını etiketlerle değiştirir."""
    if not text:
        return text
    # Sıra önemli: IBAN ve e-posta, rakam desenlerinden önce maskelenir.
    text = _IBAN_RE.sub("[IBAN]", text)
    text = _EMAIL_RE.sub("[EPOSTA]", text)
    text = _PHONE_RE.sub("[TELEFON]", text)
    text = _TCKN_RE.sub(_mask_tckn, text)
    text = _CARD_RE.sub(_mask_card, text)
    return text


__all__ = ["is_valid_tckn", "luhn_valid", "mask_pii"]
