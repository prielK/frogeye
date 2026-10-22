"""drop JFrog-self news: disable self-tied sources, suppress scope-jfrog signals

Data-only revision, no schema change:
1. disable every source tied to the `self` competitor except github_repo_stats, which writes
   metrics, not news. The seed never overwrites `enabled`, so existing rows are
   switched off here; fresh installs no longer seed them;
2. move every scope = 'jfrog' signal to status 'suppressed', so it leaves every reader
   surface. `Scope.jfrog` and the CHECK stay: these legacy rows still carry it;
3. mark irrelevant every relevant document triaged as scope 'jfrog' that no story holds yet,
   so the next run's clustering never opens a signal for it (the cluster node also
   skips such documents);
4. delete every JFrog company link from signal_competitors and document_competitors, so no
   visible signal keeps a JFrog chip and clustering cannot copy one (the cluster node
   also ignores self links).

Every statement is idempotent.

The downgrade is a no-op on data: the earlier flags, statuses and links are not recorded,
and re-enabling JFrog-self news is not wanted.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE sources SET enabled = false"
            " WHERE competitor_id IN (SELECT id FROM competitors WHERE is_self)"
            " AND kind <> 'github_repo_stats'"
        )
    )
    op.execute(sa.text("UPDATE signals SET status = 'suppressed' WHERE scope = 'jfrog'"))
    op.execute(
        sa.text(
            "UPDATE documents SET status = 'irrelevant'"
            " WHERE status = 'relevant' AND triage->>'scope' = 'jfrog' AND story_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM signal_competitors"
            " WHERE competitor_id IN (SELECT id FROM competitors WHERE is_self)"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM document_competitors"
            " WHERE competitor_id IN (SELECT id FROM competitors WHERE is_self)"
        )
    )


def downgrade() -> None:
    pass  # data only: nothing to undo (see the module docstring)
