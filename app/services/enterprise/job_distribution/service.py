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
    AggregatorFeedProvider,
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
    # Global aggregators — free, and all fed by the same hosted XML feed. Each one wants
    # the publisher to register that feed URL once; the card says so rather than implying
    # Croar has already done it.
    PortalMeta(
        key="jooble",
        name="Jooble",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://jooble.org/ats",
        submit_url="https://jooble.org/ats",
        note="Job search engine covering 70+ countries. Publishes vacancies straight from an ATS XML feed — free in most countries.",
    ),
    PortalMeta(
        key="talent_com",
        name="Talent.com",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.talent.com/employers",
        submit_url="https://www.talent.com/employers",
        note="One of the largest candidate pools online, ~75M job seekers a month. Ingests employer XML feeds; organic listings are free.",
    ),
    PortalMeta(
        key="careerjet",
        name="Careerjet",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.careerjet.com/partners/",
        submit_url="https://www.careerjet.com/partners/",
        note="Search engine indexing job boards and career sites in 90 countries. Accepts publisher XML feeds free of charge.",
    ),
    PortalMeta(
        key="adzuna",
        name="Adzuna",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.adzuna.com/",
        submit_url="https://www.adzuna.com/",
        note="Job ad search engine listing every job, everywhere. Takes publisher feeds; organic listings are free.",
    ),
    PortalMeta(
        key="jora",
        name="Jora",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://au.jora.com/",
        submit_url="https://au.jora.com/",
        note="Aggregator across 36 countries with free job posting and XML feed support.",
    ),
    PortalMeta(
        key="whatjobs",
        name="WhatJobs",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.whatjobs.com/",
        submit_url="https://www.whatjobs.com/",
        note="100,000+ new jobs a week across every sector; free for job seekers. Ingests publisher feeds.",
    ),
    PortalMeta(
        key="ziprecruiter",
        name="ZipRecruiter",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.ziprecruiter.com/xml-import",
        submit_url="https://www.ziprecruiter.com/xml-import",
        note="Top-rated US hiring site. Has an XML import for employers and ATS feeds; posting tiers vary.",
    ),
    PortalMeta(
        key="jobrapido",
        name="Jobrapido",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.jobrapido.com/",
        submit_url="https://www.jobrapido.com/",
        note="Aggregates job offers across the web using its own matching and taxonomy stack.",
    ),
    PortalMeta(
        key="jobted",
        name="Jobted",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.jobted.com/",
        submit_url="https://www.jobted.com/",
        note="Aggregates vacancies from career sites, agencies and boards in 32 countries.",
    ),
    PortalMeta(
        key="jobsora",
        name="Jobsora",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://jobsora.com/",
        submit_url="https://jobsora.com/",
        note="Job search platform in 35+ countries; ingests publisher XML feeds.",
    ),
    PortalMeta(
        key="recruit_net",
        name="Recruit.net",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.recruit.net/",
        submit_url="https://www.recruit.net/",
        note="Global job search and HR data platform operating in 36 countries.",
    ),
    PortalMeta(
        key="grabjobs",
        name="GrabJobs",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://grabjobs.co/",
        submit_url="https://grabjobs.co/",
        note="End-to-end hiring automation platform with a job board across Asia-Pacific.",
    ),
    PortalMeta(
        key="remotive",
        name="Remotive",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://remotive.com/",
        submit_url="https://remotive.com/",
        note="One of the largest remote-work communities and remote job boards.",
    ),
    PortalMeta(
        key="jobleads",
        name="JobLeads",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.jobleads.com/",
        submit_url="https://www.jobleads.com/",
        note="Career platform for professionals, operating in 40 countries.",
    ),
    PortalMeta(
        key="bebee",
        name="beBee",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.bebee.com/",
        submit_url="https://www.bebee.com/",
        note="Professional social network built around content and affinity groups.",
    ),
    PortalMeta(
        key="resume_library",
        name="Resume-Library",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.resume-library.com/",
        submit_url="https://www.resume-library.com/",
        note="US job board with a large searchable candidate database.",
    ),
    PortalMeta(
        key="cv_library",
        name="CV-Library",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.cv-library.co.uk/",
        submit_url="https://www.cv-library.co.uk/",
        note="UK job board group carrying 200,000+ live jobs across its specialist sites.",
    ),
    PortalMeta(
        key="postjobfree",
        name="PostJobFree",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.postjobfree.com/",
        submit_url="https://www.postjobfree.com/",
        note="Free job posting and resume search.",
    ),
    PortalMeta(
        key="jobvertise",
        name="Jobvertise",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.jobvertise.com/",
        submit_url="https://www.jobvertise.com/",
        note="Free jobs and resume database — employers post and search at no cost.",
    ),
    PortalMeta(
        key="jobisite",
        name="Jobisite",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.jobisite.com/",
        submit_url="https://www.jobisite.com/",
        note="Free job posting site and aggregator across a range of industries.",
    ),
    PortalMeta(
        key="expertini",
        name="Expertini",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://expertini.com/",
        submit_url="https://expertini.com/",
        note="Global employment portal network bringing openings to a wide audience.",
    ),
    PortalMeta(
        key="euspert",
        name="Euspert",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://euspert.com/",
        submit_url="https://euspert.com/",
        note="Search engine indexing vacancies across Europe and the Americas.",
    ),
    PortalMeta(
        key="talent_job_seeker",
        name="Talent Job Seeker",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://talentjobseeker.com/",
        submit_url="https://talentjobseeker.com/",
        note="Skills-based matching board built on the Key Talent Indicator methodology.",
    ),
    PortalMeta(
        key="sleekjob",
        name="Sleekjob",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.sleekjob.com/",
        submit_url="https://www.sleekjob.com/",
        note="Search engine linking to jobs found on employer career portals and boards.",
    ),
    PortalMeta(
        key="tablerotrabajo",
        name="Tablerotrabajo",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.tablerotrabajo.com/",
        submit_url="https://www.tablerotrabajo.com/",
        note="Spanish-language search engine collecting jobs from a wide range of sources.",
    ),
    PortalMeta(
        key="jobesto",
        name="Jobesto",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://jobesto.com/",
        submit_url="https://jobesto.com/",
        note="Job postings for Poland and Germany.",
    ),
    PortalMeta(
        key="brenxor",
        name="Brenxor",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://brenxor.com/",
        submit_url="https://brenxor.com/",
        note="Remote tech job platform for software, design and data roles.",
    ),
    PortalMeta(
        key="drjob",
        name="Dr. Job",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://drjobpro.com/",
        submit_url="https://drjobpro.com/",
        note="AI-powered recruitment portal; posting jobs is free and unlimited.",
    ),
    PortalMeta(
        key="aviationcv",
        name="AviationCV",
        country="GLOBAL",
        integration=IntegrationType.AGGREGATOR,
        docs_url="https://www.aviationcv.com/",
        submit_url="https://www.aviationcv.com/",
        note="Aviation-specific board — pilots, cabin crew and maintenance engineers. Free posting.",
    ),
    # Listed for completeness — these want their own employer account, so they sit with
    # the partner boards rather than in the free channel.
    PortalMeta(
        key="linkedin_jobs",
        name="LinkedIn",
        country="GLOBAL",
        integration=IntegrationType.PARTNER,
        docs_url="https://business.linkedin.com/talent-solutions",
        note="Posting needs a LinkedIn employer account; there is no self-serve free feed for job posts.",
    ),
    PortalMeta(
        key="monster",
        name="Monster",
        country="GLOBAL",
        integration=IntegrationType.PARTNER,
        docs_url="https://hiring.monster.com/",
        note="Posting is through a Monster employer account — paid job slots, not a free feed.",
    ),
    PortalMeta(
        key="flexjobs",
        name="FlexJobs",
        country="GLOBAL",
        integration=IntegrationType.PARTNER,
        docs_url="https://www.flexjobs.com/employer",
        note="Remote and flexible-work board; employers post through a FlexJobs account.",
    ),
    PortalMeta(
        key="x_hiring",
        name="X Hiring",
        country="GLOBAL",
        integration=IntegrationType.PARTNER,
        docs_url="https://x.com/",
        note="X Hiring listings come from a verified organisation account, not an open feed.",
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
    "jooble": "jooble.org",
    "talent_com": "talent.com",
    "careerjet": "careerjet.com",
    "adzuna": "adzuna.com",
    "jora": "jora.com",
    "whatjobs": "whatjobs.com",
    "ziprecruiter": "ziprecruiter.com",
    "jobrapido": "jobrapido.com",
    "jobted": "jobted.com",
    "jobsora": "jobsora.com",
    "recruit_net": "recruit.net",
    "grabjobs": "grabjobs.co",
    "remotive": "remotive.com",
    "jobleads": "jobleads.com",
    "bebee": "bebee.com",
    "resume_library": "resume-library.com",
    "cv_library": "cv-library.co.uk",
    "postjobfree": "postjobfree.com",
    "jobvertise": "jobvertise.com",
    "jobisite": "jobisite.com",
    "expertini": "expertini.com",
    "euspert": "euspert.com",
    "talent_job_seeker": "talentjobseeker.com",
    "sleekjob": "sleekjob.com",
    "tablerotrabajo": "tablerotrabajo.com",
    "jobesto": "jobesto.com",
    "brenxor": "brenxor.com",
    "drjob": "drjobpro.com",
    "aviationcv": "aviationcv.com",
    "linkedin_jobs": "linkedin.com",
    "monster": "monster.com",
    "flexjobs": "flexjobs.com",
    "x_hiring": "x.com",
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
    if meta.integration == IntegrationType.AGGREGATOR:
        return AggregatorFeedProvider(meta)
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
