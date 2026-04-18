import json
import uuid
from typing import Any, cast

from openai import AsyncOpenAI
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.models.enterprise.employee import Employee
from app.models.enterprise.x360 import (
    AssignmentStatus,
    CycleStatus,
    QuestionType,
    RelationType,
    X360AssessmentAssignment,
    X360AssessmentCycle,
    X360AssessmentResponse,
    X360AssessmentTemplate,
    X360EmployeeRaterMap,
    X360TemplateQuestion,
)
from app.router.enterprise.communication import send_smtp_email

_settings = get_settings()
client = AsyncOpenAI(api_key=_settings.openai_api_key)


class X360Service:
    async def _get_ai_evaluation(self, responses: list[dict[str, object]]) -> dict[str, object] | None:
        """
        Takes a list of text responses and returns an AI-calculated score and summary.
        """
        if not responses:
            return None

        content = "\n".join([f"Q: {r['question']} | A: {r['answer']}" for r in responses])

        prompt = f"""
        Analyze the following 360-degree feedback responses for an employee.
        Provide:
        1. A 'Competency Score' out of 10 (where 10 is exceptional).
        2. A brief 'Executive Summary' (max 3 sentences) of the feedback.

        Return in JSON format: {{"score": float, "summary": str}}

        Feedback:
        {content}
        """

        try:
            response = await client.chat.completions.create(
                model=_settings.openai_model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            msg_content = response.choices[0].message.content
            if not msg_content:
                return None
            data = json.loads(msg_content)
            return cast("dict[str, object]", data)
        except Exception as e:
            print(f"AI Evaluation Error: {e}")
            return None

    async def start_cycle(
        self, db: AsyncSession, cycle_id: uuid.UUID, ratee_ids: list[uuid.UUID]
    ) -> X360AssessmentCycle | None:
        """
        Generates assignments for a cycle.
        For each ratee, creates:
        1. Self-assessment (Relation: SELF)
        2. Manager-assessment (Relation: MANAGER)
        3. Peer-assessments (Relation: PEER) - from RaterMap or random peers in same dept.
        """
        stmt = select(X360AssessmentCycle).where(X360AssessmentCycle.id == cycle_id)
        result = await db.execute(stmt)
        cycle = result.scalar_one_or_none()
        if not cycle:
            return None

        for ratee_id in ratee_ids:
            # 1. Fetch Ratee Info
            ratee_stmt = select(Employee).where(Employee.id == ratee_id)
            ratee_res = await db.execute(ratee_stmt)
            ratee = ratee_res.scalar_one_or_none()
            if not ratee:
                continue

            # A. Self Assignment
            await self._create_assignment(db, cycle_id, ratee_id, ratee_id, RelationType.SELF)

            # B. Manager Assignment
            if ratee.reporting_to_id:
                await self._create_assignment(
                    db, cycle_id, ratee_id, ratee.reporting_to_id, RelationType.MANAGER
                )

            # C. Peer/Other Assignments from RaterMap
            map_stmt = select(X360EmployeeRaterMap).where(X360EmployeeRaterMap.employee_id == ratee_id)
            map_res = await db.execute(map_stmt)
            for rater_map in map_res.scalars().all():
                await self._create_assignment(
                    db, cycle_id, ratee_id, rater_map.rater_employee_id, rater_map.relation
                )

        cycle.status = CycleStatus.ACTIVE
        await db.commit()

        # Notify raters (background task recommended in real skip)
        await self.notify_raters(db, cycle_id)

        return cycle

    async def _create_assignment(
        self,
        db: AsyncSession,
        cycle_id: uuid.UUID,
        ratee_id: uuid.UUID,
        rater_id: uuid.UUID,
        relation: RelationType,
    ) -> None:
        # Check if exists to avoid doubles
        chk = select(X360AssessmentAssignment).where(
            and_(
                X360AssessmentAssignment.cycle_id == cycle_id,
                X360AssessmentAssignment.ratee_id == ratee_id,
                X360AssessmentAssignment.rater_id == rater_id,
                X360AssessmentAssignment.relation == relation,
            )
        )
        res = await db.execute(chk)
        if res.scalar_one_or_none():
            return

        # Get company_id from cycle first
        stmt = select(X360AssessmentCycle.company_id).where(X360AssessmentCycle.id == cycle_id)
        res_comp = await db.execute(stmt)
        c_id = res_comp.scalar_one_or_none()

        assignment = X360AssessmentAssignment(
            cycle_id=cycle_id,
            ratee_id=ratee_id,
            rater_id=rater_id,
            relation=relation,
            status=AssignmentStatus.PENDING,
            company_id=c_id,
        )
        db.add(assignment)

    async def notify_raters(self, db: AsyncSession, cycle_id: uuid.UUID) -> None:
        stmt = (
            select(X360AssessmentAssignment)
            .where(
                and_(
                    X360AssessmentAssignment.cycle_id == cycle_id,
                    X360AssessmentAssignment.status == AssignmentStatus.PENDING,
                )
            )
            .options(
                selectinload(X360AssessmentAssignment.rater), selectinload(X360AssessmentAssignment.cycle)
            )
        )

        result = await db.execute(stmt)
        assignments = result.scalars().all()

        portal_url = f"{_settings.frontend_url}/enterprise/assessments-360/portal"

        for ass in assignments:
            if ass.rater and ass.rater.email:
                subject = f"360 Feedback Requested: {ass.cycle.name}"
                body = (
                    f"Hello {ass.rater.first_name},\n\n"
                    f"You have been requested to provide 360-degree feedback for a colleague.\n\n"
                    f"Please complete your assessment via the Feedback Portal:\n"
                    f"{portal_url}\n\n"
                    f"To access your tasks, use these credentials:\n"
                    f"Employee ID: {ass.rater.id}\n"
                    f"Work Email: {ass.rater.email}\n\n"
                    f"Best regards,\n"
                    f"HR Team"
                )
                # For real production, use background tasks
                send_smtp_email(ass.rater.email, subject, body)

    async def get_report(
        self, db: AsyncSession, employee_id: uuid.UUID, cycle_id: uuid.UUID
    ) -> dict[str, Any] | None:
        # 1. Fetch Cycle and Template with Questions
        cycle_stmt = (
            select(X360AssessmentCycle)
            .where(X360AssessmentCycle.id == cycle_id)
            .options(
                selectinload(X360AssessmentCycle.template)
                .selectinload(X360AssessmentTemplate.questions)
                .selectinload(X360TemplateQuestion.question)
            )
        )
        cycle_res = await db.execute(cycle_stmt)
        cycle = cycle_res.scalar_one_or_none()
        if not cycle or not cycle.template:
            return None
        template = cycle.template

        # 2. Fetch all completed assignments for this ratee in this cycle
        assign_stmt = (
            select(X360AssessmentAssignment)
            .where(
                and_(
                    X360AssessmentAssignment.cycle_id == cycle_id,
                    X360AssessmentAssignment.ratee_id == employee_id,
                    X360AssessmentAssignment.status == AssignmentStatus.COMPLETED,
                )
            )
            .options(
                selectinload(X360AssessmentAssignment.responses).selectinload(X360AssessmentResponse.question)
            )
        )

        assign_res = await db.execute(assign_stmt)
        assignments = assign_res.scalars().all()

        # 3. Aggregate results
        category_scores: dict[str, dict[RelationType, list[int]]] = {}  # category -> relation -> [values]
        text_responses: list[dict[str, Any]] = []

        total_cnt_res = await db.execute(
            select(func.count(X360AssessmentAssignment.id)).where(
                and_(
                    X360AssessmentAssignment.cycle_id == cycle_id,
                    X360AssessmentAssignment.ratee_id == employee_id,
                )
            )
        )
        total_cnt = total_cnt_res.scalar() or 0
        comp_cnt = len(assignments)

        for ass in assignments:
            rel = ass.relation
            for resp in ass.responses:
                cat = resp.question.category
                if resp.question.type == QuestionType.RATING and resp.answer_value:
                    if cat not in category_scores:
                        category_scores[cat] = {}
                    if rel not in category_scores[cat]:
                        category_scores[cat][rel] = []
                    category_scores[cat][rel].append(int(cast("Any", resp.answer_value)))
                elif resp.question.type == QuestionType.TEXT and resp.answer_text:
                    # Anonymize peer/report comments
                    rater_label = str(rel.value)
                    if rel in [RelationType.PEER, RelationType.REPORT]:
                        rater_label = f"Anonymous {rel.value}"

                    text_responses.append(
                        {
                            "category": cat,
                            "question": resp.question.text,
                            "relation": rater_label,
                            "answer": resp.answer_text,
                        }
                    )

        # 4. AI Evaluation of Text Feedback
        ai_evaluation = await self._get_ai_evaluation(text_responses)

        # 5. Format category scores
        formatted_scores = []
        for cat, rel_data in category_scores.items():
            self_scores = rel_data.get(RelationType.SELF, [])
            mgr_scores = rel_data.get(RelationType.MANAGER, [])
            peer_scores = rel_data.get(RelationType.PEER, [])

            def avg(lst: list[int]) -> float | None:
                return sum(lst) / len(lst) if lst else None

            all_vals = [v for lst in rel_data.values() for v in lst]

            formatted_scores.append(
                {
                    "category": cat,
                    "self_score": avg(self_scores),
                    "manager_score": avg(mgr_scores),
                    "peer_score": avg(peer_scores),
                    "overall_average": avg(all_vals),
                }
            )

        return {
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "template_name": template.name,
            "category_scores": formatted_scores,
            "text_responses": text_responses,
            "ai_evaluation": ai_evaluation,
            "total_assignments": total_cnt,
            "completed_assignments": comp_cnt,
        }


x360_service = X360Service()
