import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from openai import OpenAI

from app.core.dependencies import PermissionChecker
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/audio", tags=["Audio"])


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    user: Annotated[dict, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))] = None,
):
    try:
        # Save uploaded file to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        with open(tmp_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)

        # Clean up
        os.unlink(tmp_path)

        return {"text": transcript.text}
    except Exception as e:
        print(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
