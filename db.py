import json
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path("data/sessions.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id                     TEXT PRIMARY KEY,
                created_at             TEXT NOT NULL,
                filename               TEXT NOT NULL,
                transcription_language TEXT NOT NULL,
                transcript             TEXT NOT NULL,
                analysis_json          TEXT NOT NULL,
                target_language        TEXT,
                translated_text        TEXT,
                audio_filename         TEXT
            )
            """
        )
        conn.commit()


def create_session(
    id: str,
    created_at: str,
    filename: str,
    transcription_language: str,
    transcript: str,
    analysis_json: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO sessions
                (id, created_at, filename, transcription_language,
                 transcript, analysis_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (id, created_at, filename, transcription_language,
             transcript, analysis_json),
        )
        conn.commit()


def update_session(
    id: str,
    target_language: str,
    translated_text: str,
    audio_filename: str | None,
) -> None:
    with _conn() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET target_language = ?,
                translated_text = ?,
                audio_filename  = ?
            WHERE id = ?
            """,
            (target_language, translated_text, audio_filename, id),
        )
        conn.commit()


def get_all_sessions() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["analysis"] = json.loads(d["analysis_json"])
        except (json.JSONDecodeError, TypeError):
            d["analysis"] = {}
        del d["analysis_json"]
        result.append(d)
    return result


def delete_session(id: str) -> str | None:
    """Delete the session row. Returns audio_filename so caller can delete the file."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT audio_filename FROM sessions WHERE id = ?", (id,)
        ).fetchone()
        if row is None:
            return None
        audio_filename = row["audio_filename"]
        conn.execute("DELETE FROM sessions WHERE id = ?", (id,))
        conn.commit()
    return audio_filename
