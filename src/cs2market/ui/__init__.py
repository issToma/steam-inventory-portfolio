from cs2market.ui import item, watchlist


def register_pages() -> None:
    watchlist.register()
    item.register()
