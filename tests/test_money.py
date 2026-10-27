from decimal import Decimal

import pytest

from cs2market.core.currency import parse_price_text
from cs2market.core.fees import buyer_price, seller_proceeds


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$32.06", "32.06"),
        ("28,64€", "28.64"),
        ("2582,16 руб.", "2582.16"),
        ("$1,234.56", "1234.56"),
        ("1.234,56€", "1234.56"),
        ("¥ 1,234", "1234"),
        ("R$ 10,5", "10.5"),
        ("1 234,56 ₴", "1234.56"),
    ],
)
def test_parse_price_text(text: str, expected: str) -> None:
    assert parse_price_text(text) == Decimal(expected)


def test_parse_price_text_without_number() -> None:
    with pytest.raises(ValueError):
        parse_price_text("N/A")


@pytest.mark.parametrize(
    ("seller", "buyer"),
    [(1, 3), (2, 4), (20, 23), (100, 115), (2490, 2863)],
)
def test_buyer_price(seller: int, buyer: int) -> None:
    assert buyer_price(seller) == buyer


@pytest.mark.parametrize("buyer", range(3, 3000, 7))
def test_seller_proceeds_is_the_largest_amount_that_fits(buyer: int) -> None:
    seller = seller_proceeds(buyer)

    assert buyer_price(seller) <= buyer
    assert buyer_price(seller + 1) > buyer


def test_seller_proceeds_below_minimum_price() -> None:
    assert seller_proceeds(2) == 0
