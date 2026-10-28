from collections.abc import Callable
from typing import Any, cast

import pytest

from cs2market.core.http import Params, SteamClient
from cs2market.core.steam import EXTERIOR_TAGS, QUALITY_TAGS, TYPE_TAGS, Quality, SteamMarket


class FakeClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[str, Params | None, dict[str, Any]]] = []

    async def get_json(self, path: str, params: Params | None = None, **kwargs: Any) -> Any:
        self.calls.append((path, params, kwargs))
        return self.payload


@pytest.mark.parametrize(
    ("quality", "tags"),
    [
        (None, []),
        ("normal", ["tag_normal", "tag_unusual"]),
        ("stattrak", ["tag_strange", "tag_unusual_strange"]),
        ("souvenir", ["tag_tournament"]),
    ],
)
async def test_search_filters_by_quality(
    load_fixture: Callable[[str], Any], quality: Quality | None, tags: list[str]
) -> None:
    client = FakeClient(load_fixture("search_render.json"))
    market = SteamMarket(cast(SteamClient, client))

    results = await market.search("breakout", quality=quality, priority=True)

    assert len(results) == 1
    path, params, kwargs = client.calls[0]
    assert path == "/market/search/render/"
    assert isinstance(params, list)
    assert ("query", "breakout") in params
    assert [v for k, v in params if k == "category_730_Quality[]"] == tags
    assert kwargs["priority"] is True


async def test_search_by_kind_and_exterior(load_fixture: Callable[[str], Any]) -> None:
    client = FakeClient(load_fixture("search_render.json"))
    market = SteamMarket(cast(SteamClient, client))

    await market.search("", kind="knife", quality="stattrak", exterior="fn")

    _, params, _ = client.calls[0]
    assert isinstance(params, list)
    assert ("category_730_Type[]", "tag_CSGO_Type_Knife") in params
    assert ("category_730_Exterior[]", "tag_WearCategory0") in params
    assert ("category_730_Quality[]", "tag_unusual_strange") in params


def test_filter_tags_exist_on_steam(load_fixture: Callable[[str], Any]) -> None:
    facets = load_fixture("appfilters.json")["facets"]

    def known(facet: str) -> set[str]:
        return {f"tag_{tag}" for tag in facets[f"730_{facet}"]["tags"]}

    assert set(TYPE_TAGS.values()) <= known("Type")
    assert {t for tags in QUALITY_TAGS.values() for t in tags} <= known("Quality")
    assert set(EXTERIOR_TAGS.values()) <= known("Exterior")
