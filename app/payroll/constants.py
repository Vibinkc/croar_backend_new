import uuid
from enum import Enum

# ----- Default App Setup -----
LOG_PATH = "data/logs"

# ----- Multi-tenancy -----
# Auth/RBAC is deferred (see spec §7). Until JWT + get_current_user is wired,
# every request is scoped to this single seeded default company so the module
# behaves multi-tenant by construction (every row carries company_id).
DEFAULT_COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# ----- Payroll calculation -----
# Spec §5: working_days default = a fixed 30 (calendar/business-day basis is a
# later, configurable decision). Monthly component amounts are defined against
# this basis; LOP days pro-rate against it.
DEFAULT_WORKING_DAYS = 30


# ----- Status values (stored as String, validated via these enums) -----
class PayrollCycleStatus(str, Enum):
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    APPROVED = "APPROVED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class PayslipStatus(str, Enum):
    PENDING = "PENDING"
    PAID = "PAID"


# ----- Money-line component types (earnings & deductions share this union) -----
class LineType(str, Enum):
    FIXED = "fixed"
    PERCENT = "percent"
    # A "balancing" earning that absorbs whatever CTC is left after the other
    # lines are resolved (i.e. period_CTC - sum(other earnings)). Lets a salary
    # template stay CTC-driven: change the CTC and every line rescales while the
    # balance line keeps the package summing to exactly the CTC. See
    # compute_payslip. Percent/fixed lines can also target the reserved code
    # "CTC" via `percent_of` to scale against the per-period cost-to-company.
    BALANCE = "balance"


# Reserved component code that `percent_of` / templates resolve to the
# per-period cost-to-company (annual CTC / 12 monthly, / 52 weekly).
CTC_CODE = "CTC"


class PayFrequency(str, Enum):
    MONTHLY = "MONTHLY"
    WEEKLY = "WEEKLY"
    # Hourly-paid staff: the structure carries an `hourly_rate` (not a CTC) and
    # gross = hours_worked * rate. Attendance is logged as hours on the timesheet
    # rather than day statuses. See payroll_service.compute_hourly_payslip.
    HOURLY = "HOURLY"


# ----- Timesheets / attendance ---------------------------------------------
# Weekly-off days used when deriving the working-day count for a period from the
# calendar (vs the fixed DEFAULT_WORKING_DAYS). Stored per company in
# Company.statutory_settings (see schema.settings WorkCalendarConfig); these are
# the fallback defaults. Day codes are the 3-letter uppercase weekday names.
DEFAULT_WEEKLY_OFFS = ["SAT", "SUN"]

# Python date.weekday() (Mon=0 .. Sun=6) -> the day code used in weekly_offs.
WEEKDAY_CODES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class TimesheetStatus(str, Enum):
    """A timesheet's lifecycle. Only APPROVED timesheets feed a payroll run."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class TimesheetMode(str, Enum):
    """How a timesheet's days are recorded — derived from the employee's active
    salary structure's pay_frequency (HOURLY -> hours; else day statuses)."""

    ATTENDANCE = "ATTENDANCE"
    HOURLY = "HOURLY"


class DayStatus(str, Enum):
    """Per-day attendance status (ATTENDANCE-mode timesheets).

    Paid days: PRESENT, PAID_LEAVE, WFH, HOLIDAY, WEEKLY_OFF (the last two are
    non-working days that never count as LOP). UNPAID_LEAVE is a full LOP day.
    HALF_DAY is half a worked day / half an *unpaid* day (0.5 LOP);
    HALF_DAY_PAID is half worked / half *paid* leave (no LOP) — the two differ
    only by whether the off-half is covered by a paid leave type. See
    timesheet_service.recompute_aggregates.
    """

    PRESENT = "PRESENT"
    PAID_LEAVE = "PAID_LEAVE"
    UNPAID_LEAVE = "UNPAID_LEAVE"
    HALF_DAY = "HALF_DAY"
    HALF_DAY_PAID = "HALF_DAY_PAID"
    WFH = "WFH"
    HOLIDAY = "HOLIDAY"
    WEEKLY_OFF = "WEEKLY_OFF"


# ----- Leave management -------------------------------------------------------
class AccrualMethod(str, Enum):
    """How a leave type's annual quota is credited to an employee's balance.

    ANNUAL  — the full quota is available up front (credited on balance creation).
    MONTHLY — the quota accrues 1/12 per month elapsed in the financial year.
    """

    ANNUAL = "ANNUAL"
    MONTHLY = "MONTHLY"


class LeaveStatus(str, Enum):
    """A leave request's lifecycle. Only APPROVED requests decrement a balance
    and stamp timesheet days (PAID_LEAVE / UNPAID_LEAVE)."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


# Standard India-market leave types, offered as one-click defaults so a new
# company starts with a sensible set instead of an empty list. Quotas follow the
# common private-sector norms (CL/SL ~12, EL ~15 accruing monthly with carry
# forward, statutory maternity 26 weeks ≈ 182 days). An admin can edit/extend
# these afterwards. Codes must stay unique per company (seeding skips existing).
DEFAULT_LEAVE_TYPES: list[dict] = [
    {
        "name": "Casual Leave",
        "code": "CL",
        "is_paid": True,
        "annual_quota": 12,
        "accrual": AccrualMethod.ANNUAL.value,
        "carry_forward_cap": None,
    },
    {
        "name": "Sick Leave",
        "code": "SL",
        "is_paid": True,
        "annual_quota": 12,
        "accrual": AccrualMethod.ANNUAL.value,
        "carry_forward_cap": None,
    },
    {
        "name": "Earned Leave",
        "code": "EL",
        "is_paid": True,
        "annual_quota": 15,
        "accrual": AccrualMethod.MONTHLY.value,
        "carry_forward_cap": 30,
    },
    {
        "name": "Maternity Leave",
        "code": "ML",
        "is_paid": True,
        "annual_quota": 182,
        "accrual": AccrualMethod.ANNUAL.value,
        "carry_forward_cap": None,
    },
    {
        "name": "Paternity Leave",
        "code": "PL",
        "is_paid": True,
        "annual_quota": 15,
        "accrual": AccrualMethod.ANNUAL.value,
        "carry_forward_cap": None,
    },
    {
        "name": "Bereavement Leave",
        "code": "BL",
        "is_paid": True,
        "annual_quota": 5,
        "accrual": AccrualMethod.ANNUAL.value,
        "carry_forward_cap": None,
    },
    {
        "name": "Loss of Pay",
        "code": "LOP",
        "is_paid": False,
        "annual_quota": 0,
        "accrual": AccrualMethod.ANNUAL.value,
        "carry_forward_cap": None,
    },
]


class TaxRegime(str, Enum):
    """India income-tax regime for TDS. NEW is the statutory default since
    FY 2023-24. Stored on the employee tax profile; consumed by a future TDS
    engine (not yet built — see statutory.py)."""

    OLD = "OLD"
    NEW = "NEW"


# Current financial year (Apr–Mar) used as the default for tax profiles and TDS
# challans. A literal (not computed) since Date.now() is unavailable in some
# contexts; bump when the FY rolls over.
DEFAULT_FINANCIAL_YEAR = "2026-27"


class AdjustmentKind(str, Enum):
    """A per-cycle, one-time pay line (not stored on the salary structure).

    EARNING adds to gross (bonus, arrears, ad-hoc pay); DEDUCTION subtracts
    (recovery, one-off deduction). Flat amounts: not LOP-prorated and outside
    the statutory wage base — see compute_payslip.
    """

    EARNING = "earning"
    DEDUCTION = "deduction"


# ----- Auth / RBAC (spec §7) -----------------------------------------------
# Permissions are fine-grained `payroll:*` capabilities. Routes require a
# specific permission; roles are bundles of permissions. The frontend hides
# nav/actions for capabilities the current user lacks, and the API returns 403.
class Permission(str, Enum):
    PAYROLL_READ = "payroll:read"  # view cycles, structures, payslips, employees
    PAYROLL_CONFIGURE = "payroll:configure"  # create/edit salary structures, employees, cycles
    PAYROLL_RUN = "payroll:run"  # run/recalculate a cycle
    PAYROLL_APPROVE = "payroll:approve"  # approve a processed cycle
    PAYROLL_PAY = "payroll:pay"  # mark a cycle paid
    PAYROLL_MANAGE = "payroll:manage"  # cancel/delete cycles
    USERS_MANAGE = "users:manage"  # create/list users (admin)
    # Employee self-service: read ONLY one's own records (timesheet, payslips…),
    # scoped by the user's linked employee_id. Deliberately distinct from
    # PAYROLL_READ (which is company-wide) so a self-service user can never reach
    # the admin /enterprise endpoints that return every employee's data.
    SELF_READ = "self:read"


class Role(str, Enum):
    ADMIN = "ADMIN"  # full access incl. user management
    HR = "HR"  # full payroll lifecycle, no user management
    VIEWER = "VIEWER"  # read-only (company-wide)
    EMPLOYEE = "EMPLOYEE"  # self-service: only their own linked records


# Role -> set of permissions it grants.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(p for p in Permission if p is not Permission.SELF_READ),
    Role.HR: frozenset(
        {
            Permission.PAYROLL_READ,
            Permission.PAYROLL_CONFIGURE,
            Permission.PAYROLL_RUN,
            Permission.PAYROLL_APPROVE,
            Permission.PAYROLL_PAY,
            Permission.PAYROLL_MANAGE,
        }
    ),
    Role.VIEWER: frozenset({Permission.PAYROLL_READ}),
    # EMPLOYEE gets ONLY self-scoped read — no payroll:* capability, so it can
    # never hit a company-wide endpoint even by direct URL.
    Role.EMPLOYEE: frozenset({Permission.SELF_READ}),
}


def permissions_for(role: str) -> frozenset[Permission]:
    """Resolve a (string) role to its permission set; unknown roles get none."""
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except (ValueError, KeyError):
        return frozenset()
