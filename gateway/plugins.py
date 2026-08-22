"""Compatibility imports for plugin repositories.

New code imports from the focused :mod:`common.storage` modules directly.
"""

from common.errors import PluginStoreError
from common.storage.assignments import (
    apply_plugin_defaults,
    enabled_aliases,
    enabled_package_links,
    get_user_plugin,
    mark_user_plugins_ready,
    remove_user_plugin,
    set_plugin_default,
    set_user_plugin,
)
from common.storage.plugin_catalog import (
    create_plugin_schema,
    get_plugin_version,
    list_plugin_versions,
    list_plugins,
    register_plugin_version,
)
from common.storage.plugin_jobs import (
    PLUGIN_ACTIONS,
    PLUGIN_JOB_KINDS,
    create_job,
    get_job,
    list_jobs,
    set_job_item_status,
    set_job_status,
    unfinished_job_ids,
)

__all__ = [
    "PLUGIN_ACTIONS",
    "PLUGIN_JOB_KINDS",
    "PluginStoreError",
    "apply_plugin_defaults",
    "create_job",
    "create_plugin_schema",
    "enabled_aliases",
    "enabled_package_links",
    "get_job",
    "get_plugin_version",
    "get_user_plugin",
    "list_jobs",
    "list_plugin_versions",
    "list_plugins",
    "mark_user_plugins_ready",
    "register_plugin_version",
    "remove_user_plugin",
    "set_job_item_status",
    "set_job_status",
    "set_plugin_default",
    "set_user_plugin",
    "unfinished_job_ids",
]
