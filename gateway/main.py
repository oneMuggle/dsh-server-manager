"""ASGI entry point and Gateway application composition."""

from fastapi import FastAPI

from .application import SWAGGER_UI_STATIC_DIR, app, config, login, swagger_ui


def create_app() -> FastAPI:
    return app


__all__ = [
    "SWAGGER_UI_STATIC_DIR",
    "app",
    "config",
    "create_app",
    "login",
    "swagger_ui",
]
