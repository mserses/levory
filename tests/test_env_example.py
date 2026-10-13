"""`.env.example` her ayarı (SZ_ önekli) belgelemeli: yeni ayar eklenince örnek dosya da güncellenir."""

import re
from pathlib import Path

from serviszeka.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def test_env_example_documents_every_setting():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*(SZ_[A-Z0-9_]+)=", text, flags=re.MULTILINE))
    expected = {f"SZ_{name.upper()}" for name in Settings.model_fields}
    assert sorted(expected - documented) == []
    assert sorted(documented - expected) == []
