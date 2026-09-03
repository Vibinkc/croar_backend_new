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
    INTERVIEW = "interview"
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
    # A paragraph on what connecting actually does, shown on the integration's own page. Longer
    # and more specific than `summary`, which is the one-liner on the card.
    what_it_does: str = ""
    # Whether the connect form requires agreeing to the provider's terms. True for anything
    # where credentials or candidate data reach a third party.
    requires_consent: bool = True
    # "free"   — the provider's API is usable at no cost, and Croar verifies the key on connect.
    # "paid"   — an API exists but is gated behind a paid/enterprise plan.
    # "link"   — no usable API for us; the integration is the invite link only.
    api_tier: str = "link"
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
            "what_it_does": self.what_it_does,
            "requires_consent": self.requires_consent,
            "api_tier": self.api_tier,
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

FREE_CAPS = (
    "Croar checks the key with the provider when you connect, so a wrong key is caught here.",
    "Candidates reaching a round that uses this tool are emailed its link.",
    "The round still respects its trigger criteria, schedule and auto-move.",
)

FREE_LIMITS = ("Croar does not pull scores back automatically yet — results stay in the tool.",)

INTEGRATIONS: tuple[IntegrationMeta, ...] = (
    IntegrationMeta(
        key="jotform",
        name="Jotform",
        category=IntegrationCategory.ASSESSMENT,
        summary="Build a quiz or screening form. Free plan includes API access.",
        docs_url="https://api.jotform.com/docs/",
        icon_url="https://cdn.jotfor.ms/assets/img/favicons/apple-touch-icon-180x180.png",
        brand_color="#FF6100",
        api_tier="free",
        what_it_does=(
            "Jotform builds quizzes and screening forms with no coding. Croar verifies your API key with Jotform when you connect, so a wrong key is caught here rather than when a candidate never receives their test. Candidates reaching a round set to use Jotform are emailed the form link."
        ),
        fields=(
            IntegrationField(
                name="api_key",
                label="API key",
                help="Jotform → Settings → API → Create New Key. Works on the free Starter plan.",
            ),
            IntegrationField(
                name="invite_url",
                label="Form link",
                type="url",
                required=False,
                help="The form candidates should fill in. Leave blank to add it per round.",
            ),
        ),
        capabilities=FREE_CAPS,
        limitations=FREE_LIMITS,
    ),
    IntegrationMeta(
        key="google_forms",
        name="Google Forms",
        category=IntegrationCategory.ASSESSMENT,
        summary="Use a Google Forms quiz as the test. The API is free to use.",
        docs_url="https://developers.google.com/workspace/forms/api/limits",
        icon_url="https://cdn.jsdelivr.net/npm/simple-icons@13/icons/googleforms.svg",
        brand_color="#7248B9",
        api_tier="free",
        what_it_does=(
            "A Google Forms quiz can serve as the test. Its own scoring is free and unlimited, and candidates reaching a round set to use it are emailed the form link. Reading responses back into Croar needs Google credentials on the server and is not wired up yet."
        ),
        fields=(
            IntegrationField(
                name="invite_url",
                label="Form link",
                type="url",
                help="The published form URL. Its quiz scoring stays in Google Forms.",
            ),
        ),
        capabilities=(
            "Candidates reaching a round that uses this tool are emailed the form link.",
            "The round still respects its trigger criteria, schedule and auto-move.",
            "Google Forms' own quiz scoring is free and unlimited.",
        ),
        limitations=("Reading responses back needs Google credentials on the server; not wired up yet.",),
    ),
    IntegrationMeta(
        key="codility",
        api_tier="paid",
        icon_url="https://www.codility.com/wp-content/uploads/2026/05/favicon.svg",
        brand_color="#00B2A9",
        name="Codility",
        category=IntegrationCategory.ASSESSMENT,
        summary="Coding assessments and technical screening.",
        docs_url="https://codility.com/",
        what_it_does=(
            "Codility tests coding skill with real programming tasks. Connect it once and any round set to use Codility emails the candidate its invite link, instead of pasting a URL on every round. Requires an active Codility subscription."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="hackerrank",
        api_tier="paid",
        icon_url="https://cdn.jsdelivr.net/npm/simple-icons@13/icons/hackerrank.svg",
        brand_color="#00EA64",
        name="HackerRank",
        category=IntegrationCategory.ASSESSMENT,
        summary="Coding tests and technical interviews.",
        docs_url="https://www.hackerrank.com/work/",
        what_it_does=(
            "HackerRank runs coding tests and technical interviews. Connected here it becomes selectable on an assessment round, and candidates reaching that round are emailed the test link."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="testgorilla",
        api_tier="paid",
        icon_url="https://www.testgorilla.com/favicon.png",
        brand_color="#12B981",
        name="TestGorilla",
        category=IntegrationCategory.ASSESSMENT,
        summary="Skills and personality tests across roles.",
        docs_url="https://www.testgorilla.com/",
        what_it_does=(
            "TestGorilla covers skills, cognitive ability and personality across roles. Connect it to offer it as the tool behind an assessment round."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="testlify",
        api_tier="paid",
        icon_url="https://testlify.com/favicon.ico",
        brand_color="#6D28D9",
        name="Testlify",
        category=IntegrationCategory.ASSESSMENT,
        summary="Skills assessments with a large test library.",
        docs_url="https://testlify.com/",
        what_it_does=(
            "Testlify is an AI-assisted assessment platform with a large test library. Connected here it becomes a choice on any assessment round."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    # ── Assessment & testing ─────────────────────────────────────────────────────────────────
    IntegrationMeta(
        key="shl",
        name="SHL TalentCentral",
        category=IntegrationCategory.ASSESSMENT,
        summary="Enterprise assessment platform for cognitive and behavioural testing.",
        docs_url="https://www.shl.com/",
        icon_url="https://www.shl.com/favicon.ico",
        brand_color="#00A0DF",
        api_tier="paid",
        what_it_does=(
            "SHL TalentCentral administers cognitive, personality and skills assessments. Connect "
            "it once and any hiring round set to use SHL emails the candidate its invite link "
            "instead of asking you to paste a URL per round. Requires an SHL subscription."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="xobin",
        name="Xobin",
        category=IntegrationCategory.ASSESSMENT,
        summary="Skills-based, role-specific assessments with a validated question bank.",
        docs_url="https://xobin.com/",
        icon_url="https://xobin.com/favicon.ico",
        brand_color="#1A73E8",
        api_tier="paid",
        what_it_does=(
            "Xobin runs validated, role-based skill tests with proctoring. Connecting it makes "
            "Xobin selectable on a hiring round, and candidates reaching that round are emailed "
            "the test link."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="testtrick",
        name="TestTrick",
        category=IntegrationCategory.ASSESSMENT,
        summary="Pre-employment testing across technical and non-technical roles.",
        docs_url="https://testtrick.com/",
        icon_url="https://testtrick.com/favicon.ico",
        brand_color="#6D28D9",
        api_tier="paid",
        what_it_does=(
            "TestTrick evaluates candidates before an interview with role-specific tests. "
            "Connected here, it becomes a choice on any assessment round."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="invirtus",
        name="Invirtus",
        category=IntegrationCategory.ASSESSMENT,
        summary="AI deep-vetting for technical and communication skills.",
        docs_url="https://invirtus.ai/",
        icon_url="https://invirtus.ai/favicon.ico",
        brand_color="#0F766E",
        api_tier="paid",
        what_it_does=(
            "Invirtus vets candidates on both technical depth and communication. Connect it to "
            "offer it as the tool behind an assessment round."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    # ── Video & live interviewing ────────────────────────────────────────────────────────────
    IntegrationMeta(
        key="hireflix",
        name="Hireflix",
        category=IntegrationCategory.INTERVIEW,
        summary="One-way video interviews candidates record in their own time.",
        docs_url="https://hireflix.com/",
        icon_url="https://hireflix.com/favicon.ico",
        brand_color="#4F46E5",
        api_tier="paid",
        what_it_does=(
            "Hireflix collects recorded video answers so a first screen does not need a call. "
            "Connected here it becomes the tool behind an interview round, and candidates "
            "reaching that round are emailed the recording link."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="jobma",
        name="Jobma",
        category=IntegrationCategory.INTERVIEW,
        summary="Video interviewing with scheduling and scoring.",
        docs_url="https://www.jobma.com/",
        icon_url="https://www.jobma.com/favicon.ico",
        brand_color="#F97316",
        api_tier="paid",
        what_it_does=(
            "Jobma runs one-way and live video interviews. Connect it to use Jobma for an "
            "interview round instead of Croar's own interview flow."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="hirevire",
        name="Hirevire",
        category=IntegrationCategory.INTERVIEW,
        summary="Collects video, audio and file answers for screening.",
        docs_url="https://hirevire.com/",
        icon_url="https://hirevire.com/favicon.ico",
        brand_color="#E11D48",
        api_tier="paid",
        what_it_does=(
            "Hirevire asks candidates for short video, audio or file responses. Connected here "
            "it can stand in for a screening round."
        ),
        fields=(
            _INVITE_FIELD,
            IntegrationField(name="api_key", label="API key", required=False, help=_API_KEY_HELP),
        ),
        capabilities=_ASSESSMENT_CAPS,
        limitations=_ASSESSMENT_LIMITS,
    ),
    IntegrationMeta(
        key="screenify",
        name="Screenify",
        category=IntegrationCategory.INTERVIEW,
        summary="Automated AI interviews that screen applicants at volume.",
        docs_url="https://screenify.ai/",
        icon_url="https://screenify.ai/favicon.ico",
        brand_color="#7C3AED",
        api_tier="paid",
        what_it_does=(
            "Screenify runs AI-led interviews so a large applicant pool can be screened without "
            "a recruiter on each call. Connect it to use it for an interview round."
        ),
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
        what_it_does=(
            "Any provider that gives you a candidate invite link. Give it a name and paste the link, and it behaves like the built-in tools on a round."
        ),
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
