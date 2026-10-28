from nicegui import ui


def header() -> None:
    with ui.header().classes("items-center gap-6 bg-slate-800"):
        ui.link("cs2market", "/").classes("text-h6 text-white no-underline")
        ui.link("Portfolio", "/").classes("text-white no-underline")
        ui.link("Watchlist", "/watchlist").classes("text-white no-underline")


def fmt_usd(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.1%}"


def pct_color(value: float | None) -> str:
    if value is None or value == 0:
        return "text-grey-7"
    return "text-positive" if value > 0 else "text-negative"
