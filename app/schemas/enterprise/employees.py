from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, field_validator


class DepartmentBase(BaseModel):
    name: str
    description: str | None = None


class DepartmentCreate(DepartmentBase):
    company_id: UUID


class DepartmentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class DepartmentOut(DepartmentBase):
    id: UUID
    company_id: UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EmployeeBase(BaseModel):
    employee_id: str
    first_name: str
    middle_name: str | None = None
    last_name: str
    email: EmailStr
    mobile: str | None = None
    phone_number: str | None = None
    designation: str | None = None
    status: str = "Active"
    employment_type: str | None = None
    hire_date: date | None = None
    original_hire_date: date | None = None
    probation_end_date: date | None = None
    source: str | None = None
    notice_period: int | None = None
    about_yourself: str | None = None
    pan_card_number: str | None = None
    aadhar_card_number: str | None = None
    passport_number: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    marital_status: str | None = None
    blood_group: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    country: str = "India"
    pincode: str | None = None
    department_id: UUID | None = None
    reporting_to_id: UUID | None = None
    dependents: list[dict[str, Any]] | None = []
    educational_details: list[dict[str, Any]] | None = []
    emergency_contacts: list[dict[str, Any]] | None = []
    social_profiles: dict[str, str] | None = {}
    payment_information: list[dict[str, Any]] | None = []
    roles_responsibilities: str | None = None
    skills: list[str] | None = []
    documents: list[dict[str, Any]] | None = []

    @field_validator("*", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v


class EmployeeCreate(EmployeeBase):
    company_id: UUID
    candidate_id: UUID | None = None


class EmployeeUpdate(BaseModel):
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    mobile: str | None = None
    phone_number: str | None = None
    designation: str | None = None
    status: str | None = None
    employment_type: str | None = None
    hire_date: date | None = None
    original_hire_date: date | None = None
    probation_end_date: date | None = None
    source: str | None = None
    notice_period: int | None = None
    about_yourself: str | None = None
    pan_card_number: str | None = None
    aadhar_card_number: str | None = None
    passport_number: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    marital_status: str | None = None
    blood_group: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    department_id: UUID | None = None
    reporting_to_id: UUID | None = None
    dependents: list[dict[str, Any]] | None = None
    educational_details: list[dict[str, Any]] | None = None
    emergency_contacts: list[dict[str, Any]] | None = None
    social_profiles: dict[str, str] | None = None
    payment_information: list[dict[str, Any]] | None = None
    roles_responsibilities: str | None = None
    skills: list[str] | None = None
    documents: list[dict[str, Any]] | None = None

    @field_validator("*", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v


class EmployeeOut(EmployeeBase):
    id: UUID
    company_id: UUID
    candidate_id: UUID | None = None
    department: DepartmentOut | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
