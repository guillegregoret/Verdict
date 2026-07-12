# La imagen oficial de Superset 5+/6+ no trae drivers de DB: hay que extenderla.
# Solo agregamos el driver de Postgres (metadata en superset_meta + data del
# proyecto vía verdict_ro) dentro del venv que usa la imagen (/app/.venv).
FROM apache/superset:6.1.0

USER root
RUN uv pip install --python /app/.venv/bin/python --no-cache-dir psycopg2-binary
USER superset
