import logging
from typing import cast

from fastapi import APIRouter, BackgroundTasks, Request

from app.core.database import db_manager
from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.services.enterprise.hiring_agent import hiring_agent_service

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/voice/vapi/webhook")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, object]:
    """
    Receives webhooks from Vapi.ai.
    Specifically listens for the 'end-of-call-report' to extract the transcript
    and evaluate the candidate's responses.
    """
    try:
        payload = cast("dict[str, object]", await request.json())
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    message_type = cast("dict[str, object]", payload.get("message", {})).get("type")

    if message_type == "end-of-call-report":
        call_info = cast("dict[str, object]", payload.get("message", {}))
        call_id = cast("dict[str, object]", call_info.get("call", {})).get("id")
        transcript = cast("str", call_info.get("transcript", ""))
        recording_url = cast("str", call_info.get("recordingUrl", ""))

        logger.info(f"Received Vapi end-of-call-report for call {call_id}")

        if not call_id:
            return {"status": "ignored", "reason": "No call ID"}

        background_tasks.add_task(
            process_vapi_transcript, str(call_id), transcript, recording_url, background_tasks
        )

    return {"status": "success"}


async def process_vapi_transcript(
    call_id: str, transcript: str, recording_url: str, background_tasks: BackgroundTasks
) -> None:
    """
    Background worker to find the associated application and process the transcript.
    """
    async with db_manager.session() as session:
        # We need to find the application that has this call_id in its ai_feedback
        # NOTE: In a real production app, we would index this or store it in a dedicated table.
        # For now, since we store it in JSONB, we'll do a simple query.
        from sqlalchemy import text

        stmt = text("""
            SELECT id FROM candidate_applications
            WHERE ai_feedback->>'voice_call_id' = :call_id
        """)
        result = await session.execute(stmt, {"call_id": call_id})
        app_row = result.first()

        if not app_row:
            logger.error(f"Could not find application associated with Vapi call {call_id}")
            return

        app_id = str(app_row[0])
        app = await session.get(CandidateApplication, app_id)

        if not app:
            return

        candidate = await session.get(Candidate, app.candidate_id)
        if not candidate:
            return

        # Analyze Transcript
        prompt = (
            "Analyze this phone interview transcript. Extract any notice period or salary "
            "expectations mentioned. Give a score from 0-100 on how well they fit the role. "
            f"Transcript: {transcript}"
        )
        intelligence = await hiring_agent_service.evaluate_candidate_response(prompt, transcript)

        # Save recording and analysis to log
        feedback = cast("dict[str, object]", app.ai_feedback or {})
        log = cast("list[dict[str, object]]", feedback.get("agent_log", []))

        values_extracted = cast("dict[str, object]", intelligence.get("values_extracted", {}))

        log.append(
            {
                "time": "NOW",  # This should be dynamic datetime, updated below
                "event": "VOICE_INTERVIEW_COMPLETED",
                "recording_url": recording_url,
                "transcript_summary": intelligence.get("analysis"),
                "extracted": values_extracted,
            }
        )

        # Update app score based on interview
        app.ai_match_score = cast("float | None", intelligence.get("score", app.ai_match_score))

        # Set status back to allow agent to make next decision
        app.ai_feedback = {**feedback, "agent_log": log, "agent_status": "VOICE_INTERVIEW_COMPLETED"}

        # Save extracted candidate values
        if values_extracted.get("notice_period"):
            candidate.notice_period = int(cast("int", values_extracted["notice_period"]))

        await session.commit()

        # Re-trigger the background loop so the agent can MOVE_TO_NEXT or AUTO_REJECT based on the new score
        await hiring_agent_service.process_application(str(app.id), session, background_tasks)
