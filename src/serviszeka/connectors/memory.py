"""Bellek içi sahte dizin: kimlik + posta + lisans + bildirim sağlayıcılarının tek sahtesi.

Kullanım yerleri: testler, yerel deneme (`SZ_CONNECTOR_PROVIDER=fake`, yalnızca development) ve
Faz 3 kapı değerlendirmesi (standart taleplerin insansız çözüm oranı). Ağ yoktur.

Her yazma `writes` listesine (işlem, argümanlar) olarak eklenir; gönderilen bildirimler
`messages` listesindedir. Kullanıcı; kimlik (id), UPN, posta ya da UPN'nin yerel kısmıyla
(`ayse@ornek.com.tr` -> `ayse`) bulunur: geliştirme belirteçlerindeki kısa adlar böylece eşleşir.
Grup yalnızca kimliğiyle (id) bulunur.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from serviszeka.connectors.base import (
    ConnectorError,
    ConnectorNotFoundError,
    DirectoryGroup,
    DirectoryUser,
    MailboxAccess,
    SkuInfo,
)

# Parola hiçbir zaman kaydedilmez/döndürülmez; yalnızca "atandı" bilgisi tutulur
_PASSWORD_SET = "<ayarlandı>"


@dataclass
class SentMessage:
    kind: str  # "user" | "channel" | "mail"
    target: str
    subject: str
    text: str
    sensitive: bool = False


@dataclass
class InMemoryDirectory:
    users: dict[str, DirectoryUser] = field(default_factory=dict)
    groups: dict[str, DirectoryGroup] = field(default_factory=dict)
    members: dict[str, set[str]] = field(default_factory=dict)  # grup id -> kullanıcı id'leri
    managers: dict[str, str] = field(default_factory=dict)  # kullanıcı id -> yönetici id
    skus: dict[str, SkuInfo] = field(default_factory=dict)  # sku id -> bilgi
    licenses: dict[str, set[str]] = field(default_factory=dict)  # kullanıcı id -> sku id'leri
    mailbox_access: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    mfa_methods: dict[str, list[str]] = field(default_factory=dict)
    locked: set[str] = field(default_factory=set)
    channels: set[str] = field(default_factory=set)
    privileged_groups: set[str] = field(default_factory=set)
    writes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    messages: list[SentMessage] = field(default_factory=list)
    password_resets: dict[str, str] = field(default_factory=dict)
    # Belirli işlemler için hata enjekte etmek (testler): işlem adı -> istisna
    failures: dict[str, Exception] = field(default_factory=dict)
    name: str = "memory"

    # --- kurulum -------------------------------------------------------------------------------

    def add_user(
        self,
        username: str,
        display_name: str = "",
        *,
        domain: str = "ornek.com.tr",
        manager: str | None = None,
        department: str | None = None,
        other_mails: Iterable[str] = (),
        enabled: bool = True,
        mfa: Iterable[str] = ("microsoftAuthenticator",),
    ) -> DirectoryUser:
        upn = username if "@" in username else f"{username}@{domain}"
        user = DirectoryUser(
            id=f"u-{upn.split('@')[0]}",
            user_principal_name=upn,
            display_name=display_name or username,
            mail=upn,
            account_enabled=enabled,
            department=department,
            other_mails=list(other_mails),
        )
        self.users[user.id] = user
        self.mfa_methods[user.id] = list(mfa)
        if manager:
            self.managers[user.id] = self._require_user(manager).id
        return user

    def add_group(self, group_id: str, display_name: str = "", *, privileged: bool = False):
        group = DirectoryGroup(id=group_id, display_name=display_name or group_id)
        self.groups[group_id] = group
        self.members.setdefault(group_id, set())
        if privileged:
            self.privileged_groups.add(group_id)
        return group

    def add_member(self, group_id: str, user: str) -> None:
        self.members.setdefault(group_id, set()).add(self._require_user(user).id)

    def add_sku(self, sku_id: str, part_number: str, enabled: int, consumed: int = 0) -> SkuInfo:
        sku = SkuInfo(sku_id=sku_id, sku_part_number=part_number, enabled=enabled, consumed=consumed)
        self.skus[sku_id] = sku
        return sku

    def add_mailbox(self, username: str, display_name: str = "") -> DirectoryUser:
        user = self.add_user(username, display_name, mfa=())
        self.mailbox_access[user.id] = {"full_access": set(), "send_as": set()}
        return user

    @classmethod
    def demo(cls) -> InMemoryDirectory:
        """Yerel deneme / E2E / kapı değerlendirmesi için Türkçe örnek dizin."""
        d = cls()
        d.add_user("zeynep", "Zeynep Kaya", department="BT", other_mails=["zeynep.kaya@kisisel.ornek"])
        for name, display in (
            ("ayse", "Ayşe Yılmaz"),
            ("ali", "Ali Demir"),
            ("mehmet", "Mehmet Şahin"),
            ("son.kullanici", "Son Kullanıcı"),
            ("yeni.calisan", "Yeni Çalışan"),
            ("sef.ali", "Ali Şef"),
            ("mudur", "Değişiklik Müdürü"),
        ):
            d.add_user(
                name,
                display,
                manager="zeynep",
                department="Finans" if name in ("ali", "son.kullanici") else "BT",
                other_mails=[f"{name}@kisisel.ornek"],
            )
        for gid, display in (
            ("sg-yazilim-standart", "Standart yazılım paketi"),
            ("sg-vpn-kullanicilari", "VPN kullanıcıları"),
            ("m365-ekip-proje-x", "Proje X Teams ekibi"),
            ("sg-finans-paylasim", "Finans dosya paylaşımı"),
            ("sg-ik-onaycilari", "İK onaycıları"),
        ):
            d.add_group(gid, display)
        d.add_group("sg-domain-admins", "Etki alanı yöneticileri", privileged=True)
        d.add_group("sg-sunucu-yoneticileri", "Sunucu yöneticileri", privileged=True)
        d.add_member("sg-ik-onaycilari", "zeynep")
        d.add_mailbox("muhasebe", "Muhasebe paylaşılan posta kutusu")
        d.add_mailbox("destek", "Destek paylaşılan posta kutusu")
        d.add_sku("sku-spe-e3", "SPE_E3", enabled=100, consumed=97)
        d.add_sku("sku-power-bi-pro", "POWER_BI_PRO", enabled=10, consumed=10)
        d.channels.update({"servis-masasi", "bt-duyurular"})
        return d

    def copy(self) -> InMemoryDirectory:
        """Bağımsız kopya (testler kendi dizinini değiştirebilsin)."""
        import copy as _copy

        return _copy.deepcopy(self)

    # --- iç yardımcılar ----------------------------------------------------------------------------

    def _find_user(self, user: str) -> DirectoryUser | None:
        key = (user or "").strip().casefold()
        if not key:
            return None
        for u in self.users.values():
            upn = u.user_principal_name.casefold()
            if key in (u.id.casefold(), upn, (u.mail or "").casefold(), upn.split("@")[0]):
                return u
        return None

    def _require_user(self, user: str) -> DirectoryUser:
        found = self._find_user(user)
        if found is None:
            raise ConnectorNotFoundError(f"Kullanıcı dizinde bulunamadı: {user}")
        return found

    def _require_group(self, group: str) -> DirectoryGroup:
        found = self.groups.get(group)
        if found is None:
            raise ConnectorNotFoundError(f"Grup dizinde bulunamadı: {group}")
        return found

    def _maybe_fail(self, op: str) -> None:
        exc = self.failures.get(op)
        if exc is not None:
            raise exc

    def _write(self, op: str, dry_run: bool, **details: Any) -> dict[str, Any]:
        self._maybe_fail(op)
        if not dry_run:
            self.writes.append((op, details))
        return {"dry_run": dry_run, **details}

    # --- IdentityProvider --------------------------------------------------------------------------

    async def get_user(self, user: str) -> DirectoryUser | None:
        found = self._find_user(user)
        return replace(found) if found else None

    async def get_manager(self, user: str) -> DirectoryUser | None:
        u = self._require_user(user)
        mid = self.managers.get(u.id)
        return replace(self.users[mid]) if mid else None

    async def list_groups(self, user: str) -> list[DirectoryGroup]:
        u = self._require_user(user)
        return [
            replace(self.groups[g], privileged=g in self.privileged_groups)
            for g, ids in sorted(self.members.items())
            if u.id in ids and g in self.groups
        ]

    async def get_group(self, group: str) -> DirectoryGroup | None:
        found = self.groups.get(group)
        if found is None:
            return None
        return replace(found, privileged=group in self.privileged_groups)

    async def is_privileged_group(self, group: str) -> bool:
        return group not in self.groups or group in self.privileged_groups

    async def add_to_group(self, user, group, *, dry_run=False, idempotency_key=None):
        u, g = self._require_user(user), self._require_group(group)
        already = u.id in self.members[g.id]
        result = self._write("add_to_group", dry_run, user_id=u.id, group_id=g.id)
        if not dry_run:
            self.members[g.id].add(u.id)
        return {**result, "already_member": already}

    async def remove_from_group(self, user, group, *, dry_run=False, idempotency_key=None):
        u, g = self._require_user(user), self._require_group(group)
        was = u.id in self.members[g.id]
        result = self._write("remove_from_group", dry_run, user_id=u.id, group_id=g.id)
        if not dry_run:
            self.members[g.id].discard(u.id)
        return {**result, "was_member": was}

    async def revoke_sessions(self, user, *, dry_run=False, idempotency_key=None):
        u = self._require_user(user)
        return self._write("revoke_sessions", dry_run, user_id=u.id)

    async def set_account_enabled(self, user, enabled, *, dry_run=False, idempotency_key=None):
        u = self._require_user(user)
        result = self._write("set_account_enabled", dry_run, user_id=u.id, enabled=enabled)
        if not dry_run:
            u.account_enabled = enabled
        return result

    async def reset_password(self, user, new_password, *, dry_run=False, idempotency_key=None):
        u = self._require_user(user)
        if not new_password:
            raise ConnectorError("Yeni parola boş olamaz.")
        result = self._write("reset_password", dry_run, user_id=u.id)
        if not dry_run:
            # Gerçek parola saklanmaz; testler yalnızca teslim edilen mesajda arar
            self.password_resets[u.id] = _PASSWORD_SET
        return {**result, "force_change_next_sign_in": True}

    async def reset_mfa(self, user, *, dry_run=False, idempotency_key=None):
        u = self._require_user(user)
        methods = list(self.mfa_methods.get(u.id, []))
        result = self._write("reset_mfa", dry_run, user_id=u.id)
        if not dry_run:
            self.mfa_methods[u.id] = []
        return {**result, "removed_methods": methods}

    async def unlock_account(self, user, *, dry_run=False, idempotency_key=None):
        u = self._require_user(user)
        was = u.id in self.locked
        result = self._write("unlock_account", dry_run, user_id=u.id)
        if not dry_run:
            self.locked.discard(u.id)
        return {**result, "was_locked": was}

    # --- MailProvider ------------------------------------------------------------------------------

    async def grant_shared_access(
        self, mailbox, user, access: MailboxAccess, *, dry_run=False, idempotency_key=None
    ):
        mb, u = self._require_user(mailbox), self._require_user(user)
        if mb.id not in self.mailbox_access:
            raise ConnectorNotFoundError(f"Paylaşılan posta kutusu bulunamadı: {mailbox}")
        result = self._write(
            "grant_shared_access", dry_run, mailbox_id=mb.id, user_id=u.id, access=access
        )
        if not dry_run:
            self.mailbox_access[mb.id].setdefault(access, set()).add(u.id)
        return result

    async def send_mail(self, to, subject, body, *, dry_run=False, idempotency_key=None):
        result = self._write("send_mail", dry_run, to=to, subject=subject)
        if not dry_run:
            self.messages.append(SentMessage("mail", to, subject, body))
        return result

    # --- LicenseProvider ---------------------------------------------------------------------------

    async def list_skus(self) -> list[SkuInfo]:
        return [replace(s) for s in self.skus.values()]

    async def assign_license(self, user, sku_id, *, dry_run=False, idempotency_key=None):
        u = self._require_user(user)
        sku = self.skus.get(sku_id)
        if sku is None:
            raise ConnectorNotFoundError(f"Lisans (SKU) bulunamadı: {sku_id}")
        already = sku_id in self.licenses.get(u.id, set())
        if not already and sku.available <= 0:
            raise ConnectorError(f"{sku.sku_part_number} için boş lisans yok.")
        result = self._write("assign_license", dry_run, user_id=u.id, sku_id=sku_id)
        if not dry_run and not already:
            self.licenses.setdefault(u.id, set()).add(sku_id)
            sku.consumed += 1
        return {**result, "already_assigned": already}

    # --- Notifier ------------------------------------------------------------------------------------

    async def send_to_user(
        self, user, subject, text, *, sensitive=False, dry_run=False, idempotency_key=None
    ):
        u = self._require_user(user)
        if sensitive and not u.other_mails:
            raise ConnectorError(
                "Hassas bildirim için kullanıcının doğrulanmış ikincil adresi yok."
            )
        target = u.other_mails[0] if sensitive else (u.mail or u.user_principal_name)
        result = self._write("send_to_user", dry_run, user_id=u.id, sensitive=sensitive)
        if not dry_run:
            self.messages.append(SentMessage("user", target, subject, text, sensitive))
        return {**result, "channel": "secondary_mail" if sensitive else "mail"}

    async def post_to_channel(self, channel, text, *, dry_run=False, idempotency_key=None):
        if channel not in self.channels:
            raise ConnectorNotFoundError(f"Kanal yapılandırılmamış: {channel}")
        result = self._write("post_to_channel", dry_run, channel=channel)
        if not dry_run:
            self.messages.append(SentMessage("channel", channel, "", text))
        return result
