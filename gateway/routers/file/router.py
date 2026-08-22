from fastapi import APIRouter

from . import operations


router = APIRouter(prefix="/api/v1", tags=["文件"])
router.include_router(operations.router)
