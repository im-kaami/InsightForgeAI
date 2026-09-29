# Locked-down Python runtime for InsightForge "python" plan steps.
# Built once with network access; every analysis runs with --network none, a read-only root
# filesystem, dropped capabilities and an unprivileged user (see insightforge/core/sandbox.py).
FROM python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp OPENBLAS_NUM_THREADS=1
COPY requirements.txt /sandbox/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /sandbox/requirements.txt \
    && pip check \
    && rm -rf /root/.cache
COPY runner.py /sandbox/runner.py
USER 65534:65534
WORKDIR /tmp
