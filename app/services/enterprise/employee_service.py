from datetime import date
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enterprise.candidate import Candidate, CandidateApplication
from app.models.enterprise.employee import Employee
from app.models.enterprise.onboarding import Onboarding


class EmployeeService:
    @staticmethod
    async def generate_employee_id(session: AsyncSession, company_id: UUID) -> str:
        """Generate a unique employee ID like EMP-1001 for a specific company."""
        stmt = (
            select(Employee)
            .where(Employee.company_id == company_id)
            .order_by(Employee.employee_id.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        last_emp = result.scalar_one_or_none()

        next_num = 1001
        if last_emp and last_emp.employee_id and last_emp.employee_id.startswith("EMP-"):
            try:
                # Extract number from EMP-XXXX
                curr_num = int(last_emp.employee_id.split("-")[1])
                next_num = curr_num + 1
            except (IndexError, ValueError):
                pass

        return f"EMP-{next_num}"

    @staticmethod
    async def convert_candidate_to_employee(
        session: AsyncSession, candidate_id: UUID, _performed_by: str
    ) -> Employee:
        """Convert an onboarded candidate to an employee."""
        # 1. Fetch Candidate with related data
        stmt = select(Candidate).where(Candidate.id == candidate_id)
        result = await session.execute(stmt)
        candidate = result.scalar_one_or_none()
        if not candidate:
            raise ValueError("Candidate not found")

        # 2. Fetch the most recent completed onboarding for this candidate
        onb_stmt = (
            select(Onboarding)
            .options(selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement))
            .join(Onboarding.application)
            .where(CandidateApplication.candidate_id == candidate_id)
            .order_by(Onboarding.created_at.desc())
            .limit(1)
        )

        onb_result = await session.execute(onb_stmt)
        onboarding = onb_result.scalar_one_or_none()

        if not onboarding:
            raise ValueError("No onboarding process found for this candidate")

        # 3. Extract information
        job_info = cast("dict[str, Any]", onboarding.job_info or {})
        personal_info = cast("dict[str, Any]", onboarding.personal_info or {})
        form_data = cast("dict[str, Any]", onboarding.form_data or {})

        # 4. Extract names safely
        full_name_parts = (candidate.full_name or "").split(" ")
        first_name = (
            str(personal_info.get("first_name"))
            if personal_info.get("first_name")
            else (full_name_parts[0] if full_name_parts else "Unknown")
        )
        last_name = (
            str(personal_info.get("last_name"))
            if personal_info.get("last_name")
            else (" ".join(full_name_parts[1:]) if len(full_name_parts) > 1 else "Unknown")
        )

        # 5. Get Company ID
        company_id_val: UUID | None = None
        if onboarding.application and onboarding.application.job_requirement:
            company_id_val = cast("UUID", onboarding.application.job_requirement.company_id)

        if not company_id_val:
            raise ValueError("Could not determine company for candidate conversion")

        # 6. Generate Employee ID
        employee_id = await EmployeeService.generate_employee_id(session, company_id_val)

        # 7. Create Employee Record
        employee = Employee(
            employee_id=employee_id,
            first_name=first_name,
            middle_name=str(personal_info.get("middle_name")) if personal_info.get("middle_name") else None,
            last_name=last_name,
            email=candidate.email,
            mobile=candidate.phone,
            phone_number=str(personal_info.get("phone_number"))
            if personal_info.get("phone_number")
            else candidate.phone,
            designation=str(job_info.get("designation"))
            if job_info.get("designation")
            else (
                onboarding.application.job_requirement.title
                if onboarding.application and onboarding.application.job_requirement
                else None
            ),
            status="Active",
            employment_type=str(job_info.get("employment_type"))
            if job_info.get("employment_type")
            else "Full-time",
            hire_date=cast("Any", job_info.get("hire_date")) or date.today(),
            original_hire_date=cast("Any", job_info.get("hire_date")) or date.today(),
            source=candidate.source_platform or "Recruitment",
            notice_period=candidate.notice_period,
            pan_card_number=str(
                form_data.get("pan_card_number") or personal_info.get("pan_card_number") or ""
            ),
            aadhar_card_number=str(
                form_data.get("aadhar_card_number") or personal_info.get("aadhar_card_number") or ""
            ),
            passport_number=str(form_data.get("passport_number") or ""),
            date_of_birth=cast("Any", personal_info.get("date_of_birth")),
            gender=str(personal_info.get("gender") or ""),
            marital_status=str(personal_info.get("marital_status") or ""),
            blood_group=str(personal_info.get("blood_group") or ""),
            address_line_1=str(personal_info.get("address_line_1") or personal_info.get("address") or ""),
            address_line_2=str(personal_info.get("address_line_2") or ""),
            city=str(personal_info.get("city") or ""),
            state=str(personal_info.get("state") or ""),
            pincode=str(personal_info.get("pincode") or ""),
            country=str(personal_info.get("country", "India")),
            company_id=company_id_val,
            candidate_id=candidate.id,
            # Additional JSON sections from form_data
            dependents=cast("list[dict[str, Any]]", form_data.get("dependents", [])),
            educational_details=cast("list[dict[str, Any]]", form_data.get("educational_details", [])),
            emergency_contacts=cast("list[dict[str, Any]]", form_data.get("emergency_contacts", [])),
            social_profiles=cast("dict[str, Any]", form_data.get("social_profiles", {})),
            payment_information=cast("list[dict[str, Any]]", form_data.get("payment_information", [])),
            roles_responsibilities=str(form_data.get("roles_responsibilities") or ""),
            skills=candidate.skills or [],
            documents=cast("list[dict[str, Any]]", form_data.get("documents", [])),
        )

        session.add(employee)
        await session.flush()

        return employee


employee_service = EmployeeService()
