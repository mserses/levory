# ServisZeka uygulama imajı
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Önce yalnızca bağımlılıklar (katman önbelleği için)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Sonra uygulama kodu
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

# Root olmayan kullanıcı
RUN useradd --create-home --uid 10001 serviszeka \
    && chown -R serviszeka:serviszeka /app
USER serviszeka

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "serviszeka.main:app", "--host", "0.0.0.0", "--port", "8000"]
