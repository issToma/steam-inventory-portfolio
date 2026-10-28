"""multi-game items: a game (Steam app id) and a name colour for every item

Existing rows belong to Counter-Strike 2 (app id 730). Items and catalog entries are now
identified by (appid, market_hash_name), and collector job targets that name an item or a
market group carry the app id as "<appid>:<target>".

Revision ID: 1a45ffafa0ff
Revises: 3178592c3ed3
Create Date: 2026-10-28 15:35:07.770607

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1a45ffafa0ff"
down_revision: str | Sequence[str] | None = "3178592c3ed3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ITEM_JOBS = "('resolve', 'history', 'group')"


def upgrade() -> None:
    op.add_column("catalog", sa.Column("appid", sa.Integer(), server_default="730", nullable=False))
    op.add_column("catalog", sa.Column("name_color", sa.Text(), nullable=True))
    op.drop_constraint("pk_catalog", "catalog", type_="primary")
    op.create_primary_key("pk_catalog", "catalog", ["appid", "market_hash_name"])

    op.add_column("items", sa.Column("appid", sa.Integer(), server_default="730", nullable=False))
    op.add_column("items", sa.Column("name_color", sa.Text(), nullable=True))
    op.drop_constraint("uq_items_market_hash_name", "items", type_="unique")
    op.create_unique_constraint("uq_items_appid", "items", ["appid", "market_hash_name"])

    op.execute(f"UPDATE fetch_jobs SET target = '730:' || target WHERE kind IN {_ITEM_JOBS}")


def downgrade() -> None:
    op.execute(
        f"UPDATE fetch_jobs SET target = substr(target, strpos(target, ':') + 1) "
        f"WHERE kind IN {_ITEM_JOBS}"
    )
    op.execute("DELETE FROM items WHERE appid <> 730")
    op.drop_constraint("uq_items_appid", "items", type_="unique")
    op.create_unique_constraint("uq_items_market_hash_name", "items", ["market_hash_name"])
    op.drop_column("items", "name_color")
    op.drop_column("items", "appid")

    op.execute("DELETE FROM catalog WHERE appid <> 730")
    op.drop_constraint("pk_catalog", "catalog", type_="primary")
    op.create_primary_key("pk_catalog", "catalog", ["market_hash_name"])
    op.drop_column("catalog", "name_color")
    op.drop_column("catalog", "appid")
