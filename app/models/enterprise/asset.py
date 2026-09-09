"""Company assets, and who currently holds each one.

Two tables rather than an `assigned_to` column on the asset, because the question that gets
asked at exit is not "who has this laptop" but "who has had it, and did they give it back". A
column answers the first and destroys the second every time it changes.

    An asset can be held by at most one person at a time. That is enforced by there being at
    most one assignment row with no returned_at, checked on assign — not by a database
    constraint, because Postgres cannot express "unique among rows where a column is null"
    without a partial index, and a partial index here would make a legitimate re-issue after a
    return look like a violation depending on transaction ordering.

The status on the asset and the open assignment are kept in step by the router, deliberately in
that one place. Two sources of truth for "is this out?" is how you end up chasing a laptop that
the system says is on a shelf.
"""

import uuid

from sqlalchemy import TIMESTAMP, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import text

from .base import EnterpriseBase

# available → nobody has it. assigned → somebody does. The last three are why an asset can be
# absent from circulation without anybody holding it, which a boolean could not express.
ASSET_STATUSES = ("available", "assigned", "in_repair", "retired", "lost")

# Who owns the thing, which decides who is out of pocket when it does not come back.
ASSET_OWNERSHIP = ("company", "leased", "employee")

# Deliberately a short list. Categories exist to answer "how many laptops do we have", and a
# free-text field answers that question wrong the moment someone types "Laptop " with a space.
ASSET_CATEGORIES = (
    "laptop",
    "desktop",
    "monitor",
    "phone",
    "tablet",
    "peripheral",
    "furniture",
    "access_card",
    "software_licence",
    "vehicle",
    "other",
)

# The state the thing came back in. Recorded because "returned" alone lets a smashed screen and
# a pristine machine settle identically.
RETURN_CONDITIONS = ("good", "damaged", "unusable", "not_returned")


class Asset(EnterpriseBase):
    __tablename__ = "assets"
    __table_args__ = (
        # Per company: two companies will both have an "IT-001", and a global constraint would
        # let whichever registered first block the other.
        UniqueConstraint("company_id", "asset_tag", name="uq_assets_company_tag"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )

    # What is written on the sticker. The thing people actually say out loud when they are
    # looking for it, so it is required and unique rather than a nice-to-have.
    asset_tag: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False, server_default="other", index=True)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Kept separate from serial_number because a licence key is a credential: it is the thing
    # you must be careful about showing, and the serial is not.
    licence_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    ownership: Mapped[str] = mapped_column(String(20), nullable=False, server_default="company")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="available", index=True)

    purchased_on: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)
    purchase_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    assignments = relationship(
        "AssetAssignment",
        back_populates="asset",
        order_by="AssetAssignment.assigned_at.desc()",
        lazy="selectin",
    )


class AssetAssignment(EnterpriseBase):
    """One period during which one person held one asset.

    A row with no `returned_at` is the asset being out right now. That is the whole state
    machine, and it is why the history survives: issuing the same laptop to four people over
    three years leaves four rows, not one column overwritten four times.
    """

    __tablename__ = "asset_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("uuid_generate_v4()")
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )

    assigned_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, nullable=False, server_default=func.now())
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # When it is expected back. Null for "indefinitely", which is the normal case for a laptop
    # and not the same as "no date was set by mistake" — the UI says which.
    due_back_on: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True)

    returned_at: Mapped[TIMESTAMP | None] = mapped_column(TIMESTAMP, nullable=True, index=True)
    returned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    return_condition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[TIMESTAMP] = mapped_column(TIMESTAMP, default=func.now(), server_default=func.now())
    updated_at: Mapped[TIMESTAMP] = mapped_column(
        TIMESTAMP, default=func.now(), server_default=func.now(), onupdate=func.now()
    )

    asset = relationship("Asset", back_populates="assignments", lazy="selectin")
    employee = relationship("Employee", lazy="selectin")
