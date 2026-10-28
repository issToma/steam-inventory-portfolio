"""listing prices: the cheapest listing of every item, its own collector job

Items get the market's state from priceoverview (cheapest listing, median and number of
sales of the last 24 hours), and every item tracked now gets a "listing" job.

Revision ID: 0a8fb6a52488
Revises: 7db6330158d9
Create Date: 2026-10-28 22:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a8fb6a52488"
down_revision: str | Sequence[str] | None = "7db6330158d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("items", sa.Column("lowest_listing_usd", sa.Numeric(14, 4), nullable=True))
    op.add_column("items", sa.Column("median_24h_usd", sa.Numeric(14, 4), nullable=True))
    op.add_column("items", sa.Column("sales_24h", sa.Integer(), nullable=True))
    op.add_column(
        "items", sa.Column("listing_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        INSERT INTO fetch_jobs (kind, target, priority)
        SELECT 'listing', i.appid || ':' || i.market_hash_name, 20
        FROM items i
        WHERE EXISTS (SELECT 1 FROM watchlist w WHERE w.item_id = i.id)
           OR EXISTS (SELECT 1 FROM alert_rules r WHERE r.item_id = i.id AND r.enabled)
           OR EXISTS (
               SELECT 1 FROM holdings h
               WHERE h.item_id = i.id AND h.removed_at IS NULL
                 AND h.owner IS NOT DISTINCT FROM
                     (SELECT value FROM app_settings WHERE key = 'steam_id64')
           )
        ON CONFLICT (kind, target) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM fetch_jobs WHERE kind = 'listing'")
    op.drop_column("items", "listing_checked_at")
    op.drop_column("items", "sales_24h")
    op.drop_column("items", "median_24h_usd")
    op.drop_column("items", "lowest_listing_usd")
