"""FastAPI giriş noktası."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from serviszeka import __version__
from serviszeka.api import audit, incidents, integrations, knowledge
from serviszeka.config import get_settings
from serviszeka.db import create_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Geliştirmede tablolar otomatik kurulur; üretimde göçler (alembic upgrade head) kullanılır
    if get_settings().auto_create_schema:
        await create_all()
    yield


app = FastAPI(title="ServisZeka", version=__version__, lifespan=lifespan)
app.include_router(incidents.router)
app.include_router(knowledge.router)
app.include_router(integrations.router)
app.include_router(audit.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
