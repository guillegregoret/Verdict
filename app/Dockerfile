# ── App: monolito modular (non-root, read-only FS) ───────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Dependencias primero (cache de capas)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Código
COPY src ./src

# Usuario no-root (§8: containers non-root)
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Placeholder: reemplazar cuando exista el entrypoint real (§11 roadmap).
CMD ["python", "-m", "portfolio_monitor.main"]
