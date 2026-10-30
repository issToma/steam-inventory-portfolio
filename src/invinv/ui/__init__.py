from pathlib import Path

from nicegui import app

from invinv.ui import alerts_page, item, market, portfolio, settings_page, watchlist

# Fonts and other files the pages load; bundled, so the app needs nothing from other sites.
STATIC = Path(__file__).parent / "static"


def register_pages() -> None:
    app.add_static_files("/static", STATIC)
    portfolio.register()
    watchlist.register()
    item.register()
    alerts_page.register()
    market.register()
    settings_page.register()
