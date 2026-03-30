from uuid import UUID
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enterprise.employee import Employee, Department
from app.models.enterprise.onboarding import Onboarding
from app.models.enterprise.candidate import Candidate, CandidateApplication

class EmployeeService:
    @staticmethod
    async def generate_employee_id(session: AsyncSession) -> str:
        """Generate a unique employee ID like EMP-1001."""
        stmt = select(Employee).order_by(Employee.employee_id.desc()).limit(1)
        result = await session.execute(stmt)
        last_emp = result.scalar_one_or_none()
        
        next_num = 1001
        if last_emp and last_emp.employee_id.startswith("EMP-"):
            try:
                # Extract number from EMP-XXXX
                curr_num = int(last_emp.employee_id.split("-")[1])
                next_num = curr_num + 1
            except (IndexError, ValueError):
                pass
        
        return f"EMP-{next_num}"

    @staticmethod
    async def convert_candidate_to_employee(session: AsyncSession, candidate_id: UUID, performed_by: str) -> Employee:
        """Convert an onboarded candidate to an employee."""
        # 1. Fetch Candidate with related data
        stmt = select(Candidate).where(Candidate.id == candidate_id)
        result = await session.execute(stmt)
        candidate = result.scalar_one_or_none()
        if not candidate:
            raise ValueError("Candidate not found")

        # 2. Fetch the most recent completed onboarding for this candidate
        onb_stmt = select(Onboarding).options(
            selectinload(Onboarding.application).selectinload(CandidateApplication.job_requirement)
        ).join(Onboarding.application).where(
            CandidateApplication.candidate_id == candidate_id
        ).order_by(Onboarding.created_at.desc()).limit(1)
        
        onb_result = await session.execute(onb_stmt)
        onboarding = onb_result.scalar_one_or_none()
        
        if not onboarding:
             raise ValueError("No onboarding process found for this candidate")

        # 3. Extract information
        job_info = onboarding.job_info or {}
        personal_info = onboarding.personal_info or {}
        form_data = onboarding.form_data or {}
        
        # 4. Extract names safely
        full_name_parts = (candidate.full_name or "").split(" ")
        first_name = personal_info.get("first_name") or (full_name_parts[0] if full_name_parts else "Unknown")
        last_name = personal_info.get("last_name") or (" ".join(full_name_parts[1:]) if len(full_name_parts) > 1 else "Unknown")
        
        # 5. Get Company ID
        company_id = None
        if onboarding.application and onboarding.application.job_requirement:
            company_id = onboarding.application.job_requirement.company_id
        
        if not company_id:
            raise ValueError("Could not determine company for candidate conversion")

        # 6. Generate Employee ID
        employee_id = await EmployeeService.generate_employee_id(session)

        # 7. Create Employee Record
        employee = Employee(
            employee_id=employee_id,
            first_name=first_name,
            middle_name=personal_info.get("middle_name"),
            last_name=last_name,
            email=candidate.email,
            mobile=candidate.phone,
            phone_number=personal_info.get("phone_number") or candidate.phone,
            designation=job_info.get("designation") or (onboarding.application.job_requirement.title if onboarding.application and onboarding.application.job_requirement else None),
            status="Active",
            employment_type=job_info.get("employment_type") or "Full-time",
            hire_date=job_info.get("hire_date") or date.today(),
            original_hire_date=job_info.get("hire_date") or date.today(),
            source=candidate.source_platform or "Recruitment",
            notice_period=candidate.notice_period,
            pan_card_number=form_data.get("pan_card_number") or personal_info.get("pan_card_number"),
            aadhar_card_number=form_data.get("aadhar_card_number") or personal_info.get("aadhar_card_number"),
            passport_number=form_data.get("passport_number"),
            date_of_birth=personal_info.get("date_of_birth"),
            gender=personal_info.get("gender"),
            marital_status=personal_info.get("marital_status"),
            blood_group=personal_info.get("blood_group"),
            address_line_1=personal_info.get("address_line_1") or personal_info.get("address"),
            address_line_2=personal_info.get("address_line_2"),
            city=personal_info.get("city"),
            state=personal_info.get("state"),
            pincode=personal_info.get("pincode"),
            country=personal_info.get("country", "India"),
            company_id=company_id,
            candidate_id=candidate.id,
            # Additional JSON sections from form_data
            dependents=form_data.get("dependents", []),
            educational_details=form_data.get("educational_details", []),
            emergency_contacts=form_data.get("emergency_contacts", []),
            social_profiles=form_data.get("social_profiles", {}),
            payment_information=form_data.get("payment_information", []),
            roles_responsibilities=form_data.get("roles_responsibilities"),
            skills=candidate.skills or [],
            documents=form_data.get("documents", [])
        )
        
        session.add(employee)
        await session.flush()
        
        return employee

employee_service = EmployeeService()
