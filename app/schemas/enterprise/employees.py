from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, date

class DepartmentBase(BaseModel):
    name: str
    description: Optional[str] = None

class DepartmentCreate(DepartmentBase):
    company_id: UUID

class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

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
    middle_name: Optional[str] = None
    last_name: str
    email: EmailStr
    mobile: Optional[str] = None
    phone_number: Optional[str] = None
    designation: Optional[str] = None
    status: str = "Active"
    employment_type: Optional[str] = None
    hire_date: Optional[date] = None
    original_hire_date: Optional[date] = None
    probation_end_date: Optional[date] = None
    source: Optional[str] = None
    notice_period: Optional[int] = None
    about_yourself: Optional[str] = None
    pan_card_number: Optional[str] = None
    aadhar_card_number: Optional[str] = None
    passport_number: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    blood_group: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "India"
    pincode: Optional[str] = None
    department_id: Optional[UUID] = None
    reporting_to_id: Optional[UUID] = None
    dependents: Optional[List[Dict[str, Any]]] = []
    educational_details: Optional[List[Dict[str, Any]]] = []
    emergency_contacts: Optional[List[Dict[str, Any]]] = []
    social_profiles: Optional[Dict[str, str]] = {}
    payment_information: Optional[List[Dict[str, Any]]] = []
    roles_responsibilities: Optional[str] = None
    skills: Optional[List[str]] = []
    documents: Optional[List[Dict[str, Any]]] = []

    @field_validator('*', mode='before')
    @classmethod
    def empty_string_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v

class EmployeeCreate(EmployeeBase):
    company_id: UUID
    candidate_id: Optional[UUID] = None

class EmployeeUpdate(BaseModel):
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    mobile: Optional[str] = None
    phone_number: Optional[str] = None
    designation: Optional[str] = None
    status: Optional[str] = None
    employment_type: Optional[str] = None
    hire_date: Optional[date] = None
    original_hire_date: Optional[date] = None
    probation_end_date: Optional[date] = None
    source: Optional[str] = None
    notice_period: Optional[int] = None
    about_yourself: Optional[str] = None
    pan_card_number: Optional[str] = None
    aadhar_card_number: Optional[str] = None
    passport_number: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    blood_group: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pincode: Optional[str] = None
    department_id: Optional[UUID] = None
    reporting_to_id: Optional[UUID] = None
    dependents: Optional[List[Dict[str, Any]]] = None
    educational_details: Optional[List[Dict[str, Any]]] = None
    emergency_contacts: Optional[List[Dict[str, Any]]] = None
    social_profiles: Optional[Dict[str, str]] = None
    payment_information: Optional[List[Dict[str, Any]]] = None
    roles_responsibilities: Optional[str] = None
    skills: Optional[List[str]] = None
    documents: Optional[List[Dict[str, Any]]] = None

class EmployeeOut(EmployeeBase):
    id: UUID
    company_id: UUID
    candidate_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
