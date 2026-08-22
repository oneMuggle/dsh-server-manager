from fastapi import APIRouter

from . import accounts, providers


router = APIRouter(prefix="/api/v1", tags=["用户"])
router.include_router(accounts.router)
router.include_router(providers.router)
