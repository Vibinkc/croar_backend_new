import asyncio
import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.settings import get_settings
from app.models.enterprise.survey import SurveyInvite, SurveyInviteStatus
from app.router.enterprise.communication import send_smtp_email

_settings = get_settings()


class SurveyService:
    async def notify_participants(
        self, db: AsyncSession, instance_id: uuid.UUID, only_pending: bool = True
    ) -> int:
        """
        Sends email invitations to all participants of a survey instance.
        """
        stmt = (
            select(SurveyInvite)
            .where(SurveyInvite.instance_id == instance_id)
            .options(selectinload(SurveyInvite.employee), selectinload(SurveyInvite.instance))
        )

        if only_pending:
            stmt = stmt.where(SurveyInvite.status == SurveyInviteStatus.PENDING)

        res = await db.execute(stmt)
        invites = cast("list[SurveyInvite]", res.scalars().all())

        sent_count = 0
        for invite in invites:
            if invite.employee and invite.employee.email:
                success = await self.send_invite_email(invite)
                if success:
                    sent_count += 1

        return sent_count

    async def send_invite_email(self, invite: SurveyInvite) -> bool:
        """
        Sends a single survey invitation email.
        """
        await asyncio.sleep(0)  # async kept for awaiting callers (notify loop / route handler)
        employee = invite.employee
        instance = invite.instance
        if not employee or not employee.email:
            return False

        frontend_url = _settings.frontend_url
        survey_link = f"{frontend_url}/enterprise/surveys/fill/{invite.token}"
        portal_link = f"{frontend_url}/enterprise/assessments-360/portal"

        subject = f"Feedback Request: {instance.name}"
        body = (
            f"Hello {employee.first_name},\n\n"
            f"You have been invited to participate in the '{instance.name}' survey.\n\n"
            f"--- OPTION 1: Direct Access ---\n"
            f"Complete this specific survey immediately:\n"
            f"{survey_link}\n\n"
            f"--- OPTION 2: Unified Portal ---\n"
            f"View all your pending surveys and performance reviews at once:\n"
            f"{portal_link}\n\n"
            f"To access the portal, use your unique ID: {employee.id}\n\n"
            f"Thank you,\n"
            f"HR Team"
        )

        success, _ = cast(
            "tuple[bool, object]",
            send_smtp_email(to_email=employee.email, subject=subject, body=body, company_name="Croar"),
        )
        return success


survey_service = SurveyService()
