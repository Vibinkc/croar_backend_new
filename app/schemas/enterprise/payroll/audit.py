import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    actor_email: str | None = None
    action: str
    method: str
    path: str
    status_code: int
    created_at: datetime
