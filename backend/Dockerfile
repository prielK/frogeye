FROM python:3.12-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# bake the encodings in so nothing downloads at runtime (the image runs as a
# non-root user and cannot write here): o200k_base for chunking, cl100k_base for
# langchain-openai's OpenAIEmbeddings, which tokenizes text-embedding-3-* inputs with it.
RUN mkdir -p /opt/tiktoken \
    && /opt/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('o200k_base'); tiktoken.get_encoding('cl100k_base')" \
    && chmod -R a+rX /opt/tiktoken

FROM deps AS dev
ENV PATH="/opt/venv/bin:$PATH"
# psql: the demo-snapshot restore (app.snapshot) and its tests
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-install-project
COPY . .

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken
# psql restores the demo snapshot into a brand-new database (app.snapshot)
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=deps /opt/venv /opt/venv
COPY --from=deps /opt/tiktoken /opt/tiktoken
WORKDIR /app
COPY --chown=app:app app/ ./app/
COPY --chown=app:app docker/ ./docker/
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations/ ./migrations/
# the demo snapshot a brand-new database starts from (app.snapshot; DEMO_SNAPSHOT=off skips it)
COPY --chown=app:app snapshot/ ./snapshot/
# GET /api/v1/evals serves the committed eval results (the harness itself stays out)
COPY --chown=app:app evals/results/latest.json ./evals/results/latest.json
RUN chmod +x /app/docker/entrypoint.sh
USER app
ENTRYPOINT ["/app/docker/entrypoint.sh"]
# Open SSE streams (GET /events) never finish on their own: without a graceful-shutdown
# timeout uvicorn waits for them forever on SIGTERM (browsers' EventSource reconnects).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
