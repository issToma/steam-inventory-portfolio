from fastapi import APIRouter

from cs2market import __version__

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
