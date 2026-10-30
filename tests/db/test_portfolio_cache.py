from decimal import Decimal

from invinv.db import repo
from invinv.db.session import SessionMaker
from invinv.portfolio import load_portfolio, load_portfolios


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


async def test_every_game_is_valued_at_once_and_adds_up(sessions: SessionMaker) -> None:
    async with sessions() as session:
        await repo.add_manual_holding(session, "Kilowatt Case", quantity=2, appid=730)
        await repo.add_manual_holding(session, "Hoodie", quantity=1, appid=252490)
        await session.commit()

    portfolios = await load_portfolios(sessions)

    assert set(portfolios) == {None, 730, 252490}
    assert await load_portfolio(sessions, 252490) is portfolios[252490]
    assert len(portfolios[None].positions) == 2
    assert portfolios[None].value == portfolios[730].value + portfolios[252490].value
    assert (await load_portfolio(sessions, 570)).positions == []
