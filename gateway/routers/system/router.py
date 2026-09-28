from fastapi import APIRouter

from .settings import router as settings_router


router = APIRouter(prefix="/api/v1/system", tags=["系统"])
router.include_router(settings_router)


@router.get("/health", summary="健康检查")
async def health() -> dict[str, bool]:
    return {"ok": True}
