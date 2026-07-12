#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# 0006 — Rol read-only + bases de metadata para la analítica
#
# Script .sh (no .sql) porque necesita interpolar variables de entorno.
# Corre SOLO en un volumen de datos vacío, como el resto de las migraciones.
#
# - verdict_ro: usuario de SOLO LECTURA que usan Grafana/Metabase/Superset para
#   leer la data del proyecto (§8: mínimo privilegio en los dashboards).
# - metabase / superset_meta: metadata interna de cada herramienta, en bases
#   separadas para no mezclarla con la data del portfolio.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RO_USER="${POSTGRES_RO_USER:-verdict_ro}"
RO_PASSWORD="${POSTGRES_RO_PASSWORD:-${POSTGRES_PASSWORD}}"

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE ${RO_USER} LOGIN PASSWORD '${RO_PASSWORD}';
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${RO_USER};
    GRANT USAGE ON SCHEMA public TO ${RO_USER};
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${RO_USER};
    -- Tablas/vistas futuras heredan el SELECT automáticamente.
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ${RO_USER};
EOSQL

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE metabase OWNER ${POSTGRES_USER};
    CREATE DATABASE superset_meta OWNER ${POSTGRES_USER};
EOSQL

echo "0006: rol ${RO_USER} + bases metabase/superset_meta creadas."
