"""Faz 4 — izleme ve olay (event) yönetimi: webhook doğrulama, ayrıştırma, CI eşleme, bağıntı.

Akış (`POST /events/alertmanager|generic`, bkz. `api/events.py`, `docs/faz4.md`):

1. **Doğrulama** (`authenticate_webhook`): `SZ_EVENTS_WEBHOOK_SECRET` boşsa reddedilir. Ya paylaşılan
   gizli (`Authorization: Bearer <gizli>` / `X-ServisZeka-Token`) ya da HMAC-SHA256 imza
   (`X-ServisZeka-Signature: sha256=hex(HMAC(gizli, "<ts>." + gövde))` + `X-ServisZeka-Timestamp`
   Unix saniye, ±pencere). Karşılaştırmalar sabit zamanlı (`hmac.compare_digest`).
2. **Yeniden oynatma koruması**: zaman damgası penceresi + teslimat tekilleştirme anahtarı
   (`EventDelivery.dedup_key`; imzalıda ts+gövde, gizlide gövde özeti; iki pencere saklanır).
   Ayrıca uyarı düzeyinde idempotans: (kaynak, parmak izi, starts_at) tekildir ve çözülen uyarı
   eski bir "firing" gönderimiyle yeniden açılmaz.
3. **Ayrıştırma + maskeleme**: önem `normalize_severity`; etiket/açıklama değerleri
   `privacy.mask_pii`, gizli görünen anahtarlar (`password`, `token`...) `***`.
4. **CI eşleme** (`CIResolver`): `SZ_EVENTS_CI_MAPPING` kuralları sırayla (ilk eşleşen kazanır):
   etiket değeri -> CI kimliği / adı (büyük-küçük harf duyarsız, `strip_port` ile `host:9100` ->
   `host`; aynı ad birden çok ortamda varsa `environment`/`env` etiketi, yoksa prod) / harici referans
   (`external_refs[key]`) / öznitelik (`attributes[key]`).
5. **Bağıntı** (`_assign_group`): aday gruplar açık gruplar ve uyarıları çözülmüş ama olayı hâlâ
   açık gruplardır (yeniden ateşleme yeni olay açmaz; grup yeniden açılır, olaya not düşülür).
   (a) aynı kaynak + parmak izi açık grupta ateşleniyorsa (ya da olayı açık çözülmüş grupta
   görüldüyse) o grup (tekilleştirme); (b) CMDB etki grafiği: pencere içinde olay görmüş açık grupların kök CI'ı yeni
   uyarının CI'ının **bağımlılığıysa** (yukarı yön, `cmdb` ile aynı ilişki yönleri) uyarı o gruba
   katılır (en yukarıdaki kök); (c) yeni CI açık bir grubun kökünün bağımlılığıysa yeni uyarı o
   grubu **açıklar**: grup yeni CI'a yeniden köklenir ve açıkladığı diğer olaysız gruplar birleşir
   (`merged`). Olay açılmış gruplar birleştirilmez (grup başına tek olay kuralı korunur).
6. **Çırpınma**: aynı parmak izi `SZ_EVENTS_FLAP_WINDOW_MINUTES` içinde `SZ_EVENTS_FLAP_THRESHOLD`
   kez ateşlenirse uyarı `suppressed="flapping"`; susturulmuş grupta `silenced`. Bastırılan uyarı
   önemi/değişiklik zamanını etkilemez (olay açmaz, not tetiklemez).
7. **Olay** (`open_or_update_incident`, yalnızca `event.create_incident` aracının işleyicisinden):
   grup başına tek olay; olay varsa yeni/çözülen uyarılar çalışma notu (`incident.work_note`
   denetimi) + yeni CI bağları. Öncelik mevcut kuraldan: etki = grubun CI'larının etki analizindeki
   en yüksek kritiklik (1 -> high, 2 -> medium, diğer -> low; kesilen analiz en kötü), aciliyet =
   önem (critical -> high, high -> medium, diğer -> low), `calculate_priority`.

Fonksiyonlar flush eder, commit etmez. Yapay zekâ kararı burada verilmez: olay açma/güncelleme ve
çözüm önerisi `agents/events.py` (`ai:events`) tarafından araç boru hattından geçer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from serviszeka import audit, taxonomy
from serviszeka.config import EventCIRule, Settings
from serviszeka.domain.enums import (
    CIRelationshipType,
    EventGroupState,
    EventSeverity,
    EventStatus,
    Impact,
    IncidentStatus,
    Priority,
    TicketKind,
    Urgency,
)
from serviszeka.domain.priority import calculate_priority
from serviszeka.models import (
    ActionRequest,
    AuditLog,
    CIRelationship,
    ConfigurationItem,
    Event,
    EventDelivery,
    EventGroup,
    Incident,
)
from serviszeka.privacy import mask_pii
from serviszeka.services import cmdb
from serviszeka.services import incidents as incident_service
from serviszeka.services.event_schemas import (
    AlertmanagerPayload,
    CorrelatedMember,
    CorrelationRead,
    EventGroupRead,
    GenericPayload,
)
from serviszeka.services.schemas import IncidentCreate
from serviszeka.services.sla import ensure_utc

SYSTEM_ACTOR = "system:events"
# Arayan bir kullanıcı adıyla çakışmamalı: self-servis (requester) kullanıcı caller == kullanıcı adı
# olan kayıtları görür; "izleme" adlı bir kullanıcı tüm izleme olaylarını görürdü. ':' dev
# belirtecinde ayraçtır; Entra UPN ve Keycloak varsayılan kullanıcı adı doğrulayıcısı da kabul etmez.
INCIDENT_CALLER = "system:izleme"
CORRELATION_DEPTH = 5
MAX_CANDIDATE_GROUPS = 500
MAX_NOTE_LINES = 30
MAX_DESCRIPTION_EVENTS = 20
MAX_VALUE_LEN = 2000
MAX_KEY_LEN = 100

SIGNATURE_HEADER = "x-serviszeka-signature"
TIMESTAMP_HEADER = "x-serviszeka-timestamp"
TOKEN_HEADER = "x-serviszeka-token"

SEVERITY_RANK: Mapping[EventSeverity, int] = {
    EventSeverity.INFO: 0,
    EventSeverity.WARNING: 1,
    EventSeverity.HIGH: 2,
    EventSeverity.CRITICAL: 3,
}
_SEVERITY_ALIASES: Mapping[str, EventSeverity] = {
    **dict.fromkeys(
        ("critical", "crit", "fatal", "emergency", "emerg", "alert", "page", "disaster", "p1"),
        EventSeverity.CRITICAL,
    ),
    **dict.fromkeys(("high", "error", "err", "major", "severe", "p2"), EventSeverity.HIGH),
    **dict.fromkeys(("warning", "warn", "minor", "medium", "average", "p3"), EventSeverity.WARNING),
    **dict.fromkeys(
        ("info", "informational", "information", "notice", "low", "none", "ok", "p4", "p5"),
        EventSeverity.INFO,
    ),
}
_SECRET_KEY_RE = re.compile(
    r"pass(word)?|parola|sifre|şifre|secret|token|api[_-]?key|credential|authorization|cookie",
    re.IGNORECASE,
)
_REDACTED = "***"
_ZERO_TIME_PREFIX = "0001-01-01"
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")
_PORT_RE = re.compile(r"^(?P<host>.+?):\d{1,5}$")

_R = CIRelationshipType
_REVERSE_TYPES = (_R.DEPENDS_ON, _R.RUNS_ON, _R.CONNECTS_TO)
_FORWARD_TYPES = (_R.PART_OF,)


class EventError(Exception):
    """Olay yönetimi hatalarının tabanı."""


class WebhookAuthError(EventError):
    """Webhook kimlik doğrulaması başarısız (401)."""


class EventPayloadError(EventError, ValueError):
    """Geçersiz webhook gövdesi (422)."""

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class EventGroupNotFoundError(EventError, LookupError):
    def __init__(self, group_id: str) -> None:
        super().__init__(f"Olay grubu bulunamadı: {group_id}")


def _utc(value: datetime | None) -> datetime | None:
    return ensure_utc(value) if value is not None else None


# --- 1-2) doğrulama ------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeliveryAuth:
    mode: str  # "hmac" | "token"
    dedup_key: str


def _header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        value = headers.get(name.title())
    return value.strip() if isinstance(value, str) and value.strip() else None


def _secret(settings: Settings) -> bytes:
    secret = settings.events_webhook_secret
    value = secret.get_secret_value() if secret is not None else ""
    if not value:
        raise WebhookAuthError("Olay webhook'u yapılandırılmamış (SZ_EVENTS_WEBHOOK_SECRET).")
    return value.encode("utf-8")


def _check_timestamp(raw: str | None, now: datetime, window: int, required: bool) -> str | None:
    if raw is None:
        if required:
            raise WebhookAuthError("İmzalı istekte zaman damgası (X-ServisZeka-Timestamp) zorunlu.")
        return None
    try:
        ts = int(raw)
    except ValueError as exc:
        raise WebhookAuthError("Zaman damgası geçersiz.") from exc
    if abs(now.timestamp() - ts) > window:
        raise WebhookAuthError("Zaman damgası izin verilen pencerenin dışında (yeniden oynatma?).")
    return str(ts)


def presented_token(headers: Mapping[str, str]) -> str | None:
    token = _header(headers, TOKEN_HEADER)
    if token:
        return token
    auth_header = _header(headers, "authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    return None


def has_credentials(headers: Mapping[str, str]) -> bool:
    return bool(_header(headers, SIGNATURE_HEADER) or presented_token(headers))


def check_preconditions(settings: Settings, headers: Mapping[str, str]) -> None:
    """Gövde okunmadan önce: gizli yapılandırılmış mı, kimlik bilgisi var mı.

    İmzasız (paylaşılan gizli) istekte belirteç burada doğrulanır: kimliği doğrulanmamış istemci
    için gövde (en fazla `SZ_EVENTS_MAX_BODY_BYTES`) hiç okunmaz. İmza gövdeyle doğrulanır.
    """
    secret = _secret(settings)
    if _header(headers, SIGNATURE_HEADER):
        return
    token = presented_token(headers)
    if not token:
        raise WebhookAuthError("Kimlik bilgisi yok (paylaşılan gizli ya da imza gerekli).")
    if not hmac.compare_digest(token.encode("utf-8"), secret):
        raise WebhookAuthError("Paylaşılan gizli doğrulanamadı.")


def authenticate_webhook(
    settings: Settings,
    headers: Mapping[str, str],
    body: bytes,
    *,
    source: str,
    now: datetime | None = None,
) -> DeliveryAuth:
    """İsteği doğrular ve teslimat tekilleştirme anahtarını döndürür (hata: WebhookAuthError)."""
    now = now or datetime.now(UTC)
    secret = _secret(settings)
    window = settings.events_replay_window_seconds
    signature = _header(headers, SIGNATURE_HEADER)
    if signature:
        ts = _check_timestamp(_header(headers, TIMESTAMP_HEADER), now, window, required=True)
        assert ts is not None
        expected = hmac.new(secret, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
        provided = signature.removeprefix("sha256=").strip().lower()
        if not hmac.compare_digest(expected.encode(), provided.encode("utf-8", "replace")):
            raise WebhookAuthError("İmza doğrulanamadı.")
        digest = hashlib.sha256(f"{source}|hmac|{ts}|".encode() + body).hexdigest()
        return DeliveryAuth("hmac", digest)
    token = presented_token(headers)
    if not token or not hmac.compare_digest(token.encode("utf-8"), secret):
        raise WebhookAuthError("Paylaşılan gizli doğrulanamadı.")
    ts = _check_timestamp(_header(headers, TIMESTAMP_HEADER), now, window, required=False)
    digest = hashlib.sha256(f"{source}|token|{ts or ''}|".encode() + body).hexdigest()
    return DeliveryAuth("token", digest)


async def register_delivery(
    session: AsyncSession, auth: DeliveryAuth, *, source: str, now: datetime, settings: Settings
) -> bool:
    """Teslimatı kaydeder; pencere içinde aynı anahtar varsa False (yinelenen teslimat).

    Kayıt **iki pencere** saklanır: zaman damgası ±pencere kabul edildiğinden ileri tarihli (saati
    ileride göndericinin) imzası alındıktan sonra 2 pencereye kadar geçerli kalabilir; kayıt tek
    pencerede silinseydi aynı imzalı istek yeniden oynatılabilirdi.
    """
    cutoff = now - timedelta(seconds=2 * settings.events_replay_window_seconds)
    await session.execute(delete(EventDelivery).where(EventDelivery.received_at < cutoff))
    try:
        async with session.begin_nested():
            session.add(EventDelivery(dedup_key=auth.dedup_key, source=source, received_at=now))
            await session.flush()
    except IntegrityError:
        return False
    return True


# --- 3) ayrıştırma ------------------------------------------------------------------------------


@dataclass
class IncomingAlert:
    source: str
    fingerprint: str
    status: EventStatus
    severity: EventSeverity
    title: str
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: datetime
    ends_at: datetime | None


def normalize_severity(value: Any) -> EventSeverity:
    """Kaynağa özgü önem adını (critical, error, warning, P2...) ortak ölçeğe çevirir."""
    key = str(value or "").strip().lower()
    return _SEVERITY_ALIASES.get(key, EventSeverity.WARNING)


def clean_map(values: Mapping[str, Any]) -> dict[str, str]:
    """Etiket/açıklama: gizli görünen anahtarlar gizlenir, değerler PII maskeli ve kısaltılır."""
    out: dict[str, str] = {}
    for key, value in values.items():
        k = str(key)[:MAX_KEY_LEN]
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        out[k] = _REDACTED if _SECRET_KEY_RE.search(k) else mask_pii(text)[:MAX_VALUE_LEN]
    return out


def parse_time(raw: str | None, *, field_name: str) -> datetime | None:
    """RFC 3339 (Alertmanager nanosaniye kesirleri dahil); sıfır zaman (`0001-01-01`) -> None."""
    if raw is None or not str(raw).strip() or str(raw).startswith(_ZERO_TIME_PREFIX):
        return None
    text = _FRACTION_RE.sub(r"\1", str(raw).strip())
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EventPayloadError(f"Geçersiz zaman ({field_name}): {raw!r}") from exc
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def compute_fingerprint(source: str, labels: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        {"source": source, "labels": {str(k): str(v) for k, v in labels.items()}},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _title(labels: Mapping[str, str], annotations: Mapping[str, str], fingerprint: str) -> str:
    name = labels.get("alertname") or ""
    summary = annotations.get("summary") or annotations.get("title") or ""
    if name and summary and summary != name:
        text = f"{name}: {summary}"
    else:
        text = summary or name or f"Uyarı {fingerprint[:12]}"
    return text[:300]


def _validation_error(exc: ValidationError) -> EventPayloadError:
    errors = json.loads(exc.json(include_url=False, include_input=False))
    fields = ", ".join(".".join(str(p) for p in e.get("loc", ())) or "-" for e in errors[:10])
    return EventPayloadError(f"Gövde geçersiz ({fields}).", errors)


def parse_alertmanager(payload: Any) -> list[IncomingAlert]:
    try:
        data = AlertmanagerPayload.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    alerts: list[IncomingAlert] = []
    for item in data.alerts:
        raw_labels = {str(k): str(v) for k, v in item.labels.items()}
        fingerprint = item.fingerprint or compute_fingerprint("alertmanager", raw_labels)
        labels = clean_map(raw_labels)
        annotations = clean_map(item.annotations)
        starts_at = parse_time(item.startsAt, field_name="startsAt")
        if starts_at is None:
            raise EventPayloadError("startsAt zorunlu.")
        alerts.append(
            IncomingAlert(
                source="alertmanager",
                fingerprint=fingerprint,
                status=EventStatus(item.status),
                severity=normalize_severity(raw_labels.get("severity")),
                title=_title(labels, annotations, fingerprint),
                labels=labels,
                annotations=annotations,
                starts_at=starts_at,
                ends_at=parse_time(item.endsAt, field_name="endsAt"),
            )
        )
    return alerts


def parse_generic(payload: Any, *, now: datetime | None = None) -> list[IncomingAlert]:
    now = now or datetime.now(UTC)
    try:
        data = GenericPayload.model_validate(payload)
    except ValidationError as exc:
        raise _validation_error(exc) from exc
    alerts: list[IncomingAlert] = []
    for item in data.events:
        raw_labels = {str(k): str(v) for k, v in item.labels.items()}
        fingerprint = item.fingerprint or compute_fingerprint(item.source, raw_labels)
        labels = clean_map(raw_labels)
        annotations = clean_map(item.annotations)
        title = (
            mask_pii(item.title)[:300] if item.title else _title(labels, annotations, fingerprint)
        )
        alerts.append(
            IncomingAlert(
                source=item.source,
                fingerprint=fingerprint,
                status=item.status,
                severity=normalize_severity(item.severity),
                title=title,
                labels=labels,
                annotations=annotations,
                starts_at=item.starts_at or now,
                ends_at=item.ends_at,
            )
        )
    return alerts


# --- 4) CI eşleme ---------------------------------------------------------------------------------


class CIResolver:
    """Etiketlerden CI bulur (kurallar sırayla); harici referans/öznitelik dizinleri bir kez kurulur."""

    def __init__(self, session: AsyncSession, rules: Sequence[EventCIRule]) -> None:
        self.session = session
        self.rules = list(rules)
        self._maps: dict[tuple[str, str], dict[str, str]] = {}
        self._cache: dict[tuple[int, str, str | None], str | None] = {}

    async def _index(self, kind: str, key: str) -> dict[str, str]:
        if (kind, key) not in self._maps:
            column = (
                ConfigurationItem.external_refs if kind == "external_ref"
                else ConfigurationItem.attributes
            )  # fmt: skip
            rows = (
                await self.session.execute(
                    select(ConfigurationItem.id, column).order_by(ConfigurationItem.created_at)
                )
            ).all()
            index: dict[str, str] = {}
            for ci_id, values in rows:
                value = (values or {}).get(key) if isinstance(values, dict) else None
                if value is not None:
                    index.setdefault(str(value).lower(), ci_id)
            self._maps[(kind, key)] = index
        return self._maps[(kind, key)]

    async def _by_name(self, value: str, env: str | None) -> str | None:
        rows = (
            await self.session.execute(
                select(ConfigurationItem.id, ConfigurationItem.environment)
                .where(func.lower(ConfigurationItem.name) == value.lower())
                .order_by(ConfigurationItem.created_at, ConfigurationItem.id)
            )
        ).all()
        if not rows:
            return None
        envs = {str(getattr(e, "value", e)): i for i, e in reversed(rows)}
        if env and env.lower() in envs:
            return envs[env.lower()]
        return envs.get("prod", rows[0][0])

    async def resolve(self, labels: Mapping[str, str]) -> tuple[str | None, str | None]:
        env = labels.get("environment") or labels.get("env")
        for index, rule in enumerate(self.rules):
            raw = labels.get(rule.label)
            if not raw or raw == _REDACTED:
                continue
            value = raw.strip()
            if rule.strip_port and (m := _PORT_RE.match(value)) and "]" not in value[-7:]:
                value = m.group("host")
            cache_key = (index, value, env)
            if cache_key not in self._cache:
                self._cache[cache_key] = await self._match(rule, value, env)
            ci_id = self._cache[cache_key]
            if ci_id:
                return ci_id, f"{rule.label}:{rule.match}"[:100]
        return None, None

    async def _match(self, rule: EventCIRule, value: str, env: str | None) -> str | None:
        if rule.match == "id":
            ci = await self.session.get(ConfigurationItem, value[:36])
            return ci.id if ci is not None else None
        if rule.match == "name":
            return await self._by_name(value, env)
        if not rule.key:
            return None
        return (await self._index(rule.match, rule.key)).get(value.lower())


# --- CMDB grafiği ----------------------------------------------------------------------------------


async def upstream(
    session: AsyncSession, ci_id: str, max_depth: int = CORRELATION_DEPTH
) -> dict[str, int]:
    """CI'ın bağımlılıkları (arızası bu CI'ı etkileyebilecek CI'lar) -> uzaklık.

    `cmdb.impact_analysis`'in tersi: depends_on/runs_on/connects_to'da hedef, part_of'ta parça.
    """
    dist: dict[str, int] = {ci_id: 0}
    frontier = [ci_id]
    for depth in range(1, max_depth + 1):
        if not frontier or len(dist) > cmdb.MAX_IMPACT_NODES:
            break
        rels = (
            await session.scalars(
                select(CIRelationship).where(
                    or_(
                        CIRelationship.source_id.in_(frontier)
                        & CIRelationship.type.in_(list(_REVERSE_TYPES)),
                        CIRelationship.target_id.in_(frontier)
                        & CIRelationship.type.in_(list(_FORWARD_TYPES)),
                    )
                )
            )
        ).all()
        nxt: list[str] = []
        for rel in sorted(rels, key=lambda r: (r.source_id, r.target_id, str(r.type))):
            up = rel.target_id if CIRelationshipType(rel.type) in _REVERSE_TYPES else rel.source_id
            if up not in dist:
                dist[up] = depth
                nxt.append(up)
        frontier = nxt
    dist.pop(ci_id, None)
    return dist


async def downstream(
    session: AsyncSession, ci_id: str, max_depth: int = CORRELATION_DEPTH
) -> dict[str, list[str]]:
    """CI'a bağımlı CI'lar -> kökten yol (CI kimlikleri)."""
    analysis = await cmdb.impact_analysis(session, [ci_id], max_depth=max_depth)
    return {a.ci.id: a.path for a in analysis.affected}


# --- 5-6) alım ve bağıntı -------------------------------------------------------------------------


@dataclass
class IngestResult:
    received: int = 0
    created: int = 0
    updated: int = 0
    ignored: int = 0
    group_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)


async def _next_group_number(session: AsyncSession) -> str:
    # Webhook başına 500'e kadar yeni grup açılabilir: tüm numaraları okumak (O(n)) yerine en
    # büyüğü (önce uzunluk, sonra sözlük sırası: 7 haneyi aşan numaralarda da doğru)
    numbers = (
        await session.scalars(
            select(EventGroup.number)
            .where(EventGroup.number.like("EVG%"))
            .order_by(func.length(EventGroup.number).desc(), EventGroup.number.desc())
            .limit(10)
        )
    ).all()
    highest = max((int(n[3:]) for n in numbers if n[3:].isdigit()), default=0)
    return f"EVG{highest + 1:07d}"


def is_silenced(group: EventGroup, now: datetime) -> bool:
    until = _utc(group.silenced_until)
    return until is not None and until > now


_OPEN_INCIDENT_STATUSES = (
    IncidentStatus.NEW,
    IncidentStatus.ASSIGNED,
    IncidentStatus.IN_PROGRESS,
    IncidentStatus.ON_HOLD,
)


def _has_open_incident() -> Any:
    """Grubun olayı hâlâ açık (çözülmemiş) mı — ilişkili alt sorgu."""
    return (
        select(Incident.id)
        .where(Incident.id == EventGroup.incident_id, Incident.status.in_(_OPEN_INCIDENT_STATUSES))
        .exists()
    )


def _live_group() -> Any:
    """Yeni uyarının katılabileceği grup: açık ya da uyarıları çözülmüş ama olayı hâlâ açık.

    Çözülen grup yeniden ateşlenince (olay çözümü insan onayı beklerken) yeni grup + yeni olay
    açılmaz; aynı grup yeniden açılır ve mevcut olaya not düşülür (olay spamı önlenir).
    """
    return or_(
        EventGroup.state == EventGroupState.OPEN,
        (EventGroup.state == EventGroupState.RESOLVED)
        & EventGroup.incident_id.is_not(None)
        & _has_open_incident(),
    )


async def _open_group_with_fingerprint(
    session: AsyncSession, source: str, fingerprint: str
) -> EventGroup | None:
    group = await session.scalar(
        select(EventGroup)
        .join(Event, Event.group_id == EventGroup.id)
        .where(
            Event.source == source,
            Event.fingerprint == fingerprint,
            Event.status == EventStatus.FIRING,
            EventGroup.state == EventGroupState.OPEN,
        )
        .order_by(EventGroup.last_event_at.desc())
        .limit(1)
    )
    if group is not None:
        return group
    # Aynı parmak izi çözülmüş ama olayı hâlâ açık bir grupta: o grup yeniden açılır
    group = await session.scalar(
        select(EventGroup)
        .join(Event, Event.group_id == EventGroup.id)
        .where(
            Event.source == source,
            Event.fingerprint == fingerprint,
            EventGroup.state == EventGroupState.RESOLVED,
            EventGroup.incident_id.is_not(None),
            _has_open_incident(),
        )
        .order_by(EventGroup.last_event_at.desc())
        .limit(1)
    )
    if group is not None:
        return group
    return await session.scalar(
        select(EventGroup)
        .where(
            _live_group(),
            EventGroup.root_ci_id.is_(None),
            EventGroup.fingerprint == fingerprint,
        )
        .order_by(EventGroup.last_event_at.desc())
        .limit(1)
    )


async def _new_group(session: AsyncSession, event: Event, now: datetime, actor: str) -> EventGroup:
    group = EventGroup(
        number=await _next_group_number(session),
        state=EventGroupState.OPEN,
        title=event.title,
        root_ci_id=event.ci_id,
        fingerprint=None if event.ci_id else event.fingerprint,
        severity=event.severity,
        event_count=0,
        first_event_at=event.starts_at,
        last_event_at=event.starts_at,
        last_change_at=now,
        flapping=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(group)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="event.group.create",
        entity_type="event_group",
        entity_id=group.id,
        details={"number": group.number, "root_ci_id": group.root_ci_id},
    )
    return group


async def _assign_group(
    session: AsyncSession, event: Event, *, settings: Settings, now: datetime, actor: str
) -> tuple[EventGroup, list[EventGroup]]:
    """Uyarının grubu (gerekirse yeni/yeniden köklenmiş) ve birleştirilen gruplar."""
    same = await _open_group_with_fingerprint(session, event.source, event.fingerprint)
    if same is not None:
        return same, []
    if event.ci_id is None:
        return await _new_group(session, event, now, actor), []

    since = event.starts_at - timedelta(minutes=settings.events_correlation_window_minutes)
    candidates = list(
        (
            await session.scalars(
                select(EventGroup)
                .where(
                    _live_group(),
                    EventGroup.root_ci_id.is_not(None),
                    EventGroup.last_event_at >= since,
                )
                .order_by(EventGroup.first_event_at, EventGroup.number)
                .limit(MAX_CANDIDATE_GROUPS)
            )
        ).all()
    )
    if not candidates:
        return await _new_group(session, event, now, actor), []
    ci = event.ci_id
    ups = await upstream(session, ci)
    downs = await downstream(session, ci)
    same_root = [g for g in candidates if g.root_ci_id == ci]
    explaining = sorted(
        (g for g in candidates if g.root_ci_id in ups),
        key=lambda g: (-ups[g.root_ci_id or ""], g.incident_id is None),
    )
    explained = [g for g in candidates if g.root_ci_id in downs]

    target: EventGroup | None = None
    if same_root:
        target = same_root[0]
    elif explaining:
        target = explaining[0]  # en yukarıdaki kök (eşitlikte olaylı grup)
    elif explained:
        # Yeni uyarı, açık grupların kök nedenini açıklıyor: olaylı grup varsa o, yoksa en eski
        target = next((g for g in explained if g.incident_id), explained[0])
        old_root = target.root_ci_id
        target.root_ci_id = ci
        target.last_change_at = now
        target.version += 1
        await audit.record(
            session,
            actor=actor,
            action="event.group.reroot",
            entity_type="event_group",
            entity_id=target.id,
            details={"number": target.number, "from_ci_id": old_root, "to_ci_id": ci},
        )
    if target is None:
        return await _new_group(session, event, now, actor), []

    target_downs = (
        downs if target.root_ci_id == ci else await downstream(session, target.root_ci_id or ci)
    )
    merged: list[EventGroup] = []
    for g in candidates:
        if g is target or g.incident_id is not None or is_silenced(g, now):
            continue
        if g.root_ci_id in target_downs or g.root_ci_id == target.root_ci_id:
            await _merge(session, g, target, now=now, actor=actor)
            merged.append(g)
    return target, merged


async def _merge(
    session: AsyncSession, source: EventGroup, target: EventGroup, *, now: datetime, actor: str
) -> None:
    members = (await session.scalars(select(Event).where(Event.group_id == source.id))).all()
    for member in members:
        member.group_id = target.id
    source.state = EventGroupState.MERGED
    source.merged_into_id = target.id
    source.version += 1
    target.last_change_at = now
    await audit.record(
        session,
        actor=actor,
        action="event.group.merge",
        entity_type="event_group",
        entity_id=source.id,
        details={"number": source.number, "into": target.number, "events": len(members)},
    )


async def _flapping(
    session: AsyncSession, event: Event, *, settings: Settings, now: datetime
) -> bool:
    since = now - timedelta(minutes=settings.events_flap_window_minutes)
    count = await session.scalar(
        select(func.count())
        .select_from(Event)
        .where(
            Event.source == event.source,
            Event.fingerprint == event.fingerprint,
            Event.starts_at >= since,
        )
    )
    return int(count or 0) >= settings.events_flap_threshold


async def refresh_group(session: AsyncSession, group: EventGroup, *, now: datetime) -> None:
    """Grup özetini üyelerden yeniden hesaplar (durum, önem, sayılar, çırpınma)."""
    members = list((await session.scalars(select(Event).where(Event.group_id == group.id))).all())
    if group.state == EventGroupState.MERGED:
        return
    active = [m for m in members if not m.suppressed]
    firing = [m for m in members if EventStatus(m.status) == EventStatus.FIRING]
    group.event_count = len(members)
    if active:
        group.severity = max(
            (EventSeverity(m.severity) for m in active), key=SEVERITY_RANK.__getitem__
        )
    if members:
        group.first_event_at = min(ensure_utc(m.starts_at) for m in members)
        group.last_event_at = max(
            max(ensure_utc(m.starts_at), ensure_utc(m.ends_at or m.starts_at)) for m in members
        )
    group.flapping = any(m.suppressed == "flapping" for m in firing)
    if firing:
        group.state = EventGroupState.OPEN
        group.resolved_at = None
    elif members and group.state == EventGroupState.OPEN:
        group.state = EventGroupState.RESOLVED
        group.resolved_at = now
        group.last_change_at = now
    group.updated_at = now


async def ingest(
    session: AsyncSession,
    alerts: Iterable[IncomingAlert],
    *,
    settings: Settings,
    now: datetime | None = None,
    actor: str = SYSTEM_ACTOR,
    delivery: str | None = None,
) -> IngestResult:
    """Uyarıları kaydeder/günceller, CI eşler, gruplar; dokunulan grup kimliklerini döndürür."""
    now = now or datetime.now(UTC)
    result = IngestResult()
    resolver = CIResolver(session, settings.events_ci_mapping)
    touched: dict[str, EventGroup] = {}
    for alert in alerts:
        result.received += 1
        existing = await session.scalar(
            select(Event).where(
                Event.source == alert.source,
                Event.fingerprint == alert.fingerprint,
                Event.starts_at == alert.starts_at,
            )
        )
        if existing is not None:
            changed = await _update_existing(session, existing, alert, now)
            if changed is None:
                result.ignored += 1
                continue
            result.updated += 1
            result.event_ids.append(existing.id)
            if existing.group_id and (group := await session.get(EventGroup, existing.group_id)):
                if changed and not existing.suppressed:
                    group.last_change_at = now
                touched[group.id] = group
            continue

        ci_id, rule = await resolver.resolve(alert.labels)
        event = Event(
            source=alert.source,
            fingerprint=alert.fingerprint,
            status=alert.status,
            severity=alert.severity,
            title=alert.title,
            labels=alert.labels,
            annotations=alert.annotations,
            starts_at=alert.starts_at,
            ends_at=alert.ends_at if alert.status == EventStatus.RESOLVED else None,
            ci_id=ci_id,
            ci_rule=rule,
            received_count=1,
            last_received_at=now,
            changed_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(event)
        await session.flush()
        result.created += 1
        result.event_ids.append(event.id)
        if alert.status == EventStatus.RESOLVED:
            # Ateşlenmesi hiç görülmemiş çözülme: geçmiş için saklanır, gruplanmaz
            continue
        if await _flapping(session, event, settings=settings, now=now):
            event.suppressed = "flapping"
        group, merged = await _assign_group(session, event, settings=settings, now=now, actor=actor)
        if is_silenced(group, now) and not event.suppressed:
            event.suppressed = "silenced"
        event.group_id = group.id
        if not event.suppressed:
            group.last_change_at = now
        touched[group.id] = group
        for m in merged:
            touched.pop(m.id, None)
        await session.flush()

    for group in touched.values():
        await refresh_group(session, group, now=now)
    await session.flush()
    result.group_ids = [g.id for g in touched.values() if g.state != EventGroupState.MERGED]
    await audit.record(
        session,
        actor=actor,
        action="event.ingest",
        entity_type="event_delivery",
        entity_id=(delivery or "-")[:100],
        details={
            "received": result.received,
            "created": result.created,
            "updated": result.updated,
            "ignored": result.ignored,
            "groups": sorted(g.number for g in touched.values()),
        },
    )
    return result


async def _update_existing(
    session: AsyncSession, event: Event, alert: IncomingAlert, now: datetime
) -> bool | None:
    """Tekrar gönderim: True anlamlı değişiklik, False yalnızca tekrar, None yok sayıldı."""
    current = EventStatus(event.status)
    if current == EventStatus.RESOLVED and alert.status == EventStatus.FIRING:
        return None  # çözülen uyarı eski/yeniden oynatılan "firing" ile yeniden açılmaz
    event.received_count += 1
    event.last_received_at = now
    event.updated_at = now
    changed = False
    if current == EventStatus.FIRING and alert.status == EventStatus.RESOLVED:
        event.status = EventStatus.RESOLVED
        event.ends_at = alert.ends_at or now
        changed = True
    elif current == EventStatus.FIRING and alert.severity != EventSeverity(event.severity):
        event.severity = alert.severity
        event.title = alert.title
        event.annotations = alert.annotations
        changed = True
    if changed:
        event.changed_at = now
    await session.flush()
    return changed


# --- 7) olay planı, olay açma/güncelleme ---------------------------------------------------------


@dataclass
class IncidentPlan:
    impact: Impact
    urgency: Urgency
    priority: Priority
    ci_ids: list[str]
    title: str
    description: str
    assignment_group: str | None
    highest_criticality: int | None
    truncated: bool


_URGENCY_BY_SEVERITY: Mapping[EventSeverity, Urgency] = {
    EventSeverity.CRITICAL: Urgency.HIGH,
    EventSeverity.HIGH: Urgency.MEDIUM,
    EventSeverity.WARNING: Urgency.LOW,
    EventSeverity.INFO: Urgency.LOW,
}


def impact_from_criticality(criticality: int | None, truncated: bool = False) -> Impact:
    if truncated or criticality == 1:
        return Impact.HIGH
    if criticality == 2:
        return Impact.MEDIUM
    return Impact.LOW


async def group_events(session: AsyncSession, group_id: str) -> list[Event]:
    return list(
        (
            await session.scalars(
                select(Event)
                .where(Event.group_id == group_id)
                .order_by(Event.starts_at, Event.created_at, Event.id)
            )
        ).all()
    )


async def incident_plan(session: AsyncSession, group: EventGroup) -> IncidentPlan:
    """Grubun açacağı olayın etki/aciliyet/önceliği ve içeriği (yazmaz)."""
    members = await group_events(session, group.id)
    active = [m for m in members if not m.suppressed] or members
    ci_ids = list(dict.fromkeys(c for c in [group.root_ci_id, *(m.ci_id for m in active)] if c))[
        :100
    ]
    criticality: int | None = None
    truncated = False
    root: ConfigurationItem | None = None
    if ci_ids:
        analysis = await cmdb.impact_analysis(session, ci_ids, max_depth=CORRELATION_DEPTH)
        criticality, truncated = analysis.highest_criticality, analysis.truncated
        root = next((r for r in analysis.roots if r.id == group.root_ci_id), None)
    severity = EventSeverity(group.severity)
    impact = impact_from_criticality(criticality, truncated)
    urgency = _URGENCY_BY_SEVERITY[severity]
    lines = [f"İzleme uyarı grubu {group.number} (önem: {severity.value})."]
    if root is not None:
        lines.append(f"Kök CI: {root.name} (kritiklik {root.criticality}).")
    for m in active[:MAX_DESCRIPTION_EVENTS]:
        lines.append(
            f"- [{EventSeverity(m.severity).value}] {m.title} ({m.source}, "
            f"başlangıç {ensure_utc(m.starts_at).isoformat()})"
        )
    if len(active) > MAX_DESCRIPTION_EVENTS:
        lines.append(f"... ve {len(active) - MAX_DESCRIPTION_EVENTS} uyarı daha.")
    owner = root.owner_group if root is not None else None
    if owner is not None and owner not in taxonomy.allowed("assignment_group"):
        owner = None
    prefix = f"{root.name}: " if root is not None else ""
    return IncidentPlan(
        impact=impact,
        urgency=urgency,
        priority=calculate_priority(impact, urgency),
        ci_ids=ci_ids,
        title=mask_pii(f"[İzleme] {prefix}{group.title}")[:300],
        description=mask_pii("\n".join(lines)),
        assignment_group=owner,
        highest_criticality=criticality,
        truncated=truncated,
    )


def is_actionable(group: EventGroup, settings: Settings, now: datetime) -> bool:
    """Grup olay açmalı mı: açık, susturulmamış, eşik üstü önemde bastırılmamış ateşleyen uyarı."""
    if EventGroupState(group.state) != EventGroupState.OPEN or is_silenced(group, now):
        return False
    threshold = SEVERITY_RANK[EventSeverity(settings.events_incident_min_severity)]
    return SEVERITY_RANK[EventSeverity(group.severity)] >= threshold


async def has_active_firing(session: AsyncSession, group_id: str) -> bool:
    return (
        await session.scalar(
            select(Event.id)
            .where(
                Event.group_id == group_id,
                Event.status == EventStatus.FIRING,
                Event.suppressed.is_(None),
            )
            .limit(1)
        )
    ) is not None


def needs_note(group: EventGroup) -> bool:
    noted = _utc(group.noted_at)
    return group.incident_id is not None and (
        noted is None or ensure_utc(group.last_change_at) > noted
    )


def _note_text(group: EventGroup, changes: Sequence[Event]) -> str:
    lines = [f"İzleme ({group.number}): {len(changes)} uyarı değişikliği."]
    for m in changes[:MAX_NOTE_LINES]:
        state = "ÇÖZÜLDÜ" if EventStatus(m.status) == EventStatus.RESOLVED else "ATEŞLENİYOR"
        lines.append(f"- {state} [{EventSeverity(m.severity).value}] {m.title}")
    if len(changes) > MAX_NOTE_LINES:
        lines.append(f"... ve {len(changes) - MAX_NOTE_LINES} değişiklik daha.")
    if EventGroupState(group.state) == EventGroupState.RESOLVED:
        lines.append("Gruptaki tüm uyarılar çözüldü; olay çözümü insan onayıyla yapılır.")
    return mask_pii("\n".join(lines))


async def open_or_update_incident(
    session: AsyncSession, group: EventGroup, actor: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Grup başına tek olay: yoksa açar, varsa çalışma notu + yeni CI bağları ekler (çözmez)."""
    now = now or datetime.now(UTC)
    if EventGroupState(group.state) == EventGroupState.MERGED:
        raise EventPayloadError(
            f"{group.number} başka bir gruba birleştirildi ({group.merged_into_id})."
        )
    if group.incident_id is None:
        plan = await incident_plan(session, group)
        incident = await incident_service.create_incident(
            session,
            IncidentCreate(
                title=plan.title,
                description=plan.description,
                caller=INCIDENT_CALLER,
                impact=plan.impact,
                urgency=plan.urgency,
                kind=TicketKind.INCIDENT,
            ),
            actor,
        )
        if plan.assignment_group:
            incident = await incident_service.apply_changes(
                session,
                incident.id,
                {"assignment_group": plan.assignment_group},
                actor,
                audit_details={"source": "event"},
            )
        if plan.ci_ids:
            incident = await incident_service.set_affected_cis(
                session, incident.id, plan.ci_ids, actor
            )
        group.incident_id = incident.id
        group.noted_at = now
        group.version += 1
        await session.flush()
        await audit.record(
            session,
            actor=actor,
            action="event.group.incident",
            entity_type="event_group",
            entity_id=group.id,
            details={
                "number": group.number,
                "incident_id": incident.id,
                "incident_number": incident.number,
                "priority": Priority(incident.priority).value,
            },
        )
        return {
            "mode": "created",
            "group_id": group.id,
            "incident_id": incident.id,
            "incident_number": incident.number,
            "priority": Priority(incident.priority).value,
        }

    incident = await session.get(Incident, group.incident_id)
    if incident is None:  # pragma: no cover - FK
        raise EventGroupNotFoundError(group.id)
    noted = _utc(group.noted_at)
    members = await group_events(session, group.id)
    changes = [
        m for m in members
        if not m.suppressed and (noted is None or ensure_utc(m.changed_at) > noted)
    ]  # fmt: skip
    note = _note_text(group, changes) if changes or group.state == EventGroupState.RESOLVED else ""
    linked: list[str] = []
    # Kapalı/iptal olayın (geçişi olmayan durum) CI bağı değiştirilemez; not yine yazılır
    if incident_service.ALLOWED_TRANSITIONS[IncidentStatus(incident.status)]:
        current = {c.id for c in await incident_service.list_affected_cis(session, incident.id)}
        wanted = [c for c in dict.fromkeys(m.ci_id for m in changes if m.ci_id) if c not in current]
        if wanted:
            linked = wanted
            await incident_service.set_affected_cis(
                session, incident.id, sorted(current | set(wanted)), actor
            )
    if note:
        await audit.record(
            session,
            actor=actor,
            action="incident.work_note",
            entity_type="incident",
            entity_id=incident.id,
            details={"note": note, "source": "event", "group_id": group.id},
        )
    group.noted_at = now
    group.version += 1
    await session.flush()
    return {
        "mode": "updated",
        "group_id": group.id,
        "incident_id": incident.id,
        "incident_number": incident.number,
        "note": note,
        "linked_ci_ids": linked,
    }


# --- onay/susturma (araç işleyicileri) -------------------------------------------------------------


async def get_group(session: AsyncSession, group_id: str) -> EventGroup:
    group = await session.get(EventGroup, group_id)
    if group is None:
        raise EventGroupNotFoundError(group_id)
    return group


async def acknowledge(
    session: AsyncSession, group: EventGroup, actor: str, *, note: str = "", now: datetime
) -> EventGroup:
    if group.acknowledged_at is None:
        group.acknowledged_by = actor
        group.acknowledged_at = now
        group.version += 1
        await session.flush()
        await audit.record(
            session,
            actor=actor,
            action="event.group.acknowledge",
            entity_type="event_group",
            entity_id=group.id,
            details={"number": group.number, "note": mask_pii(note)[:500]},
        )
    return group


async def silence(
    session: AsyncSession,
    group: EventGroup,
    actor: str,
    *,
    minutes: int,
    comment: str,
    now: datetime,
) -> EventGroup:
    if EventGroupState(group.state) == EventGroupState.MERGED:
        raise EventPayloadError(f"{group.number} birleştirilmiş; ana grubu susturun.")
    group.silenced_until = now + timedelta(minutes=minutes)
    group.silenced_by = actor
    group.version += 1
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="event.group.silence",
        entity_type="event_group",
        entity_id=group.id,
        details={
            "number": group.number,
            "until": group.silenced_until.isoformat(),
            "comment": mask_pii(comment)[:500],
        },
    )
    return group


# --- okuma (API ve R0 araçları) -----------------------------------------------------------------


async def list_events(
    session: AsyncSession,
    *,
    status: EventStatus | str | None = None,
    severity: EventSeverity | str | None = None,
    group_id: str | None = None,
    ci_id: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Event]:
    stmt = select(Event)
    if status is not None:
        stmt = stmt.where(Event.status == EventStatus(status))
    if severity is not None:
        stmt = stmt.where(Event.severity == EventSeverity(severity))
    if group_id is not None:
        stmt = stmt.where(Event.group_id == group_id)
    if ci_id is not None:
        stmt = stmt.where(Event.ci_id == ci_id)
    if source is not None:
        stmt = stmt.where(Event.source == source)
    stmt = stmt.order_by(Event.starts_at.desc(), Event.id).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def list_groups(
    session: AsyncSession,
    *,
    state: EventGroupState | str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[EventGroup]:
    stmt = select(EventGroup)
    if state is not None:
        stmt = stmt.where(EventGroup.state == EventGroupState(state))
    stmt = stmt.order_by(EventGroup.last_event_at.desc(), EventGroup.number.desc())
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


async def incidents_opened_since(session: AsyncSession, since: datetime) -> int:
    """`since`'ten beri izleme gruplarından açılan olay sayısı (olay fırtınası sınırı için)."""
    count = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.action == "event.group.incident", AuditLog.occurred_at >= since)
    )
    return int(count or 0)


async def pending_create_request(session: AsyncSession, group_id: str) -> str | None:
    rows = (
        await session.scalars(
            select(ActionRequest).where(
                ActionRequest.tool == "event.create_incident", ActionRequest.state == "pending"
            )
        )
    ).all()
    return next((r.id for r in rows if (r.args or {}).get("group_id") == group_id), None)


async def correlation(
    session: AsyncSession,
    group: EventGroup,
    *,
    settings: Settings,
    now: datetime,
) -> CorrelationRead:
    """Grubun bağıntı açıklaması: kök CI'dan her uyarının CI'ına bağımlılık yolu (yazmaz)."""
    members = await group_events(session, group.id)
    names: dict[str, str] = {}
    paths: dict[str, list[str]] = {}
    if group.root_ci_id:
        analysis = await cmdb.impact_analysis(
            session, [group.root_ci_id], max_depth=CORRELATION_DEPTH
        )
        names.update({r.id: r.name for r in analysis.roots})
        for a in analysis.affected:
            names[a.ci.id] = a.ci.name
            paths[a.ci.id] = a.path
        paths[group.root_ci_id] = [group.root_ci_id]
    missing = {m.ci_id for m in members if m.ci_id and m.ci_id not in names}
    if missing:
        names.update({c.id: c.name for c in await cmdb.get_cis(session, sorted(missing))})
    out_members = [
        CorrelatedMember(
            event_id=m.id,
            title=m.title,
            status=EventStatus(m.status),
            severity=EventSeverity(m.severity),
            suppressed=m.suppressed,
            ci_id=m.ci_id,
            ci_name=names.get(m.ci_id or ""),
            path=[names.get(c, c) for c in paths.get(m.ci_id or "", [])],
        )
        for m in members
    ]
    actionable = (
        group.incident_id is None
        and is_actionable(group, settings, now)
        and await has_active_firing(session, group.id)
    )
    proposed = None
    if group.incident_id is None and EventGroupState(group.state) != EventGroupState.MERGED:
        proposed = Priority((await incident_plan(session, group)).priority).value
    return CorrelationRead(
        group=EventGroupRead.model_validate(group),
        root_ci_name=names.get(group.root_ci_id or ""),
        members=out_members,
        actionable=actionable,
        proposed_priority=proposed,
        pending_action_request_id=await pending_create_request(session, group.id),
    )
