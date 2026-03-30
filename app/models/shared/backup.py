from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON, String
from datetime import datetime
from typing import Optional, Any
from . import SharedBase

class Backup(SharedBase):
    __tablename__ = "backup"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)
    db_name: Mapped[str] = mapped_column(index=True)
    backup_type: Mapped[str] = mapped_column(String(50)) # FULL, INCREMENTAL
    file_path: Mapped[str] = mapped_column()
    file_size: Mapped[Optional[int]] = mapped_column()
    status: Mapped[str] = mapped_column(String(50), default="PENDING") # PENDING, COMPLETED, FAILED
    error_message: Mapped[Optional[str]] = mapped_column()
    metadata_info: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(nullable=True)
