import uuid
from typing import Any, Optional

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase


class Company(EnterpriseBase):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    logo_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    is_consultancy: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    # ----- Payroll: organisation profile (Settings → Organisation Profile) -----
    # Added on payroll-module integration. All optional; edited from the payroll
    # Settings screen and read onto payslips.
    currency: Mapped[str] = mapped_column(String(8), default="INR", server_default="INR")
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    country: Mapped[str] = mapped_column(String(80), default="India", server_default="India")
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tan: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # ----- Payroll: payslip + statutory configuration -----
    # JSON config blobs (branding/section toggles, statutory rate overrides) and
    # an optional uploaded .docx payslip template (+ wizard mapping). NULL falls
    # back to built-in defaults at render/compute time.
    payslip_settings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    statutory_settings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    payslip_doc_template: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payslip_doc_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payslip_doc_original: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payslip_doc_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    # Relationships
    parent: Mapped[Optional["Company"]] = relationship("Company", remote_side=[id], back_populates="partners")
    partners: Mapped[list["Company"]] = relationship(
        "Company", back_populates="parent", cascade="all, delete-orphan"
    )
