"""Steam Community Market fees, in integer cents.

The buyer pays the seller's amount plus two fees, each rounded down: 5% to Steam and the
game publisher's share, 10% for most games. A game can set its own share, which Steam then
reports as `market_fee` in the item's inventory description. Each fee is at least one cent,
except a publisher share of zero.
"""

STEAM_FEE_RATE = 0.05
PUBLISHER_FEE_RATE = 0.10


def total_fee(seller_cents: int, publisher_fee: float = PUBLISHER_FEE_RATE) -> int:
    steam = max(int(seller_cents * STEAM_FEE_RATE), 1)
    publisher = max(int(seller_cents * publisher_fee), 1) if publisher_fee > 0 else 0
    return steam + publisher


def buyer_price(seller_cents: int, publisher_fee: float = PUBLISHER_FEE_RATE) -> int:
    """What a buyer pays for a listing that gives the seller `seller_cents`."""
    return seller_cents + total_fee(seller_cents, publisher_fee)


def seller_proceeds(buyer_cents: int, publisher_fee: float = PUBLISHER_FEE_RATE) -> int:
    """The most a seller receives from a sale at `buyer_cents` (0 if the price is too low)."""
    estimate = int(buyer_cents / (1 + STEAM_FEE_RATE + publisher_fee))
    seller = estimate + 2
    while seller > 0 and buyer_price(seller, publisher_fee) > buyer_cents:
        seller -= 1
    return seller
