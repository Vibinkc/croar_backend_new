from fastapi import APIRouter

from .admin import router as admin_router
from .system import router as system_router

router = APIRouter()
router.include_router(admin_router)
router.include_router(system_router)
