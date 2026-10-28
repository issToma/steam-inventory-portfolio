from datetime import UTC, datetime, timedelta


def fmt_usd(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1%}"


def pct_color(value: float | None) -> str:
    if value is None or value == 0:
        return "text-grey-7"
    return "text-positive" if value > 0 else "text-negative"


def _span(delta: timedelta) -> str:
    seconds = abs(delta.total_seconds())
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = round(seconds / size)
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return "less than a minute"


def time_ago(moment: datetime) -> str:
    delta = datetime.now(UTC) - moment
    return "just now" if delta < timedelta(minutes=1) else f"{_span(delta)} ago"


def time_until(moment: datetime) -> str:
    delta = moment - datetime.now(UTC)
    return "any moment now" if delta < timedelta(minutes=1) else f"in {_span(delta)}"
