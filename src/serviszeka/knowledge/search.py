"""Hibrit bilgi bankası araması: saf Python BM25 + isteğe bağlı embedding benzerliği.

Harici servis gerektirmez; SQLite üzerinde çalışır. Yalnızca 'published' makaleler aranır.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka.models import KnowledgeArticle

# BM25 parametreleri
BM25_K1 = 1.5
BM25_B = 0.75
# Başlık terimleri gövdeye göre daha ağır sayılır (başlık tekrar edilerek)
TITLE_WEIGHT = 2
# Bilinen hata (KEDB) kayıtlarına küçük çarpan
KNOWN_ERROR_BOOST = 1.15
# Hibrit karışım: BM25 (normalize) ve kosinüs benzerliği ağırlıkları
LEXICAL_WEIGHT = 0.6
SEMANTIC_WEIGHT = 0.4
# Basit Türkçe kök yaklaşımı: token'ın ilk N harfi önek eşleşmesi için kullanılır
PREFIX_LEN = 5

TURKISH_STOPWORDS: frozenset[str] = frozenset(
    {
        "acaba", "ama", "ancak", "bazı", "bu", "bir", "biz", "bunu", "bunun", "da", "daha",
        "de", "değil", "diye", "en", "gibi", "hem", "hep", "hiç", "için", "ile", "ise", "ki",
        "mi", "mı", "mu", "mü", "nasıl", "ne", "neden", "o", "olan", "olarak", "ol", "şey",
        "şu", "sonra", "veya", "ve", "ya", "yani", "çok", "her", "ben", "sen", "siz", "onlar",
        "var", "yok", "the", "a", "an", "and", "or", "of", "to", "in", "is", "for", "on",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def turkish_casefold(text: str) -> str:
    """Türkçe'ye duyarlı küçük harfe çevirme (I→ı, İ→i)."""
    text = text.replace("I", "ı").replace("İ", "i")
    text = text.lower()
    # 'İ'.lower() bazı ortamlarda 'i̇' (birleşik nokta) üretir; temizle
    return unicodedata.normalize("NFC", text.replace("i̇", "i"))


def tokenize(text: str) -> list[str]:
    """Normalize et, noktalamayı at, durak kelimeleri çıkar."""
    text = _PUNCT_RE.sub(" ", turkish_casefold(text or ""))
    return [t for t in text.split() if t and t not in TURKISH_STOPWORDS and not t.isspace()]


def _expand(tokens: Sequence[str]) -> list[str]:
    """Her token'a önek token'ı ekler: 'bağlanamıyorum' ~ 'bağlantı' ('bağla' öneki)."""
    out: list[str] = []
    for t in tokens:
        out.append(t)
        if len(t) > PREFIX_LEN:
            out.append(f"{t[:PREFIX_LEN]}*")
    return out


@dataclass(slots=True)
class SearchHit:
    article_id: str
    number: str
    title: str
    snippet: str
    score: float


@runtime_checkable
class Embedder(Protocol):
    """Metni vektöre çeviren bileşen. Varsayılan bir ağ embedder'ı yoktur."""

    async def embed(self, text: str) -> list[float]: ...


class BM25:
    """Saf Python Okapi BM25."""

    def __init__(self, docs: Sequence[Sequence[str]], k1: float = BM25_K1, b: float = BM25_B):
        self.k1 = k1
        self.b = b
        self.tfs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.n = len(docs)
        self.avgdl = (sum(self.lens) / self.n) if self.n else 0.0
        df: Counter[str] = Counter()
        for tf in self.tfs:
            df.update(tf.keys())
        self.idf = {
            term: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for term, f in df.items()
        }

    def score(self, query: Sequence[str], idx: int) -> float:
        tf = self.tfs[idx]
        dl = self.lens[idx]
        norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl) if self.avgdl else self.k1
        s = 0.0
        for term in set(query):
            f = tf.get(term)
            if not f:
                continue
            s += self.idf.get(term, 0.0) * f * (self.k1 + 1) / (f + norm)
        return s


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def make_snippet(body: str, query_tokens: Sequence[str], width: int = 200) -> str:
    """Sorgu terimlerinden birinin geçtiği yerin çevresinden kısa bir alıntı."""
    body = " ".join((body or "").split())
    if len(body) <= width:
        return body
    folded = turkish_casefold(body)
    pos = -1
    for t in query_tokens:
        p = folded.find(t if len(t) <= PREFIX_LEN else t[:PREFIX_LEN])
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    start = max(0, pos - width // 4) if pos != -1 else 0
    end = min(len(body), start + width)
    snippet = body[start:end].strip()
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(body) else "")


def _doc_tokens(article: KnowledgeArticle) -> list[str]:
    title = tokenize(article.title)
    body = tokenize(article.body)
    extra = tokenize(article.workaround or "")
    return _expand(title * TITLE_WEIGHT + body + extra)


async def search(
    session: AsyncSession,
    query: str,
    limit: int = 5,
    *,
    embedder: Embedder | None = None,
    category: str | None = None,
) -> list[SearchHit]:
    """Yayındaki makalelerde hibrit arama; skora göre azalan SearchHit listesi döner."""
    q_tokens = tokenize(query)
    if not q_tokens or limit <= 0:
        return []

    stmt = select(KnowledgeArticle).where(KnowledgeArticle.state == "published")
    if category:
        stmt = stmt.where(KnowledgeArticle.category == category)
    articles = list((await session.scalars(stmt)).all())
    if not articles:
        return []

    bm25 = BM25([_doc_tokens(a) for a in articles])
    q_expanded = _expand(q_tokens)
    lexical = [bm25.score(q_expanded, i) for i in range(len(articles))]
    max_lex = max(lexical) or 1.0

    semantic: list[float] | None = None
    if embedder is not None and any(a.embedding for a in articles):
        q_vec = await embedder.embed(query)
        semantic = [max(0.0, cosine(q_vec, a.embedding or [])) for a in articles]

    hits: list[SearchHit] = []
    for i, art in enumerate(articles):
        lex = lexical[i] / max_lex
        if semantic is not None:
            score = LEXICAL_WEIGHT * lex + SEMANTIC_WEIGHT * semantic[i]
        else:
            score = lex
        if score <= 0:
            continue
        if art.is_known_error:
            score *= KNOWN_ERROR_BOOST
        hits.append(
            SearchHit(
                article_id=art.id,
                number=art.number,
                title=art.title,
                snippet=make_snippet(art.body, q_tokens),
                score=round(score, 6),
            )
        )
    hits.sort(key=lambda h: (-h.score, h.number))
    return hits[:limit]
