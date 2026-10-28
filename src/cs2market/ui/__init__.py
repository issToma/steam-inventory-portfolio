from cs2market.ui import alerts_page, item, portfolio, settings_page, watchlist


def register_pages() -> None:
    portfolio.register()
    watchlist.register()
    item.register()
    alerts_page.register()
    settings_page.register()
