from __future__ import annotations

from .config import Config
from .web import STATIC_ROOT


config = Config.from_environment()
SWAGGER_UI_STATIC_DIR = STATIC_ROOT / "swagger-ui"
