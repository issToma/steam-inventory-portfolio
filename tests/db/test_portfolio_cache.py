from decimal import Decimal

from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.portfolio import load_portfolio


async def test_cache_is_reused_until_positions_change(sessions: SessionMaker) -> None:
    async with sessions() as session:
        holding = await repo.add_manual_holding(session, "Kilowatt Case", quantity=2, appid=730)
        await session.commit()

    first = await load_portfolio(sessions)
    assert await load_portfolio(sessions) is first

    async with sessions() as session:
        await repo.update_holding(
            session, holding.id, quantity=5, buy_price_usd=Decimal("1"), bought_at=None
        )
        await session.commit()

    changed = await load_portfolio(sessions)
    assert changed is not first
    assert [p.quantity for p in changed.positions] == [5]
