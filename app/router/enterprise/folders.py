"""Candidate folders — create, rename, delete, and move people in and out.

A folder is a bookmark, not a pipeline. Nothing here emails anyone, moves a stage, or fires an
automation, and that is the point: it is the safe place to put someone you have not decided
about yet. Everything is scoped to the caller's company, and membership is idempotent so the
UI can offer "Add to folder" without first checking whether they are already in it.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.enterprise.candidate import Candidate
from app.models.enterprise.folder import CandidateFolder, CandidateFolderMember
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/candidates/folders", tags=["Candidate Folders"])


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None


class MemberIn(BaseModel):
    # A list rather than one id, so the hub's bulk selection is one request rather than N.
    candidate_ids: list[UUID] = Field(min_length=1)


def _company_of(user: object) -> UUID:
    company_id = getattr(user, "company_id", None)
    if not company_id:
        raise HTTPException(status_code=404, detail="No company on this account.")
    return company_id


async def _owned_folder(session: Any, user: object, folder_id: UUID) -> CandidateFolder:
    """Fetch a folder, or 404 — never 403.

    Telling a caller that a folder exists but belongs to someone else leaks the shape of
    another tenant's data. An id they cannot use simply does not exist to them.
    """
    folder = (
        await session.execute(
            select(CandidateFolder).where(
                CandidateFolder.id == folder_id, CandidateFolder.company_id == _company_of(user)
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


@router.get("")
async def list_folders(
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> list[dict[str, Any]]:
    """Every folder in the company, each with how many people are in it.

    The count is what makes the list usable — an empty folder and one holding forty people
    should not look the same — so it is aggregated here rather than left to N follow-up calls.
    """
    company_id = _company_of(current_user)
    rows = (
        await session.execute(
            select(CandidateFolder, func.count(CandidateFolderMember.id))
            .outerjoin(CandidateFolderMember, CandidateFolderMember.folder_id == CandidateFolder.id)
            .where(CandidateFolder.company_id == company_id)
            .group_by(CandidateFolder.id)
            .order_by(CandidateFolder.name)
        )
    ).all()
    return [
        {
            "id": str(f.id),
            "name": f.name,
            "description": f.description,
            "candidate_count": count,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f, count in rows
    ]


@router.post("", status_code=201)
async def create_folder(
    body: FolderIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.create))
    ],
) -> dict[str, Any]:
    company_id = _company_of(current_user)
    name = body.name.strip()
    existing = (
        await session.execute(
            select(CandidateFolder).where(
                CandidateFolder.company_id == company_id, CandidateFolder.name == name
            )
        )
    ).scalar_one_or_none()
    if existing:
        # Returning the existing folder rather than 409 keeps "create and add" a single call
        # from the hub, where the user's intent is the folder existing, not it being new.
        return {"id": str(existing.id), "name": existing.name, "created": False}

    folder = CandidateFolder(
        name=name,
        description=(body.description or "").strip() or None,
        company_id=company_id,
        created_by=getattr(current_user, "id", None),
    )
    session.add(folder)
    await session.commit()
    return {"id": str(folder.id), "name": folder.name, "created": True}


@router.patch("/{folder_id}")
async def rename_folder(
    folder_id: UUID,
    body: FolderPatch,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
) -> dict[str, Any]:
    folder = await _owned_folder(session, current_user, folder_id)
    if body.name is not None:
        folder.name = body.name.strip()
    if body.description is not None:
        folder.description = body.description.strip() or None
    await session.commit()
    return {"id": str(folder.id), "name": folder.name, "description": folder.description}


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.delete))
    ],
) -> None:
    """Delete the folder. The candidates in it are untouched — only the grouping goes."""
    folder = await _owned_folder(session, current_user, folder_id)
    await session.delete(folder)
    await session.commit()


@router.get("/{folder_id}/candidates")
async def folder_candidates(
    folder_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ],
) -> dict[str, Any]:
    folder = await _owned_folder(session, current_user, folder_id)
    rows = (
        await session.execute(
            select(Candidate, CandidateFolderMember.added_at)
            .join(CandidateFolderMember, CandidateFolderMember.candidate_id == Candidate.id)
            .where(CandidateFolderMember.folder_id == folder.id, Candidate.deleted_at.is_(None))
            .order_by(CandidateFolderMember.added_at.desc())
        )
    ).all()
    return {
        "id": str(folder.id),
        "name": folder.name,
        "description": folder.description,
        "candidates": [
            {
                "id": str(c.id),
                "full_name": c.full_name,
                "email": c.email,
                "phone": c.phone,
                "skills": c.skills or [],
                "source_platform": c.source_platform,
                "headline": (c.parsed_data or {}).get("headline"),
                "location": (c.parsed_data or {}).get("location"),
                "company": (c.parsed_data or {}).get("company"),
                "added_at": added.isoformat() if added else None,
            }
            for c, added in rows
        ],
    }


@router.post("/{folder_id}/candidates", status_code=201)
async def add_to_folder(
    folder_id: UUID,
    body: MemberIn,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
) -> dict[str, Any]:
    """Add candidates to a folder, skipping any already in it.

    Idempotent on purpose: the hub offers "Add to folder" without knowing what a folder already
    holds, and a second click should be a no-op rather than an error the user has to read.
    """
    folder = await _owned_folder(session, current_user, folder_id)
    company_id = _company_of(current_user)

    # Only candidates belonging to this company — an id from elsewhere is silently not found
    # rather than filed into someone else's folder.
    owned = set(
        (
            await session.execute(
                select(Candidate.id).where(
                    Candidate.id.in_(body.candidate_ids),
                    Candidate.company_id == company_id,
                    Candidate.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    already = set(
        (
            await session.execute(
                select(CandidateFolderMember.candidate_id).where(
                    CandidateFolderMember.folder_id == folder.id,
                    CandidateFolderMember.candidate_id.in_(owned),
                )
            )
        )
        .scalars()
        .all()
    )

    added = 0
    for candidate_id in owned - already:
        session.add(
            CandidateFolderMember(
                folder_id=folder.id, candidate_id=candidate_id, added_by=getattr(current_user, "id", None)
            )
        )
        added += 1
    await session.commit()
    return {
        "folder_id": str(folder.id),
        "added": added,
        "already_in_folder": len(already),
        "not_found": len(set(body.candidate_ids) - owned),
    }


@router.delete("/{folder_id}/candidates/{candidate_id}", status_code=204)
async def remove_from_folder(
    folder_id: UUID,
    candidate_id: UUID,
    session: DBSessionDep,
    current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.update))
    ],
) -> None:
    """Take someone out of a folder. The candidate record itself is not touched."""
    folder = await _owned_folder(session, current_user, folder_id)
    await session.execute(
        delete(CandidateFolderMember).where(
            CandidateFolderMember.folder_id == folder.id, CandidateFolderMember.candidate_id == candidate_id
        )
    )
    await session.commit()
