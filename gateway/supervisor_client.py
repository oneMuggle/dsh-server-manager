"""Compatibility imports for the Supervisor IPC client."""

from .services.supervisor import (
    SupervisorError,
    _request,
    assign_plugin,
    create_user,
    discover_plugin_versions,
    import_offline_plugin,
    import_online_plugin,
    install_upload,
    list_upload_directories,
)

__all__ = [
    "SupervisorError",
    "assign_plugin",
    "create_user",
    "discover_plugin_versions",
    "import_offline_plugin",
    "import_online_plugin",
    "install_upload",
    "list_upload_directories",
]
