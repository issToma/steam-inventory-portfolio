from decimal import Decimal

from cs2market.db import repo
from cs2market.db.session import SessionMaker
from cs2market.portfolio import load_portfolio


async def test_cache_is_reused_until_positions_change(sessions: SessionMaker) -> None:
    async with sessions() as session:
        holding = await repo.add_manual_holding(session, "Kilowatt Case", quantity=2)
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
