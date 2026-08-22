"""Supervisor-owned shared plugin runtime."""

from .errors import PluginOperationError
from .manager import SharedPluginManager
from .builtins import builtin_overlay_path, discover_builtin_catalog
from .registry import validate_package_name, validate_requested_version

__all__ = [
    "PluginOperationError",
    "SharedPluginManager",
    "builtin_overlay_path",
    "discover_builtin_catalog",
    "validate_package_name",
    "validate_requested_version",
]
