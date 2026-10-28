"""fetch job priority: due jobs run highest priority first

Existing price jobs start at 0 and get their priority at the next inventory sync.

Revision ID: 4749fffee016
Revises: b4e8d2a6c913
Create Date: 2026-10-28 18:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4749fffee016"
down_revision: str | Sequence[str] | None = "b4e8d2a6c913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fetch_jobs", sa.Column("priority", sa.SmallInteger(), server_default="0", nullable=False)
    )
    op.execute("UPDATE fetch_jobs SET priority = 100 WHERE kind IN ('inventory', 'fx')")
    op.execute("UPDATE fetch_jobs SET priority = 50 WHERE kind = 'resolve'")


def downgrade() -> None:
    op.drop_column("fetch_jobs", "priority")
