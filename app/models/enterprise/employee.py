import uuid

from sqlalchemy import TIMESTAMP, Date, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from . import EnterpriseBase


class Department(EnterpriseBase):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    company = relationship("Company", backref="departments")


class Employee(EnterpriseBase):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    employee_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)  # e.g., EMP-1001

    # Names
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Contact
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    mobile: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Job Information
    designation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="Active")  # Active, Inactive, etc.
    employment_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # Full-time, Part-time, Contract, etc.
    hire_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    original_hire_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    probation_end_date: Mapped[Date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notice_period: Mapped[int | None] = mapped_column(Integer, nullable=True)  # in days
    about_yourself: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Government IDs
    pan_card_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    aadhar_card_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    passport_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Personal Information
    date_of_birth: Mapped[Date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    blood_group: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Contact Information
    address_line_1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line_2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str] = mapped_column(String(100), default="India")
    pincode: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Relationships
    company_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    department_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    reporting_to_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )

    # JSONB for complex structures
    dependents: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))
    educational_details: Mapped[dict] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb")
    )
    emergency_contacts: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))
    social_profiles: Mapped[dict] = mapped_column(
        JSONB, nullable=True, server_default=text("'{}'::jsonb")
    )  # LinkedIn, Twitter, Facebook
    payment_information: Mapped[dict] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb")
    )
    roles_responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=True)
    documents: Mapped[dict] = mapped_column(JSONB, nullable=True, server_default=text("'[]'::jsonb"))

    # Audit
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    company = relationship("Company", backref="employees")
    department = relationship("Department", backref="employees")
    reporting_to = relationship("Employee", remote_side=[id], backref="direct_reports")
    candidate_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True
    )
    candidate = relationship("Candidate")
