"""Ayarlardan (SZ_CORS_ORIGINS) beslenen CORS ara katmanı.

Kökenler istek anında ``get_settings()`` üzerinden okunur; liste boşsa CORS başlıkları
hiç eklenmez (varsayılan: kapalı). Kimlik doğrulama bearer belirteçle yapıldığından
çerez/kimlik bilgisi paylaşımı (allow_credentials) kapalıdır.
"""

from __future__ import annotations

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from serviszeka.config import get_settings

ALLOW_METHODS = ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"]
ALLOW_HEADERS = ["Authorization", "Content-Type", "If-Match", "Accept", "Accept-Language"]
# Portal iyimser kilit için ETag'i okuyabilmeli
EXPOSE_HEADERS = ["ETag"]


class SettingsCORSMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._by_origins: dict[tuple[str, ...], CORSMiddleware] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        origins = tuple(get_settings().cors_origins)
        if not origins:
            await self.app(scope, receive, send)
            return
        middleware = self._by_origins.get(origins)
        if middleware is None:
            middleware = CORSMiddleware(
                self.app,
                allow_origins=list(origins),
                allow_methods=ALLOW_METHODS,
                allow_headers=ALLOW_HEADERS,
                expose_headers=EXPOSE_HEADERS,
                allow_credentials=False,
                max_age=600,
            )
            self._by_origins[origins] = middleware
        await middleware(scope, receive, send)
