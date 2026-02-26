import base64
import io
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests
from openai import OpenAI
from pydub import AudioSegment
from sarvamai import SarvamAI

SARVAM_API_KEY: str = os.environ["SARVAM_API_KEY"]  # fail-fast — checked in main.py first
SARVAM_BASE_URL = "https://api.sarvam.ai"
CHUNK_SECONDS = 25
MAX_DURATION = int(os.getenv("MAX_AUDIO_DURATION", "60"))  # seconds; override via env
FFMPEG = "ffmpeg"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sarvam() -> SarvamAI:
    return SarvamAI(api_subscription_key=SARVAM_API_KEY)


def _llm() -> OpenAI:
    return OpenAI(api_key=SARVAM_API_KEY, base_url=f"{SARVAM_BASE_URL}/v1")


def _headers() -> dict[str, str]:
    return {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}


def _chunk_text(text: str, max_chars: int = 500) -> list[str]:
    """Split text into sentence-boundary chunks of at most max_chars each."""
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?।])\s+", text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            if len(sentence) > max_chars:
                for i in range(0, len(sentence), max_chars):
                    chunks.append(sentence[i : i + max_chars])
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current)

    return chunks


# ── Step 1a: Transcription ────────────────────────────────────────────────────

def get_audio_duration(audio_path: str) -> float:
    """Return duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def transcribe_audio(audio_path: str, language_code: str = "en-IN") -> dict:
    """
    Transcribe an MP3 file using Saarika v3.

    Files longer than MAX_DURATION seconds are silently trimmed to the first
    MAX_DURATION seconds before transcription; the result includes truncated=True.
    Chunks audio into 25s segments via ffmpeg if duration > 30s.
    Returns {"transcript": str, "language_code": str, "truncated": bool}.
    """
    sarvam = _sarvam()
    duration = get_audio_duration(audio_path)
    truncated = duration > MAX_DURATION
    trimmed_path: str | None = None

    try:
        if truncated:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            trimmed_path = tmp.name
            tmp.close()
            subprocess.run(
                [FFMPEG, "-y", "-t", str(MAX_DURATION), "-i", audio_path,
                 "-c", "copy", trimmed_path],
                capture_output=True,
                check=True,
            )
            audio_path = trimmed_path
            duration = MAX_DURATION

        def _transcribe_file(path: str, name: str) -> tuple[str, str | None]:
            with open(path, "rb") as f:
                resp = sarvam.speech_to_text.transcribe(
                    file=(name, f, "audio/mpeg"),
                    model="saarika:v2.5",
                    mode="transcribe",
                    language_code=language_code,
                )
            return (resp.transcript or ""), resp.language_code

        if duration <= 30:
            text, lang = _transcribe_file(audio_path, Path(audio_path).name)
            return {"transcript": text, "language_code": lang or language_code,
                    "truncated": truncated}

        # Chunked path
        starts = list(range(0, int(duration), CHUNK_SECONDS))
        transcripts: list[str] = []
        detected_lang: str | None = None

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, start_t in enumerate(starts):
                chunk_path = os.path.join(tmpdir, f"chunk_{i:03d}.mp3")
                subprocess.run(
                    [
                        FFMPEG, "-y",
                        "-ss", str(start_t),
                        "-t", str(CHUNK_SECONDS),
                        "-i", audio_path,
                        "-ar", "16000",
                        "-ac", "1",
                        chunk_path,
                    ],
                    capture_output=True,
                    check=True,
                )
                text, lang = _transcribe_file(chunk_path, f"chunk_{i:03d}.mp3")
                if text:
                    transcripts.append(text)
                if not detected_lang and lang:
                    detected_lang = lang

        return {
            "transcript": " ".join(transcripts),
            "language_code": detected_lang or language_code,
            "truncated": truncated,
        }

    finally:
        if trimmed_path:
            Path(trimmed_path).unlink(missing_ok=True)


# ── Step 1b: Analysis ─────────────────────────────────────────────────────────

def analyse_transcript(transcript: str) -> dict:
    """
    Send transcript to Sarvam-M and return structured JSON analysis.

    On JSON parse failure returns {"raw_analysis": <raw text>} instead of raising.
    """
    llm = _llm()

    prompt = f"""You are an analyst reviewing a transcribed audio segment.

Transcript:
{transcript}

Return ONLY a valid JSON object — no markdown, no code fences, no explanation. Use exactly these keys:
{{
  "summary": "3-5 sentence summary of the content",
  "language_detected": "Language name in English (e.g. Hindi, Tamil)",
  "key_entities": ["entity1", "entity2"],
  "topics": ["topic1", "topic2"],
  "tone": "Positive | Negative | Neutral",
  "tone_explanation": "One sentence explaining the tone"
}}"""

    response = _llm().chat.completions.create(
        model="sarvam-m",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if the model adds them
    clean = raw
    if clean.startswith("```"):
        parts = clean.split("```")
        # parts[1] is the fenced block; strip optional language tag
        inner = parts[1]
        if inner.startswith("json"):
            inner = inner[4:]
        clean = inner.strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {"raw_analysis": raw}


# ── Step 2a: Translation ──────────────────────────────────────────────────────

def translate_text(
    text: str,
    target_language: str,
    source_language: str = "en-IN",
) -> str:
    """Translate text using Sarvam Translate."""
    payload = {
        "input": text,
        "source_language_code": source_language,
        "target_language_code": target_language,
        "speaker_gender": "Male",
        "mode": "formal",
        "model": "mayura:v1",
        "enable_preprocessing": True,
    }
    resp = requests.post(
        f"{SARVAM_BASE_URL}/translate",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("translated_text", "")


# ── Step 2b: Text-to-Speech ───────────────────────────────────────────────────

def text_to_speech(text: str, language_code: str, output_path: str) -> str:
    """
    Generate speech from text using Bulbul v2.

    Chunks text to ≤ 500 chars (Bulbul limit), calls TTS for each chunk,
    concatenates WAV segments with pydub, exports as MP3.
    Returns output_path.
    """
    chunks = _chunk_text(text, max_chars=500)
    segments: list[AudioSegment] = []

    for chunk in chunks:
        payload = {
            "inputs": [chunk],
            "target_language_code": language_code,
            "model": "bulbul:v2",
            "speaker": "anushka",
            "pitch": 0,
            "pace": 1.0,
            "loudness": 1.5,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
        }
        resp = requests.post(
            f"{SARVAM_BASE_URL}/text-to-speech",
            headers=_headers(),
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        audio_b64: str = resp.json()["audios"][0]
        audio_bytes = base64.b64decode(audio_b64)
        segment = AudioSegment.from_wav(io.BytesIO(audio_bytes))
        segments.append(segment)

    combined = segments[0]
    for seg in segments[1:]:
        combined += seg

    combined.export(output_path, format="mp3")
    return output_path
