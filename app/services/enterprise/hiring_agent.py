import asyncio
import json
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING, Any, cast

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models.enterprise.candidate import ApplicationStatus, Candidate, CandidateApplication
from app.models.enterprise.job import JobRequirement

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

_settings = get_settings()


class HiringAgentService:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(api_key=_settings.openai_api_key)

    async def generate_automated_workflow(self, job_title: str, job_description: str) -> list[dict[str, Any]]:
        """
        Uses AI to suggest a complete automated workflow with policies for a given job.
        """
        prompt = f"""
        Design a 5-stage automated hiring workflow for the role: "{job_title}".
        Job Description Summary: {job_description[:500]}

        For each stage, provide:
        1. Stage Name (e.g., Screening, Technical Assessment, Culture Fit)
        2. Agent Policy:
           - auto_pilot: (boolean) Can the AI move candidates automatically?
           - time_limit_hours: (integer) Max time for this stage.
           - evaluation_criteria: (object) What to check? (min_score, required_skills, etc.)
           - screening_questions: (array) Questions for the candidate if this is a screening stage.

        Return a JSON array of stages. Each stage must follow this structure:
        {{
          "id": "string", (Must be sequential starting from "1", e.g., "1", "2", "3", "4", "5")
          "name": "string",
          "order": number, (Must match the id, e.g., 1, 2, 3...)
          "agent_policy": {{
            "auto_pilot": boolean,
            "time_limit_hours": number,
            "evaluation_criteria": {{
              "min_score": number,
              "required_skills": ["skill1", "skill2"],
              "check_notice_period": boolean,
              "check_salary": boolean
            }},
            "screening_questions": ["question 1", "question 2"],
            "automated_actions": {{
              "on_pass": "MOVE_TO_NEXT",
              "on_fail": "AUTO_REJECT",
              "send_email": true
            }}
          }}
        }}
        """

        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a specialized AI Recruiting Architect. Output valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = str(response.choices[0].message.content)
            data: dict[str, list[dict[str, Any]]] = json.loads(content)
            stages = data.get("stages", [])
            for i, stage in enumerate(stages):
                stage["id"] = str(i + 1)
                stage["order"] = i + 1
            return stages
        except Exception as e:
            print(f"Error generating AI workflow: {e}")
            return []

    async def evaluate_application_logic(
        self,
        application: CandidateApplication,
        candidate: Candidate,
        job: JobRequirement,
        stage_policy: dict[str, Any],
    ) -> dict[str, Any]:
        """
        The core engine. Evaluates the candidate against a specific stage policy.
        """
        await asyncio.sleep(0)  # async kept for awaiting callers (part of the async pipeline)
        feedback = cast("dict[str, Any]", application.ai_feedback or {})
        log = cast("list[dict[str, Any]]", feedback.get("agent_log", []))
        now = datetime.now().isoformat()

        criteria = cast("dict[str, Any]", stage_policy.get("evaluation_criteria", {}))

        decision: dict[str, Any] = {"action": "STAY", "reason": "Awaiting further data or manual review."}

        rejection_reasons: list[str] = []

        required_skills = cast("list[str] | None", criteria.get("required_skills"))
        if required_skills and candidate.skills:
            matching_skills = [
                s for s in required_skills if s.lower() in [cs.lower() for cs in candidate.skills]
            ]
            match_percent = (len(matching_skills) / len(required_skills)) * 100 if required_skills else 0
            min_skill_match = float(criteria.get("min_skill_match", 50.0))
            if match_percent < min_skill_match:
                rejection_reasons.append(
                    f"Insufficient skill match: {int(match_percent)}% (Required: {int(min_skill_match)}%)"
                )

        if criteria.get("check_notice_period") and job.notice_period_max is not None:
            if candidate.notice_period is not None and candidate.notice_period > job.notice_period_max:
                rejection_reasons.append(
                    f"Notice period of {candidate.notice_period} days "
                    f"exceeds the job limit of {job.notice_period_max} days."
                )

        if criteria.get("check_salary") and job.salary_max is not None:
            if candidate.expected_salary is not None and candidate.expected_salary > job.salary_max:
                rejection_reasons.append(
                    f"Expected salary of {candidate.expected_salary} "
                    f"exceeds the job budget max of {job.salary_max}."
                )

        if rejection_reasons:
            decision = {
                "action": "AUTO_REJECT",
                "reason": "FAILED_LOGIC_CHECKS",
                "details": rejection_reasons,
            }
            log.append(
                {
                    "time": now,
                    "event": "AGENT_DECISION",
                    "status": "REJECTED",
                    "detail": "Failed logical validation checks.",
                    "reasons": rejection_reasons,
                }
            )
        elif application.ai_match_score and application.ai_match_score >= float(
            criteria.get("min_score", 60.0)
        ):
            min_score = float(criteria.get("min_score", 60.0))
            decision = {
                "action": "MOVE_TO_NEXT",
                "reason": "PASSED_EVALUATION",
                "details": [
                    f"Scored {application.ai_match_score} which meets the threshold of {int(min_score)}."
                ],
            }
            log.append(
                {
                    "time": now,
                    "event": "AGENT_DECISION",
                    "status": "PASSED",
                    "detail": f"Application meets all criteria for {stage_policy.get('name')}.",
                }
            )

        return {"decision": decision, "updated_log": log}

    async def _send_agent_email(self, to_email: str, subject: str, body: str) -> tuple[bool, str]:
        """Private helper to send emails from the agent."""
        await asyncio.sleep(0)  # async kept: scheduled via BackgroundTasks alongside async work
        try:
            msg = MIMEMultipart()
            msg["From"] = str(_settings.mailer_sender_email)
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))

            with smtplib.SMTP(str(_settings.smtp_address), int(_settings.smtp_port)) as server:
                server.starttls()
                if _settings.smtp_username and _settings.smtp_password:
                    server.login(str(_settings.smtp_username), str(_settings.smtp_password))
                server.send_message(msg)
            return True, ""
        except Exception as e:
            return False, str(e)

    async def process_application(
        self, application_id: str, session: AsyncSession, background_tasks: object
    ) -> dict[str, Any]:
        """
        The main autonomous processing loop for a single application.
        """
        app = await session.get(CandidateApplication, application_id)
        if not app:
            return {"error": "Application not found"}

        job = await session.get(JobRequirement, app.job_requirement_id)
        candidate = await session.get(Candidate, app.candidate_id)

        if not job or not candidate or not job.workflow_stages:
            return {"error": "Missing required data"}

        current_stage_idx = app.current_stage - 1
        workflow_stages = cast("list[dict[str, Any]]", job.workflow_stages)
        if current_stage_idx >= len(workflow_stages):
            return {"status": "Already at final stage"}

        stage_config = workflow_stages[current_stage_idx]
        feedback = cast("dict[str, Any]", app.ai_feedback or {})
        if feedback.get("agent_status") == "AWAITING_SCREENING":
            return {"status": "Waiting for candidate response"}

        evaluation = await self.evaluate_application_logic(app, candidate, job, stage_config)
        decision = cast("dict[str, Any]", evaluation["decision"])
        app.ai_feedback = {**feedback, "agent_log": evaluation["updated_log"]}

        if decision["action"] == "MOVE_TO_NEXT":
            app.current_stage += 1
            await session.commit()
            return await self.process_application(application_id, session, background_tasks)

        bt = cast("BackgroundTasks", background_tasks)

        if decision["action"] == "STAY" and stage_config.get("screening_questions"):
            questions = cast("list[str]", stage_config["screening_questions"])
            q_text = "<br>".join([f"{i + 1}. {q}" for i, q in enumerate(questions)])

            subject = f"[REF:{app.id}] Screening: Following up on your application for {job.title}"
            body = (
                f"Hi {candidate.full_name},<br><br>To proceed with your "
                f"application for <b>{job.title}</b>, please answer the "
                f"following questions:<br><br>{q_text}<br><br>"
                "Simply reply to this email with your answers.<br><br>"
                "Best regards,<br>Autonomous Hiring Agent"
            )

            bt.add_task(self._send_agent_email, str(candidate.email), subject, body)

            app.ai_feedback = {**feedback, "agent_status": "AWAITING_SCREENING"}
            await session.commit()
            return {"status": "Sent screening questions"}

        if decision["action"] == "AUTO_REJECT":
            rejection_msg = await self.get_rejection_explanation(
                str(candidate.full_name), str(job.title), cast("list[str]", decision.get("details", []))
            )
            subject = f"[REF:{app.id}] Update regarding your application for {job.title}"
            body = f"Hi {candidate.full_name},<br><br>{rejection_msg}<br><br>Thank you for your time."

            bt.add_task(self._send_agent_email, str(candidate.email), subject, body)

            stmt = select(ApplicationStatus).where(ApplicationStatus.name == "Rejected")
            res = await session.execute(stmt)
            status_obj = res.scalar_one_or_none()
            if status_obj:
                app.status_id = status_obj.id

            app.ai_feedback = {**feedback, "agent_status": "REJECTED"}
            await session.commit()
            return {"status": "Candidate rejected autonomously"}

        await session.commit()
        return {"status": "Decision: No change"}

    async def process_inbound_email(
        self, from_email: str, subject: str, body: str, session: AsyncSession, background_tasks: object
    ) -> dict[str, Any]:
        """
        Receives an email, finds the application via REF tag, extracts data, and resumes the agent.
        """
        match = re.search(r"\[REF:([0-9a-fA-F-]{36})\]", subject)
        app_id = match.group(1) if match else None

        if not app_id:
            stmt = (
                select(CandidateApplication)
                .join(Candidate)
                .where(Candidate.email == from_email)
                .order_by(CandidateApplication.created_at.desc())
            )
            res = await session.execute(stmt)
            app_found = res.scalars().first()
            if app_found:
                app_id = str(app_found.id)

        if not app_id:
            return {"error": "Could not identify application"}

        app = await session.get(CandidateApplication, app_id)
        if not app:
            return {"error": "Application not found"}
        candidate = await session.get(Candidate, app.candidate_id)
        if not candidate:
            return {"error": "Candidate not found"}

        intelligence = await self.evaluate_candidate_response(
            "Extract notice period and salary expectation from this reply.", body
        )

        values = cast("dict[str, Any]", intelligence.get("values_extracted", {}))
        # LLM-extracted values may be non-numeric ("2 months", "12 LPA"); never let a
        # bad cast crash the inbound-email handler / IMAP sync loop.
        if values.get("notice_period"):
            try:
                candidate.notice_period = int(values["notice_period"])
            except (TypeError, ValueError):
                pass
        if values.get("salary"):
            try:
                candidate.expected_salary = float(values["salary"])
            except (TypeError, ValueError):
                pass

        now = datetime.now().isoformat()
        feedback = cast("dict[str, Any]", app.ai_feedback or {})
        log = cast("list[dict[str, Any]]", feedback.get("agent_log", []))
        log.append(
            {
                "time": now,
                "event": "REPLY_RECEIVED",
                "from": from_email,
                "extracted": values,
                "detail": f"AI parsed response: {intelligence.get('analysis')}",
            }
        )

        app.ai_feedback = {**feedback, "agent_log": log, "agent_status": "REPLY_RECEIVED"}
        candidate.parsed_data = {
            **cast("dict[str, Any]", candidate.parsed_data or {}),
            "inbound_intelligence": intelligence,
        }

        await session.commit()
        return await self.process_application(str(app.id), session, background_tasks)

    async def evaluate_candidate_response(self, question: str, response: str) -> dict[str, Any]:
        """
        Uses AI to evaluate a specific response to a screening question.
        Returns a score and analysis.
        """
        prompt = f"""
        Question: {question}
        Candidate Response: {response}

        Evaluate this response for a professional job application.
        Return JSON with:
        - score: (0-100)
        - analysis: (brief text)
        - values_extracted: (e.g. {{"notice_period": 30, "salary": 1200000}}) if any.
        """
        try:
            res = await self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an HR analyst. Output JSON."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = str(res.choices[0].message.content)
            return cast("dict[str, Any]", json.loads(content))
        except Exception:
            return {"score": 50, "analysis": "Could not parse response with AI."}

    async def get_rejection_explanation(self, candidate_name: str, job_title: str, reasons: list[str]) -> str:
        """
        Generates a professional, AI-crafted rejection reason.
        """
        prompt = (
            f"Write a professional and polite 1-sentence reason for rejecting {candidate_name} "
            f"for the {job_title} role. Reasons for rejection: {', '.join(reasons)}. "
            "Keep it constructive."
        )
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
            )
            return str(response.choices[0].message.content)
        except Exception:
            return (
                "Thank you for your interest, but your profile does not meet our "
                "current requirements for notice period or salary expectations."
            )

    async def generate_smart_reply(self, message_body: str, candidate_name: str, job_title: str) -> str:
        """
        Drafts a smart reply to a candidate email.
        """
        prompt = f"""
        Candidate: {candidate_name}
        Job: {job_title}
        Last Message: {message_body}

        Draft a polite, professional 2-3 sentence reply as an AI Hiring Assistant.
        If the candidate asked a question, try to acknowledge it.
        If they provided info, thank them and say we will get back to them.
        Keep it concise and friendly.
        """
        try:
            response = await self.client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}]
            )
            return str(response.choices[0].message.content)
        except Exception:
            return (
                f"Hi {candidate_name}, thank you for your message. We have received "
                f"your update regarding the {job_title} position and will get "
                "back to you shortly."
            )


hiring_agent_service = HiringAgentService()
