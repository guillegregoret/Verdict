#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# superset-init — Bootstrap one-shot de Superset (idempotente).
#
# 1. Migra la metadata (base superset_meta) — reintenta hasta que postgres esté.
# 2. Crea el admin (si ya existe, fab devuelve error → se ignora).
# 3. `superset init` (roles/permisos base).
# 4. Provisiona conexión read-only + datasets + charts + dashboard vía REST API.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

echo "superset-init: migrando metadata (superset db upgrade) ..."
for i in $(seq 1 30); do
    if superset db upgrade; then
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "superset-init: ERROR — no pude migrar la metadata." >&2
        exit 1
    fi
    echo "superset-init: postgres no listo aún, reintento ${i}/30 ..."
    sleep 5
done

echo "superset-init: asegurando admin '${SUPERSET_ADMIN_USER}' ..."
superset fab create-admin \
    --username "${SUPERSET_ADMIN_USER}" \
    --firstname Verdict \
    --lastname Admin \
    --email admin@verdict.local \
    --password "${SUPERSET_ADMIN_PASSWORD}" \
    || echo "superset-init: admin ya existía, sigo."

echo "superset-init: superset init (roles/permisos) ..."
superset init

echo "superset-init: provisionando datasets/charts/dashboard ..."
exec python /init/superset_assets.py
