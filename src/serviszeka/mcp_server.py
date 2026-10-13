"""MCP sunucusu (Faz 3): araç kayıt defterini resmi Python MCP SDK ile `/mcp`'de sunar.

- Taşıma: streamable HTTP (durumsuz, JSON yanıt), FastAPI uygulamasına `/mcp` yolu olarak eklenir;
  oturum yöneticisi uygulama yaşam döngüsünde (`gateway.run()`) çalışır.
- Kimlik: `Authorization: Bearer <OIDC JWT>` REST ile aynı doğrulamadan (`auth.authenticate_token`)
  geçer; eksik/geçersiz belirteç -> 401. `ai_agent` rolü taşıyan hizmet sorumlusu `ai:<istemci>`
  aktörü olur. Araç bazında yetki `tools.execute_tool` içinde denetlenir; `tools/list` yalnızca
  çağıranın kullanabileceği araçları döndürür.
- Her araç JSON şemasıyla ve risk sınıfını belirten açıklamayla listelenir. R1-R3 araçlar zarf
  alanlarını da kabul eder: `dry_run`, `idempotency_key`, `reason`, `confidence`.
- Sonuç gövdesi `ToolResult.to_dict()`; R2/R3 için `{"status": "pending_approval",
  "action_request_id": ...}`. Yapay zekâ çağıranlara dönen sonuç PII maskelidir.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import mcp_types as types
from fastapi import HTTPException
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse

from serviszeka import __version__, auth, db, notifications
from serviszeka.config import Settings, get_settings
from serviszeka.domain.enums import RiskClass
from serviszeka.tools import (
    RESERVED_ARGS,
    RISK_LABELS,
    ToolError,
    ToolStatus,
    ToolValidationError,
    can_call,
    execute_tool,
    registry,
)
from serviszeka.tools.redaction import mask_value
from serviszeka.tools.spec import ToolSpec

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
_USER_KEY = "serviszeka_user"

INSTRUCTIONS = (
    "ServisZeka ITSM araçları. Her aracın açıklaması risk sınıfıyla başlar: R0 okuma; R1 düşük "
    "riskli yazma (politika izin verirse otomatik, yoksa onaya düşer); R2/R3 her zaman insan onayı "
    'ister ve {"status": "pending_approval", "action_request_id": ...} döner. Yazan araçlara '
    "güven skorunuzu (confidence, 0-1) ve gerekçenizi (reason) verin; tekrar denemede aynı "
    "idempotency_key'i kullanın; emin değilseniz önce dry_run=true deneyin."
)

ENVELOPE_SCHEMA: dict[str, Any] = {
    "dry_run": {
        "type": "boolean",
        "default": False,
        "description": "Deneme: hiçbir şey yazılmaz; ne olacağı (çalışır/onaya düşer) döner",
    },
    "idempotency_key": {
        "type": "string",
        "maxLength": 200,
        "description": "Aynı anahtar + aynı argüman -> önceki sonuç; farklı argüman -> hata",
    },
    "reason": {
        "type": "string",
        "maxLength": 2000,
        "description": "Eylemin gerekçesi (onaylayan görür)",
    },
    "confidence": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "description": "Yapay zekâ güven skoru; verilmezse 0 sayılır ve R1 eylem onaya düşer",
    },
}


def tool_definition(spec: ToolSpec) -> types.Tool:
    schema = spec.input_schema()
    risk = RiskClass(spec.risk_class)
    if risk != RiskClass.R0:
        schema = {**schema, "properties": {**schema.get("properties", {}), **ENVELOPE_SCHEMA}}
    conditional = " Risk argümanlara göre yükselebilir." if spec.risk_fn else ""
    return types.Tool(
        name=spec.name,
        description=f"[{RISK_LABELS[risk]}] {spec.description}{conditional}",
        input_schema=schema,
        annotations=types.ToolAnnotations(
            read_only_hint=risk == RiskClass.R0,
            destructive_hint=risk in (RiskClass.R2, RiskClass.R3),
            idempotent_hint=risk == RiskClass.R0,
            open_world_hint=bool(spec.connectors),
        ),
        meta={
            "serviszeka/risk_class": risk.value,
            "serviszeka/conditional_risk": bool(spec.risk_fn),
        },
    )


def _envelope(args: dict[str, Any]) -> dict[str, Any]:
    env = {k: args.pop(k) for k in list(args) if k in RESERVED_ARGS}
    errors = []
    if not isinstance(env.get("dry_run", False), bool):
        errors.append("dry_run boolean olmalı")
    key = env.get("idempotency_key")
    if key is not None and (not isinstance(key, str) or not 0 < len(key) <= 200):
        errors.append("idempotency_key 1-200 karakter olmalı")
    reason = env.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 2000:
        errors.append("reason en fazla 2000 karakterlik metin olmalı")
    conf = env.get("confidence")
    if conf is not None and (
        isinstance(conf, bool) or not isinstance(conf, int | float) or not 0 <= conf <= 1
    ):
        errors.append("confidence 0 ile 1 arasında sayı olmalı")
    if errors:
        raise ToolValidationError("Zarf alanları geçersiz: " + "; ".join(errors))
    return env


def _result(payload: dict[str, Any], *, is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=is_error,
    )


class MCPGateway:
    """MCP ASGI uygulaması + oturum yöneticisi yaşam döngüsü.

    Sağlayıcılar testlerde değiştirilebilir (FastAPI `dependency_overrides` karşılığı).
    """

    def __init__(self) -> None:
        self.settings_provider: Callable[[], Settings] = get_settings
        self.auth_settings_provider: Callable[[], Settings] = auth.get_auth_settings
        self.authenticator_provider: Callable[[Settings], auth.OIDCAuthenticator | None] = (
            auth.get_authenticator
        )
        self.llm_provider: Callable[[], Any] = lambda: None
        self.notifier_provider: Callable[[Settings], notifications.Notifier] = (
            notifications.get_notifier
        )
        self.server: Server[Any] = Server(
            "serviszeka",
            version=__version__,
            title="ServisZeka",
            instructions=INSTRUCTIONS,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
        )
        self._manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Oturum yöneticisini çalıştırır (uygulama yaşam döngüsü ya da test fikstürü)."""
        manager = StreamableHTTPSessionManager(app=self.server, stateless=True, json_response=True)
        async with manager.run():
            self._manager = manager
            try:
                yield
            finally:
                self._manager = None

    # --- ASGI --------------------------------------------------------------------------------

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":  # pragma: no cover - yalnızca HTTP yolu bağlanır
            return
        user, error = await self._authenticate(scope)
        if error is not None:
            await error(scope, receive, send)
            return
        if self._manager is None:
            response = JSONResponse({"detail": "MCP sunucusu başlatılmadı"}, status_code=503)
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})[_USER_KEY] = user
        await self._manager.handle_request(scope, receive, send)

    async def _authenticate(self, scope) -> tuple[auth.CurrentUser | None, Any]:
        header = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"authorization"), ""
        )
        scheme, _, token = header.partition(" ")
        if not header:
            return None, self._unauthorized(auth.MSG_MISSING, None)
        if scheme.lower() != "bearer" or not token.strip():
            return None, self._unauthorized(auth.MSG_INVALID, "invalid_request")
        settings = self.auth_settings_provider()
        try:
            authenticator = (
                None if settings.auth_mode == "dev" else self.authenticator_provider(settings)
            )
        except HTTPException as exc:
            return None, JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        try:
            user = await auth.authenticate_token(token.strip(), settings, authenticator)
        except auth.AuthError as exc:
            logger.info("MCP kimlik doğrulama reddedildi: %s", exc)
            return None, self._unauthorized(auth.MSG_INVALID, "invalid_token")
        return user, None

    @staticmethod
    def _unauthorized(detail: str, error: str | None) -> JSONResponse:
        challenge = f'Bearer error="{error}"' if error else "Bearer"
        return JSONResponse(
            {"detail": detail}, status_code=401, headers={"WWW-Authenticate": challenge}
        )

    # --- MCP işleyicileri ---------------------------------------------------------------------

    @staticmethod
    def _user(ctx: Any) -> auth.CurrentUser:
        request = getattr(ctx, "request", None)
        user = getattr(getattr(request, "state", None), _USER_KEY, None) if request else None
        if not isinstance(user, auth.CurrentUser):
            # Kimliği doğrulanmamış (ör. süreç içi) bağlantı hiçbir aracı çağıramaz
            return auth.CurrentUser("anonim", frozenset())
        return user

    async def _list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        user = self._user(ctx)
        return types.ListToolsResult(
            tools=[tool_definition(spec) for spec in registry.all() if can_call(spec, user)]
        )

    async def _call_tool(
        self, ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        user = self._user(ctx)
        args = dict(params.arguments or {})
        settings = self.settings_provider()
        async with db.get_sessionmaker()() as session:
            try:
                env = _envelope(args)
                result = await execute_tool(
                    session,
                    user,
                    params.name,
                    args,
                    dry_run=env.get("dry_run", False),
                    idempotency_key=env.get("idempotency_key"),
                    reason=env.get("reason", ""),
                    confidence=env.get("confidence"),
                    model=f"mcp:{user.username}"[:100],
                    settings=settings,
                    llm=self.llm_provider(),
                )
            except ToolError as exc:
                await session.rollback()
                return _result(
                    {"status": "error", "tool": params.name, "error": exc.to_dict()}, is_error=True
                )
            await session.commit()
            await notifications.send_pending(session, self.notifier_provider(settings))
        payload = result.to_dict()
        if user.is_ai:
            payload = mask_value(payload)
        return _result(payload, is_error=result.status in (ToolStatus.FAILED, ToolStatus.REJECTED))


gateway = MCPGateway()
