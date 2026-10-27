import logging

import uvicorn
from fastapi import FastAPI
from nicegui import app as nicegui_app
from nicegui import ui

from cs2market import __version__, api
from cs2market.runtime import runtime
from cs2market.settings import get_settings
from cs2market.ui import register_pages


def create_api() -> FastAPI:
    app = FastAPI(title="cs2market", version=__version__)
    app.include_router(api.router)
    return app


def create_app() -> FastAPI:
    app = create_api()
    register_pages()
    nicegui_app.on_startup(runtime.start)
    nicegui_app.on_shutdown(runtime.stop)
    ui.run_with(app, title="cs2market", storage_secret=get_settings().ui_storage_secret)
    return app


def run() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    uvicorn.run(create_app(), host=settings.host, port=settings.port)
