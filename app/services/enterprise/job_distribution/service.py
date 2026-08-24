"""Job-distribution registry + catalog.

Mirrors ``SourcingService``: a dict of key -> provider, plus a static catalog of portals
(with honest, research-backed metadata) that the UI renders. Direction is PUBLISH: send a
Croar job OUT to external boards.

Catalog reality (2026, publish direction), see the feature notes:
- Truly self-serve today: Google for Jobs (schema.org JSON-LD) + JP aggregators 求人ボックス
  / スタンバイ that crawl the same structured data.
- Feed-based: Indeed (hosted Indeed XML job feed; the employer registers the feed URL).
- Credentialed API: Wanted (KR) via the company's recruitment-solution corporate key.
- Partner/console only: Saramin posting, JobKorea, JobPlanet, Incruit, Rikunabi, Mynavi,
  doda, en転職, Wantedly. Labeled honestly; we never fake a post.
"""

from __future__ import annotations

from .base import (
    ConnectField,
    DistributionResult,
    DistributionStatus,
    IntegrationType,
    JobDistributionProvider,
    PortalMeta,
    PublishContext,
)
from .providers import (
    ConnectionProvider,
    GoogleForJobsProvider,
    IndeedFeedProvider,
    PartnerProvider,
    StructuredDataProvider,
)

# --- Portal catalog (ordered: recommended/self-serve first) --------------------------
_CATALOG: list[PortalMeta] = [
    # Global standards-based (real, self-serve)
    PortalMeta(
        key="google_jobs",
        name="Google for Jobs",
        country="GLOBAL",
        integration=IntegrationType.STRUCTURED,
        docs_url="https://developers.google.com/search/docs/appearance/structured-data/job-posting",
        note="Free. Listed via schema.org JobPosting on the job page; an Indexing API ping speeds up crawling. Works in Korea & Japan.",
    ),
    PortalMeta(
        key="indeed",
        name="Indeed",
        country="GLOBAL",
        integration=IntegrationType.FEED,
        docs_url="https://docs.indeed.com/job-sync-xml/xml-feed",
        note="Job is added to a hosted Indeed XML job feed (and crawled via schema.org). Register the feed URL with your Indeed employer account to activate. Reaches Japan via Indeed.",
    ),
    # Japan aggregators that crawl schema.org (real, organic, self-serve)
    PortalMeta(
        key="kyujinbox",
        name="Kyujin Box",
        country="JP",
        integration=IntegrationType.STRUCTURED,
        docs_url="https://xn--pckua2a7gp15o89zb.com/",
        note="Crawls the job page's schema.org data automatically — no push or account required.",
    ),
    PortalMeta(
        key="stanby",
        name="Stanby",
        country="JP",
        integration=IntegrationType.STRUCTURED,
        docs_url="https://jp.stanby.com/",
        note="Crawls the job page's schema.org data automatically — no push or account required.",
    ),
    # Korea — credentialed API (real capability, per-company key)
    PortalMeta(
        key="wanted",
        name="Wanted",
        country="KR",
        integration=IntegrationType.API,
        requires_credentials=True,
        docs_url="https://openapi.wanted.jobs/",
        note="Publish via your Wanted recruitment-solution corporate key (채용 솔루션 → 외부 연동 → ATS 연동). Requires a Wanted employer account.",
    ),
    # Korea — partner/console only
    PortalMeta(
        key="saramin",
        name="Saramin",
        country="KR",
        integration=IntegrationType.PARTNER,
        docs_url="https://oapi.saramin.co.kr/",
        note="Saramin's open API is job-search (read) only; posting a job requires a Saramin employer account.",
    ),
    PortalMeta(
        key="jobkorea",
        name="JobKorea",
        country="KR",
        integration=IntegrationType.PARTNER,
        docs_url="https://www.jobkorea.co.kr/service/api",
        note="No self-serve posting API; JobKorea's API is read-only and prioritized for public institutions.",
    ),
    PortalMeta(
        key="jobplanet",
        name="JobPlanet",
        country="KR",
        integration=IntegrationType.PARTNER,
        docs_url="https://www.jobplanet.co.kr/partners/landing/group_survey",
        note="Posting is available only via a JobPlanet B2B partnership (pm@jobplanet.com).",
    ),
    PortalMeta(
        key="incruit",
        name="Incruit",
        country="KR",
        integration=IntegrationType.PARTNER,
        docs_url="https://www.incruit.com/",
        note="No public API; posting requires an Incruit employer account.",
    ),
    # Japan — partner/console only
    PortalMeta(
        key="rikunabi",
        name="Rikunabi NEXT",
        country="JP",
        integration=IntegrationType.PARTNER,
        docs_url="https://next.rikunabi.com/",
        note="No public API. Reachable programmatically only via Indeed PLUS (Recruit-owned) or a Recruit sales contract.",
    ),
    PortalMeta(
        key="mynavi",
        name="Mynavi",
        country="JP",
        integration=IntegrationType.PARTNER,
        docs_url="https://saponet.mynavi.jp/",
        note="No public posting API; posting is done through Mynavi's employer console / sales contract.",
    ),
    PortalMeta(
        key="doda",
        name="doda",
        country="JP",
        integration=IntegrationType.PARTNER,
        docs_url="https://doda.jp/",
        note="No public API; posting is via Persol Career's recruiter console / sales contract.",
    ),
    PortalMeta(
        key="en_japan",
        name="en Japan",
        country="JP",
        integration=IntegrationType.PARTNER,
        docs_url="https://employment.en-japan.com/",
        note="No open posting API; an advertiser account can pipe applicants to an ATS via a self-issued key.",
    ),
    PortalMeta(
        key="wantedly",
        name="Wantedly",
        country="JP",
        integration=IntegrationType.PARTNER,
        docs_url="https://www.wantedly.com/",
        note="Public API is embed widgets only; posting is via the Wantedly company console.",
    ),
]


# Brand logo per portal, by domain (the UI falls back to an icon if it fails to load).
_LOGO_DOMAIN = {
    "google_jobs": "google.com",
    "indeed": "indeed.com",
    "kyujinbox": "kyujinbox.com",
    "stanby": "stanby.com",
    "wanted": "wanted.co.kr",
    "saramin": "saramin.co.kr",
    "jobkorea": "jobkorea.co.kr",
    "jobplanet": "jobplanet.co.kr",
    "incruit": "incruit.com",
    "rikunabi": "rikunabi.com",
    "mynavi": "mynavi.jp",
    "doda": "doda.jp",
    "en_japan": "en-japan.com",
    "wantedly": "wantedly.com",
}

# Per-portal connect fields — each board's OFFICIAL connection differs (a single key, an
# OAuth pair + source name, a service-account JSON, an IP + call link, …). Crawl-only boards
# (Kyujin Box, Stanby) need nothing. Google/Indeed work via crawl/feed even with no key, so
# their fields are optional (they only speed up / enable the API push).
_CONNECT_FIELDS: dict[str, list[ConnectField]] = {
    "google_jobs": [
        ConnectField(
            "service_account_json",
            "Google service-account JSON",
            "textarea",
            required=False,
            placeholder='{ "type": "service_account", ... }',
            help="Optional. A Google Cloud service account with the Indexing API enabled, added as an Owner in Search Console — speeds up indexing. Jobs are listed via schema.org even without it.",
        ),
        ConnectField(
            "property_url",
            "Search Console property URL",
            "url",
            required=False,
            placeholder="https://careers.yourcompany.com",
            help="The Search Console property where the service account is an Owner.",
        ),
    ],
    "indeed": [
        ConnectField(
            "client_id",
            "Indeed client ID",
            "text",
            required=False,
            help="From console.indeed.com after signing Indeed's Developer Agreement (Job Sync API).",
        ),
        ConnectField("client_secret", "Indeed client secret", "password", required=False),
        ConnectField(
            "source_name",
            "Source name",
            "text",
            required=False,
            help="The source name Indeed issued for your feed / Job Sync integration.",
        ),
    ],
    "wanted": [
        ConnectField(
            "corporate_key",
            "Wanted corporate key",
            "password",
            required=True,
            placeholder="WK-...",
            help="채용 솔루션 → 외부 연동 → ATS 연동. Requires a Wanted recruitment-solution account.",
        )
    ],
    "saramin": [
        ConnectField(
            "access_key",
            "Saramin access-key",
            "password",
            required=True,
            help="Issued after approval at oapi.saramin.co.kr. Note: the open API is read-only; posting needs a Saramin employer account.",
        )
    ],
    "jobkorea": [
        ConnectField(
            "server_ip",
            "Registered server IP",
            "text",
            required=True,
            placeholder="e.g. 203.0.113.10",
            help="JobKorea's API is IP-based — register your server IP to receive a call link.",
        ),
        ConnectField("call_link", "Issued call link", "url", required=False, placeholder="https://..."),
    ],
    "jobplanet": [
        ConnectField(
            "partner_id",
            "Partner ID",
            "text",
            required=True,
            help="Issued by JobPlanet under a B2B partnership (pm@jobplanet.com).",
        ),
        ConnectField("api_key", "API key", "password", required=True),
    ],
    "incruit": [
        ConnectField(
            "api_key",
            "Employer API key",
            "password",
            required=True,
            help="Provided by Incruit under an employer/partner agreement.",
        )
    ],
    "en_japan": [
        ConnectField(
            "en_login_id",
            "en転職 corporate login ID",
            "text",
            required=True,
            help="Your en転職 (エン転職) corporate-account login ID — used by the engage (エンゲージ) integration. en Japan has no public API key for posting.",
        ),
        ConnectField(
            "en_password",
            "Password",
            "password",
            required=True,
            help="Your en転職 corporate-account password.",
        ),
    ],
    "rikunabi": [
        ConnectField(
            "indeed_employer_account",
            "Indeed employer account (email)",
            "text",
            required=True,
            help="Rikunabi NEXT has no direct API — it is fed via Indeed PLUS (Recruit). Link your Indeed employer account; jobs distribute through Indeed PLUS.",
        )
    ],
    "mynavi": [
        ConnectField(
            "client_id",
            "AOL client ID",
            "text",
            required=True,
            help="From Mynavi's AOL (アクセスオンライン) system-integration settings. Partner integration.",
        ),
        ConnectField("client_secret", "AOL client secret", "password", required=True),
        ConnectField(
            "signature",
            "AOL signature",
            "password",
            required=False,
            help="Signature value from the AOL integration settings.",
        ),
    ],
    "doda": [
        ConnectField(
            "api_auth_key",
            "API認証キー (API authentication key)",
            "password",
            required=True,
            help="Issued by doda / Persol Career in the doda Assist console. Add one key per doda account window.",
        )
    ],
    "wantedly": [
        ConnectField(
            "api_key",
            "Wantedly Hire API key",
            "password",
            required=True,
            help="Wantedly Hire → settings → issue API key. Note: Wantedly job posts are published manually in the Wantedly admin; this key connects Wantedly Hire (applicant sync).",
        )
    ],
    # Crawl-only aggregators — no credentials; they index the job page's schema.org data.
    "kyujinbox": [],
    "stanby": [],
}

# Logos + connect fields onto each meta. requires_credentials is true only when a portal
# actually has connect fields (crawl-only boards stay "no setup").
for _m in _CATALOG:
    _m.connect_fields = _CONNECT_FIELDS.get(_m.key, [])
    _m.requires_credentials = bool(_m.connect_fields)
    _domain = _LOGO_DOMAIN.get(_m.key)
    if _domain:
        _m.logo = f"https://www.google.com/s2/favicons?domain={_domain}&sz=64"


def _provider_for(meta: PortalMeta) -> JobDistributionProvider:
    if meta.key == "google_jobs":
        return GoogleForJobsProvider(meta)
    if meta.integration == IntegrationType.FEED:
        return IndeedFeedProvider(meta)
    if meta.integration == IntegrationType.STRUCTURED:
        return StructuredDataProvider(meta)
    if meta.integration == IntegrationType.API:
        return ConnectionProvider(meta)
    return PartnerProvider(meta)


class JobDistributionService:
    def __init__(self) -> None:
        self.metas: dict[str, PortalMeta] = {m.key: m for m in _CATALOG}
        self.providers: dict[str, JobDistributionProvider] = {m.key: _provider_for(m) for m in _CATALOG}
        # Back-compat aliases for legacy platform strings stored in job_postings.platform.
        self._aliases = {
            "google jobs": "google_jobs",
            "linkedin": "google_jobs",  # legacy label had no real integration; keep discoverable
            "naukri": "google_jobs",
        }

    def catalog(self, country: str | None = None) -> list[PortalMeta]:
        metas = list(self.metas.values())
        if country:
            c = country.upper()
            metas = [m for m in metas if m.country == c or m.country == "GLOBAL"]
        return metas

    def resolve_key(self, platform: str) -> str:
        key = (platform or "").strip()
        if key in self.metas:
            return key
        return self._aliases.get(key.lower(), key.lower())

    def get(self, platform: str) -> JobDistributionProvider | None:
        return self.providers.get(self.resolve_key(platform))

    def meta(self, platform: str) -> PortalMeta | None:
        return self.metas.get(self.resolve_key(platform))

    async def publish(self, platform: str, ctx: PublishContext) -> DistributionResult:
        provider = self.get(platform)
        if not provider:
            return DistributionResult(
                platform=platform,
                status=DistributionStatus.ERROR,
                ok=False,
                message=f"Unknown job portal '{platform}'.",
            )
        try:
            return await provider.publish(ctx)
        except Exception as exc:  # never let one board break the publish loop
            return DistributionResult(
                platform=provider.key,
                status=DistributionStatus.ERROR,
                ok=False,
                message=f"Distribution error: {exc}",
            )


job_distribution_service = JobDistributionService()
