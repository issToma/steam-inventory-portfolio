"""alert rules and events: price conditions per item and the notifications they fired

Revision ID: 731dbca5c682
Revises: 7d2b9e4c1f60
Create Date: 2026-10-28 16:24:35.206776

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "731dbca5c682"
down_revision: str | Sequence[str] | None = "7d2b9e4c1f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("armed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_alert_rules_item_id_items"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_rules")),
    )
    op.create_index(op.f("ix_alert_rules_item_id"), "alert_rules", ["item_id"], unique=False)
    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("threshold", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=True),
        sa.Column("price_usd", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("change", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram", sa.Text(), server_default="off", nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.id"],
            name=op.f("fk_alert_events_item_id_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["alert_rules.id"],
            name=op.f("fk_alert_events_rule_id_alert_rules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_events")),
    )
    op.create_index(
        op.f("ix_alert_events_created_at"), "alert_events", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_alert_events_item_id"), "alert_events", ["item_id"], unique=False)
    op.create_index(op.f("ix_alert_events_rule_id"), "alert_events", ["rule_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_alert_events_rule_id"), table_name="alert_events")
    op.drop_index(op.f("ix_alert_events_item_id"), table_name="alert_events")
    op.drop_index(op.f("ix_alert_events_created_at"), table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_index(op.f("ix_alert_rules_item_id"), table_name="alert_rules")
    op.drop_table("alert_rules")
