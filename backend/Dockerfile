# Backend (FastAPI + Postgres/pgvector) container.
#
# Runtime deps only: run `alembic upgrade head` and
# `python scripts/ingest_real_docs.py` as one-off commands after the
# container is up (see docker-compose.yml comments) — not baked into CMD,
# so restarting the container never silently re-runs migrations/ingestion.
FROM python:3.14-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libpq for asyncpg/psycopg-style Postgres wheels that need it at import time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000

# fastembed's cross-encoder reranker downloads its ONNX model from Hugging
# Face on first use — the container needs outbound network access at least
# once (or a pre-warmed model cache volume) for reranking to work.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
