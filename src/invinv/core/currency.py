import re
from decimal import Decimal

USD = 1

# Steam's ECurrencyCode values, used by priceoverview's `currency` parameter and returned
# as `ecurrency` in price history.
STEAM_CURRENCIES: dict[int, str] = {
    1: "USD",
    2: "GBP",
    3: "EUR",
    4: "CHF",
    5: "RUB",
    6: "PLN",
    7: "BRL",
    8: "JPY",
    9: "NOK",
    10: "IDR",
    11: "MYR",
    12: "PHP",
    13: "SGD",
    14: "THB",
    15: "VND",
    16: "KRW",
    17: "TRY",
    18: "UAH",
    19: "MXN",
    20: "CAD",
    21: "AUD",
    22: "NZD",
    23: "CNY",
    24: "INR",
    25: "CLP",
    26: "PEN",
    27: "COP",
    28: "ZAR",
    29: "HKD",
    30: "TWD",
    31: "SAR",
    32: "AED",
    34: "ARS",
    35: "ILS",
    37: "KZT",
    38: "KWD",
    39: "QAR",
    40: "CRC",
    41: "UYU",
}


def currency_name(code: int) -> str:
    return STEAM_CURRENCIES.get(code, f"#{code}")


_NUMBER = re.compile(r"\d[\d.,\s]*")


def parse_price_text(text: str) -> Decimal:
    """Parse a localized Steam price such as "$32.06", "28,64€", "2582,16 руб." or "¥ 1,234".

    The last "." or "," is the decimal separator only when one or two digits follow it;
    every other separator is a thousands separator.
    """
    match = _NUMBER.search(text)
    if match is None:
        raise ValueError(f"no number in price text {text!r}")
    number = re.sub(r"\s", "", match.group()).rstrip(".,")
    last_sep = max(number.rfind("."), number.rfind(","))
    if last_sep != -1 and 1 <= len(number) - last_sep - 1 <= 2:
        integer, fraction = number[:last_sep], number[last_sep + 1 :]
    else:
        integer, fraction = number, ""
    integer = integer.replace(".", "").replace(",", "")
    return Decimal(f"{integer}.{fraction}" if fraction else integer)
