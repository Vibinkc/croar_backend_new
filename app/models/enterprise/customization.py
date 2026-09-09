"""Custom fields and tags — one system, five record types.

Manatal has a Customization screen per record type (candidates, jobs, departments, contacts and
guests, matches) and each one offers the same two things: custom fields and, for candidates,
tags. Five screens, one mechanism. Building five mechanisms would mean five migrations the next
time someone wants a date field.

    Definitions and values are separate tables. The alternative — a JSONB blob per record — makes
    "which candidates have Notice Period set" a full scan of every row, cannot enforce that a
    select field holds one of its own options, and leaves orphaned keys behind forever when a
    field is deleted. Two tables cost one join and answer all three.

Deleting a definition is a soft delete and the values stay. A field removed by mistake comes
back with its data; a field removed on purpose stops appearing everywhere at once. Hard-deleting
the values would make the first case unrecoverable to save space nobody is short of.

The entity is a plain string rather than an enum column, because adding a sixth record type
should be a constant in this file and not a migration on a Postgres type.
"""

import uuid
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# The record types a custom field can hang off. Each one is a real table in Croar — a field on a
# record type that does not exist would be a settings screen that configures nothing.
CUSTOM_FIELD_ENTITIES = ("candidate", "job", "department", "guest", "match")

# Deliberately short. Every type here has an obvious control, an obvious empty value and an
# obvious way to be searched. "Rich text" and "file" do not, and would each drag in a rendering
# and a storage decision that belongs to a bigger conversation.
CUSTOM_FIELD_TYPES = (
    "text",
    "textarea",
    "number",
    "date",
    "select",
    "multiselect",
    "checkbox",
    "url",
    "email",
    "phone",
)

# Only candidates and jobs get tags, matching Manatal. A tag is a filter people apply in bulk;
# on a record type you have five of, it is just a field.
TAG_ENTITIES = ("candidate", "job")


class CustomFieldDefinition(EnterpriseBase):
    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        # The key is what an API caller and an import file use, so it has to be stable and
        # unique within its record type. Two "notice_period" fields on candidates would make
        # every lookup ambiguous.
        UniqueConstraint("company_id", "entity", "key", name="uq_custom_field_company_entity_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    entity: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    field_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="text")
    # Only meaningful for select/multiselect. Stored as a list rather than a joined string so an
    # option containing a comma is not a bug waiting to happen.
    options: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    help_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Manatal separates "this field exists" from "ask for it when creating". A field that is
    # useful to record later is not always useful to demand up front.
    show_on_create: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)


class CustomFieldValue(EnterpriseBase):
    __tablename__ = "custom_field_values"
    __table_args__ = (
        # One value per field per record. Without this a partial write could leave two rows and
        # the reader would silently pick whichever came back first.
        UniqueConstraint("definition_id", "record_id", name="uq_custom_field_value_def_record"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Not a foreign key, on purpose: it points at one of five tables depending on the
    # definition's entity, and Postgres has no way to express that. The company scope on every
    # query is what keeps it honest.
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # JSONB rather than text so a number stays a number, a checkbox stays a boolean and a
    # multiselect stays a list — no parsing on the way out and no "true"/"True"/"1" question.
    value: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    definition = relationship("CustomFieldDefinition", lazy="selectin")


tag_assignments = Table(
    "tag_assignments",
    EnterpriseBase.metadata,
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    Column("record_id", UUID(as_uuid=True), primary_key=True),
    Column("assigned_at", TIMESTAMP, server_default=func.now()),
)


class Tag(EnterpriseBase):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("company_id", "entity", "name", name="uq_tags_company_entity_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    entity: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    colour: Mapped[str | None] = mapped_column(String(7), nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
