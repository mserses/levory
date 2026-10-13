"""Hizmet talebi / bağlayıcı testleri için ortak yardımcılar (ayarlar, kullanıcılar).

Sahte dizin fikstürü `directory` `tests/conftest.py` içindedir.
"""

from __future__ import annotations

from serviszeka.auth import CurrentUser
from serviszeka.config import Settings
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.request_evaluation import STANDARD_GROUPS
from serviszeka.services import catalog

# Canlı politika (gölge kapalı, eşik 0.75) + standart grup izin listesi
LIVE_REQ = Settings(
    ai_shadow_mode=False, ai_confidence_threshold=0.75, identity_standard_groups=STANDARD_GROUPS
)
SHADOW_REQ = Settings(
    ai_shadow_mode=True, ai_confidence_threshold=0.75, identity_standard_groups=STANDARD_GROUPS
)

ALI = CurrentUser("ali", frozenset({"requester"}))
MEHMET = CurrentUser("mehmet", frozenset({"requester"}))
ZEYNEP = CurrentUser("zeynep", frozenset({"requester"}))  # demo dizinde herkesin yöneticisi
DESK = CurrentUser("ayse", frozenset({"agent"}))
APPROVER = CurrentUser("sef.ali", frozenset({"approver"}))
APPROVER2 = CurrentUser("sef.veli", frozenset({"approver"}))


async def seeded_item(session, key: str):
    await catalog.seed_default_items(session)
    return await catalog.get_item(session, key)


def writes(d: InMemoryDirectory, op: str | None = None) -> list:
    return [w for w in d.writes if op is None or w[0] == op]
