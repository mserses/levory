"""Kapı değerlendirmesi için sağlayıcı biçimli sahte yığınlar (ağ yok).

Faz 3 kapısı ("standart taleplerin %50'si insansız") bellek içi sahte dizinle ölçülür. Sahte dizin
her işlemi destekler; gerçek sağlayıcılar desteklemez. Kapının hedef yığına göre raporlanabilmesi için
aynı dizin verisi, gerçek bağlayıcının **belgelenmiş** semantiğiyle sarılır:

- `memory`: `InMemoryDirectory` olduğu gibi (bugünkü kapı; değişmez).
- `entra`: Entra ID + Microsoft 365 (Graph) + Teams. `connectors.graph` ile aynı: bulutta hesap
  kilidi açma (`unlock_account`) ve paylaşılan posta kutusu izni (`grant_shared_access`)
  `ConnectorNotSupportedError` (dry_run dahil). Diğer her şey sahte dizinle aynı.
- `keycloak`: Keycloak (kimlik) + Slack (bildirim). `connectors.keycloak` ile aynı: kilit açma
  desteklenir (brute-force kaydı silinir), kullanıcıların ikincil e-postası (`other_mails`) yoktur,
  posta/lisans sağlayıcısı yoktur (bu araçlar 503 `unavailable`). Bildirim **gerçek**
  `SlackNotifier`'dır; Slack Web API'si `FakeSlackWorkspace` ile `httpx.MockTransport` üzerinden
  taklit edilir. Hassas bildirim (geçici parola) Slack'e gitmez ve devredilecek ikincil adres
  gönderici olmadığından `ConnectorError` olur — gerçek Keycloak + Slack kurulumundaki davranış.

Bilinçli sadeleştirmeler (gerçek Keycloak'tan farkları; docs/faz3.md "doğrulanmalı" K3/K10):
grup yalnızca kimliğiyle bulunur (gerçek bağlayıcı tam yol/ad da kabul eder), grup ayrıcalığı
`privileged_groups` kümesinden gelir (gerçekte ayrıca `realm-management` rol eşlemesi), kalıcı
kilitlemenin `enabled=false` yapması (K8) modellenmez.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

import httpx

from serviszeka.connectors.base import (
    IDENTITY,
    KNOWN_KINDS,
    LICENSE,
    MAIL,
    MESSAGING,
    ConnectorNotSupportedError,
    DirectoryUser,
)
from serviszeka.connectors.memory import InMemoryDirectory
from serviszeka.connectors.slack import SlackClient, SlackNotifier

STACKS: tuple[str, ...] = ("memory", "entra", "keycloak")

# Sahte Slack çalışma alanında `default` kanalı
FAKE_SLACK_DEFAULT_CHANNEL = "C0SERVISMASASI"


def _clone(directory: InMemoryDirectory, cls: type[InMemoryDirectory], name: str):
    """Dizinin bağımsız kopyası, verilen alt sınıf olarak (kaynak dizin değişmez)."""
    copied = directory.copy()
    values = {f.name: getattr(copied, f.name) for f in dataclasses.fields(InMemoryDirectory)}
    values["name"] = name
    return cls(**values)


class EntraShapedDirectory(InMemoryDirectory):
    """Sahte dizin, Graph'ın desteklemediği işlemleri reddeder (`connectors.graph` ile aynı)."""

    async def unlock_account(self, user, *, dry_run=False, idempotency_key=None):
        raise ConnectorNotSupportedError(
            "Entra ID bulutta hesap kilidi açma API'si yok (Akıllı Kilitleme süre dolunca "
            "kendiliğinden açılır). Şirket içi AD kilidi LDAP ile açılır ve kapsam dışıdır."
        )

    async def grant_shared_access(
        self, mailbox, user, access, *, dry_run=False, idempotency_key=None
    ):
        raise ConnectorNotSupportedError(
            "Graph v1.0'da paylaşılan posta kutusu izni (FullAccess/SendAs) API'si yok; Exchange "
            "Online PowerShell (Add-MailboxPermission / Add-RecipientPermission) gerekir."
        )


class KeycloakShapedDirectory(InMemoryDirectory):
    """Keycloak kimlik semantiği: ikincil e-posta yok, kilit açma desteklenir.

    Yalnızca `IdentityProvider` olarak kaydedilir (posta/lisans/bildirim Keycloak'ta yok).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for user in self.users.values():
            user.other_mails = []

    def add_user(self, username: str, display_name: str = "", **kw: Any) -> DirectoryUser:
        # Keycloak kullanıcı gösteriminde ikincil e-posta alanı yoktur
        kw["other_mails"] = ()
        return super().add_user(username, display_name, **kw)


class FakeSlackWorkspace:
    """Slack Web API'sinin taklidi (`httpx.MockTransport` yanıtlayıcısı: `respond`).

    Uygulanan yöntemler (Slack belgelerindeki gövde biçimiyle; `connectors.slack` testleri ile aynı):
    `users.lookupByEmail` (dizindeki etkin kullanıcının posta/UPN'i -> `U…`), `conversations.open`
    (`D…`), `chat.postMessage` (yalnızca bilinen kanal ya da açılmış DM). Diğerleri
    `unknown_method`. Gönderilen mesajlar `posts` listesindedir.
    """

    def __init__(self, directory: InMemoryDirectory | None = None) -> None:
        self.directory = directory
        self.channels: set[str] = {FAKE_SLACK_DEFAULT_CHANNEL}
        self.open_dms: set[str] = set()
        self.posts: list[dict[str, Any]] = []

    @staticmethod
    def slack_id(email: str) -> str:
        return "U" + hashlib.sha256(email.casefold().encode()).hexdigest()[:10].upper()

    def _lookup(self, email: str) -> str | None:
        if self.directory is None:
            return None
        key = email.strip().casefold()
        for user in self.directory.users.values():
            if key in {(user.mail or "").casefold(), user.user_principal_name.casefold()}:
                return self.slack_id(key) if user.account_enabled else None
        return None

    def respond(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "users.lookupByEmail":
            uid = self._lookup(request.url.params.get("email", ""))
            if uid is None:
                return httpx.Response(200, json={"ok": False, "error": "users_not_found"})
            return httpx.Response(200, json={"ok": True, "user": {"id": uid, "deleted": False}})
        body = json.loads(request.content or b"{}")
        if method == "conversations.open":
            dm = "D" + str(body.get("users", ""))[1:]
            self.open_dms.add(dm)
            return httpx.Response(200, json={"ok": True, "channel": {"id": dm}})
        if method == "chat.postMessage":
            channel = body.get("channel")
            if channel not in self.channels and channel not in self.open_dms:
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
            self.posts.append(body)
            return httpx.Response(200, json={"ok": True, "ts": f"{len(self.posts)}.000100"})
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})


def build_stack(
    stack: str,
    directory: InMemoryDirectory,
    *,
    slack_workspace: FakeSlackWorkspace | None = None,
) -> dict[str, Any]:
    """`stack` biçimli bağlayıcı kümesi (tür -> uygulama). `memory` dizinin kendisini kullanır."""
    if stack == "memory":
        return {IDENTITY: directory, MAIL: directory, LICENSE: directory, MESSAGING: directory}
    if stack == "entra":
        entra = _clone(directory, EntraShapedDirectory, "entra-fake")
        return {IDENTITY: entra, MAIL: entra, LICENSE: entra, MESSAGING: entra}
    if stack == "keycloak":
        kc = _clone(directory, KeycloakShapedDirectory, "keycloak-fake")
        workspace = slack_workspace or FakeSlackWorkspace()
        workspace.directory = kc
        slack = SlackNotifier(
            # Sahte belirteç: istek yalnızca MockTransport'a gider
            SlackClient("xoxb-sahte", transport=httpx.MockTransport(workspace.respond)),
            channels={"default": FAKE_SLACK_DEFAULT_CHANNEL},
            default_channel=FAKE_SLACK_DEFAULT_CHANNEL,
            identity=kc,
            sensitive_delegate=None,
        )
        return {IDENTITY: kc, MESSAGING: slack}
    raise ValueError(f"Bilinmeyen yığın: {stack} (seçenekler: {', '.join(STACKS)})")


def install_stack(registry: Any, items: dict[str, Any]) -> list[str]:
    """Kayıt defterini yığına eşitler: yığında olmayan türler kayıttan çıkarılır."""
    for kind in KNOWN_KINDS - set(items):
        registry.unregister(kind)
    for kind, impl in items.items():
        registry.register(kind, impl)
    return sorted(items)
