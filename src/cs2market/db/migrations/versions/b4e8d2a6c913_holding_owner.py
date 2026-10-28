"""holding owner: every position belongs to the Steam account connected when it was made

Existing positions go to the account connected now, if any.

Revision ID: b4e8d2a6c913
Revises: 731dbca5c682
Create Date: 2026-10-28 17:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e8d2a6c913"
down_revision: str | Sequence[str] | None = "731dbca5c682"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("holdings", sa.Column("owner", sa.Text(), nullable=True))
    op.execute(
        "UPDATE holdings SET owner = (SELECT value FROM app_settings WHERE key = 'steam_id64')"
    )
    op.drop_index("uq_holdings_inventory_item", table_name="holdings")
    op.create_index(
        "uq_holdings_inventory_item",
        "holdings",
        ["owner", "item_id"],
        unique=True,
        postgresql_where=sa.text("source = 'inventory'"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    # Only one inventory position per item fits the old index: keep the connected account's.
    op.execute(
        "DELETE FROM holdings WHERE source = 'inventory' AND owner IS DISTINCT FROM "
        "(SELECT value FROM app_settings WHERE key = 'steam_id64')"
    )
    op.drop_index("uq_holdings_inventory_item", table_name="holdings")
    op.create_index(
        "uq_holdings_inventory_item",
        "holdings",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("source = 'inventory'"),
    )
    op.drop_column("holdings", "owner")
