"""The app shell: header and navigation are built once per visit, and moving between pages
swaps only the content beside them (ui.sub_pages), without reloading the page.

Pages move into the shell one by one; the others still load as pages of their own, which
the sub pages router opens with a reload.
"""

from collections.abc import Awaitable, Callable

from fastapi import Request
from nicegui import ui

from invinv.ui import portfolio
from invinv.ui.layout import page_frame

ROUTES: dict[str, Callable[[], Awaitable[None]]] = {
    "/": portfolio.content,
}
# The navigation entry each route belongs to.
_NAV_KEYS = {"/": "portfolio"}


def register() -> None:
    for path in ROUTES:
        ui.page(path)(_shell)


async def _shell(request: Request) -> None:
    with page_frame(_NAV_KEYS.get(request.url.path, ""), animate=False):
        ui.sub_pages(ROUTES).classes("w-full")
