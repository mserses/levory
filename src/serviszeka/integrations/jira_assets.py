"""Jira Service Management Assets (CMDB) — yalnızca arayüz taslağı.

Jira SM'de CMDB (Assets, eski adıyla Insight) Premium/Enterprise planlara ya da ücretli bir
eklentiye bağlıdır; erişimimiz ve gerçek bir şema örneğimiz olmadığı için sahte bir uygulama
yazılmadı. Bağlayıcı `CMDBSourceConnector` protokolünü uygulayacak şekilde iskelet olarak
durur; her çağrı NotImplementedError yükseltir. Uygulanacaksa: Assets REST API
(`/jsm/assets/workspace/{workspaceId}/v1/object/aql`) ile nesne türleri -> CIClass eşlemesi
ve nesne referansları -> CIRelationshipType eşlemesi; ardından `servicenow_cmdb.pull_cmdb`
aynı upsert akışıyla kullanılabilir (bkz. docs/faz2.md).
"""

from __future__ import annotations

from serviszeka.domain.enums import ExternalSystem
from serviszeka.integrations.cmdb_base import RemoteCI, RemoteCIRelationship

NOT_SUPPORTED_MESSAGE = (
    "Jira Assets (CMDB) entegrasyonu desteklenmiyor: ücretli eklenti; yalnızca arayüz taslağı var."
)


class JiraAssetsCMDBConnector:
    """Arayüz taslağı: CMDBSourceConnector imzaları, gerçek uygulama yok."""

    system = ExternalSystem.JIRA_SM

    async def list_cis_updated_since(self, since: str | None) -> list[RemoteCI]:
        raise NotImplementedError(NOT_SUPPORTED_MESSAGE)

    async def list_relationships_updated_since(
        self, since: str | None
    ) -> list[RemoteCIRelationship]:
        raise NotImplementedError(NOT_SUPPORTED_MESSAGE)


__all__ = ["NOT_SUPPORTED_MESSAGE", "JiraAssetsCMDBConnector"]
