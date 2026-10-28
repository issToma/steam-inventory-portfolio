from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException

from cs2market import __version__
from cs2market.core.http import SteamBusyError, SteamError
from cs2market.core.steam import Exterior, ItemKind, Quality
from cs2market.search import SearchQuery, search_local, search_steam

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/search")
async def search(
    q: str = "",
    kind: ItemKind | None = None,
    quality: Quality | None = None,
    exterior: Exterior | None = None,
    source: Literal["steam", "local"] = "steam",
) -> list[dict[str, Any]]:
    """Search items. source=steam asks Steam (at most 10 results, ahead of background
    requests); source=local searches the catalog of items seen before, instantly."""
    query = SearchQuery(q, kind=kind, quality=quality, exterior=exterior)
    if source == "local":
        return [asdict(r) for r in await search_local(query)]
    try:
        results = await search_steam(query)
    except SteamBusyError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (SteamError, TimeoutError) as exc:
        raise HTTPException(502, f"Steam search failed: {exc}") from exc
    return [asdict(r) for r in results]
