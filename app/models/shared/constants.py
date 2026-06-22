from enum import StrEnum


class ModuleScope(StrEnum):
    platform = "platform"
    organization = "organization"
    jobs = "jobs"
    candidates = "candidates"
    assessments = "assessments"
    interviews = "interviews"
    onboarding = "onboarding"
    ai_training = "ai_training"
    surveys = "surveys"
    communications = "communications"
    employees = "employees"
    projects = "projects"
    tasks = "tasks"
    automation = "automation"
    billing = "billing"
    analytics = "analytics"
    payroll = "payroll"


class PermissionScope(StrEnum):
    own = "own"
    tenant = "tenant"
    system = "global"


class PermissionAction(StrEnum):
    create = "create"
    read = "read"
    update = "update"
    delete = "delete"
    moderate = "moderate"
    assign = "assign"
    publish = "publish"
    submit = "submit"
    review = "review"
    attempt = "attempt"
    generate = "generate"
    finalize = "finalize"
