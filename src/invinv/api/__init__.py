from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from invinv import __version__
from invinv.core.http import SteamBusyError, SteamError
from invinv.core.steam import Exterior, ItemKind, Quality
from invinv.search import SearchQuery, search_local, search_steam

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/search")
async def search(
    q: str = "",
    appid: int | None = None,
    kind: ItemKind | None = None,
    quality: Quality | None = None,
    exterior: Exterior | None = None,
    source: Literal["steam", "local"] = "steam",
) -> list[dict[str, Any]]:
    """Search items. source=steam asks Steam about one game (`appid`, at most 10 results,
    ahead of background requests); source=local searches the catalog of items seen before,
    instantly, in one game or in every game when `appid` is not given."""
    if source == "steam" and appid is None:
        raise HTTPException(422, "appid is required to search Steam")
    query = SearchQuery(q, appid=appid, kind=kind, quality=quality, exterior=exterior)
    if source == "local":
        return [asdict(r) for r in await search_local(query)]
    try:
        results = await search_steam(query)
    except SteamBusyError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (SteamError, TimeoutError) as exc:
        raise HTTPException(502, f"Steam search failed: {exc}") from exc
    return [asdict(r) for r in results]
