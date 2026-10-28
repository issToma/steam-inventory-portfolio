from sqlalchemy.ext.asyncio import AsyncSession

from cs2market.db import repo

REDLINE_FT = repo.CatalogRecord(
    "AK-47 | Redline (Field-Tested)",
    "Classified Rifle",
    sell_price_usd_cents=3160,
    sell_listings=1275,
)
REDLINE_ST_MW = repo.CatalogRecord(
    "StatTrak™ AK-47 | Redline (Minimal Wear)", "StatTrak™ Classified Rifle"
)
AWP_REDLINE = repo.CatalogRecord(
    "AWP | Redline (Field-Tested)", "Classified Sniper Rifle", sell_listings=285
)
CASE = repo.CatalogRecord("Operation Breakout Weapon Case", "Base Grade Container")


async def names(
    session: AsyncSession,
    text: str,
    category: str | None = None,
    quality: str | None = None,
    exterior_name: str | None = None,
) -> list[str]:
    entries = await repo.search_catalog(
        session, text, category=category, quality=quality, exterior_name=exterior_name
    )
    return [e.market_hash_name for e in entries]


async def test_search_by_words_and_with_typos(session: AsyncSession) -> None:
    await repo.upsert_catalog(session, [REDLINE_FT, REDLINE_ST_MW, AWP_REDLINE, CASE])

    # Names containing every word come first, similar names after them.
    assert await names(session, "ak redline") == [
        "AK-47 | Redline (Field-Tested)",
        "StatTrak™ AK-47 | Redline (Minimal Wear)",
        "AWP | Redline (Field-Tested)",
    ]
    assert "AK-47 | Redline (Field-Tested)" in await names(session, "redlin akk")
    assert await names(session, "breakout") == ["Operation Breakout Weapon Case"]
    assert await names(session, "dragon lore") == []


async def test_filters(session: AsyncSession) -> None:
    await repo.upsert_catalog(session, [REDLINE_FT, REDLINE_ST_MW, AWP_REDLINE, CASE])

    assert await names(session, "redline", category="Sniper Rifle") == [
        "AWP | Redline (Field-Tested)"
    ]
    assert await names(session, "redline", quality="stattrak") == [
        "StatTrak™ AK-47 | Redline (Minimal Wear)"
    ]
    assert await names(session, "redline", exterior_name="Minimal Wear") == [
        "StatTrak™ AK-47 | Redline (Minimal Wear)"
    ]
    assert await names(session, "", category="Container") == ["Operation Breakout Weapon Case"]


async def test_upsert_keeps_known_price(session: AsyncSession) -> None:
    await repo.upsert_catalog(session, [REDLINE_FT])
    await repo.upsert_catalog(
        session, [repo.CatalogRecord(REDLINE_FT.market_hash_name, "Classified Rifle", "G1")]
    )

    (entry,) = await repo.search_catalog(session, "redline")

    assert entry.sell_price_usd_cents == 3160
    assert entry.bucket_group_id == "G1"
    assert entry.category == "Rifle"


async def test_fill_from_catalog_completes_missing_details(session: AsyncSession) -> None:
    await repo.upsert_catalog(
        session,
        [
            repo.CatalogRecord(
                "StatTrak™ AK-47 | Redline (Minimal Wear)",
                "StatTrak™ Classified Rifle",
                bucket_group_id="G1",
                icon_url="abc",
            )
        ],
    )
    item = await repo.get_or_create_item(session, "Unknown Sticker")
    item.market_hash_name = "StatTrak™ AK-47 | Redline (Minimal Wear)"

    await repo.fill_from_catalog(session, item)

    assert (item.icon_url, item.bucket_group_id, item.rarity) == ("abc", "G1", "Classified")
