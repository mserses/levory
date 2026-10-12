"""FastAPI giriş noktası."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from serviszeka import __version__, auth
from serviszeka.api import audit, incidents, integrations, knowledge, me
from serviszeka.config import get_settings
from serviszeka.cors import SettingsCORSMiddleware
from serviszeka.db import create_all
from serviszeka.llm.fake import check_provider


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Güvensiz kimlik doğrulama ayarıyla (örn. üretimde dev kipi) uygulama açılmaz
    auth.check_startup(settings)
    # Sahte (fake) LLM sağlayıcısı geliştirme dışında açılışı engeller
    check_provider(settings)
    # Geliştirmede tablolar otomatik kurulur; üretimde göçler (alembic upgrade head) kullanılır
    if settings.auto_create_schema:
        await create_all()
    yield


app = FastAPI(title="ServisZeka", version=__version__, lifespan=lifespan)
# CORS yalnızca SZ_CORS_ORIGINS doluysa etkin (web portalı başka kökenden çağırır)
app.add_middleware(SettingsCORSMiddleware)
app.include_router(me.router)
app.include_router(incidents.router)
app.include_router(knowledge.router)
app.include_router(integrations.router)
app.include_router(audit.router)


# Kimlik doğrulamasız tek uç nokta (ve /docs, /openapi.json)
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
