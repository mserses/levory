"""FastAPI giriş noktası."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from serviszeka import __version__, auth
from serviszeka.api import (
    actions,
    audit,
    catalog,
    changes,
    cmdb,
    incidents,
    integrations,
    knowledge,
    me,
    problems,
    requests,
)
from serviszeka.config import get_settings
from serviszeka.connectors import setup as connector_setup
from serviszeka.cors import SettingsCORSMiddleware
from serviszeka.db import create_all, get_sessionmaker
from serviszeka.llm.fake import check_provider
from serviszeka.mcp_server import MCP_PATH, gateway


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Güvensiz kimlik doğrulama ayarıyla (örn. üretimde dev kipi) uygulama açılmaz
    auth.check_startup(settings)
    # Sahte (fake) LLM sağlayıcısı geliştirme dışında açılışı engeller
    check_provider(settings)
    # Faz 3: bağlayıcılar (Entra ID / M365 / Teams ya da geliştirmede sahte dizin)
    connector_setup.check_startup(settings)
    connector_setup.configure(settings)
    # Geliştirmede tablolar otomatik kurulur; üretimde göçler (alembic upgrade head) kullanılır
    if settings.auto_create_schema:
        await create_all()
        if settings.environment == "development" and settings.connector_provider == "fake":
            # Yerel deneme / E2E (sahte dizin): varsayılan hizmet kataloğu; öğeler sahte dizinin
            # grup/posta kutusu kimliklerini kullanır (var olan öğelere dokunmaz)
            from serviszeka.services.catalog import seed_default_items

            async with get_sessionmaker()() as session:
                await seed_default_items(session)
                await session.commit()
    # Faz 3: MCP oturum yöneticisi (durumsuz streamable HTTP) uygulama boyunca çalışır
    async with gateway.run():
        yield


app = FastAPI(title="ServisZeka", version=__version__, lifespan=lifespan)
# CORS yalnızca SZ_CORS_ORIGINS doluysa etkin (web portalı başka kökenden çağırır)
app.add_middleware(SettingsCORSMiddleware)
app.include_router(me.router)
app.include_router(incidents.router)
app.include_router(knowledge.router)
app.include_router(integrations.router)
app.include_router(audit.router)
app.include_router(cmdb.router)
app.include_router(changes.router)
app.include_router(actions.router)
app.include_router(problems.router)
app.include_router(catalog.router)
app.include_router(requests.router)
# MCP (Faz 3): araç kayıt defteri; kimlik doğrulaması gateway içinde (bearer OIDC, 401)
app.add_route(MCP_PATH, gateway, methods=["GET", "POST", "DELETE"], include_in_schema=False)


# Kimlik doğrulamasız tek uç nokta (ve /docs, /openapi.json); /mcp kendi doğrulamasını yapar
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
