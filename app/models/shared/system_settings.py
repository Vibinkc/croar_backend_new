from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from .base import SharedBase


class SystemSettings(SharedBase):
    __tablename__ = "system_settings"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value_bool: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_str: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
