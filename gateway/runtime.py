from __future__ import annotations

from pathlib import Path

from .config import Config


config = Config.from_environment()
SWAGGER_UI_STATIC_DIR = Path(__file__).parent / "static" / "swagger-ui"
