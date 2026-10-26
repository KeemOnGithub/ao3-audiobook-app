# 🎙️ AO3 Audiobook Studio

> **Transform Archive of Our Own (AO3) fanfiction stories into immersive, multi-character voiced audiobooks powered by LLMs and Qwen3-TTS.**

AO3 Audiobook Studio is a full-stack, end-to-end literary audio platform. It extracts fanfiction directly from AO3, uses an advanced LLM to deconstruct the prose into narration and character dialogue, identifies speaking characters, allows you to review and customize cast voices, and synthesizes high-fidelity audio with synchronized text highlighting.

---

## ✨ Features

- **📖 Frictionless AO3 Ingestion**  
  Paste any AO3 work or chapter URL. The app automatically navigates adult content warnings, cleans HTML artifacts, extracts story metadata, and preserves 100% of the author's original prose, quotes, and punctuation.
  
- **🎭 Intelligent Cast & Script Deconstruction**  
  Powered by LLMs (OpenAI-compatible, e.g. GLM-5.2, GPT-4o), the engine parses narrative text into chronological segments, accurately distinguishing narration from spoken dialogue and assigning lines to characters using context and turn-taking.

- **🎛️ Pre-Synthesis Review & Greenroom**  
  Full human-in-the-loop control *before* audio generation:
  - Review the extracted character cast and speaker assignments.
  - Rename characters, add missing speakers, or merge duplicates.
  - Reassign dialogue lines between characters with a single click.
  - Audition instant 15-second voice previews and customize vocal style prompts.
  - Explicitly approve the cast before generating the final audio.

- **🔊 Studio-Grade Audio Pipeline (Qwen3-TTS)**  
  - Voice design and zero-shot voice cloning per character.
  - Realistic timing with pre-baked silence intervals (intra-paragraph pauses, paragraph ends, and scene breaks).
  - Broadcast-standard loudness normalization (`-19 LUFS`) via `pyloudnorm`.
  - Full chapter concatenation and mastering into a single downloadable MP3 with ID3 metadata tags.

- **🎧 Gapless Streaming Web Player**  
  - Real-time Server-Sent Events (SSE) stream progress and ready audio segments.
  - Custom HTML5 / Web Audio playback engine with HTTP 206 byte-range streaming.
  - Real-time karaoke-style sentence highlighting synchronized with audio playback.
  - Dynamic playback queue prioritizing segments around your current seek position.

- **📚 My Audio Bookshelf (Library)**  
  SQLite-backed personal library that tracks your fics, reading progress, and chapter audio states (`Ready`, `Synthesizing`, `Awaiting Approval`, `Not Started`), with real-time title search and filtering.

- **☕ Cozy Reading Aesthetics**  
  Wattpad-inspired reader interface featuring:
  - **Themes:** Dark Charcoal, Warm Sepia Paper, and Clean Light mode.
  - **Typography:** Seamless toggle between modern sans-serif (*Plus Jakarta Sans*) and literary serif (*Lora*), with instant font scaling (`A-` / `A+`).

---

## 🏗️ System Architecture

```mermaid
flowchart TD
    User["User / Web Browser"]

    subgraph Frontend ["Frontend Client (Port 5173)"]
        UI["React 18 + Tailwind CSS + Lucide"]
        Store["Zustand App Store"]
        Player["Gapless Web Audio Engine"]
        SSEClient["SSE Event Listener"]
    end

    subgraph Backend ["Backend Orchestrator (Port 8000)"]
        FastAPI_App["FastAPI REST & SSE Router"]
        Ingestion["AO3 Scraper & Sanitizer (curl_cffi + lxml)"]
        LLM["LLM Script Analyzer (OpenAI-compatible)"]
        Orchestrator["Job Orchestrator & Priority Queue"]
        DB[(SQLite Database)]
    end

    subgraph TTS ["TTS Microservice (Port 8002)"]
        TTS_App["FastAPI TTS Service"]
        DSP["DSP Engine (-19 LUFS, Pauses, MP3 CBR)"]
        Masterer["Chapter Concatenator & Tag Masterer"]
    end

    subgraph VoiceEngine ["Voice Generation Options"]
        Mock["Mock Simulation Mode (Local CPU)"]
        Remote["Remote Qwen-TTS Endpoint (Base URL + API Key)"]
    end

    User <--> UI
    UI <--> Store
    Store <--> Player
    UI <--> SSEClient

    UI -- "REST API (Chapters, Review, Audio)" --> FastAPI_App
    SSEClient <-- "Server-Sent Events" -- FastAPI_App
    FastAPI_App <--> Orchestrator
    Orchestrator <--> Ingestion
    Orchestrator <--> LLM
    Orchestrator <--> DB

    Orchestrator -- "Internal HTTP API" --> TTS_App
    TTS_App <--> DSP
    TTS_App <--> Masterer
    TTS_App --> Mock
    TTS_App --> Remote
```

---

## 📋 Prerequisites & External Services

Make sure you have the following installed and configured:

1. **Python 3.10+** (Tested on Python 3.11 and 3.12)
2. **Node.js 18+** and **npm**
3. **FFmpeg** (Required for audio encoding, pause generation, and chapter mastering)
   - **Windows:** `winget install Gyan.FFmpeg` or `choco install ffmpeg`
   - **macOS:** `brew install ffmpeg`
   - **Linux (Ubuntu/Debian):** `sudo apt update && sudo apt install ffmpeg`
   - *Verify installation in terminal:* `ffmpeg -version`
4. **An LLM API Key**: Any OpenAI-compatible endpoint (e.g. OpenAI, OpenRouter, DeepSeek, RootSys, etc.) used for narrative parsing and character identification.
5. **A Qwen-TTS Serving Endpoint (Base URL & API Key)**:
   - To synthesize real multi-voiced audio, you need access to a running Qwen-TTS serving endpoint specified via `REMOTE_TTS_URL` (and `TTS_API_KEY` if your endpoint requires authentication).
   - **Model Used:** This application was developed and tested specifically using:
     - **`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`** for generating baseline acoustic character voice samples and 5-second audition clips.
     - **`Qwen/Qwen3-TTS-12Hz-1.7B-Base`** for speaker embedding extraction and continuous segment speech synthesis.
   - ⚠️ **Important:** Other TTS models, versions, or model families are **untested**.
   - *Note for local development / testing without a GPU:* You can set `TTS_USE_MOCK=true` in `.env` to run the entire pipeline with simulated audio waveforms without needing an active Qwen-TTS server.

---

## 🚀 Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/your-username/audiobook-tts.git
cd audiobook-tts
```

### 2. Set Up the Python Virtual Environment
```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Windows (CMD):
.\.venv\Scripts\activate.bat
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Set Up the Frontend Client
```bash
cd frontend
npm install
cd ..
```

### 4. Configure Environment Variables
Copy the template configuration file:
```bash
cp .env.example .env
```

Open `.env` in your text editor and configure your endpoints:
```ini
# Server Ports & Paths
HOST=0.0.0.0
PORT=8000
DATA_DIR=./data
DATABASE_PATH=./data/ao3_audiobook.db

# TTS Microservice & Engine Configuration
TTS_SERVICE_URL=http://localhost:8002
FFMPEG_BINARY=ffmpeg

# --- Choose TTS Mode ---
# For development/testing without a GPU (generates simulated audio):
TTS_USE_MOCK=true

# For real voice synthesis with Qwen3-TTS:
# Set TTS_USE_MOCK=false and provide your Qwen-TTS server base URL:
# TTS_USE_MOCK=false
# REMOTE_TTS_URL=https://your-qwen-tts-server-url
# TTS_API_KEY=your_api_key_if_required

# LLM Script Deconstruction Service (OpenAI-compatible)
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_llm_api_key_here
LLM_MODEL=glm-5.2
LLM_MAX_TOKENS=8192
```

---

## 🏃 Running the Application

### Method A: One-Click Launch (Windows)
Double-click or run each batch script in separate terminal windows:
1. `run_tts.bat` (Starts the TTS microservice on port 8002)
2. `run_backend.bat` (Starts the Backend orchestrator on port 8000)
3. `run_frontend.bat` (Starts the Frontend Vite server on port 5173)

### Method B: Manual CLI Launch (Cross-Platform)

Open **three** separate terminal windows:

#### Terminal 1: TTS Microservice
```bash
# Activate virtual environment first
source .venv/bin/activate  # or .\.venv\Scripts\activate on Windows
uvicorn tts_service.app:app --host 0.0.0.0 --port 8002 --reload
```

#### Terminal 2: Backend Orchestrator
```bash
# Activate virtual environment first
source .venv/bin/activate  # or .\.venv\Scripts\activate on Windows
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Terminal 3: Frontend Client
```bash
cd frontend
npm run dev
```

Now open your browser and navigate to: **`http://localhost:5173`** 🎉

---

## 🎮 How to Use the Studio

```text
  1. Paste AO3 URL   ──▶   2. Review Cast & Script   ──▶   3. Audition Voices   ──▶   4. Approve & Play
 (Work or Chapter)        (Rename, merge, reassign)        (15s audio previews)       (Stream or Download)
```

1. **Load a Chapter:**
   - Paste any valid AO3 URL (e.g. `https://archiveofourown.org/works/12345678/chapters/87654321`) into the top bar and click **Load**.
2. **Review Script & Characters:**
   - Once analysis completes, the chapter enters the `Awaiting Approval` phase.
   - The reader displays dialogue lines attributed to distinct characters alongside narration.
   - Open the **Cast Greenroom** on the right to inspect characters, aliases, and physical acoustic descriptions.
   - Use the **Review Toolbar** or segment controls to fix names, reassign misattributed lines, or merge duplicate characters.
3. **Audition Voices:**
   - In the Cast Greenroom, click **Audition** on any character to generate an instant 15-second sample preview.
   - Tweak the acoustic voice description (e.g. *"A low, gravelly baritone with a steady cadence"*).
   - Click **Apply** on the preview candidate you wish to use.
4. **Approve & Synthesize:**
   - Click **Approve & Synthesize** on the review toolbar.
   - The engine begins synthesizing chapter audio segments in priority playback order.
   - Hit **Play** on the floating audio dock to listen immediately as audio streams in gaplessly!
5. **Download Master MP3:**
   - When all segments finish synthesizing, the chapter is mastered into a single normalized MP3 with ID3 metadata tags.
   - Click **Download MP3** to save the finished audiobook locally.

---

## 🧪 Testing & Verification

The project includes an extensive test suite for both backend and frontend.

### Backend Tests (pytest)
Runs 135+ automated tests verifying ingestion, LLM parsing, SQLite migrations, review authorization CAS transactions, audio DSP, and playback streaming:
```bash
pytest -v
```

### Frontend Tests (Vitest)
Runs 95+ unit and component tests verifying the gapless audio engine, SSE reconnection, reading preferences, and review UI:
```bash
cd frontend
npm test
```

### Frontend Production Build
Verify TypeScript compilation and Vite bundling:
```bash
cd frontend
npm run build
```

---

## 📁 Repository Structure

```text
audiobook-tts/
├── backend/                  # Backend FastAPI Orchestrator
│   ├── app/
│   │   ├── api/              # REST endpoints, SSE stream, audio streaming
│   │   ├── db/               # SQLite connection, migrations, review repository
│   │   ├── ingestion/        # AO3 scraper, sanitizer, URL validator
│   │   ├── orchestration/    # Job orchestrator, SSE event bus, priority queues
│   │   └── scripting/        # LLM prompt director and JSON schema analyzers
│   └── tests/                # Comprehensive backend pytest suite
├── frontend/                 # React 18 + Vite + Tailwind CSS Client
│   ├── src/
│   │   ├── components/       # Reader, Bookshelf, Review Toolbar, Audio Dock, Greenroom
│   │   ├── services/         # Gapless audio engine, SSE manager, API client
│   │   ├── store/            # Zustand state management (App, Reader preferences)
│   │   └── test/             # Vitest component and logic test suite
├── tts_service/              # Qwen3-TTS Microservice
│   ├── app.py                # Microservice endpoints (synthesis, preview, stitch)
│   ├── dsp.py                # Audio normalization (-19 LUFS), CBR encoding
│   ├── models.py             # Qwen3-TTS remote client & mock simulation engine
│   ├── silence.py            # Pre-baked pause generation
│   └── stitcher.py           # FFmpeg chapter concatenation and mastering
├── data/                     # Local runtime data root (voices, silences, master MP3s)
├── run_backend.bat           # Windows startup script for Backend
├── run_frontend.bat          # Windows startup script for Frontend
├── run_tts.bat               # Windows startup script for TTS Microservice
├── .env.example              # Environment configuration template
└── requirements.txt          # Python dependencies
```

---

## 📄 License & Fair Use

This software is intended for personal, non-commercial use to convert accessible web literature into private audiobooks. Please respect author rights, AO3 terms of service, and original creators' work.
