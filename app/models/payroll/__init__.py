"""Payroll/HR domain models (ported from the standalone payroll module).

Only the payroll-*specific* tables live here. The colliding ``users``,
``companies`` and ``employees`` tables from the original module were dropped on
integration; payroll now reuses Croar's :class:`EnterpriseUser`, :class:`Company`
and :class:`Employee` (all UUID-keyed and company-scoped, so the foreign keys
line up unchanged).

Importing this package registers every payroll table on Croar's shared
``Base.metadata`` (each model subclasses ``EnterpriseBase``), so Alembic
autogenerate and ``create_all`` pick them up.
"""

from .audit import AuditLog
from .calendar import Holiday
from .leave import LeaveBalance, LeaveRequest, LeaveType
from .payroll import PayrollAdjustment, PayrollCycle, Payslip, SalaryStructure, SalaryTemplate
from .taxes import EmployeeTaxProfile, TdsChallan
from .timesheets import Timesheet, TimesheetEntry

__all__ = [
    "AuditLog",
    "EmployeeTaxProfile",
    "Holiday",
    "LeaveBalance",
    "LeaveRequest",
    "LeaveType",
    "PayrollAdjustment",
    "PayrollCycle",
    "Payslip",
    "SalaryStructure",
    "SalaryTemplate",
    "TdsChallan",
    "Timesheet",
    "TimesheetEntry",
]
