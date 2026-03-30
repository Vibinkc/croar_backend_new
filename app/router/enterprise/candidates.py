from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from uuid import UUID

from app.core.dependencies import DBSessionDep
from app.models.enterprise.candidate import Candidate
from app.schemas.enterprise.applications import CandidateBase

router = APIRouter(prefix="/candidates", tags=["Enterprise Candidates"])

@router.get("/{candidate_id}", response_model=CandidateBase)
async def get_candidate(
    candidate_id: UUID, 
    session: DBSessionDep
):
    """Get candidate details by ID."""
    stmt = select(Candidate).where(Candidate.id == candidate_id)
    result = await session.execute(stmt)
    candidate = result.scalar_one_or_none()
    
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
        
    return candidate
