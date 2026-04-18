from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import SharedBase


class Backup(SharedBase):
    __tablename__ = "backup"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)
    db_name: Mapped[str] = mapped_column(index=True)
    backup_type: Mapped[str] = mapped_column(String(50))  # FULL, INCREMENTAL
    file_path: Mapped[str] = mapped_column()
    file_size: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(50), default="PENDING")  # PENDING, COMPLETED, FAILED
    error_message: Mapped[str | None] = mapped_column()
    metadata_info: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by: Mapped[str | None] = mapped_column(nullable=True)
