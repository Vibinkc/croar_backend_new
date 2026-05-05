import uuid

from sqlalchemy import JSON, UUID, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.models.shared.base import SharedBase


class AgentAction(SharedBase):
    __tablename__ = "agent_actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_type = Column(String, nullable=False)  # "recruitment", "onboarding"
    action_type = Column(String, nullable=False)  # "score_resume", "send_reminder"
    context = Column(JSON, nullable=True)  # Store variables like candidate_id, employee_id
    reasoning = Column(String, nullable=True)  # The LLM's thought process
    status = Column(String, default="completed")  # "completed", "failed", "pending_approval"
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ApprovalRequest(SharedBase):
    __tablename__ = "approval_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_type = Column(String, nullable=False)  # "offer_letter", "rejection", "salary_change"
    content = Column(JSON, nullable=False)  # The draft data
    status = Column(String, default="pending")  # "pending", "approved", "rejected"
    requested_by_agent = Column(String, nullable=False)
    approved_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    responded_at = Column(DateTime(timezone=True), nullable=True)
