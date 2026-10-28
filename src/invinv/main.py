import logging

import uvicorn
from fastapi import FastAPI
from nicegui import app as nicegui_app
from nicegui import ui

from invinv import APP_NAME, __version__, api
from invinv.runtime import runtime
from invinv.settings import get_settings
from invinv.ui import register_pages


def create_api() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=__version__)
    app.include_router(api.router)
    return app


RECONNECT_TIMEOUT_SECONDS = 15.0


def create_app() -> FastAPI:
    app = create_api()
    register_pages()
    nicegui_app.on_startup(runtime.start)
    nicegui_app.on_shutdown(runtime.stop)
    ui.run_with(
        app,
        title=APP_NAME,
        storage_secret=get_settings().ui_storage_secret,
        # NiceGUI's default of 3 s also sets a 2 s ping timeout: a browser busy drawing a big
        # table for longer lost the connection and reloaded the page.
        reconnect_timeout=RECONNECT_TIMEOUT_SECONDS,
    )
    return app


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)
