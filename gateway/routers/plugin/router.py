from fastapi import APIRouter

from . import builtins, catalog, imports, jobs, registry


router = APIRouter(prefix="/api/v1", tags=["插件"])
router.include_router(catalog.router)
router.include_router(builtins.router)
router.include_router(jobs.router)
router.include_router(imports.router)
router.include_router(registry.router)
