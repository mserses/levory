"""Kimlik, posta kutusu, lisans ve kullanıcı/kanal bildirimi araçları (Entra ID / M365 / Teams).

Araçlar somut sağlayıcıyı değil `serviszeka.connectors` arayüzlerini kullanır (kayıt:
`tools.connectors`; yoksa 503). Risk sınıfları (ürün sahibiyle tasarlanan katalog):

- R0: `identity.get_user`, `identity.verify_requester`, `license.check_available`
- R1: `license.assign`, `notify.send_to_user`, `notify.post_to_channel`
- R1 koşullu: `identity.reset_password` ve `identity.unlock_account` (yalnızca doğrulanmış
  self-servis talepte R1, aksi R2),
  `identity.add_to_group` (`SZ_IDENTITY_STANDARD_GROUPS` izin listesinde ve ayrıcalıklı değilse R1,
  aksi R3)
- R2: `identity.reset_mfa`, `identity.remove_from_group`, `identity.revoke_sessions`,
  `mailbox.grant_shared_access`
- R3: `identity.disable_user`

Geçici parola hiçbir zaman araç sonucunda, eylem isteğinde, denetimde ya da günlükte yer almaz:
yalnızca kullanıcının doğrulanmış ikincil adresine (`Notifier.send_to_user(sensitive=True)`) gider.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from serviszeka.connectors.base import (
    IDENTITY,
    LICENSE,
    MAIL,
    MESSAGING,
    IdentityProvider,
    LicenseProvider,
    MailProvider,
    Notifier,
    SkuInfo,
    generate_temporary_password,
)
from serviszeka.domain.enums import RiskClass
from serviszeka.privacy import mask_pii
from serviszeka.services import requests as request_service
from serviszeka.tools.builtin._common import WRITERS, Args, Id
from serviszeka.tools.connectors import connectors
from serviszeka.tools.registry import registry
from serviszeka.tools.spec import ToolContext, ToolExecutionError, ToolNotFoundError, ToolSpec

LINK = {"request_id": "request"}
UserRef = Annotated[
    str,
    Field(min_length=1, max_length=200, pattern=r"^[^\s/\\#?]+$",
          description="Kullanıcı (UPN ya da dizin kimliği)"),
]
GroupRef = Annotated[
    str, Field(min_length=1, max_length=200, pattern=r"^[^\s/\\#?]+$", description="Grup kimliği")
]
RequestLink = Annotated[
    Id | None, Field(default=None, description="Bağlı hizmet talebi kimliği (varsa)")
]


def _identity() -> IdentityProvider:
    return connectors.require(IDENTITY)


def _mail() -> MailProvider:
    return connectors.require(MAIL)


def _license() -> LicenseProvider:
    return connectors.require(LICENSE)


def _notifier() -> Notifier:
    return connectors.require(MESSAGING)


async def _require_user(user: str):
    found = await _identity().get_user(user)
    if found is None:
        raise ToolNotFoundError(f"Kullanıcı dizinde bulunamadı: {user}")
    return found


# --- R0 ------------------------------------------------------------------------------------------


class GetUserArgs(Args):
    user: UserRef
    include_groups: bool = Field(default=True, description="Doğrudan grup üyelikleri de dönsün")


async def get_user(ctx: ToolContext, args: GetUserArgs) -> dict[str, Any]:
    provider = _identity()
    user = await _require_user(args.user)
    data = user.to_dict()
    data.pop("other_mails", None)  # ikincil adresler yalnızca hassas teslimat içindir
    manager = await provider.get_manager(user.id)
    data["manager"] = (
        {"id": manager.id, "user_principal_name": manager.user_principal_name,
         "display_name": manager.display_name}
        if manager
        else None
    )
    if args.include_groups:
        data["groups"] = [g.to_dict() for g in await provider.list_groups(user.id)]
    return data


class VerifyArgs(Args):
    request_id: Id = Field(description="Hizmet talebi kimliği")
    user: UserRef


async def verify_requester(ctx: ToolContext, args: VerifyArgs) -> dict[str, Any]:
    result = await request_service.verify_self_service(
        ctx.session, args.request_id, args.user, _identity()
    )
    return {"request_id": args.request_id, **result.to_dict()}


async def _find_sku(sku: str) -> SkuInfo:
    key = sku.strip().casefold()
    for item in await _license().list_skus():
        if key in (item.sku_id.casefold(), item.sku_part_number.casefold()):
            return item
    raise ToolNotFoundError(f"Lisans (SKU) bulunamadı: {sku}")


class SkuArgs(Args):
    sku: str = Field(min_length=1, max_length=100, description="SKU kimliği ya da parça numarası")


async def check_available(ctx: ToolContext, args: SkuArgs) -> dict[str, Any]:
    return (await _find_sku(args.sku)).to_dict()


# --- R1 / koşullu ----------------------------------------------------------------------------------


class UserTargetArgs(Args):
    user: UserRef
    request_id: RequestLink


async def _self_service_risk(ctx: ToolContext, args: UserTargetArgs, tool: str) -> RiskClass:
    # Yalnızca talebi kendi oturumuyla açan kullanıcının kendi hesabı R1; diğer her durum R2
    result = await request_service.verify_self_service(
        ctx.session, args.request_id, args.user, _identity(), tool=tool
    )
    return RiskClass.R1 if result.verified else RiskClass.R2


async def _reset_password_risk(ctx: ToolContext, args: UserTargetArgs) -> RiskClass:
    return await _self_service_risk(ctx, args, "identity.reset_password")


async def _unlock_account_risk(ctx: ToolContext, args: UserTargetArgs) -> RiskClass:
    # Kilit açma da hesabı ele geçirmeye kapı açar: başkası adına / talepsiz / devre dışı hesapta R2
    return await _self_service_risk(ctx, args, "identity.unlock_account")


async def reset_password(ctx: ToolContext, args: UserTargetArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    notifier = _notifier()
    subject = "Geçici parolanız"
    # Önce teslim edilebilir mi bak (ikincil adres yoksa parola değiştirilmez)
    await notifier.send_to_user(user.id, subject, "-", sensitive=True, dry_run=True)
    password = generate_temporary_password()
    result = await _identity().reset_password(user.id, password)
    await notifier.send_to_user(
        user.id,
        subject,
        f"Geçici parolanız: {password}\nİlk girişte yeni bir parola belirlemeniz istenecek. "
        "Bu talebi siz yapmadıysanız hemen BT servis masasına bildirin.",
        sensitive=True,
    )
    del password
    return {
        "user_id": user.id,
        "delivered_to": "secondary_mail",
        "force_change_next_sign_in": bool(result.get("force_change_next_sign_in", True)),
        "status": result.get("status", "completed"),
    }


async def unlock_account(ctx: ToolContext, args: UserTargetArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    result = await _identity().unlock_account(user.id)
    return {"user_id": user.id, "was_locked": result.get("was_locked")}


class GroupArgs(Args):
    user: UserRef
    group: GroupRef
    request_id: RequestLink


async def _add_to_group_risk(ctx: ToolContext, args: GroupArgs) -> RiskClass:
    # Standart (izin listesindeki) ve ayrıcalıklı olmayan grup R1; diğer her grup R3
    if args.group not in set(ctx.settings.identity_standard_groups):
        return RiskClass.R3
    if args.group in set(ctx.settings.identity_privileged_groups):
        return RiskClass.R3
    if await _identity().is_privileged_group(args.group):
        return RiskClass.R3
    return RiskClass.R1


async def add_to_group(ctx: ToolContext, args: GroupArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    result = await _identity().add_to_group(user.id, args.group)
    return {"user_id": user.id, "group_id": args.group,
            "already_member": bool(result.get("already_member"))}


class AssignLicenseArgs(Args):
    user: UserRef
    sku: str = Field(min_length=1, max_length=100, description="SKU kimliği ya da parça numarası")
    request_id: RequestLink


async def assign_license(ctx: ToolContext, args: AssignLicenseArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    sku = await _find_sku(args.sku)
    if sku.available <= 0:
        raise ToolExecutionError(f"{sku.sku_part_number} için boş lisans yok.")
    await _license().assign_license(user.id, sku.sku_id)
    return {"user_id": user.id, "sku_id": sku.sku_id, "sku_part_number": sku.sku_part_number}


class SendToUserArgs(Args):
    user: UserRef
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    request_id: RequestLink


async def send_to_user(ctx: ToolContext, args: SendToUserArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    result = await _notifier().send_to_user(user.id, args.subject, args.message)
    return {"user_id": user.id, "channel": result.get("channel", "mail")}


class ChannelArgs(Args):
    channel: str = Field(min_length=1, max_length=100, description="Yapılandırılmış kanal adı")
    message: str = Field(min_length=1, max_length=4000, description="Mesaj (PII maskelenir)")
    request_id: RequestLink


async def post_to_channel(ctx: ToolContext, args: ChannelArgs) -> dict[str, Any]:
    # Kanal ortak alandır: kişisel veri maskelenir
    await _notifier().post_to_channel(args.channel, mask_pii(args.message))
    return {"channel": args.channel, "posted": True}


# --- R2 / R3 -------------------------------------------------------------------------------------------


async def reset_mfa(ctx: ToolContext, args: UserTargetArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    result = await _identity().reset_mfa(user.id)
    return {"user_id": user.id, "removed_methods": list(result.get("removed_methods", []))}


async def remove_from_group(ctx: ToolContext, args: GroupArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    result = await _identity().remove_from_group(user.id, args.group)
    return {"user_id": user.id, "group_id": args.group, "was_member": result.get("was_member")}


async def revoke_sessions(ctx: ToolContext, args: UserTargetArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    await _identity().revoke_sessions(user.id)
    return {"user_id": user.id, "revoked": True}


class DisableArgs(UserTargetArgs):
    reason_code: Literal["offboarding", "security_incident", "other"] = Field(
        default="other", description="Neden (denetim için)"
    )


async def disable_user(ctx: ToolContext, args: DisableArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    await _identity().set_account_enabled(user.id, False)
    return {"user_id": user.id, "account_enabled": False, "reason_code": args.reason_code}


class MailboxArgs(Args):
    mailbox: UserRef = Field(description="Paylaşılan posta kutusu (UPN)")
    user: UserRef
    access: Literal["full_access", "send_as"] = "full_access"
    request_id: RequestLink


async def grant_shared_access(ctx: ToolContext, args: MailboxArgs) -> dict[str, Any]:
    user = await _require_user(args.user)
    await _mail().grant_shared_access(args.mailbox, user.id, args.access)
    return {"mailbox": args.mailbox, "user_id": user.id, "access": args.access}


def register_all() -> None:
    R0, R1, R2, R3 = RiskClass.R0, RiskClass.R1, RiskClass.R2, RiskClass.R3
    ident, msg = (IDENTITY,), (IDENTITY, MESSAGING)
    for spec in (
        ToolSpec("identity.get_user", "Dizindeki kullanıcıyı, yöneticisini ve grup üyeliklerini "
                 "getirir.", R0, GetUserArgs, get_user, WRITERS, connectors=ident),
        ToolSpec("identity.verify_requester", "Hizmet talebini hesabın sahibinin kendi oturumuyla "
                 "açıp açmadığını ve dizindeki hesapla eşleştiğini doğrular.", R0, VerifyArgs,
                 verify_requester, WRITERS, connectors=ident),
        ToolSpec("identity.reset_password", "Geçici parola atar ve yalnızca kullanıcının kayıtlı "
                 "ikincil adresine gönderir (doğrulanmış self-servis talepte R1, diğer her durumda "
                 "R2).", R1, UserTargetArgs, reset_password, WRITERS,
                 risk_fn=_reset_password_risk, connectors=msg, links=LINK),
        ToolSpec("identity.unlock_account", "Kilitlenen hesabın kilidini açar (doğrulanmış "
                 "self-servis talepte R1, diğer her durumda R2).", R1, UserTargetArgs,
                 unlock_account, WRITERS, risk_fn=_unlock_account_risk, connectors=ident,
                 links=LINK),
        ToolSpec("identity.reset_mfa", "Kullanıcının parola dışındaki MFA yöntemlerini siler "
                 "(yeniden kayıt gerekir).", R2, UserTargetArgs, reset_mfa, WRITERS,
                 connectors=ident, links=LINK),
        ToolSpec("identity.add_to_group", "Kullanıcıyı gruba ekler (standart izin listesindeki "
                 "grup R1; listede olmayan ya da ayrıcalıklı grup R3).", R1, GroupArgs,
                 add_to_group, WRITERS, risk_fn=_add_to_group_risk, connectors=ident, links=LINK),
        ToolSpec("identity.remove_from_group", "Kullanıcıyı gruptan çıkarır.", R2, GroupArgs,
                 remove_from_group, WRITERS, connectors=ident, links=LINK),
        ToolSpec("identity.revoke_sessions", "Kullanıcının tüm oturumlarını ve yenileme "
                 "belirteçlerini iptal eder.", R2, UserTargetArgs, revoke_sessions, WRITERS,
                 connectors=ident, links=LINK),
        ToolSpec("identity.disable_user", "Kullanıcı hesabını devre dışı bırakır (oturum "
                 "açamaz).", R3, DisableArgs, disable_user, WRITERS, connectors=ident, links=LINK),
        ToolSpec("mailbox.grant_shared_access", "Paylaşılan posta kutusuna tam erişim ya da "
                 "adına gönderme izni verir.", R2, MailboxArgs, grant_shared_access, WRITERS,
                 connectors=(IDENTITY, MAIL), links=LINK),
        ToolSpec("license.check_available", "Microsoft 365 lisansının (SKU) toplam, kullanılan "
                 "ve boş adedini getirir.", R0, SkuArgs, check_available, WRITERS,
                 connectors=(LICENSE,)),
        ToolSpec("license.assign", "Boş lisans varsa kullanıcıya Microsoft 365 lisansı atar.", R1,
                 AssignLicenseArgs, assign_license, WRITERS, connectors=(IDENTITY, LICENSE),
                 links=LINK),
        ToolSpec("notify.send_to_user", "Kullanıcıya e-posta bildirimi gönderir.", R1,
                 SendToUserArgs, send_to_user, WRITERS, connectors=msg, links=LINK),
        ToolSpec("notify.post_to_channel", "Yapılandırılmış Teams kanalına mesaj gönderir (kişisel "
                 "veri maskelenir).", R1, ChannelArgs, post_to_channel, WRITERS,
                 connectors=(MESSAGING,), links=LINK),
    ):  # fmt: skip
        registry.register(spec)
