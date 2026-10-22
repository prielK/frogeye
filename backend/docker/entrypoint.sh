#!/bin/sh
set -e
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  # A brand-new, empty database starts from the bundled demo snapshot (DEMO_SNAPSHOT=off
  # skips it); any other database is left alone. Never fatal: the start goes on either way.
  python -m app.snapshot || echo "WARNING demo snapshot: restore step crashed; continuing"
  alembic upgrade head
fi
exec "$@"
