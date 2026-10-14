"""docs/KURULUM.md ortam değişkeni tablosu config.py ile eşzamanlı mı.

Tablo `docs/_gen_settings.py --write` ile üretilir. `config.Settings`'e eklenen her ayar tabloda
(bir bölümde ve açıklamasıyla) yer almalı; kaldırılan ayar tablodan da çıkmalı.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from serviszeka.config import Settings

ROOT = Path(__file__).resolve().parents[1]
GEN_PATH = ROOT / "docs" / "_gen_settings.py"
DOC_PATH = ROOT / "docs" / "KURULUM.md"
FIX = "Düzeltmek için: uv run python docs/_gen_settings.py --write"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("_gen_settings", GEN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def block(gen) -> str:
    text = DOC_PATH.read_text(encoding="utf-8")
    found = gen.current_block(text)
    assert found is not None, f"KURULUM.md'de ayar tablosu işaretleri yok. {FIX}"
    return found


def _documented(block: str) -> set[str]:
    return set(re.findall(r"^\| `(SZ_[A-Z0-9_]+)` \|", block, flags=re.MULTILINE))


def test_every_setting_is_in_kurulum_table(block):
    expected = {f"SZ_{name.upper()}" for name in Settings.model_fields}
    missing = sorted(expected - _documented(block))
    assert not missing, f"config.py'de olup KURULUM.md tablosunda olmayan ayarlar: {missing}. {FIX}"


def test_kurulum_table_has_no_stale_settings(block):
    expected = {f"SZ_{name.upper()}" for name in Settings.model_fields}
    stale = sorted(_documented(block) - expected)
    assert not stale, f"KURULUM.md tablosunda olup config.py'de olmayan ayarlar: {stale}. {FIX}"


def test_every_setting_has_area_and_description(gen):
    names = set(Settings.model_fields)
    unassigned = sorted(n for n in names if gen.area_of(n) is None)
    undescribed = sorted(names - set(gen.DESCRIPTIONS))
    assert not unassigned, f"docs/_gen_settings.py AREAS'ta bölümü olmayan ayarlar: {unassigned}"
    assert not undescribed, f"docs/_gen_settings.py DESCRIPTIONS'ta olmayan ayarlar: {undescribed}"
    unknown = sorted((set(gen.DESCRIPTIONS) | {n for _, ns in gen.AREAS for n in ns}) - names)
    assert not unknown, f"docs/_gen_settings.py config.py'de olmayan ayarları anıyor: {unknown}"


def test_kurulum_table_matches_generator(gen, block):
    """Tür, varsayılan ve açıklamalar da güncel (config.py'de varsayılan değişirse yakalanır)."""
    assert block == gen.render(), f"KURULUM.md ayar tablosu güncel değil. {FIX}"
