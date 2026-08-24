"""Job-distribution provider framework.

Mirrors the sourcing-provider pattern (`services/enterprise/sourcing/base.py`): a small
ABC + a registry, so publishing a Croar job to an external board is a matter of picking
the right provider by key. Each provider declares *how* it reaches its board:

- ``STRUCTURED`` — the board indexes our public careers page via schema.org/JobPosting
  JSON-LD (Google for Jobs, 求人ボックス/Kyujin Box, スタンバイ/Stanby). No live push; the
  JSON-LD we render on the job page *is* the integration. We optionally ping the board
  (e.g. Google Indexing API) to speed up crawling.
- ``FEED`` — the board pulls an XML/JSON feed we host (Indeed XML job feed).
- ``API`` — a real per-company API push (e.g. Wanted's corporate-key ATS integration).
- ``PARTNER`` — posting requires a signed B2B/console account with the board and cannot
  be done self-serve today (Saramin posting, JobKorea, Rikunabi, Mynavi, doda …). We
  record the intent and surface it honestly instead of pretending to post.

Design goals: no fabricated HTTP calls to undocumented endpoints, honest per-portal
status, and a clean seam to add a portal later.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IntegrationType(StrEnum):
    STRUCTURED = "structured"  # schema.org crawl (Google for Jobs, aggregators)
    FEED = "feed"  # XML/JSON feed the board pulls (Indeed)
    API = "api"  # real per-company API push (Wanted corporate key)
    PARTNER = "partner"  # signed partnership / employer console required


class DistributionStatus(StrEnum):
    PUBLISHED = "PUBLISHED"  # live push / indexing ping succeeded
    LISTED = "LISTED"  # structured-data / feed: discoverable, board crawls on its own
    QUEUED = "QUEUED"  # connected via credentials; sync pending
    PARTNER_REQUIRED = "PARTNER_REQUIRED"  # needs a partnership/console — cannot self-serve
    NOT_CONNECTED = "NOT_CONNECTED"  # portal needs a connection the company hasn't made
    ERROR = "ERROR"


@dataclass
class ConnectField:
    """One input in a portal's connect form (portals differ: one key, an OAuth pair,
    a service-account JSON, an IP + call link, etc.)."""

    name: str  # stored key inside credentials
    label: str
    type: str = "password"  # text | password | textarea | url
    required: bool = True
    placeholder: str = ""
    help: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "placeholder": self.placeholder,
            "help": self.help,
        }


@dataclass
class PortalMeta:
    """Static description of a job board, surfaced to the UI catalog."""

    key: str  # stable id used in JobPosting.platform + API calls
    name: str  # display name (English)
    country: str  # "GLOBAL" | "KR" | "JP"
    integration: IntegrationType
    requires_credentials: bool = False
    docs_url: str | None = None
    note: str | None = None  # honest one-liner on how/whether it works today
    logo: str | None = None  # brand logo URL
    connect_fields: list[ConnectField] = field(default_factory=list)  # per-portal connect form

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "country": self.country,
            "integration": self.integration.value,
            "requires_credentials": self.requires_credentials,
            "docs_url": self.docs_url,
            "note": self.note,
            "logo": self.logo,
            "connect_fields": [f.as_dict() for f in self.connect_fields],
        }


@dataclass
class DistributionResult:
    """Outcome of publishing one job to one portal."""

    platform: str
    status: DistributionStatus
    ok: bool = True
    external_id: str | None = None  # remote job id, if the board returns one
    url: str | None = None  # public listing / feed / careers URL
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "status": self.status.value,
            "ok": self.ok,
            "external_id": self.external_id,
            "url": self.url,
            "message": self.message,
        }


@dataclass
class PublishContext:
    """Everything a provider needs to publish, assembled by the caller."""

    job: Any  # JobRequirement ORM instance
    company: Any | None  # Company ORM instance (name, logo, website)
    job_url: str  # public careers-page URL for this job
    feed_url_base: str  # backend base for hosted feeds
    creds: dict[str, Any] = field(default_factory=dict)  # per-company connection, secrets included


class JobDistributionProvider(ABC):
    """Publish a single job to a single external board."""

    meta: PortalMeta

    @property
    def key(self) -> str:
        return self.meta.key

    @abstractmethod
    async def publish(self, ctx: PublishContext) -> DistributionResult:
        """Distribute the job. Must never raise for expected conditions (missing creds,
        partner-gated) — return a DistributionResult with the right status instead."""
        raise NotImplementedError

    async def unpublish(self, ctx: PublishContext) -> DistributionResult:
        """Best-effort removal. Default: nothing to actively remove (crawl-based)."""
        return DistributionResult(platform=self.key, status=DistributionStatus.LISTED, message="Removed")
