import os
import tempfile
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from openai import OpenAI

from app.core.dependencies import PermissionChecker
from app.models.shared.constants import ModuleScope, PermissionAction

router = APIRouter(prefix="/audio", tags=["Audio"])


def _transcribe_bytes(content: bytes) -> str:
    """Blocking transcription (temp-file write + Whisper call); run off the event loop."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        with open(tmp_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
        return transcript.text
    finally:
        os.unlink(tmp_path)


@router.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...),
    user: Annotated[
        dict | None, Depends(PermissionChecker(ModuleScope.candidates, PermissionAction.read))
    ] = None,
):
    try:
        content = await file.read()
        # Run the blocking file/Whisper work in a worker thread so the event loop isn't blocked.
        text = await run_in_threadpool(_transcribe_bytes, content)
        return {"text": text}
    except Exception as e:
        print(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
