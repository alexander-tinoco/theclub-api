# syntax=docker/dockerfile:1

# ------------------------------------------------------------------ builder
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Las dependencias se instalan antes de copiar el código: mientras el lock no
# cambie, esta capa se reutiliza y el build es casi instantáneo.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# ------------------------------------------------------------------ runtime
FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system theclub \
    && useradd --system --gid theclub --home-dir /app --no-create-home theclub

WORKDIR /app

COPY --from=builder --chown=theclub:theclub /app/.venv /app/.venv
COPY --from=builder --chown=theclub:theclub /app/app /app/app
COPY --from=builder --chown=theclub:theclub /app/alembic /app/alembic
COPY --from=builder --chown=theclub:theclub /app/alembic.ini /app/alembic.ini

USER theclub

EXPOSE 8000

# Sin curl en la imagen slim: el healthcheck usa el propio intérprete.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health').read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
