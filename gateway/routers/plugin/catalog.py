from fastapi import APIRouter, HTTPException, Request

from common.errors import PluginStoreError
from common.storage.plugin_catalog import list_plugins
from common.storage.builtin_plugins import list_builtin_plugins

from ...dependencies import ApiToken
from ...runtime import config
from ...services.auth import require_admin


router = APIRouter(prefix="/plugins")


@router.get("", summary="列出共享插件及用户状态")
async def registered_plugins(request: Request, token: ApiToken) -> dict:
    require_admin(request, token)
    try:
        return {
            "plugins": list_plugins(config.user_database_path),
            "builtinPlugins": list_builtin_plugins(config.user_database_path),
        }
    except PluginStoreError as error:
        raise HTTPException(status_code=503, detail="插件存储不可用") from error
