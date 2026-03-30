import os
import shutil
from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from app.core.dependencies import DBSessionDep, get_current_agent
from app.models.enterprise.user_role import EnterpriseUser as HiringAgent

router = APIRouter(prefix="/upload", tags=["Enterprise Upload"])

UPLOAD_DIR = "uploads/branding"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.post("/logo")
async def upload_company_logo(
    session: DBSessionDep,
    current_agent: Annotated[HiringAgent, Depends(get_current_agent)],
    file: UploadFile = File(...)
):
    """
    Upload an organization logo.
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    # Generate unique filename
    ext = os.path.splitext(file.filename)[1]
    filename = f"logo_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
    file_path = os.path.join(UPLOAD_DIR, filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    # Return the relative URL
    return {"url": f"/uploads/branding/{filename}"}
