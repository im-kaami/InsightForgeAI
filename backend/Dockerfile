FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 insightforge \
    && mkdir -p /app /data \
    && chown insightforge:insightforge /app /data

WORKDIR /app
COPY --chown=insightforge:insightforge pyproject.toml alembic.ini ./
COPY --chown=insightforge:insightforge insightforge ./insightforge
RUN pip install -e ".[db,pdf]"

USER insightforge
ENV DATABASE_URL=sqlite:////data/insightforge.db STORAGE_DIR=/data/storage
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

CMD ["uvicorn", "insightforge.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
