"""SQLite WAL mode connection management and schema migration per Spec 04."""

import asyncio
from contextlib import asynccontextmanager
import os
import uuid
import aiosqlite

CHARACTERS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS characters (
    character_uuid TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    voice_scope TEXT NOT NULL DEFAULT '',
    character_key TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    is_narrator INTEGER NOT NULL DEFAULT 0 CHECK(is_narrator IN (0, 1)),
    aliases_json TEXT NOT NULL DEFAULT '[]',
    voice_instruct TEXT NOT NULL,
    reference_text TEXT NOT NULL,
    voice_status TEXT NOT NULL CHECK(
        voice_status IN ('PENDING', 'GENERATING', 'READY', 'FAILED')
    ) DEFAULT 'PENDING',
    embedding_pt_path TEXT,
    preview_mp3_path TEXT,
    voice_version INTEGER NOT NULL DEFAULT 1,
    artifact_provider TEXT,
    artifact_fingerprint TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(work_id) REFERENCES works(work_id) ON DELETE CASCADE,
    UNIQUE(work_id, voice_scope, character_key)
);
"""

REVIEW_COLUMNS = {
    "review_phase": "TEXT NOT NULL DEFAULT 'ANALYZING' CHECK(review_phase IN ('ANALYZING','AWAITING_APPROVAL','SYNTHESIZING','MASTERING','COMPLETED','FAILED'))",
    "review_revision": "INTEGER NOT NULL DEFAULT 0",
    "analysis_completed_at": "TEXT",
    "approved_run_generation": "INTEGER",
    "approved_review_revision": "INTEGER",
    "approved_at": "TEXT",
}

CANDIDATES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS voice_preview_candidates (
    candidate_uuid TEXT PRIMARY KEY,
    chapter_uuid TEXT NOT NULL REFERENCES chapters(chapter_uuid) ON DELETE CASCADE,
    character_uuid TEXT NOT NULL REFERENCES characters(character_uuid) ON DELETE CASCADE,
    run_generation INTEGER NOT NULL,
    base_voice_version INTEGER NOT NULL,
    voice_instruct TEXT NOT NULL,
    reference_text TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','GENERATING','READY','FAILED','REVOKED')),
    embedding_pt_path TEXT, preview_mp3_path TEXT, duration_ms INTEGER,
    artifact_provider TEXT, artifact_fingerprint TEXT,
    error_code TEXT, error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_preview_chapter ON voice_preview_candidates(chapter_uuid);
"""


async def isolate_chapter_cast(db, chapter_uuid):
    """Clone an unproven/shared scope without touching any other chapter's cast."""
    cursor = await db.execute("SELECT work_id, voice_scope FROM chapters WHERE chapter_uuid=?", (chapter_uuid,))
    work_id, scope = await cursor.fetchone()
    cursor = await db.execute("SELECT COUNT(*) FROM chapters WHERE work_id=? AND voice_scope=?", (work_id, scope))
    exclusive = (await cursor.fetchone())[0] == 1 and bool(scope)
    cursor = await db.execute("SELECT * FROM characters WHERE work_id=? AND voice_scope=?", (work_id, scope))
    columns = [c[0] for c in cursor.description]
    cast = [dict(zip(columns, row)) for row in await cursor.fetchall()]
    if sum(c['is_narrator'] for c in cast) > 1:
        raise ValueError('Ambiguous duplicate narrators in chapter cast')
    if not exclusive:
        scope = str(uuid.uuid4())
        for character in cast:
            old = character['character_uuid']
            character['character_uuid'] = str(uuid.uuid4())
            character['voice_scope'] = scope
            await db.execute(f"INSERT INTO characters ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", tuple(character[k] for k in columns))
            await db.execute("UPDATE segments SET character_uuid=? WHERE chapter_uuid=? AND character_uuid=?", (character['character_uuid'], chapter_uuid, old))
        await db.execute("UPDATE chapters SET voice_scope=? WHERE chapter_uuid=?", (scope, chapter_uuid))
    if not any(c['is_narrator'] for c in cast):
        await db.execute("""INSERT INTO characters(character_uuid,work_id,voice_scope,character_key,canonical_name,is_narrator,voice_instruct,reference_text)
            VALUES(?,?,?,'char_narrator','Narrator',1,?,?)""", (str(uuid.uuid4()), work_id, scope,
            'A warm, articulate, neutral-accented audiobook narrator with steady pacing and clear studio diction.',
            'The evening shadows lengthened across the quiet room as the story began to unfold.'))


async def validate_complete_script(db, chapter_uuid):
    cursor = await db.execute("SELECT total_chunks,llm_completed_chunks,work_id,voice_scope FROM chapters WHERE chapter_uuid=?", (chapter_uuid,))
    total, completed, work, scope = await cursor.fetchone()
    if total < 1 or completed != total:
        raise ValueError('All analysis chunks must finish before approval')
    cursor = await db.execute("""SELECT s.segment_order,s.chunk_index,s.text_content,c.work_id,c.voice_scope,c.voice_instruct
        FROM segments s LEFT JOIN characters c ON c.character_uuid=s.character_uuid
        WHERE s.chapter_uuid=? ORDER BY s.segment_order""", (chapter_uuid,))
    rows = await cursor.fetchall()
    if not rows or [r[0] for r in rows] != list(range(len(rows))):
        raise ValueError('Script must be nonempty with contiguous segment orders')
    if {r[1] for r in rows} != set(range(total)):
        raise ValueError('Script is missing analysis chunks')
    if any(not r[2].strip() or r[3] != work or r[4] != scope or not 10 <= len(r[5].strip()) <= 600 for r in rows):
        raise ValueError('Script has invalid text, speaker ownership or voice descriptions')
    cursor = await db.execute("SELECT COUNT(*) FROM characters WHERE work_id=? AND voice_scope=? AND is_narrator=1", (work, scope))
    if (await cursor.fetchone())[0] != 1:
        raise ValueError('Exactly one narrator is required')
    cursor = await db.execute('SELECT voice_instruct FROM characters WHERE work_id=? AND voice_scope=?',(work,scope))
    if any(not 10 <= len(r[0].strip()) <= 600 for r in await cursor.fetchall()):
        raise ValueError('Every character needs a valid voice description')
    return len(rows)

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -64000;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS works (
    work_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    fandoms_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS chapters (
    chapter_uuid TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    chapter_number INTEGER NOT NULL DEFAULT 1,
    chapter_title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK(
        status IN ('PENDING', 'STREAMING', 'MASTERING', 'COMPLETED', 'FAILED')
    ) DEFAULT 'PENDING',
    voice_scope TEXT NOT NULL DEFAULT '',
    run_generation INTEGER NOT NULL DEFAULT 0,
    state_revision INTEGER NOT NULL DEFAULT 0,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    llm_completed_chunks INTEGER NOT NULL DEFAULT 0,
    total_segments INTEGER NOT NULL DEFAULT 0,
    completed_segments INTEGER NOT NULL DEFAULT 0,
    master_mp3_path TEXT,
    master_version INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(work_id) REFERENCES works(work_id) ON DELETE CASCADE,
    UNIQUE(work_id, chapter_id)
);
""" + CHARACTERS_SCHEMA_SQL + """
CREATE TABLE IF NOT EXISTS segments (
    segment_uuid TEXT PRIMARY KEY,
    chapter_uuid TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    segment_order INTEGER NOT NULL,
    paragraph_index INTEGER,
    source_continuation INTEGER NOT NULL DEFAULT 0,
    character_uuid TEXT NOT NULL,
    text_content TEXT NOT NULL,
    delivery_instruct TEXT,
    is_paragraph_end INTEGER NOT NULL DEFAULT 0 CHECK(is_paragraph_end IN (0, 1)),
    audio_path TEXT,
    duration_ms INTEGER,
    status TEXT NOT NULL CHECK(
        status IN ('QUEUED', 'SYNTHESIZING', 'READY', 'FAILED')
    ) DEFAULT 'QUEUED',
    voice_version_used INTEGER NOT NULL DEFAULT 1,
    retry_count INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    claimed_generation INTEGER,
    claimed_at TEXT,
    last_error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(chapter_uuid) REFERENCES chapters(chapter_uuid) ON DELETE CASCADE,
    FOREIGN KEY(character_uuid) REFERENCES characters(character_uuid),
    UNIQUE(chapter_uuid, segment_order)
);

CREATE INDEX IF NOT EXISTS idx_segments_chapter_order
    ON segments(chapter_uuid, segment_order);

CREATE INDEX IF NOT EXISTS idx_segments_chapter_status
    ON segments(chapter_uuid, status);

CREATE INDEX IF NOT EXISTS idx_segments_character
    ON segments(chapter_uuid, character_uuid);

CREATE INDEX IF NOT EXISTS idx_characters_work
    ON characters(work_id);
"""


class DatabaseManager:
    """Manages SQLite connections and provides process-level write serialization."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.write_lock = asyncio.Lock()

    async def init_db(self) -> None:
        """Create tables, indexes, apply WAL pragmas, and run versioned migrations."""
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        async with self.write_lock:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executescript(SCHEMA_SQL)
                await db.commit()
                await self._run_migrations(db)

    async def _run_migrations(self, db: aiosqlite.Connection) -> None:
        """Apply additive migrations idempotently per Spec 07."""
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );
            """
        )
        await db.commit()

        cursor = await db.execute("SELECT version FROM schema_migrations;")
        applied = {row[0] for row in await cursor.fetchall()}

        # Migration 1: Additive columns for Spec 07
        if 1 not in applied:
            # Helper to inspect columns
            async def get_cols(table: str) -> set[str]:
                c = await db.execute(f"PRAGMA table_info({table});")
                return {r[1] for r in await c.fetchall()}

            chapter_cols = await get_cols("chapters")
            if "run_generation" not in chapter_cols:
                await db.execute("ALTER TABLE chapters ADD COLUMN run_generation INTEGER NOT NULL DEFAULT 0;")
            if "state_revision" not in chapter_cols:
                await db.execute("ALTER TABLE chapters ADD COLUMN state_revision INTEGER NOT NULL DEFAULT 0;")

            segment_cols = await get_cols("segments")
            if "paragraph_index" not in segment_cols:
                await db.execute("ALTER TABLE segments ADD COLUMN paragraph_index INTEGER;")
            if "source_continuation" not in segment_cols:
                await db.execute("ALTER TABLE segments ADD COLUMN source_continuation INTEGER NOT NULL DEFAULT 0;")
            if "claimed_by" not in segment_cols:
                await db.execute("ALTER TABLE segments ADD COLUMN claimed_by TEXT;")
            if "claimed_generation" not in segment_cols:
                await db.execute("ALTER TABLE segments ADD COLUMN claimed_generation INTEGER;")
            if "claimed_at" not in segment_cols:
                await db.execute("ALTER TABLE segments ADD COLUMN claimed_at TEXT;")
            if "last_error_code" not in segment_cols:
                await db.execute("ALTER TABLE segments ADD COLUMN last_error_code TEXT;")

            character_cols = await get_cols("characters")
            if "artifact_provider" not in character_cols:
                await db.execute("ALTER TABLE characters ADD COLUMN artifact_provider TEXT;")
            if "artifact_fingerprint" not in character_cols:
                await db.execute("ALTER TABLE characters ADD COLUMN artifact_fingerprint TEXT;")

            await db.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (1);")
            await db.commit()

        if 2 not in applied:
            # Rebuild the registry to allow independent profiles for each fresh run.
            await db.execute("PRAGMA foreign_keys = OFF;")
            try:
                await db.execute("BEGIN;")
                for table in ("chapters", "characters"):
                    cursor = await db.execute(f"PRAGMA table_info({table});")
                    columns = {row[1] for row in await cursor.fetchall()}
                    if "voice_scope" not in columns:
                        await db.execute(f"ALTER TABLE {table} ADD COLUMN voice_scope TEXT NOT NULL DEFAULT '';")

                await db.execute(CHARACTERS_SCHEMA_SQL.replace(
                    "IF NOT EXISTS characters", "characters_scoped", 1
                ))
                columns = (
                    "character_uuid, work_id, voice_scope, character_key, canonical_name, "
                    "is_narrator, aliases_json, voice_instruct, reference_text, voice_status, "
                    "embedding_pt_path, preview_mp3_path, voice_version, artifact_provider, "
                    "artifact_fingerprint, created_at, updated_at"
                )
                await db.execute(f"INSERT INTO characters_scoped ({columns}) SELECT {columns} FROM characters;")
                await db.execute("DROP TABLE characters;")
                await db.execute("ALTER TABLE characters_scoped RENAME TO characters;")
                await db.execute("CREATE INDEX idx_characters_work ON characters(work_id);")
                cursor = await db.execute("PRAGMA foreign_key_check;")
                if await cursor.fetchall():
                    raise RuntimeError("Voice scope migration would break foreign key references")
                await db.execute("INSERT INTO schema_migrations (version) VALUES (2);")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            finally:
                await db.execute("PRAGMA foreign_keys = ON;")

        if 3 not in applied:
            try:
                await db.execute("BEGIN")
                cursor = await db.execute("PRAGMA table_info(chapters)")
                columns = {r[1] for r in await cursor.fetchall()}
                for name, definition in REVIEW_COLUMNS.items():
                    if name not in columns:
                        await db.execute(f"ALTER TABLE chapters ADD COLUMN {name} {definition}")
                cursor = await db.execute("SELECT chapter_uuid,status,master_mp3_path FROM chapters")
                for chapter_uuid, status, master in await cursor.fetchall():
                    if status == 'COMPLETED' and master and os.path.isfile(master):
                        await db.execute("""UPDATE chapters SET review_phase='COMPLETED', review_revision=1,
                            analysis_completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), approved_run_generation=run_generation,
                            approved_review_revision=1,approved_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE chapter_uuid=?""", (chapter_uuid,))
                        continue
                    await db.execute("""UPDATE chapters SET run_generation=run_generation+1,state_revision=state_revision+1,
                        completed_segments=0,master_mp3_path=NULL,master_version=master_version+1 WHERE chapter_uuid=?""", (chapter_uuid,))
                    await db.execute("""UPDATE segments SET status='QUEUED',audio_path=NULL,duration_ms=NULL,
                        claimed_by=NULL,claimed_generation=NULL,claimed_at=NULL WHERE chapter_uuid=?""", (chapter_uuid,))
                    try:
                        await isolate_chapter_cast(db, chapter_uuid)
                        count = await validate_complete_script(db, chapter_uuid)
                    except ValueError:
                        await db.execute("""UPDATE chapters SET status='FAILED',review_phase='FAILED',
                            error_message='Reload chapter to finish analysis; legacy execution was not approved.' WHERE chapter_uuid=?""", (chapter_uuid,))
                    else:
                        await db.execute("""UPDATE chapters SET status='PENDING',review_phase='AWAITING_APPROVAL',review_revision=1,
                            total_segments=?,analysis_completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),error_message=NULL WHERE chapter_uuid=?""", (count, chapter_uuid))
                await db.execute("INSERT INTO schema_migrations(version) VALUES(3)")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        await db.executescript(CANDIDATES_SCHEMA_SQL)
        await db.commit()

    @asynccontextmanager
    async def get_connection(self):
        """Async context manager yielding a connection with standard PRAGMAs."""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute("PRAGMA busy_timeout = 10000;")
            yield conn
