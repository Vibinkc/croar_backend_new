import os
import shutil
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.dependencies import DBSessionDep, PermissionChecker
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/upload", tags=["Enterprise Upload"])

UPLOAD_DIR = "uploads/branding"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/logo")
def upload_company_logo(
    _session: DBSessionDep,
    _current_user: Annotated[
        object, Depends(PermissionChecker(ModuleScope.organization, PermissionAction.update))
    ],
    file: UploadFile = File(...),
) -> dict[str, str]:
    """
    Upload an organization logo.
    """
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    # Generate unique filename
    ext = os.path.splitext(cast("str", file.filename))[1]
    filename = f"logo_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e!s}") from e

    # Return the relative URL
    return {"url": f"/uploads/branding/{filename}"}
