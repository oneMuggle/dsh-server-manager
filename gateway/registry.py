"""Compatibility imports for the user repository.

New code imports from :mod:`common.storage.users` directly.
"""

from common.storage.users import (
    SCHEMA_VERSION,
    RegistryError,
    add_user,
    get_user,
    initialize_registry,
    list_users,
    load_registry,
    replace_registry,
)

__all__ = [
    "SCHEMA_VERSION",
    "RegistryError",
    "add_user",
    "get_user",
    "initialize_registry",
    "list_users",
    "load_registry",
    "replace_registry",
]
