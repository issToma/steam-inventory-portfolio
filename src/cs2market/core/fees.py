"""Steam Community Market fees for CS2, in integer cents.

The buyer pays the seller's amount plus two fees, each at least one cent and rounded down:
5% to Steam and 10% to the game publisher.
"""

STEAM_FEE_RATE = 0.05
PUBLISHER_FEE_RATE = 0.10


def total_fee(seller_cents: int) -> int:
    steam = max(int(seller_cents * STEAM_FEE_RATE), 1)
    publisher = max(int(seller_cents * PUBLISHER_FEE_RATE), 1)
    return steam + publisher


def buyer_price(seller_cents: int) -> int:
    """What a buyer pays for a listing that gives the seller `seller_cents`."""
    return seller_cents + total_fee(seller_cents)


def seller_proceeds(buyer_cents: int) -> int:
    """The most a seller receives from a sale at `buyer_cents` (0 if the price is too low)."""
    estimate = int(buyer_cents / (1 + STEAM_FEE_RATE + PUBLISHER_FEE_RATE))
    seller = estimate + 2
    while seller > 0 and buyer_price(seller) > buyer_cents:
        seller -= 1
    return seller
