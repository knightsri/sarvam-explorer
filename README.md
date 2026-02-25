# Sarvam Explorer

A browser-based tool to transcribe MP3 audio, analyse the transcript with an LLM, translate it into an Indian language, and play back the translated speech — all powered by the [Sarvam AI](https://sarvam.ai) API suite.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| [Sarvam API key](https://sarvam.ai) | Required — get one from the Sarvam developer portal |
| [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/) | Recommended setup |
| **Or:** Python 3.12+ and `ffmpeg` in `PATH` | For local run without Docker |

---

## Setup & Start

### Docker (recommended)

```bash
cd sarvam-explorer
cp .env.example .env          # then edit .env and paste your SARVAM_API_KEY
docker-compose up --build
```

Open **http://localhost:8000** in your browser.

To run detached:
```bash
docker-compose up --build -d
docker-compose logs -f        # tail logs
docker-compose down           # stop
```

To use a different port, add `PORT=9000` to your `.env` before starting.

### Local (no Docker)

Requires Python 3.12+ and `ffmpeg` on your `PATH`.

```bash
cd sarvam-explorer
pip install -r requirements.txt
cp .env.example .env          # add SARVAM_API_KEY
uvicorn main:app --reload
```

Open **http://localhost:8000**.

---

## Walkthrough

When you open the app you'll see a crimson header bar — **Sarvam Explorer POC** — with a **📋 History** button in the top-right corner, and a coloured band strip below it. The main area has two visible cards to start.

### Step 1 — Upload & Transcribe

The first card is labelled **Step 1 — Upload & Transcribe**.

1. Click **📂 Choose MP3 file** — a file picker opens. Select any `.mp3` audio file.
   After selecting, the button text changes to the filename.

2. The **Transcription language** dropdown is pre-set to **English**. If your audio is in another language (Hindi, Tamil, etc.) select it here — this tells Saarika ASR what language to expect.

3. Click **Analyse**.

   The button disables and two status messages appear in sequence:
   - `Transcribing audio…` — audio is being chunked and sent to **Saarika v2.5 ASR**
   - `Analysing transcript…` — transcript is being sent to **Sarvam-M LLM**

   For a 3-minute file, expect 20–40 seconds total.

4. When complete, the status reads **✓ Analysis complete.** and a **Results** card slides into view with two columns:

   **Left — Analysis panel**
   - **Language** — detected language name
   - **Tone** — coloured badge: green = Positive, red = Negative, yellow = Neutral, with a one-sentence explanation
   - **Topics** — flat cobalt tag chips
   - **Key Entities** — people, organisations, places as tag chips
   - **Summary** — 3–5 sentence summary of the content

   **Right — Transcript panel**
   - Full verbatim transcript, scrollable

### Step 2 — Translate & Speak

After Step 1 completes, a third card fades in: **Step 2 — Translate & Speak**.

1. Open the **Target language** dropdown. It lists all supported languages *except* the transcription language from Step 1 (no translating English → English). There is no default — you must pick one.

2. Once a language is selected, the **Submit** button activates. Click it.

   Status messages appear in sequence:
   - `Translating…` — text sent to **Sarvam Translate (Mayura v1)**
   - `Generating audio…` — translated text sent to **Bulbul v2 TTS**

3. When complete:
   - The translated text appears in a blue-bordered text box
   - A red waveform renders below it showing the generated audio
   - Click **▶ Play** to listen — the button toggles to **⏸ Pause** while playing
   - Click **⬇ Download MP3** to save the audio file

   If TTS fails (e.g. unsupported language combination), the translated text still appears with a warning — you can retry Step 2 by picking a different language.

---

## Session History

Every completed **Step 1** run is saved automatically. Click **📋 History** in the header to open the history panel, which slides in from the right.

Each card in the panel shows:
- Timestamp of the run
- Original filename
- Language flow — e.g. **English → Hindi** (or just **English** if Step 2 was never run)
- Tone badge
- Two-line summary excerpt

**To restore a session** — click anywhere on the card body. The panel closes and the main UI repopulates with that session's transcript, analysis, translated text, and waveform (if the audio file still exists).

**To delete a session** — click the **🗑** button on the card. An inline confirmation row appears: click **Confirm** to permanently delete the session record and its audio file, or **Cancel** to dismiss.

If there are no sessions yet, the panel shows **No saved sessions yet.**

---

## Testing the API Directly

The server exposes a **Swagger UI** at **http://localhost:8000/docs** — you can try every endpoint from the browser without writing any code.

For curl testing:

**Transcribe and analyse an MP3:**
```bash
curl -X POST http://localhost:8000/api/analyse \
  -F "file=@/path/to/audio.mp3" \
  -F "transcription_language=en-IN"
```
Response includes `transcript`, `analysis` (with `summary`, `tone`, `topics`, `key_entities`), and a `session_id` you can use in Step 2.

**Translate and generate TTS** (use the `session_id` from above):
```bash
curl -X POST http://localhost:8000/api/translate-and-speak \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "Your transcript text here",
    "source_language": "en-IN",
    "target_language": "hi-IN",
    "session_id": "<session_id from Step 1>"
  }'
```
Response includes `translated_text` and `audio_url`. Download the audio:
```bash
curl http://localhost:8000/api/audio/<filename-from-audio_url> -o output.mp3
```

**List all saved sessions:**
```bash
curl http://localhost:8000/api/sessions
```

**Delete a session:**
```bash
curl -X DELETE http://localhost:8000/api/sessions/<session_id>
```

---

## Supported Languages

| Language | Code |
|---|---|
| English | `en-IN` |
| Hindi | `hi-IN` |
| Bengali | `bn-IN` |
| Gujarati | `gu-IN` |
| Kannada | `kn-IN` |
| Malayalam | `ml-IN` |
| Marathi | `mr-IN` |
| Odia | `od-IN` |
| Punjabi | `pa-IN` |
| Tamil | `ta-IN` |
| Telugu | `te-IN` |

---

## Data Persistence (Docker)

| Docker volume | What's stored |
|---|---|
| `outputs` | Generated TTS `.mp3` files |
| `db_data` | SQLite session database (`data/sessions.db`) |

Uploaded MP3s are deleted from `uploads` immediately after transcription.

To wipe all sessions and audio:
```bash
docker-compose down -v
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `SARVAM_API_KEY` | Yes | — | Sarvam API subscription key |
| `PORT` | No | `8000` | Port the server listens on |
