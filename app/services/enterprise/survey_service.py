import uuid
from typing import List, Optional
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enterprise.survey import (
    SurveyInstance, SurveyInvite, SurveyInviteStatus
)
from app.models.enterprise.employee import Employee
from app.router.enterprise.communication import send_smtp_email
from app.core.settings import get_settings

_settings = get_settings()

class SurveyService:
    async def notify_participants(self, db: AsyncSession, instance_id: uuid.UUID, only_pending: bool = True):
        """
        Sends email invitations to all participants of a survey instance.
        """
        stmt = select(SurveyInvite).where(
            SurveyInvite.instance_id == instance_id
        ).options(
            selectinload(SurveyInvite.employee),
            selectinload(SurveyInvite.instance)
        )
        
        if only_pending:
            stmt = stmt.where(SurveyInvite.status == SurveyInviteStatus.PENDING)
            
        res = await db.execute(stmt)
        invites = res.scalars().all()
        
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
        employee = invite.employee
        instance = invite.instance
        if not employee or not employee.email:
            return False
            
        survey_link = f"{_settings.frontend_url}/enterprise/surveys/fill/{invite.token}"
        portal_link = f"{_settings.frontend_url}/enterprise/assessments-360/portal"
        
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
        
        success, _ = send_smtp_email(
            to_email=employee.email,
            subject=subject,
            body=body,
            company_name="Croar"
        )
        return success

survey_service = SurveyService()
