import asyncio
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from cs2market import __version__
from cs2market.core.http import SteamBusyError, SteamError
from cs2market.runtime import runtime

# A search waits for the shared Steam client: at most one background request plus the
# request interval. It gives up instead of waiting out a 429 cool-down.
SEARCH_MAX_WAIT = 60.0
SEARCH_TIMEOUT = 150.0

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/search")
async def search(q: str) -> list[dict[str, Any]]:
    """Search the Steam market (at most 10 results), ahead of background requests."""
    try:
        results = await asyncio.wait_for(
            runtime.market.search(q, priority=True, max_wait=SEARCH_MAX_WAIT),
            timeout=SEARCH_TIMEOUT,
        )
    except SteamBusyError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (SteamError, TimeoutError) as exc:
        raise HTTPException(502, f"Steam search failed: {exc}") from exc
    return [asdict(r) for r in results]
