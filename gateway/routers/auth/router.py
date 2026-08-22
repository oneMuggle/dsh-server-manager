from fastapi import APIRouter

from . import registrations, sessions


router = APIRouter(prefix="/api/v1/auth", tags=["认证"])
router.include_router(registrations.router)
router.include_router(sessions.router)
