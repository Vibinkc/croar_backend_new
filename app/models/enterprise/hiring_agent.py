import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.database import Base


class HiringAgent(Base):
    __tablename__ = "hiring_agents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True)

    full_name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="Recruiter")  # Admin, Recruiter, Interviewer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now(), server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
