from pathlib import Path

from nicegui import app

from invinv.ui import item, shell

# Fonts and other files the pages load; bundled, so the app needs nothing from other sites.
STATIC = Path(__file__).parent / "static"


def register_pages() -> None:
    app.add_static_files("/static", STATIC)
    item.register()
    # Last: its pages fall back to the ones above for paths it does not handle yet.
    shell.register()
