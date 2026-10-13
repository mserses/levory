"""Yerleşik araçlar için ortak parçalar: roller, katı argüman tabanı, sınıf listesi doğrulama."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from serviszeka import taxonomy
from serviszeka.auth import (
    ROLE_ADMIN,
    ROLE_AGENT,
    ROLE_APPROVER,
    ROLE_CAB,
    ROLE_CHANGE_MANAGER,
)

# İnsan rolleri (admin her zaman kapsar)
READERS = frozenset({ROLE_AGENT, ROLE_APPROVER})
WRITERS = frozenset({ROLE_AGENT})
CHANGE_READERS = frozenset({ROLE_AGENT, ROLE_CHANGE_MANAGER, ROLE_CAB})
CHANGE_WRITERS = frozenset({ROLE_AGENT, ROLE_CHANGE_MANAGER})
ADMINS = frozenset({ROLE_ADMIN})

Id = Annotated[str, Field(min_length=1, max_length=36)]


class Args(BaseModel):
    """Araç argüman tabanı: bilinmeyen alan reddedilir."""

    model_config = ConfigDict(extra="forbid")


def _taxonomy(field: str):
    def check(value: str | None) -> str | None:
        if value is not None and value not in taxonomy.allowed(field):
            raise ValueError(f"{field} sınıf listesinde yok: {value}")
        return value

    return AfterValidator(check)


_Key = Annotated[str, Field(min_length=1, max_length=100)]

Category = Annotated[
    _Key | None,
    _taxonomy("category"),
    Field(default=None, description="Kategori (taxonomy.json anahtarı)"),
]
Service = Annotated[
    _Key | None,
    _taxonomy("service"),
    Field(default=None, description="Hizmet (taxonomy.json anahtarı)"),
]
AssignmentGroup = Annotated[
    _Key | None,
    _taxonomy("assignment_group"),
    Field(default=None, description="Çözüm grubu (taxonomy.json anahtarı)"),
]
