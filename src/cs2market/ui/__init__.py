from nicegui import ui


def register_pages() -> None:
    @ui.page("/")
    def index() -> None:
        ui.label("cs2market").classes("text-h4")
        ui.label("Nothing here yet: the watchlist and charts come in the next milestone.")
