"""Aggregated payroll/HR routers (ported from the standalone payroll module).

Each sub-router carries its own absolute prefix (``/api/v1/enterprise/...``), so
this aggregate is included directly on the app in ``app.main`` with no extra
prefix. The original module's ``auth``, ``base`` (health) and ``employee``
routers were intentionally dropped: Croar provides auth/login and the employees
API, which payroll now reuses.
"""

from fastapi import APIRouter

from .audit import router as audit_router
from .calendar import router as calendar_router
from .leave import router as leave_router
from .me import router as me_router
from .payroll import router as payroll_router
from .reports import router as reports_router
from .settings import router as settings_router
from .taxes import router as taxes_router
from .timesheets import router as timesheets_router

router = APIRouter()
router.include_router(payroll_router)
router.include_router(leave_router)
router.include_router(timesheets_router)
router.include_router(taxes_router)
router.include_router(calendar_router)
router.include_router(reports_router)
router.include_router(settings_router)
router.include_router(audit_router)
router.include_router(me_router)
