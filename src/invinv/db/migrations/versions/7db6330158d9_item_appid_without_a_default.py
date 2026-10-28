"""item appid without a default: every item and catalog entry names its game explicitly

The default of 730 (Counter-Strike 2) only served rows from before multi-game support.

Revision ID: 7db6330158d9
Revises: 4749fffee016
Create Date: 2026-10-28 19:10:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "7db6330158d9"
down_revision: str | Sequence[str] | None = "4749fffee016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("items", "appid", server_default=None)
    op.alter_column("catalog", "appid", server_default=None)


def downgrade() -> None:
    op.alter_column("catalog", "appid", server_default="730")
    op.alter_column("items", "appid", server_default="730")
