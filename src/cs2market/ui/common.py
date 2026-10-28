from datetime import UTC, datetime, timedelta

from cs2market.i18n import tr, tr_n


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
    for one, many, size in (
        ("{n} day", "{n} days", 86400),
        ("{n} hour", "{n} hours", 3600),
        ("{n} minute", "{n} minutes", 60),
    ):
        if seconds >= size:
            return tr_n(round(seconds / size), one, many)
    return tr("less than a minute")


def time_ago(moment: datetime) -> str:
    delta = datetime.now(UTC) - moment
    return tr("just now") if delta < timedelta(minutes=1) else tr("{span} ago", span=_span(delta))


def time_until(moment: datetime) -> str:
    delta = moment - datetime.now(UTC)
    if delta < timedelta(minutes=1):
        return tr("any moment now")
    return tr("in {span}", span=_span(delta))
