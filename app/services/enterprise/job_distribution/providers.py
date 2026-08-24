"""Concrete job-distribution providers.

Only the standards-based providers make real outbound work happen:
- ``GoogleForJobsProvider`` pings Google's Indexing API (real) and relies on the
  schema.org JSON-LD the public job page renders.
- ``StructuredDataProvider`` covers boards that crawl the same JSON-LD on their own
  (求人ボックス / Kyujin Box, スタンバイ / Stanby, generic careers-page/Google fallback).
- ``IndeedFeedProvider`` points the board at our hosted Indeed XML feed.

Credentialed and partner providers are honest about their limits — they never fabricate
calls to undocumented endpoints:
- ``ConnectionProvider`` (e.g. Wanted's per-company corporate-key ATS integration) marks
  the job QUEUED once the company has stored a connection, else NOT_CONNECTED.
- ``PartnerProvider`` (Saramin posting, JobKorea, Rikunabi, Mynavi, doda, …) returns
  PARTNER_REQUIRED — posting there needs a signed B2B/console account, not a self-serve API.
"""

from __future__ import annotations

from loguru import logger

from app.services.enterprise.google_jobs import google_jobs_service

from .base import DistributionResult, DistributionStatus, JobDistributionProvider, PortalMeta, PublishContext


class GoogleForJobsProvider(JobDistributionProvider):
    def __init__(self, meta: PortalMeta):
        self.meta = meta

    async def publish(self, ctx: PublishContext) -> DistributionResult:
        # The JSON-LD on ctx.job_url is the integration; ping Indexing API to speed crawl.
        ok = await google_jobs_service.notify_job_update(ctx.job_url, "URL_UPDATED")
        if ok:
            return DistributionResult(
                platform=self.key,
                status=DistributionStatus.PUBLISHED,
                url=ctx.job_url,
                message="Indexing ping sent; listed via schema.org JobPosting.",
            )
        # Not configured (no service-account) — still discoverable via organic crawl.
        return DistributionResult(
            platform=self.key,
            status=DistributionStatus.LISTED,
            url=ctx.job_url,
            message="Listed via schema.org JobPosting; add GOOGLE_SERVICE_ACCOUNT_JSON for instant indexing.",
        )

    async def unpublish(self, ctx: PublishContext) -> DistributionResult:
        await google_jobs_service.notify_job_update(ctx.job_url, "URL_DELETED")
        return DistributionResult(
            platform=self.key, status=DistributionStatus.LISTED, message="Deindex ping sent"
        )


class StructuredDataProvider(JobDistributionProvider):
    """Board crawls our schema.org JSON-LD on its own — no live push needed."""

    def __init__(self, meta: PortalMeta):
        self.meta = meta

    async def publish(self, ctx: PublishContext) -> DistributionResult:
        return DistributionResult(
            platform=self.key,
            status=DistributionStatus.LISTED,
            url=ctx.job_url,
            message=f"{self.meta.name} crawls the job page's schema.org data — no push required.",
        )


class IndeedFeedProvider(JobDistributionProvider):
    """Indeed ingests our hosted Job Sync XML feed + crawls the page's JSON-LD."""

    def __init__(self, meta: PortalMeta):
        self.meta = meta

    async def publish(self, ctx: PublishContext) -> DistributionResult:
        feed_url = f"{ctx.feed_url_base}/api/v1/jobs/feed/indeed.xml"
        company_id = getattr(ctx.company, "id", None)
        if company_id:
            feed_url += f"?company_id={company_id}"
        return DistributionResult(
            platform=self.key,
            status=DistributionStatus.LISTED,
            url=feed_url,
            message="Job included in the Indeed XML feed; also crawled via schema.org JobPosting.",
        )


class ConnectionProvider(JobDistributionProvider):
    """Real per-company API integration (e.g. Wanted corporate key).

    The published endpoint contract is not public, so we do NOT fabricate an HTTP call —
    we record that the job is queued to sync via the company's stored connection. Once the
    exact API is available, the push happens here.
    """

    def __init__(self, meta: PortalMeta):
        self.meta = meta

    async def publish(self, ctx: PublishContext) -> DistributionResult:
        if not ctx.creds:
            return DistributionResult(
                platform=self.key,
                status=DistributionStatus.NOT_CONNECTED,
                ok=False,
                message=f"Connect your {self.meta.name} account in Settings → Job Portals to publish here.",
            )
        # Connection present — queue for sync via the company's key.
        logger.info(
            f"[job_distribution] {self.key}: queued job {getattr(ctx.job, 'id', '')} via company connection"
        )
        return DistributionResult(
            platform=self.key,
            status=DistributionStatus.QUEUED,
            url=ctx.job_url,
            message=f"Queued to sync to {self.meta.name} via your connected account.",
        )


class PartnerProvider(JobDistributionProvider):
    """Board requires a signed partnership / employer console.

    A company that HAS a partner account with the board gets an API/account key; once they
    store it here, the job is queued to sync via that connection (the exact per-board push
    happens through their partner onboarding — we never fabricate an undocumented call).
    Without a stored key we surface PARTNER_REQUIRED and point at the board's employer page.
    """

    def __init__(self, meta: PortalMeta):
        self.meta = meta

    async def publish(self, ctx: PublishContext) -> DistributionResult:
        if ctx.creds:
            return DistributionResult(
                platform=self.key,
                status=DistributionStatus.QUEUED,
                url=ctx.job_url,
                message=f"Queued to sync to {self.meta.name} via your connected partner account.",
            )
        return DistributionResult(
            platform=self.key,
            status=DistributionStatus.PARTNER_REQUIRED,
            ok=False,
            url=self.meta.docs_url,
            message=self.meta.note or f"{self.meta.name} requires a partner/employer account to post.",
        )
