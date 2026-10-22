"""The demo snapshot: the sanitised data a brand-new install starts from.

`backend/snapshot/` holds it: a gzip'd plain-SQL `pg_dump` (`DUMP_NAME`) and its manifest
(`MANIFEST_NAME`: sha256, alembic revision, capture time). `app.snapshot.restore` loads it
into an empty database before the migrations run; `app.cli.build_snapshot` rebuilds it.
"""

from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "snapshot"
DUMP_NAME = "frogeye-demo.sql.gz"
MANIFEST_NAME = "frogeye-demo.json"
FORMAT = 1

# The five demo personas (app/seed/users.yaml): the only users a snapshot may carry.
PERSONA_DOMAIN = "@frogeye.local"

# Tables whose rows a snapshot never carries: the LLM response cache, LangGraph's
# checkpoints and the per-host circuit breaker. Their schema stays.
EMPTIED_TABLES = (
    "llm_cache",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "host_blocks",
)
