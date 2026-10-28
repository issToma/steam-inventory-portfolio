from cs2market.ui import item, portfolio, settings_page, watchlist


def register_pages() -> None:
    portfolio.register()
    watchlist.register()
    item.register()
    settings_page.register()
