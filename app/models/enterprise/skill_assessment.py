"""Employee-facing skill assessments.

Reuses the existing `assessment_templates` engine (type APTITUDE / CODING / BOTH,
AI-generated `generated_questions`, `test_duration`) as the *definition* of a test,
and adds a thin assignment layer so an org can assign that test to its own
**employees** (as opposed to the candidate-facing `assessment_attempts`, which are
bound to a job application). One row = one employee's sitting of one template.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SkillAssessmentAssignment(Base):
    __tablename__ = "skill_assessment_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assessment_templates.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    answers: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aptitude_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coding_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, default="PENDING")  # PENDING, COMPLETED, EXPIRED

    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    template = relationship("AssessmentTemplate")
