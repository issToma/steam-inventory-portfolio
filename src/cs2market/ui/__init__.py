from cs2market.ui import item, portfolio, watchlist


def register_pages() -> None:
    portfolio.register()
    watchlist.register()
    item.register()
