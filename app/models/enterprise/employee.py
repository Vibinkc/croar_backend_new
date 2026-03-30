import uuid
from typing import Optional, List
from sqlalchemy import String, Integer, TIMESTAMP, func, ForeignKey, Text, Boolean, Numeric, Date
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text
from . import EnterpriseBase

class Department(EnterpriseBase):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    company_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())

    company = relationship("Company", backref="departments")

class Employee(EnterpriseBase):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()"))
    employee_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False) # e.g., EMP-1001
    
    # Names
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Contact
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mobile: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    # Job Information
    designation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="Active") # Active, Inactive, etc.
    employment_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True) # Full-time, Part-time, Contract, etc.
    hire_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    original_hire_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    probation_end_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    notice_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True) # in days
    about_yourself: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Government IDs
    pan_card_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    aadhar_card_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    passport_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    
    # Personal Information
    date_of_birth: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    gender: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    marital_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    blood_group: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    
    # Contact Information
    address_line_1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line_2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(100), default="India")
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    # Relationships
    company_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    department_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)
    reporting_to_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    
    # JSONB for complex structures
    dependents: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))
    educational_details: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))
    emergency_contacts: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))
    social_profiles: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'{}'::jsonb")) # LinkedIn, Twitter, Facebook
    payment_information: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))
    roles_responsibilities: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skills: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=True)
    documents: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))
    
    # Audit
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[Optional[TIMESTAMP]] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    company = relationship("Company", backref="employees")
    department = relationship("Department", backref="employees")
    reporting_to = relationship("Employee", remote_side=[id], backref="direct_reports")
    candidate_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True)
    candidate = relationship("Candidate")
