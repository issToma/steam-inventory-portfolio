from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint names keep Alembic migrations deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Item(Base):
    """A market item, identified by its game (Steam app id) and market_hash_name."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("appid", "market_hash_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appid: Mapped[int] = mapped_column(Integer)
    market_hash_name: Mapped[str] = mapped_column(Text)
    # Steam's colour for the item's name (rarity), for any game.
    name_color: Mapped[str | None] = mapped_column(Text)
    bucket_group_id: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[str | None] = mapped_column(Text)
    rarity: Mapped[str | None] = mapped_column(Text)
    collection: Mapped[str | None] = mapped_column(Text)
    exterior: Mapped[str | None] = mapped_column(Text)
    quality: Mapped[str | None] = mapped_column(Text)
    classid: Mapped[str | None] = mapped_column(Text)
    icon_url: Mapped[str | None] = mapped_column(Text)
    # The game's market fee share when it is not Steam's default 10%, from the inventory.
    publisher_fee: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    # The market right now (priceoverview, in USD): the cheapest listing (None when nothing
    # is listed), the median sale and the number of sales of the last 24 hours.
    lowest_listing_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    median_24h_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    sales_24h: Mapped[int | None] = mapped_column(Integer)
    listing_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PricePoint(Base):
    """One point of Steam's price history: daily for old data, hourly for the last month.

    Prices are stored as received, in the currency Steam chose for our IP (`currency` is
    Steam's ECurrencyCode, e.g. 1 = USD, 3 = EUR); conversion to USD happens on read.

    `resolution` is part of the key: once a day leaves Steam's hourly window, Steam returns
    a daily point at 00:00 that must not overwrite the hourly point we stored for that time.
    """

    __tablename__ = "price_history"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    resolution: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    volume: Mapped[int] = mapped_column(Integer)
    currency: Mapped[int] = mapped_column(SmallInteger)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FxRate(Base):
    """Steam's own exchange rate to USD for a currency on a given day."""

    __tablename__ = "fx_rates"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    usd_per_unit: Mapped[Decimal] = mapped_column(Numeric(18, 8))


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FetchJob(Base):
    """A persistent collector task, so a restart resumes where it stopped."""

    __tablename__ = "fetch_jobs"
    __table_args__ = (UniqueConstraint("kind", "target"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    # Due jobs run highest priority first (scheduling.fetch_priority for price jobs).
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CatalogEntry(Base):
    """Every market item we have come across, for instant local search without Steam.

    Filled from Steam search results and market group pages. Prices are a snapshot from
    the last search that returned the item and may be missing or stale.
    """

    __tablename__ = "catalog"
    __table_args__ = (
        Index(
            "ix_catalog_name_trgm",
            "market_hash_name",
            postgresql_using="gin",
            postgresql_ops={"market_hash_name": "gin_trgm_ops"},
        ),
    )

    appid: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_hash_name: Mapped[str] = mapped_column(Text, primary_key=True)
    name_color: Mapped[str | None] = mapped_column(Text)
    item_type: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text, index=True)
    bucket_group_id: Mapped[str | None] = mapped_column(Text)
    classid: Mapped[str | None] = mapped_column(Text)
    icon_url: Mapped[str | None] = mapped_column(Text)
    sell_price_usd_cents: Mapped[int | None] = mapped_column(Integer)
    sell_listings: Mapped[int | None] = mapped_column(Integer)
    price_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Holding(Base):
    """A position in the user's portfolio.

    Inventory positions (one per item) are kept in sync with the Steam inventory; when an
    item leaves the inventory the row is marked removed instead of deleted, so a purchase
    price the user entered survives if the item comes back. Manual positions (for example
    storage unit contents, which the inventory does not show) are never touched by the sync.

    Every position belongs to the Steam account (`owner`, a SteamID64) that was connected
    when it was created; the portfolio shows only the connected account's positions, so
    switching accounts hides the others' instead of removing them. Positions made while no
    account was connected have no owner and go to the first account connected.
    """

    __tablename__ = "holdings"
    __table_args__ = (
        Index(
            "uq_holdings_inventory_item",
            "owner",
            "item_id",
            unique=True,
            postgresql_where=text("source = 'inventory'"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str | None] = mapped_column(Text)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int] = mapped_column(Integer)
    buy_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    bought_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A pinned inventory position survives syncs: it never shrinks or disappears because the
    # items left the inventory (for example, into a storage unit).
    pinned: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Left out of the portfolio's value chart and period stats (the value still counts), say
    # a brand-new item that would otherwise make the chart start late.
    chart_excluded: Mapped[bool] = mapped_column(Boolean, server_default=false())


class AppSetting(Base):
    """Settings changed from the UI, such as the SteamID whose inventory is imported."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class AlertRule(Base):
    """A condition on an item's price that notifies the user when it becomes true.

    `kind` is "above" or "below" a price in USD (`threshold`), or "rise" or "fall" by a
    percentage (`threshold`, e.g. 10 for 10%) within `window_hours`. A rule fires when its
    condition starts to hold and is `armed` again once the condition stops holding, so a
    price that stays above the threshold notifies once rather than on every refresh.
    """

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    threshold: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    window_hours: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    armed: Mapped[bool] = mapped_column(Boolean, server_default=true())
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertEvent(Base):
    """A fired alert: shown in the UI's notification feed and sent to Telegram.

    It copies the rule's condition, so the feed still reads right after the rule is changed
    or deleted. `telegram` is "pending", "sent", "failed" or "off" (Telegram not set up).
    """

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"), index=True
    )
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    threshold: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    window_hours: Mapped[int | None] = mapped_column(Integer)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    change: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram: Mapped[str] = mapped_column(Text, server_default="off")
