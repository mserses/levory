"""Jira Service Management Cloud bağlayıcısı.

- Oluşturma: /rest/servicedeskapi/request
- Okuma/güncelleme: /rest/api/3/issue/{key}
- Durum değişimi: /rest/api/3/issue/{key}/transitions (hedef durum adına göre geçiş id'si)
- Artımlı çekme: /rest/api/3/search/jql ("updated >= ...")
"""

from datetime import UTC, datetime
from typing import Any

import httpx

from serviszeka.domain.enums import ExternalSystem, IncidentStatus, Priority
from serviszeka.integrations.base import (
    JIRA_PRIORITY_TO_LOCAL,
    JIRA_STATUS_CATEGORY_TO_LOCAL,
    JIRA_STATUS_TO_LOCAL,
    LOCAL_TO_JIRA_PRIORITY,
    LOCAL_TO_JIRA_STATUS,
    ConnectorError,
    RemoteTicket,
)

ISSUE_FIELDS = ["summary", "description", "status", "priority", "assignee", "updated"]


# --- Atlassian Document Format (ADF) <-> düz metin ----------------------------


def text_to_adf(text: str | None) -> dict[str, Any]:
    """Düz metni ADF belgesine çevirir: her satır bir paragraf."""
    content: list[dict[str, Any]] = []
    for line in (text or "").split("\n"):
        para: dict[str, Any] = {"type": "paragraph", "content": []}
        if line:
            para["content"] = [{"type": "text", "text": line}]
        content.append(para)
    return {"type": "doc", "version": 1, "content": content}


_BLOCK_TYPES = {
    "paragraph",
    "heading",
    "codeBlock",
    "blockquote",
    "listItem",
    "rule",
    "panel",
    "tableRow",
}


def adf_to_text(node: Any) -> str:
    """ADF belgesini düz metne çevirir (blok düğümler satır olur)."""
    if node is None:
        return ""
    if isinstance(node, str):  # bazı uç noktalar düz metin döndürür
        return node
    lines: list[str] = []

    def inline(n: dict[str, Any]) -> str:
        t = n.get("type")
        if t == "text":
            return n.get("text", "")
        if t == "hardBreak":
            return "\n"
        if t in ("mention", "emoji"):
            return n.get("attrs", {}).get("text", "")
        if t == "inlineCard":
            return n.get("attrs", {}).get("url", "")
        return "".join(inline(c) for c in n.get("content", []))

    def walk(n: dict[str, Any]) -> None:
        t = n.get("type")
        if t in _BLOCK_TYPES:
            children = n.get("content", [])
            if any(c.get("type") in _BLOCK_TYPES or c.get("type", "").endswith("List")
                   for c in children):
                for c in children:
                    walk(c)
            else:
                lines.append("".join(inline(c) for c in children))
        elif t in ("text", "hardBreak", "mention", "emoji", "inlineCard"):
            lines.append(inline(n))
        else:  # doc, bulletList, orderedList, table ...
            for c in n.get("content", []):
                walk(c)

    walk(node)
    return "\n".join(lines)


# --- Eşlemeler ----------------------------------------------------------------


def map_status(status: dict[str, Any] | None) -> IncidentStatus | None:
    if not status:
        return None
    name = (status.get("name") or "").strip().lower()
    if name in JIRA_STATUS_TO_LOCAL:
        return JIRA_STATUS_TO_LOCAL[name]
    cat = (status.get("statusCategory") or {}).get("key")
    return JIRA_STATUS_CATEGORY_TO_LOCAL.get(cat) if cat else None


def map_priority(priority: dict[str, Any] | None) -> Priority | None:
    if not priority:
        return None
    return JIRA_PRIORITY_TO_LOCAL.get((priority.get("name") or "").strip().lower())


def issue_to_ticket(issue: dict[str, Any]) -> RemoteTicket:
    f = issue.get("fields") or {}
    assignee = f.get("assignee") or {}
    return RemoteTicket(
        external_id=issue["key"],
        number=issue["key"],
        title=f.get("summary") or "",
        description=adf_to_text(f.get("description")),
        status=map_status(f.get("status")),
        priority=map_priority(f.get("priority")),
        impact=None,  # Jira'da etki/aciliyet özel alanlardır; eşlenmez
        urgency=None,
        assignee=assignee.get("displayName") or assignee.get("emailAddress"),
        updated_at=f.get("updated"),
        raw=issue,
    )


def to_jql_datetime(ts: str) -> str:
    """Jira zaman damgasını (örn. 2024-05-01T10:15:30.000+0000) JQL biçimine çevirir.

    JQL dakika hassasiyetlidir ve kullanıcının saat dilimini kullanır; bu yüzden UTC'ye
    çevrilir ve >= ile sorgulanır, tekrarlar senkron motorunda elenir.
    """
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            return ts  # zaten JQL biçiminde varsayılır
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return dt.strftime("%Y/%m/%d %H:%M")


class JiraSMConnector:
    system = ExternalSystem.JIRA_SM

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        service_desk_id: str | None = None,
        request_type_id: str | None = None,
        project_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        page_size: int = 50,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_desk_id = service_desk_id
        self.request_type_id = request_type_id
        self.project_key = project_key
        self.page_size = page_size
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.base_url,
            auth=httpx.BasicAuth(email, api_token),
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
            raise ConnectorError(f"Jira'ya ulaşılamadı: {exc}") from exc
        if resp.status_code >= 400:
            raise ConnectorError(
                f"Jira {method} {path} başarısız: {resp.status_code} {resp.text[:300]}"
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # --- yardımcılar ---

    async def _ensure_request_type(self) -> str:
        if self.request_type_id:
            return self.request_type_id
        if not self.service_desk_id:
            raise ConnectorError("Jira hizmet masası (service desk) kimliği yapılandırılmamış")
        data = await self._request(
            "GET", f"/rest/servicedeskapi/servicedesk/{self.service_desk_id}/requesttype"
        )
        values = (data or {}).get("values") or []
        if not values:
            raise ConnectorError("Jira hizmet masasında talep türü bulunamadı")
        self.request_type_id = str(values[0]["id"])
        return self.request_type_id

    async def _ensure_project_key(self) -> str | None:
        if self.project_key or not self.service_desk_id:
            return self.project_key
        data = await self._request(
            "GET", f"/rest/servicedeskapi/servicedesk/{self.service_desk_id}"
        )
        self.project_key = (data or {}).get("projectKey")
        return self.project_key

    async def _transition_to(self, key: str, status: IncidentStatus) -> None:
        targets = [n.lower() for n in LOCAL_TO_JIRA_STATUS[status]]
        data = await self._request("GET", f"/rest/api/3/issue/{key}/transitions")
        transitions = (data or {}).get("transitions") or []
        chosen = None
        for target in targets:  # tercih sırasına göre ara
            for t in transitions:
                to_name = ((t.get("to") or {}).get("name") or "").lower()
                if to_name == target or (t.get("name") or "").lower() == target:
                    chosen = t
                    break
            if chosen:
                break
        if chosen is None:
            raise ConnectorError(
                f"Jira {key}: '{LOCAL_TO_JIRA_STATUS[status][0]}' durumuna geçiş bulunamadı"
            )
        await self._request(
            "POST",
            f"/rest/api/3/issue/{key}/transitions",
            json={"transition": {"id": str(chosen["id"])}},
        )

    # --- protokol ---

    async def get(self, external_id: str) -> RemoteTicket:
        data = await self._request(
            "GET", f"/rest/api/3/issue/{external_id}", params={"fields": ",".join(ISSUE_FIELDS)}
        )
        return issue_to_ticket(data)

    async def list_updated_since(self, since: str | None) -> list[RemoteTicket]:
        # /search/jql sınırsız sorgu kabul etmez; ilk çekimde de alt sınır verilir.
        since_jql = to_jql_datetime(since) if since else "1970/01/01 00:00"
        jql = f'updated >= "{since_jql}"'
        project = await self._ensure_project_key()
        if project:
            jql = f'project = "{project}" AND {jql}'
        jql += " ORDER BY updated ASC"
        tickets: list[RemoteTicket] = []
        token: str | None = None
        while True:
            body: dict[str, Any] = {
                "jql": jql,
                "fields": ISSUE_FIELDS,
                "maxResults": self.page_size,
            }
            if token:
                body["nextPageToken"] = token
            data = await self._request("POST", "/rest/api/3/search/jql", json=body) or {}
            tickets.extend(issue_to_ticket(i) for i in data.get("issues", []))
            token = data.get("nextPageToken")
            if data.get("isLast", True) or not token:
                break
        return tickets

    async def create(self, fields: dict[str, Any]) -> RemoteTicket:
        if not self.service_desk_id:
            raise ConnectorError("Jira hizmet masası (service desk) kimliği yapılandırılmamış")
        request_type = await self._ensure_request_type()
        body = {
            "serviceDeskId": str(self.service_desk_id),
            "requestTypeId": request_type,
            "requestFieldValues": {
                "summary": fields.get("title") or "",
                # servicedeskapi açıklamayı düz metin olarak kabul eder
                "description": fields.get("description") or "",
            },
        }
        data = await self._request("POST", "/rest/servicedeskapi/request", json=body)
        key = data["issueKey"]
        # Öncelik ve başlangıç dışı durum talep formunda olmayabilir: issue API ile ayarlanır.
        rest = {k: fields[k] for k in ("priority", "status") if fields.get(k) is not None}
        if rest.get("status") == IncidentStatus.NEW:
            rest.pop("status")
        if rest:
            return await self.update(key, rest)
        return await self.get(key)

    async def update(self, external_id: str, fields: dict[str, Any]) -> RemoteTicket:
        jira_fields: dict[str, Any] = {}
        if "title" in fields:
            jira_fields["summary"] = fields["title"]
        if "description" in fields:
            jira_fields["description"] = text_to_adf(fields["description"])
        if fields.get("priority") is not None:
            jira_fields["priority"] = {"name": LOCAL_TO_JIRA_PRIORITY[Priority(fields["priority"])]}
        if jira_fields:
            await self._request(
                "PUT", f"/rest/api/3/issue/{external_id}", json={"fields": jira_fields}
            )
        if fields.get("status") is not None:
            target = IncidentStatus(fields["status"])
            current = await self.get(external_id)
            same = current.status == target or (
                target == IncidentStatus.ASSIGNED and current.status == IncidentStatus.IN_PROGRESS
            )
            if not same:
                await self._transition_to(external_id, target)
        return await self.get(external_id)
