"""Prove the result loop: a webhook lands, the score sticks, the candidate moves.

Uses the real application on the Appxcess account — Vibin KC on Software Engineer, stage 2,
the Jotform round — so what is exercised is the actual data, not a fixture.
"""

import asyncio
import sys

import httpx

sys.path.insert(0, r"c:\Users\vibin\OneDrive\Desktop\Croar-app\backend")

from sqlalchemy import desc, select

from app.core.database import db_manager
from app.models.enterprise.assessment import AssessmentAttempt, AssessmentAutomation
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement
from app.models.enterprise.user_role import EnterpriseUser
from app.services.enterprise.external_results import company_token, passing_score

BASE = "http://localhost:8000"
EMAIL = "vibi@appxcess.com"


async def snapshot(label: str) -> None:
    async with db_manager.sessionmaker() as s:
        u = (await s.execute(select(EnterpriseUser).where(EnterpriseUser.email == EMAIL))).scalar_one()
        cand = (
            await s.execute(
                select(Candidate).where(Candidate.email == EMAIL, Candidate.company_id == u.company_id)
            )
        ).scalar_one()
        app = (
            await s.execute(
                select(CandidateApplication)
                .where(
                    CandidateApplication.candidate_id == cand.id, CandidateApplication.deleted_at.is_(None)
                )
                .order_by(desc(CandidateApplication.applied_at))
                .limit(1)
            )
        ).scalar_one()
        att = (
            (
                await s.execute(
                    select(AssessmentAttempt)
                    .where(AssessmentAttempt.application_id == app.id)
                    .order_by(desc(AssessmentAttempt.started_at))
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        print(
            f"{label}: stage={app.current_stage} score={app.ai_match_score} "
            f"attempt={'none' if not att else f'{att.score} / {att.status}'}"
        )


async def main() -> None:
    async with db_manager.sessionmaker() as s:
        u = (await s.execute(select(EnterpriseUser).where(EnterpriseUser.email == EMAIL))).scalar_one()
        cid = u.company_id
        cand = (
            await s.execute(select(Candidate).where(Candidate.email == EMAIL, Candidate.company_id == cid))
        ).scalar_one()
        app = (
            await s.execute(
                select(CandidateApplication)
                .where(
                    CandidateApplication.candidate_id == cand.id, CandidateApplication.deleted_at.is_(None)
                )
                .order_by(desc(CandidateApplication.applied_at))
                .limit(1)
            )
        ).scalar_one()
        job = (
            await s.execute(select(JobRequirement).where(JobRequirement.id == app.job_requirement_id))
        ).scalar_one()
        auto = (
            (
                await s.execute(
                    select(AssessmentAutomation).where(
                        AssessmentAutomation.job_requirement_id == job.id,
                        AssessmentAutomation.stage_index == app.current_stage,
                    )
                )
            )
            .scalars()
            .first()
        )

        # Auto-move on, so the pass actually has somewhere to go.
        auto.auto_move = True
        await s.commit()
        await s.refresh(auto)

        stages = job.workflow_stages or []
        print(
            f"job: {job.title} | {len(stages)} stages | round {auto.stage_index} = {auto.external_provider_name}"
        )
        print(f"pass mark from criteria {auto.criteria!r}: {passing_score(auto)}")
        print(f"auto_move: {auto.auto_move}")
        token = company_token(cid)

    await snapshot("before")

    async with httpx.AsyncClient(timeout=40) as c:
        # 1. A failing score must not move anyone.
        r = await c.post(
            f"{BASE}/api/v1/public/assessments/result/{token}",
            json={"email": EMAIL, "score": 25, "provider": "Jotform", "status": "COMPLETED"},
        )
        print(f"\nwebhook (score 25) -> {r.status_code} {r.json()}")
        await snapshot("after fail")

        # 2. A passing score records and moves.
        r = await c.post(
            f"{BASE}/api/v1/public/assessments/result/{token}",
            json={"candidate_email": EMAIL, "total_score": 85, "provider": "Jotform"},
        )
        print(f"\nwebhook (score 85) -> {r.status_code} {r.json()}")
        await snapshot("after pass")

        # 3. An unknown candidate is ignored, not 500'd — providers retry on errors.
        r = await c.post(
            f"{BASE}/api/v1/public/assessments/result/{token}",
            json={"email": "nobody@example.com", "score": 90},
        )
        print(f"\nunknown candidate -> {r.status_code} {r.json()}")

        # 4. A wrong token is refused.
        r = await c.post(
            f"{BASE}/api/v1/public/assessments/result/{'0' * 24}", json={"email": EMAIL, "score": 90}
        )
        print(f"bad token         -> {r.status_code} {r.json()}")


if __name__ == "__main__":
    asyncio.run(main())
