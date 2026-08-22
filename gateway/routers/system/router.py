from fastapi import APIRouter


router = APIRouter(prefix="/api/v1/system", tags=["系统"])


@router.get("/health", summary="健康检查")
async def health() -> dict[str, bool]:
    return {"ok": True}
