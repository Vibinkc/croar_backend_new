"""The integration catalogue — every third-party tool Croar can connect to, in one place.

Croar already had a connection mechanism, but only for job boards, reachable only from the job
portals screen. Assessment tools had no home at all, so the round builder asked the recruiter to
paste a raw invite URL on every single round.

This is the shared catalogue behind a single Integrations area: one list, grouped by what the
tool does, using the same connect-form descriptors and the same credential storage the portals
already use.

Each entry declares what connecting ACTUALLY enables today, in ``capabilities``. That field is
deliberately literal: an integration that stores a key but cannot yet call the provider's API
must say so, because a connection screen that implies more than it does is how a product ends up
reporting work it never did.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IntegrationCategory(StrEnum):
    ASSESSMENT = "assessment"
    JOB_BOARD = "job_board"
    MEETING = "meeting"
    EMAIL = "email"


@dataclass(frozen=True)
class IntegrationField:
    """One input on an integration's connect form."""

    name: str
    label: str
    type: str = "password"  # text | password | url
    required: bool = True
    help: str = ""


@dataclass(frozen=True)
class IntegrationMeta:
    key: str
    name: str
    category: IntegrationCategory
    summary: str
    fields: tuple[IntegrationField, ...] = ()
    docs_url: str | None = None
    # The provider's real brand mark — an SVG where one is published, otherwise the best icon
    # the site actually serves. Every URL here was checked rather than assumed. The UI falls
    # back to a branded monogram when a mark fails to load, so nothing is ever left blank.
    icon_url: str | None = None
    # Brand colour for that fallback monogram.
    brand_color: str = "#5B53E0"
    # What connecting this actually does today. Plain sentences, shown verbatim in the UI.
    capabilities: tuple[str, ...] = ()
    # What it does NOT do yet — stated so nobody assumes a two-way sync that is not there.
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category.value,
            "summary": self.summary,
            "docs_url": self.docs_url,
            "icon_url": self.icon_url,
            "brand_color": self.brand_color,
            "capabilities": list(self.capabilities),
            "limitations": list(self.limitations),
            "fields": [
                {"name": f.name, "label": f.label, "type": f.type, "required": f.required, "help": f.help}
                for f in self.fields
            ],
        }


# An assessment tool is connected once, with the invite link it issues for a test. A round then
# picks the tool by key instead of the recruiter re-pasting a URL every time.
_INVITE_FIELD = IntegrationField(
    name="invite_url",
    label="Invite link",
    type="url",
    required=True,
    help="The test/invite URL this tool gives you. Candidates who reach a round using this tool are sent here.",
)

_API_KEY_HELP = (
    "Stored for this company and never shown again. Croar does not call this provider's API yet, "
    "so the key is kept for the integration and the invite link is what is actually sent."
)

_ASSESSMENT_LIMITS = (
    "Scores stay in the provider — Croar records that the invite was sent, not the result.",
    "Croar does not create per-candidate tests through the provider's API yet.",
)

_ASSESSMENT_CAPS = (
    "Candidates reaching a round that uses this tool are emailed its invite link.",
    "The round still respects its trigger criteria, schedule and auto-move.",
)

INTEGRATIONS: tuple[IntegrationMeta, ...] = (
    IntegrationMeta(
        key="codility",
        icon_url="https://www.codility.com/wp-content/uploads/2026/05/favicon.svg",
        brand_color="#00B2A9",
        name="Codility",
        category=IntegrationCategory.ASSESSMENT,
        summary="Coding assessments and technical screening.",
        docs_url="https://codility.com/",
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="hackerrank",
        icon_url="https://cdn.jsdelivr.net/npm/simple-icons@13/icons/hackerrank.svg",
        brand_color="#00EA64",
        name="HackerRank",
        category=IntegrationCategory.ASSESSMENT,
        summary="Coding tests and technical interviews.",
        docs_url="https://www.hackerrank.com/work/",
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="testgorilla",
        icon_url="https://www.testgorilla.com/favicon.png",
        brand_color="#12B981",
        name="TestGorilla",
        category=IntegrationCategory.ASSESSMENT,
        summary="Skills and personality tests across roles.",
        docs_url="https://www.testgorilla.com/",
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="testlify",
        icon_url="https://testlify.com/favicon.ico",
        brand_color="#6D28D9",
        name="Testlify",
        category=IntegrationCategory.ASSESSMENT,
        summary="Skills assessments with a large test library.",
        docs_url="https://testlify.com/",
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="custom_assessment",
        name="Other assessment tool",
        category=IntegrationCategory.ASSESSMENT,
        summary="Any provider that gives you a candidate invite link.",
        fields=(
            IntegrationField(
                name="display_name", label="Tool name", type="text", help="Shown on rounds using it."
            ),
            _INVITE_FIELD,
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
)

_BY_KEY = {i.key: i for i in INTEGRATIONS}


def catalog(category: str | None = None) -> list[IntegrationMeta]:
    if category:
        return [i for i in INTEGRATIONS if i.category.value == category]
    return list(INTEGRATIONS)


def meta(key: str) -> IntegrationMeta | None:
    return _BY_KEY.get(key)
