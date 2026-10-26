"""ASGI application entrypoint for AO3 Audiobook Backend per Spec 05."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.routes import router
from backend.app.db.connection import DatabaseManager
from backend.app.db.repository import AudiobookRepository
from backend.app.db.review_repository import ReviewConflict
from backend.app.orchestration.event_bus import ChapterEventBus
from backend.app.orchestration.orchestrator import JobOrchestrator
from backend.app.scripting.analyzer import LLMScriptAnalyzer

DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "./data/ao3_audiobook.db")
DEFAULT_DATA_DIR = os.getenv("DATA_DIR", "./data")
DEFAULT_TTS_URL = os.getenv("TTS_SERVICE_URL", "http://localhost:8002")
DEFAULT_LLM_URL = os.getenv("LLM_BASE_URL", "https://rootsys.cloud/v1")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if hasattr(app.state, "orchestrator") and app.state.orchestrator:
        yield
        return

    # Initialize DB & Tables
    db_path = getattr(app.state, "db_path", DEFAULT_DB_PATH)
    data_dir = getattr(app.state, "data_dir", DEFAULT_DATA_DIR)
    tts_url = getattr(app.state, "tts_url", DEFAULT_TTS_URL)

    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    db_mgr = DatabaseManager(db_path)
    await db_mgr.init_db()

    repo = AudiobookRepository(db_mgr)
    event_bus = ChapterEventBus()
    llm_analyzer = LLMScriptAnalyzer(
        base_url=os.getenv("LLM_BASE_URL", DEFAULT_LLM_URL),
        api_key=os.getenv("LLM_API_KEY", "dummy"),
        model=os.getenv("LLM_MODEL", "glm-5.2"),
    )

    orchestrator = JobOrchestrator(
        repository=repo,
        event_bus=event_bus,
        llm_analyzer=llm_analyzer,
        tts_base_url=tts_url,
        data_dir=data_dir,
    )

    # Attach to app state
    app.state.db_manager = db_mgr
    app.state.repository = repo
    app.state.event_bus = event_bus
    app.state.orchestrator = orchestrator

    # Run Crash Recovery Routine
    await orchestrator.recover_interrupted_jobs()

    # Start workers
    await orchestrator.start()

    yield

    # Shutdown
    await orchestrator.stop()


app = FastAPI(
    title="AO3 Multi-Character Voiced Audiobook API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS Middleware per Spec 05 section 8.1
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Last-Event-ID"],
)

app.include_router(router)


@app.exception_handler(ReviewConflict)
async def review_conflict_handler(request: Request, exc: ReviewConflict):
    chapter_uuid = request.path_params.get('chapter_uuid')
    repo = getattr(request.app.state,'repository',None)
    chapter = await repo.get_chapter_by_uuid(chapter_uuid) if repo and chapter_uuid else None
    return JSONResponse(status_code=exc.status_code,content={
        'code':exc.code,'message':str(exc),
        'run_generation':chapter.run_generation if chapter else None,
        'review_revision':chapter.review_revision if chapter else None})


# RFC 7807 Error Response format
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    headers = dict(exc.headers) if exc.headers else {}
    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={
            "type": f"https://errors.ao3audiobook.internal/{exc.status_code}",
            "title": "API Error",
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": request.url.path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

