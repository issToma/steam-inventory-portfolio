from invinv.ui import alerts_page, item, market, portfolio, settings_page, watchlist


def register_pages() -> None:
    portfolio.register()
    watchlist.register()
    item.register()
    alerts_page.register()
    market.register()
    settings_page.register()
