import json
import logging
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.ai import analyze_text_with_llm
from app.models.enterprise.interview import Interview, InterviewAttempt, InterviewSchedule

logger = logging.getLogger(__name__)


async def process_interview_turn(db: AsyncSession, attempt_id: str, user_text: str) -> dict[str, object]:
    """
    Processes a single turn of the AI interview.
    Analyzes user response, updates transcript, and generates next question or completes.
    """
    # 1. Load attempt and related data
    stmt = select(InterviewAttempt).where(InterviewAttempt.id == attempt_id)
    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()
    if not attempt:
        raise ValueError("Interview attempt not found")

    sched_stmt = select(InterviewSchedule).where(InterviewSchedule.id == attempt.schedule_id)
    sched_result = await db.execute(sched_stmt)
    schedule = sched_result.scalar_one_or_none()
    if not schedule:
        raise ValueError("Interview schedule not found")

    tpl_stmt = select(Interview).where(Interview.id == schedule.interview_id)
    tpl_result = await db.execute(tpl_stmt)
    template = tpl_result.scalar_one_or_none()

    if not template:
        raise ValueError("Interview template not found")

    # 2. Get current state from transcript
    transcript_data = cast(
        "dict[str, Any]",
        attempt.transcript if attempt.transcript else {"history": [], "current_question_index": 0},
    )
    history = cast("list[dict[str, str]]", transcript_data.get("history", []))
    current_index = int(cast("int", transcript_data.get("current_question_index", 0)))

    plan_data = cast("dict[str, Any]", template.plan if template.plan else {})
    questions = cast("list[dict[str, Any]]", plan_data.get("questions", []))

    if current_index >= len(questions):
        return {"status": "FINISHED", "message": "Interview is already completed."}

    current_q = cast("dict[str, Any]", questions[current_index] if current_index < len(questions) else {})
    email_verified = cast("bool", transcript_data.get("email_verified", False))
    candidate_email = cast("str", transcript_data.get("candidate_email", ""))

    # 3. Add user response to history
    history.append({"role": "user", "text": user_text})

    # 4. Use AI to decide next move
    prompt = f"""
    You are an expert technical interviewer conducting a one-on-one AI interview.

    INTERVIEW CONTEXT:
    Topic: {template.topic}
    Difficulty: {template.difficulty}
    Current Question: {current_q.get("question") if current_q else "All technical questions completed"}
    Candidate Email for Verification: {candidate_email}
    Email Verified: {email_verified}

    CONVERSATION HISTORY (Last 10 turns):
    {json.dumps(history[-10:], indent=2)}

    STRICT TASK:
    Analyze the candidate's last response: "{user_text}"

    DECIDE:
    1. If in technical interview phase:
       - If the candidate sufficiently answered, set action to NEXT_QUESTION.
       - If the candidate explicitly doesn't know the answer OR they have
         struggled for 2+ turns on this same topic, set action to NEXT_QUESTION
         (move on politely).
       - If they need minor clarification, set action to FOLLOW_UP.
       - NEVER repeat a question literally. If you must ask again,
         change your wording completely.
       - Be professional, empathetic, and conversational.

    2. If in EMAIL_VERIFICATION phase (all questions done but email not verified):
       - If they just provided an email, does it match "{candidate_email}"?
       - If it MATCHES, set action to END.
       - If it DOES NOT MATCH, ask them to re-verify the email.
       - If they haven't been asked for email yet, ASK for it.

    OUTPUT JSON FORMAT:
    {{
      "sufficient": true,
      "sufficiency_score": 85,
      "email_matched": false,
      "ai_response": "Your next response",
      "action": "FOLLOW_UP"
    }}
    """

    try:
        ai_response_str = await analyze_text_with_llm(prompt)
        ai_decision: dict[str, Any] = json.loads(ai_response_str)

        action = str(ai_decision.get("action", "FOLLOW_UP"))
        ai_text = str(ai_decision.get("ai_response", "Can you tell me more about that?"))

        # 5. Handle Actions
        if action == "NEXT_QUESTION":
            current_index += 1
            if current_index >= len(questions):
                action = "EMAIL_VERIFICATION"
                if "confirm your email" not in ai_text.lower():
                    ai_text = (
                        f"Got it. {ai_text}. Before we conclude, could you please "
                        "confirm your email address for our records?"
                    )
            else:
                next_q = questions[current_index]
                q_text = str(next_q.get("question", ""))
                if q_text.lower() not in ai_text.lower():
                    ai_text = f"Got it. {ai_text}. Now, let's move to the next topic: {q_text}"
        elif action == "EMAIL_VERIFICATION" and not bool(ai_decision.get("email_matched")):
            if "email" not in user_text.lower() and "@" not in user_text:
                ai_text = (
                    "Before we wrap up, I just need you to confirm your email "
                    "address for verification purposes."
                )

        if bool(ai_decision.get("email_matched")):
            action = "END"
            ai_text = (
                "Thank you! Your email has been verified. That covers all my "
                "questions for today. We will get back to you soon."
            )

        history.append({"role": "ai", "text": ai_text})

        new_transcript = dict(transcript_data)
        new_transcript["history"] = history
        new_transcript["current_question_index"] = current_index
        new_transcript["email_verified"] = bool(ai_decision.get("email_matched"))

        attempt.transcript = new_transcript
        flag_modified(attempt, "transcript")

        if action == "END":
            attempt.overall_score = cast("Any", 100)

        await db.commit()

        return {
            "status": "INTERVIEWING" if action != "END" else "FINISHED",
            "ai_response": ai_text,
            "action": action,
            "current_index": current_index,
        }

    except Exception as e:
        logger.error(f"Error in process_interview_turn: {e}")
        return {
            "status": "ERROR",
            "ai_response": "I'm sorry, I'm having trouble processing that. Could you please repeat it?",
            "action": "FOLLOW_UP",
        }


async def initialize_interview(
    db: AsyncSession, schedule_id: str, user_id: str, candidate_email: str = ""
) -> dict[str, object]:
    """
    Initializes or resumes an interview attempt.
    """
    stmt = select(InterviewAttempt).where(InterviewAttempt.schedule_id == schedule_id)
    result = await db.execute(stmt)
    attempt = result.scalar_one_or_none()

    if not attempt:
        sched_stmt = select(InterviewSchedule).where(InterviewSchedule.id == schedule_id)
        sched_res = await db.execute(sched_stmt)
        sched = sched_res.scalar_one_or_none()

        attempt = InterviewAttempt(
            schedule_id=schedule_id,
            user_id=user_id,
            company_id=sched.company_id if sched else None,
            transcript={
                "history": [],
                "current_question_index": 0,
                "candidate_email": candidate_email,
                "email_verified": False,
            },
        )
        db.add(attempt)
        await db.commit()
        await db.refresh(attempt)

    transcript_data = cast(
        "dict[str, Any]",
        attempt.transcript if attempt.transcript else {"history": [], "current_question_index": 0},
    )
    history = cast("list[dict[str, Any]]", transcript_data.get("history", []))

    if "candidate_email" not in transcript_data or not transcript_data["candidate_email"]:
        transcript_data["candidate_email"] = candidate_email
        attempt.transcript = transcript_data
        await db.commit()

    if not history:
        sched_stmt = select(InterviewSchedule).where(InterviewSchedule.id == schedule_id)
        sched_result = await db.execute(sched_stmt)
        schedule = sched_result.scalar_one_or_none()
        if not schedule:
            raise ValueError("Interview schedule not found")

        tpl_stmt = select(Interview).where(Interview.id == schedule.interview_id)
        tpl_result = await db.execute(tpl_stmt)
        template = tpl_result.scalar_one_or_none()

        plan_data = cast("dict[str, Any]", template.plan if template and template.plan else {})
        questions = cast("list[dict[str, Any]]", plan_data.get("questions", []))

        if questions:
            initial_text = (
                f"Hello! I am your AI interviewer. Let's start with our first "
                f"topic: {questions[0].get('question')}"
            )
            history.append({"role": "ai", "text": initial_text})
            transcript_data["history"] = history
            attempt.transcript = transcript_data
            await db.commit()
            return {"attempt_id": str(attempt.id), "ai_response": initial_text, "status": "STARTED"}

    last_ai_msg = next((str(m["text"]) for m in reversed(history) if m["role"] == "ai"), "Shall we continue?")
    return {"attempt_id": str(attempt.id), "ai_response": last_ai_msg, "status": "RESUMED"}
