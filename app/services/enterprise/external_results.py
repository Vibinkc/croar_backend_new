"""Results that come back from a third-party assessment tool.

Croar could send a candidate to Testlify or Jotform, and that was the end of it: the score
stayed in the provider, nobody was moved, and the round sat open until a human noticed. This
closes that loop.

A result arrives one of two ways, and both end in the same place:

* the provider POSTs it when the candidate finishes (Testlify has webhooks; so do most tools
  worth integrating), or
* a recruiter records it by hand, which is what you fall back on for a tool that cannot post
  anywhere, or before the backend has a public URL.

Recording a result writes an AssessmentAttempt exactly as Croar's own test does, so the
candidate's history reads the same whichever tool ran the round, and the pipeline's scoring
does not have to know the difference.
"""

from __future__ import annotations

import hmac
import re
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

from loguru import logger
from sqlalchemy import select

from app.core.settings import get_settings
from app.models.enterprise.assessment import AssessmentAttempt, AssessmentAutomation
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement

if TYPE_CHECKING:  # annotations only
    from fastapi import BackgroundTasks
    from sqlalchemy.ext.asyncio import AsyncSession

_settings = get_settings()

# What counts as a pass when the round does not say. Croar's own assessment path uses the same
# number, and the two must agree — a candidate should not pass an internal test and fail an
# external one on an identical score.
DEFAULT_PASS_SCORE = 60


def company_token(company_id: Any) -> str:
    """The company's webhook tag.

    HMAC rather than the raw id: this ends up pasted into a third-party dashboard and travels
    over the open internet, so anything reversible would let a stranger post results into a
    company's pipeline.
    """
    mac = hmac.new(_settings.secret_key.encode(), f"webhook:{company_id}".encode(), sha256)
    return mac.hexdigest()[:24]


def passing_score(automation: AssessmentAutomation) -> int:
    """The mark this round needs, read out of its own criteria.

    Rounds carry criteria as free text — "60% to pass" is what the job form writes — so the
    number is taken from there when it is there, rather than adding a field that would sit
    empty on every automation created before today.
    """
    match = re.search(r"(\d{1,3})\s*%?", automation.criteria or "")
    if match:
        value = int(match.group(1))
        if 0 < value <= 100:
            return value
    return DEFAULT_PASS_SCORE


async def find_application(
    session: AsyncSession, company_id: Any, email: str, job_id: Any | None = None
) -> CandidateApplication | None:
    """The application a result belongs to, from the candidate's email.

    Email is what these tools collect — Testlify asks for it and verifies it with a one-time
    code before the test starts — so it is the one identifier a result reliably carries.
    """
    email = (email or "").strip().lower()
    if not email:
        return None

    candidate = (
        await session.execute(
            select(Candidate).where(
                Candidate.email == email, Candidate.company_id == company_id, Candidate.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not candidate:
        return None

    stmt = select(CandidateApplication).where(
        CandidateApplication.candidate_id == candidate.id,
        CandidateApplication.company_id == company_id,
        CandidateApplication.deleted_at.is_(None),
    )
    if job_id:
        stmt = stmt.where(CandidateApplication.job_requirement_id == job_id)
    apps = (await session.execute(stmt)).scalars().all()
    if not apps:
        return None
    if len(apps) == 1:
        return apps[0]

    # One person can be on several jobs. Prefer the application whose current stage actually has
    # an external assessment waiting — that is the round the result is answering.
    for app in apps:
        auto = (
            (
                await session.execute(
                    select(AssessmentAutomation).where(
                        AssessmentAutomation.job_requirement_id == app.job_requirement_id,
                        AssessmentAutomation.stage_index == app.current_stage,
                        AssessmentAutomation.provider == "EXTERNAL",
                    )
                )
            )
            .scalars()
            .first()
        )
        if auto:
            return app
    return apps[0]


async def record_result(
    session: AsyncSession,
    application: CandidateApplication,
    score: float | None,
    *,
    provider: str = "External",
    status: str = "COMPLETED",
    raw: dict[str, Any] | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, Any]:
    """Store an external result and act on it: score the application, move if it passed."""
    stage = application.current_stage or 1

    automation = (
        (
            await session.execute(
                select(AssessmentAutomation).where(
                    AssessmentAutomation.job_requirement_id == application.job_requirement_id,
                    AssessmentAutomation.stage_index == stage,
                )
            )
        )
        .scalars()
        .first()
    )

    numeric = None
    if score is not None:
        try:
            numeric = round(float(score))
        except (TypeError, ValueError):
            numeric = None

    # One attempt per (application, round). A provider that posts twice — a retake, or a webhook
    # delivered again after a timeout — must correct the record rather than add a second one.
    attempt = None
    if automation:
        attempt = (
            (
                await session.execute(
                    select(AssessmentAttempt).where(
                        AssessmentAttempt.application_id == application.id,
                        AssessmentAttempt.automation_id == automation.id,
                    )
                )
            )
            .scalars()
            .first()
        )

    if attempt is None:
        attempt = AssessmentAttempt(
            automation_id=automation.id if automation else None,
            candidate_id=application.candidate_id,
            application_id=application.id,
            company_id=application.company_id,
        )
        session.add(attempt)

    attempt.score = numeric
    attempt.status = status
    attempt.completed_at = cast("Any", datetime.utcnow())
    # The provider's payload verbatim. When a score looks wrong, the only way to tell a bad
    # mapping from a bad test is to have kept what they actually sent.
    attempt.answers = {"provider": provider, "raw": raw or {}}
    await session.flush()

    moved = False
    passed = None
    if numeric is not None:
        application.ai_match_score = cast("Any", numeric)
        if automation:
            mark = passing_score(automation)
            passed = numeric >= mark
            if passed and automation.auto_move:
                job = (
                    await session.execute(
                        select(JobRequirement).where(JobRequirement.id == application.job_requirement_id)
                    )
                ).scalar_one_or_none()
                stages = cast("list[dict[str, Any]]", (job.workflow_stages if job else None) or [])
                if application.current_stage < len(stages):
                    application.current_stage += 1
                    moved = True
                    await session.flush()
                    from app.services.enterprise.automation_service import trigger_automations

                    await trigger_automations(
                        application.id, application.current_stage, session, background_tasks
                    )
                else:
                    logger.info(
                        f"external result: application {application.id} passed but is already at "
                        f"the last stage ({len(stages)})"
                    )

    await session.commit()
    return {
        "application_id": str(application.id),
        "score": numeric,
        "passing_score": passing_score(automation) if automation else DEFAULT_PASS_SCORE,
        "passed": passed,
        "moved_to_stage": application.current_stage if moved else None,
        "provider": provider,
    }
