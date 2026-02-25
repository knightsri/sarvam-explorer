import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load .env before importing sarvam_client so the key is present
load_dotenv()

if not os.getenv("SARVAM_API_KEY"):
    raise RuntimeError(
        "SARVAM_API_KEY is not set. "
        "Copy .env.example to .env and add your key, then restart."
    )

import sarvam_client  # noqa: E402  (import after env is loaded)
import db             # noqa: E402

# ── Directory setup ───────────────────────────────────────────────────────────

UPLOADS_DIR = Path("uploads")
OUTPUTS_DIR = Path("outputs")
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
db.init_db()

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Sarvam Explorer", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    return FileResponse("static/index.html")


# ── /api/analyse ──────────────────────────────────────────────────────────────

@app.post("/api/analyse")
async def analyse(
    file: UploadFile = File(...),
    transcription_language: str = Form("en-IN"),
) -> JSONResponse:
    """
    Upload an MP3 and a transcription language code.
    Returns transcript + structured analysis JSON + session_id.
    """
    session_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    suffix = Path(file.filename or "audio").suffix or ".mp3"
    upload_path = UPLOADS_DIR / f"{uuid.uuid4()}{suffix}"

    try:
        content = await file.read()
        upload_path.write_bytes(content)

        # Transcribe
        trans = sarvam_client.transcribe_audio(str(upload_path), transcription_language)
        transcript: str = trans["transcript"]

        if not transcript.strip():
            raise HTTPException(
                status_code=422,
                detail="Could not transcribe audio — check file quality or language support.",
            )

        # Analyse
        analysis = sarvam_client.analyse_transcript(transcript)

        # Persist session
        db.create_session(
            id=session_id,
            created_at=created_at,
            filename=file.filename or "audio",
            transcription_language=transcription_language,
            transcript=transcript,
            analysis_json=json.dumps(analysis),
        )

        return JSONResponse(
            {
                "transcript": transcript,
                "language_code": trans["language_code"],
                "analysis": analysis,
                "session_id": session_id,
                "truncated": trans.get("truncated", False),
            }
        )

    finally:
        if upload_path.exists():
            upload_path.unlink()


# ── /api/translate-and-speak ──────────────────────────────────────────────────

class TranslateRequest(BaseModel):
    transcript: str
    target_language: str
    source_language: str = "en-IN"
    session_id: str | None = None


@app.post("/api/translate-and-speak")
async def translate_and_speak(req: TranslateRequest) -> JSONResponse:
    """
    Translate transcript into target_language, then generate Bulbul TTS audio.
    If TTS fails, returns translated text with audio_url=null and an error message.
    """
    # Step 1: Translate
    translated = sarvam_client.translate_text(
        req.transcript, req.target_language, req.source_language
    )

    # Step 2: TTS
    output_filename = f"{uuid.uuid4()}.mp3"
    output_path = OUTPUTS_DIR / output_filename

    try:
        sarvam_client.text_to_speech(translated, req.target_language, str(output_path))
        audio_url = f"/api/audio/{output_filename}"
        tts_error = None
    except Exception as exc:
        audio_url = None
        tts_error = str(exc)

    if req.session_id:
        db.update_session(
            id=req.session_id,
            target_language=req.target_language,
            translated_text=translated,
            audio_filename=output_filename if audio_url else None,
        )

    return JSONResponse(
        {
            "translated_text": translated,
            "audio_url": audio_url,
            **({"tts_error": tts_error} if tts_error else {}),
        }
    )


# ── /api/audio/{filename} ─────────────────────────────────────────────────────

@app.get("/api/audio/{filename}")
async def get_audio(filename: str) -> FileResponse:
    """Stream a generated audio file. Path-traversal safe."""
    safe_name = Path(filename).name          # strip any directory components
    audio_path = OUTPUTS_DIR / safe_name

    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found.")

    return FileResponse(str(audio_path), media_type="audio/mpeg")


# ── /api/sessions ──────────────────────────────────────────────────────────────

@app.get("/api/sessions")
async def list_sessions() -> JSONResponse:
    """Return all sessions ordered newest-first."""
    return JSONResponse(db.get_all_sessions())


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session_endpoint(session_id: str) -> Response:
    """Delete a session record and its associated audio file."""
    audio_filename = db.delete_session(session_id)
    if audio_filename:
        safe_name = Path(audio_filename).name      # path-traversal guard
        audio_path = OUTPUTS_DIR / safe_name
        if audio_path.exists():
            audio_path.unlink()
    return Response(status_code=204)
