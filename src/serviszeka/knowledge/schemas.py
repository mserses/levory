"""Bilgi bankası API şemaları."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from serviszeka.domain.types import UTCDateTime

ArticleState = Literal["draft", "published", "retired"]


class ArticleCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    category: str | None = Field(default=None, max_length=100)
    is_known_error: bool = False
    workaround: str | None = None
    source_incident_ids: list[str] = Field(default_factory=list)
    valid_until: datetime | None = None
    # API üzerinden oluşturulan makaleler varsayılan olarak taslaktır
    state: Literal["draft", "published"] = "draft"


class ArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, max_length=100)
    is_known_error: bool | None = None
    workaround: str | None = None
    source_incident_ids: list[str] | None = None
    valid_until: datetime | None = None


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    number: str
    title: str
    body: str
    category: str | None
    is_known_error: bool
    workaround: str | None
    state: str
    source_incident_ids: list[str]
    valid_until: UTCDateTime | None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class SearchHitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    article_id: str
    number: str
    title: str
    snippet: str
    score: float


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitOut]
