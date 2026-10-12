"""FastAPI giriş noktası."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from serviszeka import __version__
from serviszeka.api import audit, incidents, integrations, knowledge
from serviszeka.db import create_all


@asynccontextmanager
async def lifespan(app: FastAPI):
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
