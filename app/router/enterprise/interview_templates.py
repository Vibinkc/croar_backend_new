import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.interview import Interview
from app.models.shared.constants import ModuleScope, PermissionAction
from app.schemas.enterprise.interviews import InterviewCreate, InterviewResponse
from app.services.enterprise.ai_service import generate_interview_questions_service

router = APIRouter(prefix="/interview-templates", tags=["Enterprise: Interview Templates"])


@router.get("/", response_model=list[InterviewResponse])
async def list_interview_templates(
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.read))
    ],
) -> list[Interview]:
    company_id = getattr(current_user, "company_id", None)
    query = (
        select(Interview)
        .where(Interview.deleted_at.is_(None), Interview.company_id == company_id)
        .order_by(Interview.created_at.desc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/", response_model=InterviewResponse)
async def create_interview_template(
    template_in: InterviewCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.create))
    ],
) -> Interview:
    company_id = getattr(current_user, "company_id", None)
    new_template = Interview(**template_in.model_dump(), company_id=cast("Any", company_id))
    db.add(new_template)
    await db.commit()
    await db.refresh(new_template)
    return new_template


@router.post("/generate-questions")
async def generate_questions(
    _current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.generate))
    ],
    topic: str,
    duration: int = 30,
    difficulty: str = "Intermediate",
) -> dict[str, Any]:
    # Calculate count: 1 question every 3 minutes, min 5 questions
    count = max(5, duration // 3)
    questions = await generate_interview_questions_service(topic, count, difficulty)
    return {"questions": questions}


@router.get("/{template_id}", response_model=InterviewResponse)
async def get_interview_template(
    template_id: Annotated[uuid.UUID, Path(...)],
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.read))
    ],
) -> Interview:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Interview).where(Interview.id == template_id, Interview.company_id == company_id)
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.patch("/{template_id}", response_model=InterviewResponse)
async def update_interview_template(
    template_id: Annotated[uuid.UUID, Path(...)],
    template_in: InterviewCreate,
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.update))
    ],
) -> Interview:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Interview).where(Interview.id == template_id, Interview.company_id == company_id)
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    update_data = template_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(template, key, value)

    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/{template_id}")
async def delete_interview_template(
    template_id: Annotated[uuid.UUID, Path(...)],
    db: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.interviews, PermissionAction.delete))
    ],
) -> dict[str, str]:
    company_id = getattr(current_user, "company_id", None)
    stmt = select(Interview).where(Interview.id == template_id, Interview.company_id == company_id)
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(template)
    await db.commit()
    return {"message": "Template deleted successfully"}
