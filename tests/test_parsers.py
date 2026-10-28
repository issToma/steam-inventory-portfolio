from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from cs2market.core.parsers import (
    ProfileRef,
    SteamFormatError,
    market_hash_name_from_input,
    parse_group_page,
    parse_inventory,
    parse_item_traits,
    parse_price_history,
    parse_price_overview,
    parse_profile_input,
    parse_profile_xml,
    parse_search_results,
)

FIXTURES = Path(__file__).parent / "fixtures"
FETCHED_AT = datetime(2026, 10, 26, 17, 0, tzinfo=UTC)


def test_price_history(load_fixture: Callable[[str], Any]) -> None:
    history = parse_price_history(load_fixture("price_history.json"), FETCHED_AT)

    assert history.currency == 3
    assert len(history.points) == 6
    first, last = history.points[0], history.points[-1]
    assert first.ts == datetime(2014, 2, 21, tzinfo=UTC)
    assert first.resolution == "day"
    assert first.price == Decimal("36.9798")
    assert first.volume == 198
    assert last.ts == datetime(2026, 10, 26, 12, tzinfo=UTC)
    assert last.resolution == "hour"


def test_price_history_midnight_point_inside_hourly_window_is_hourly() -> None:
    payload = {
        "data": {
            "ecurrency": 1,
            "prices": [{"time": 1792540800, "price_median": 1.5, "purchases": 3}],
        }
    }

    (point,) = parse_price_history(payload, FETCHED_AT).points

    assert point.ts == datetime(2026, 10, 21, tzinfo=UTC)
    assert point.resolution == "hour"


@pytest.mark.parametrize(
    "payload",
    [[], {"data": {}}, {"data": {"ecurrency": 3, "prices": [{"time": "x"}]}}],
)
def test_price_history_rejects_unexpected_shape(payload: Any) -> None:
    with pytest.raises(SteamFormatError):
        parse_price_history(payload, FETCHED_AT)


def test_price_overview() -> None:
    overview = parse_price_overview(
        {"success": True, "lowest_price": "28,64€", "volume": "1,058", "median_price": "27,65€"}
    )

    assert overview.lowest_price == Decimal("28.64")
    assert overview.median_price == Decimal("27.65")
    assert overview.volume == 1058


def test_price_overview_without_sales() -> None:
    overview = parse_price_overview({"success": True, "lowest_price": "$0.03"})

    assert overview.median_price is None
    assert overview.volume is None


def test_search_results(load_fixture: Callable[[str], Any]) -> None:
    (result,) = parse_search_results(load_fixture("search_render.json"))

    assert result.market_hash_name == "Operation Breakout Weapon Case"
    assert result.item_type == "Base Grade Container"
    assert result.bucket_group_id == "G18B21F3004"
    assert result.sell_price_usd_cents == 1006


@pytest.mark.parametrize(
    ("name", "item_type", "expected"),
    [
        (
            "StatTrak™ AK-47 | Redline (Field-Tested)",
            "StatTrak™ Classified Rifle",
            ("Classified", "Rifle", "StatTrak™", "Field-Tested"),
        ),
        (
            "★ StatTrak™ Karambit | Doppler (Factory New)",
            "★ StatTrak™ Covert Knife",
            ("Covert", "Knife", "★ StatTrak™", "Factory New"),
        ),
        (
            "Operation Breakout Weapon Case",
            "Base Grade Container",
            ("Base Grade", "Container", None, None),
        ),
        (
            "Sticker | Crown (Foil)",
            "Extraordinary Sticker",
            ("Extraordinary", "Sticker", None, None),
        ),
    ],
)
def test_item_traits(name: str, item_type: str, expected: tuple[str | None, ...]) -> None:
    traits = parse_item_traits(name, item_type)

    assert (traits.rarity, traits.category, traits.quality, traits.exterior) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AK-47 | Redline (Field-Tested)", "AK-47 | Redline (Field-Tested)"),
        (
            "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
            "AK-47 | Redline (Field-Tested)",
        ),
        (
            "https://steamcommunity.com/market/listings/730/G1807209A023004/"
            "AK-47%20%7C%20Redline%20%28Field-Tested%29",
            "AK-47 | Redline (Field-Tested)",
        ),
    ],
)
def test_market_hash_name_from_input(text: str, expected: str) -> None:
    assert market_hash_name_from_input(text) == expected


def test_group_url_without_variant_is_rejected() -> None:
    with pytest.raises(ValueError, match="group"):
        market_hash_name_from_input(
            "https://steamcommunity.com/market/listings/730/G1807209A023004"
        )


def test_group_page() -> None:
    html = (FIXTURES / "group_page.html").read_text(encoding="utf-8")

    variants = parse_group_page(html, FETCHED_AT)

    assert set(variants) == {
        "AK-47 | Redline (Field-Tested)",
        "StatTrak™ AK-47 | Redline (Minimal Wear)",
    }
    ft = variants["AK-47 | Redline (Field-Tested)"]
    assert ft.item_type == "Classified Rifle"
    assert ft.collection == "The Phoenix Collection"
    assert ft.history is not None
    assert ft.history.currency == 3
    assert len(ft.history.points) == 4
    assert ft.history.points[0].resolution == "day"


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        '<script id="valve-ssr-data">not json</script>',
        '<script id="valve-ssr-data">'
        '{"renderContext": {"queryData": "{\\"queries\\": []}"}}</script>',
    ],
)
def test_group_page_rejects_unexpected_shape(html: str) -> None:
    with pytest.raises(SteamFormatError):
        parse_group_page(html, FETCHED_AT)


def test_inventory(load_fixture: Callable[[str], Any]) -> None:
    page = parse_inventory(load_fixture("inventory.json"))

    assert page.total == 3
    assert page.next_start is None
    by_name = {i.market_hash_name: i for i in page.items}
    assert set(by_name) == {
        "G3SG1 | Polar Camo (Minimal Wear)",
        "Kilowatt Case",
        "5 Year Veteran Coin",
    }
    assert by_name["Kilowatt Case"].bucket_group_id == "G18A8263004"
    assert by_name["Kilowatt Case"].marketable
    assert not by_name["5 Year Veteran Coin"].marketable
    assert all(i.amount == 1 for i in page.items)


def test_empty_and_paginated_inventory() -> None:
    assert parse_inventory({"success": 1, "total_inventory_count": 0}).items == []
    page = parse_inventory(
        {
            "success": 1,
            "total_inventory_count": 2500,
            "assets": [],
            "descriptions": [],
            "more_items": 1,
            "last_assetid": "123",
        }
    )
    assert page.next_start == "123"


def test_private_inventory_body_is_rejected() -> None:
    with pytest.raises(SteamFormatError):
        parse_inventory(None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("76561198000000042", ProfileRef(steam_id64="76561198000000042")),
        (
            "https://steamcommunity.com/profiles/76561198000000042/inventory/#730",
            ProfileRef(steam_id64="76561198000000042"),
        ),
        ("steamcommunity.com/id/someone/", ProfileRef(vanity="someone")),
    ],
)
def test_profile_input(text: str, expected: ProfileRef) -> None:
    assert parse_profile_input(text) == expected


@pytest.mark.parametrize("text", ["", "hello", "https://steamcommunity.com/profiles/123/"])
def test_profile_input_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        parse_profile_input(text)


PROFILE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<profile>
	<steamID64>76561198000000001</steamID64>
	<steamID><![CDATA[Test Player]]></steamID>
	<onlineState>offline</onlineState>
	<privacyState>{privacy}</privacyState>
	<visibilityState>3</visibilityState>
	<avatarIcon><![CDATA[https://avatars.fastly.steamstatic.com/abc.jpg]]></avatarIcon>
	<avatarMedium><![CDATA[https://avatars.fastly.steamstatic.com/abc_medium.jpg]]></avatarMedium>
	<customURL><![CDATA[{custom}]]></customURL>
</profile>"""


def test_profile_xml() -> None:
    profile = parse_profile_xml(PROFILE_XML.format(privacy="public", custom=""))

    assert profile.steam_id64 == "76561198000000001"
    assert profile.name == "Test Player"
    assert profile.avatar_url == "https://avatars.fastly.steamstatic.com/abc_medium.jpg"
    assert profile.profile_url == "https://steamcommunity.com/profiles/76561198000000001/"
    assert profile.privacy == "public"


def test_profile_xml_with_custom_url_and_private() -> None:
    profile = parse_profile_xml(PROFILE_XML.format(privacy="private", custom="tester"))

    assert profile.profile_url == "https://steamcommunity.com/id/tester/"
    assert profile.privacy == "private"


@pytest.mark.parametrize(
    "xml",
    [
        "not xml",
        "<response><error><![CDATA[The specified profile could not be found.]]></error></response>",
    ],
)
def test_profile_xml_errors(xml: str) -> None:
    with pytest.raises(SteamFormatError):
        parse_profile_xml(xml)
