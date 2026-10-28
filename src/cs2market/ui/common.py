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


# Steam's name colours by rarity; ★ (knives and gloves) has its own.
RARITY_COLORS: dict[str, str] = {
    "Consumer Grade": "#b0c3d9",
    "Base Grade": "#b0c3d9",
    "Industrial Grade": "#5e98d9",
    "Mil-Spec Grade": "#4b69ff",
    "Mil-Spec": "#4b69ff",
    "High Grade": "#4b69ff",
    "Distinguished": "#4b69ff",
    "Restricted": "#8847ff",
    "Remarkable": "#8847ff",
    "Exceptional": "#8847ff",
    "Classified": "#d32ce6",
    "Exotic": "#d32ce6",
    "Superior": "#d32ce6",
    "Covert": "#eb4b4b",
    "Extraordinary": "#eb4b4b",
    "Master": "#eb4b4b",
    "Contraband": "#e4ae39",
}
STAR_COLOR = "#8650ac"

ICON_URL = "https://community.akamai.steamstatic.com/economy/image/{icon}/{size}"


def rarity_color(rarity: str | None, quality: str | None) -> str | None:
    if quality and "★" in quality:
        return STAR_COLOR
    return RARITY_COLORS.get(rarity or "")


def icon_url(icon: str | None, size: str = "96fx72f") -> str | None:
    return ICON_URL.format(icon=icon, size=size) if icon else None


def sparkline_points(values: list[float], width: int = 90, height: int = 24) -> str:
    """SVG polyline points for a small trend line; empty when there is nothing to draw."""
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    step = width / (len(values) - 1)
    return " ".join(
        f"{i * step:.1f},{height - (v - low) / span * (height - 2) - 1:.1f}"
        for i, v in enumerate(values)
    )


NAME_CELL_SLOT = (
    '<q-td :props="props">'
    '<div class="row items-center no-wrap" style="gap: 10px">'
    '<img v-if="props.row.icon" :src="props.row.icon" '
    'style="width: 48px; height: 36px; object-fit: contain" />'
    '<span :style="props.row.color ? {color: props.row.color} : {}">{{ props.value }}</span>'
    "</div></q-td>"
)
SPARK_CELL_SLOT = (
    '<q-td :props="props">'
    '<svg v-if="props.row.spark" width="90" height="24">'
    '<polyline :points="props.row.spark" fill="none" stroke-width="1.5" '
    ":stroke=\"props.row.spark_up ? '#3fb950' : '#f85149'\" />"
    "</svg></q-td>"
)
