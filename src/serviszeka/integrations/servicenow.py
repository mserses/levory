"""ServiceNow Table API bağlayıcısı (/api/now/table/incident)."""

from typing import Any

import httpx

from serviszeka.domain.enums import ExternalSystem, Impact, IncidentStatus, Urgency
from serviszeka.integrations.base import (
    LOCAL_TO_SERVICENOW_IMPACT,
    LOCAL_TO_SERVICENOW_STATE,
    LOCAL_TO_SERVICENOW_URGENCY,
    SERVICENOW_IMPACT_TO_LOCAL,
    SERVICENOW_PRIORITY_TO_LOCAL,
    SERVICENOW_STATE_TO_LOCAL,
    SERVICENOW_URGENCY_TO_LOCAL,
    ConnectorError,
    RemoteTicket,
)

TABLE_PATH = "/api/now/table/incident"
_READ_FIELDS = (
    "sys_id,number,short_description,description,state,impact,urgency,priority,"
    "assigned_to,close_notes,sys_updated_on"
)


def _value(field: Any) -> str | None:
    # sysparm_display_value=all: {"value": ..., "display_value": ...}
    if isinstance(field, dict):
        v = field.get("value")
    else:
        v = field
    if v in (None, ""):
        return None
    return str(v)


def _display(field: Any) -> str | None:
    if isinstance(field, dict):
        v = field.get("display_value") or field.get("value")
    else:
        v = field
    return str(v) if v not in (None, "") else None


def record_to_ticket(rec: dict[str, Any]) -> RemoteTicket:
    state = _value(rec.get("state"))
    impact = _value(rec.get("impact"))
    urgency = _value(rec.get("urgency"))
    priority = _value(rec.get("priority"))
    return RemoteTicket(
        external_id=_value(rec.get("sys_id")) or "",
        number=_value(rec.get("number")),
        title=_display(rec.get("short_description")) or "",
        description=_display(rec.get("description")) or "",
        status=SERVICENOW_STATE_TO_LOCAL.get(state) if state else None,
        priority=SERVICENOW_PRIORITY_TO_LOCAL.get(priority) if priority else None,
        impact=SERVICENOW_IMPACT_TO_LOCAL.get(impact) if impact else None,
        urgency=SERVICENOW_URGENCY_TO_LOCAL.get(urgency) if urgency else None,
        assignee=_display(rec.get("assigned_to")),
        # Değer (UTC, "YYYY-MM-DD HH:MM:SS") sysparm_query ile doğrudan karşılaştırılabilir.
        updated_at=_value(rec.get("sys_updated_on")),
        raw=rec,
    )


def fields_to_payload(fields: dict[str, Any]) -> dict[str, Any]:
    """Normalize alanlar -> ServiceNow incident gövdesi. Öncelik ServiceNow'da etki x aciliyetten
    hesaplandığı için gönderilmez."""
    payload: dict[str, Any] = {}
    if "title" in fields:
        payload["short_description"] = fields["title"]
    if "description" in fields:
        payload["description"] = fields["description"] or ""
    if fields.get("status") is not None:
        status = IncidentStatus(fields["status"])
        payload["state"] = LOCAL_TO_SERVICENOW_STATE[status]
        if status in (IncidentStatus.RESOLVED, IncidentStatus.CLOSED):
            # ServiceNow çözümde kapanış kodu/notu ister
            payload["close_code"] = fields.get("close_code") or "Solved (Permanently)"
            payload["close_notes"] = fields.get("resolution") or "ServisZeka üzerinden çözüldü"
    if fields.get("impact") is not None:
        payload["impact"] = LOCAL_TO_SERVICENOW_IMPACT[Impact(fields["impact"])]
    if fields.get("urgency") is not None:
        payload["urgency"] = LOCAL_TO_SERVICENOW_URGENCY[Urgency(fields["urgency"])]
    if fields.get("assignee"):
        payload["assigned_to"] = fields["assignee"]
    return payload


class ServiceNowConnector:
    system = ExternalSystem.SERVICENOW

    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        page_size: int = 100,
        timeout: float = 30.0,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.page_size = page_size
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.instance_url,
            auth=httpx.BasicAuth(username, password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            transport=transport,
            timeout=timeout,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"ServiceNow'a ulaşılamadı: {exc}") from exc
        if resp.status_code >= 400:
            raise ConnectorError(
                f"ServiceNow {method} {path} başarısız: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json()

    def _read_params(self) -> dict[str, str]:
        return {
            "sysparm_display_value": "all",
            "sysparm_exclude_reference_link": "true",
            "sysparm_fields": _READ_FIELDS,
        }

    async def get(self, external_id: str) -> RemoteTicket:
        data = await self._request(
            "GET", f"{TABLE_PATH}/{external_id}", params=self._read_params()
        )
        return record_to_ticket(data["result"])

    async def list_updated_since(self, since: str | None) -> list[RemoteTicket]:
        # >= kullanılır: aynı saniyede güncellenen kayıtlar kaçmasın; tekrarlar senkronda elenir.
        query = "ORDERBYsys_updated_on"
        if since:
            query = f"sys_updated_on>={since}^" + query
        tickets: list[RemoteTicket] = []
        offset = 0
        while True:
            params = self._read_params() | {
                "sysparm_query": query,
                "sysparm_limit": str(self.page_size),
                "sysparm_offset": str(offset),
            }
            data = await self._request("GET", TABLE_PATH, params=params)
            batch = data.get("result", [])
            tickets.extend(record_to_ticket(r) for r in batch)
            if len(batch) < self.page_size:
                break
            offset += self.page_size
        return tickets

    async def create(self, fields: dict[str, Any]) -> RemoteTicket:
        data = await self._request(
            "POST", TABLE_PATH, params=self._read_params(), json=fields_to_payload(fields)
        )
        return record_to_ticket(data["result"])

    async def update(self, external_id: str, fields: dict[str, Any]) -> RemoteTicket:
        data = await self._request(
            "PATCH",
            f"{TABLE_PATH}/{external_id}",
            params=self._read_params(),
            json=fields_to_payload(fields),
        )
        return record_to_ticket(data["result"])


__all__ = ["ServiceNowConnector", "fields_to_payload", "record_to_ticket"]
