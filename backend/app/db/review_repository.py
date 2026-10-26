"""Atomic chapter review transactions. No network calls or nested write locks."""

from backend.app.db.connection import isolate_chapter_cast, validate_complete_script
import hashlib
import json
import uuid


class ReviewConflict(ValueError):
    def __init__(self, message, code='REVIEW_CONFLICT', status_code=409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ReviewRepository:
    async def get_review_snapshot_records(self,chapter_uuid):
        async with self.db_manager.get_connection() as conn:
            await conn.execute('BEGIN')
            cursor=await conn.execute('SELECT * FROM chapters WHERE chapter_uuid=?',(chapter_uuid,))
            row=await cursor.fetchone()
            if not row:
                raise ReviewConflict('Chapter not found','NOT_FOUND',404)
            chapter=self._row_to_chapter(row)
            cursor=await conn.execute("""SELECT c.* FROM characters c WHERE c.work_id=? AND c.voice_scope=?
                ORDER BY c.is_narrator DESC,(SELECT MIN(s.segment_order) FROM segments s WHERE s.chapter_uuid=? AND s.character_uuid=c.character_uuid) IS NULL,
                (SELECT MIN(s.segment_order) FROM segments s WHERE s.chapter_uuid=? AND s.character_uuid=c.character_uuid),c.created_at,c.character_uuid""",
                (chapter.work_id,chapter.voice_scope,chapter_uuid,chapter_uuid))
            cast=[self._row_to_character(r) for r in await cursor.fetchall()]
            cursor=await conn.execute('SELECT * FROM segments WHERE chapter_uuid=? ORDER BY segment_order',(chapter_uuid,))
            segments=[self._row_to_segment(r) for r in await cursor.fetchall()]
            cursor=await conn.execute('SELECT * FROM voice_preview_candidates WHERE chapter_uuid=? ORDER BY created_at,candidate_uuid',(chapter_uuid,))
            candidates=[dict(r) for r in await cursor.fetchall()]
            blockers=[]
            if chapter.review_phase!='AWAITING_APPROVAL':
                blockers.append({'code':'WRONG_PHASE','message':'Complete analysis and return to review before approval.'})
            if not chapter.analysis_completed_at:
                blockers.append({'code':'INCOMPLETE_ANALYSIS','message':'Complete chapter analysis is required.'})
            try:
                await validate_complete_script(conn,chapter_uuid)
            except ValueError as exc:
                blockers.append({'code':'INVALID_SCRIPT','message':str(exc)})
            return chapter,cast,segments,candidates,blockers

    async def rename_review_character(self,chapter_uuid,character_uuid,generation,revision,canonical_name):
        return await self._correct_review(chapter_uuid,generation,revision,'rename',character_uuid=character_uuid,canonical_name=canonical_name)

    async def add_review_character(self,chapter_uuid,generation,revision,canonical_name,voice_instruct):
        return await self._correct_review(chapter_uuid,generation,revision,'add',canonical_name=canonical_name,voice_instruct=voice_instruct)

    async def delete_review_character(self,chapter_uuid,character_uuid,generation,revision):
        return await self._correct_review(chapter_uuid,generation,revision,'delete',character_uuid=character_uuid)

    async def merge_review_characters(self,chapter_uuid,generation,revision,source_character_uuid,target_character_uuid):
        return await self._correct_review(chapter_uuid,generation,revision,'merge',character_uuid=source_character_uuid,target_character_uuid=target_character_uuid)

    async def reassign_review_speakers(self,chapter_uuid,generation,revision,segment_uuids,target_character_uuid):
        return await self._correct_review(chapter_uuid,generation,revision,'reassign',segment_uuids=segment_uuids,target_character_uuid=target_character_uuid)

    async def _correct_review(self,chapter_uuid,generation,revision,operation,**values):
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                chapter = await self._review_identity(conn,chapter_uuid,generation,revision,('AWAITING_APPROVAL',))
                cursor = await conn.execute('SELECT COUNT(*) FROM chapters WHERE work_id=? AND voice_scope=?',(chapter['work_id'],chapter['voice_scope']))
                if not chapter['voice_scope'] or (await cursor.fetchone())[0] != 1:
                    raise ReviewConflict('Chapter cast must be isolated before editing','SHARED_SCOPE')
                name = values.get('canonical_name','').strip()
                if operation in ('rename','add') and not 1 <= len(name) <= 120:
                    raise ReviewConflict('Name must contain 1-120 trimmed characters','INVALID_NAME',422)
                character = await self._owned_character(conn,chapter,values['character_uuid']) if 'character_uuid' in values else None
                target = await self._owned_character(conn,chapter,values['target_character_uuid']) if 'target_character_uuid' in values else None
                if operation == 'rename':
                    if character['canonical_name'] == name:
                        return False
                    await conn.execute('UPDATE characters SET canonical_name=? WHERE character_uuid=?',(name,character['character_uuid']))
                elif operation == 'add':
                    prompt = values['voice_instruct'].strip()
                    if not 10 <= len(prompt) <= 600:
                        raise ReviewConflict('Voice description must contain 10-600 characters','INVALID_VOICE_INPUT',422)
                    identifier = str(uuid.uuid4())
                    await conn.execute("""INSERT INTO characters(character_uuid,work_id,voice_scope,character_key,canonical_name,
                        voice_instruct,reference_text) VALUES(?,?,?,?,?,?,?)""",(identifier,chapter['work_id'],chapter['voice_scope'],
                        'char_manual_'+uuid.UUID(identifier).hex,name,prompt,
                        'The evening shadows lengthened across the quiet room as the story began to unfold.'))
                elif operation in ('merge','delete'):
                    if character['is_narrator'] or (target and target['is_narrator']):
                        raise ReviewConflict('Narrator cannot be deleted or merged; reassign segments instead','NARRATOR_PROTECTED',422)
                    if operation == 'merge':
                        if character['character_uuid'] == target['character_uuid']:
                            raise ReviewConflict('Merge source and target must differ','INVALID_MERGE',422)
                        await conn.execute("""UPDATE segments SET character_uuid=?,status='QUEUED',audio_path=NULL,duration_ms=NULL,
                            claimed_by=NULL,claimed_generation=NULL,claimed_at=NULL WHERE chapter_uuid=? AND character_uuid=?""",
                            (target['character_uuid'],chapter_uuid,character['character_uuid']))
                    cursor = await conn.execute('SELECT COUNT(*) FROM segments WHERE character_uuid=?',(character['character_uuid'],))
                    if (await cursor.fetchone())[0]:
                        raise ReviewConflict('Character is used; reassign or merge first','CHARACTER_IN_USE')
                    await conn.execute('DELETE FROM voice_preview_candidates WHERE character_uuid=?',(character['character_uuid'],))
                    await conn.execute('DELETE FROM characters WHERE character_uuid=?',(character['character_uuid'],))
                elif operation == 'reassign':
                    identifiers = values['segment_uuids']
                    if not 1 <= len(identifiers) <= 1000 or len(set(identifiers)) != len(identifiers):
                        raise ReviewConflict('Select 1-1000 distinct segments','INVALID_SELECTION',422)
                    placeholders = ','.join('?' for _ in identifiers)
                    cursor = await conn.execute(f'SELECT segment_uuid,character_uuid FROM segments WHERE chapter_uuid=? AND segment_uuid IN ({placeholders})',(chapter_uuid,*identifiers))
                    rows = await cursor.fetchall()
                    if len(rows) != len(identifiers):
                        raise ReviewConflict('Selected segment not found in chapter','NOT_FOUND',404)
                    if all(r['character_uuid'] == target['character_uuid'] for r in rows):
                        return False
                    await conn.execute(f"""UPDATE segments SET character_uuid=?,status='QUEUED',audio_path=NULL,duration_ms=NULL,
                        claimed_by=NULL,claimed_generation=NULL,claimed_at=NULL WHERE chapter_uuid=? AND segment_uuid IN ({placeholders})""",
                        (target['character_uuid'],chapter_uuid,*identifiers))
                await conn.execute('UPDATE chapters SET review_revision=review_revision+1,state_revision=state_revision+1 WHERE chapter_uuid=?',(chapter_uuid,))
                await conn.commit()
                return True

    @staticmethod
    async def _owned_character(conn, chapter, character_uuid):
        cursor = await conn.execute('SELECT * FROM characters WHERE character_uuid=? AND work_id=? AND voice_scope=?',
            (character_uuid,chapter['work_id'],chapter['voice_scope']))
        character = await cursor.fetchone()
        if not character:
            raise ReviewConflict('Character not found in chapter', 'NOT_FOUND',404)
        return character

    async def get_voice_preview_candidates(self, chapter_uuid):
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute('SELECT * FROM voice_preview_candidates WHERE chapter_uuid=? ORDER BY created_at,candidate_uuid', (chapter_uuid,))
            return [dict(r) for r in await cursor.fetchall()]

    async def get_voice_preview_candidate(self, chapter_uuid, candidate_uuid):
        async with self.db_manager.get_connection() as conn:
            cursor = await conn.execute('SELECT * FROM voice_preview_candidates WHERE chapter_uuid=? AND candidate_uuid=?', (chapter_uuid,candidate_uuid))
            row = await cursor.fetchone()
            if not row:
                raise ReviewConflict('Preview candidate not found','NOT_FOUND',404)
            return dict(row)

    async def create_voice_preview_candidate(self, chapter_uuid, character_uuid, generation, revision, voice_instruct, reference_text=None):
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                chapter = await self._review_identity(conn,chapter_uuid,generation,revision,('AWAITING_APPROVAL',))
                character = await self._owned_character(conn,chapter,character_uuid)
                prompt = ' '.join(voice_instruct.split())
                if reference_text is None:
                    cursor = await conn.execute('SELECT text_content FROM segments WHERE chapter_uuid=? AND character_uuid=? ORDER BY segment_order', (chapter_uuid,character_uuid))
                    reference_text = next((' '.join(r[0].split())[:250] for r in await cursor.fetchall() if len(' '.join(r[0].split())) >= 20),
                        'The evening shadows lengthened across the quiet room as the story began to unfold.')
                sample = ' '.join(reference_text.split())
                if not 10 <= len(prompt) <= 600 or not 20 <= len(sample) <= 250:
                    raise ReviewConflict('Invalid voice prompt or reference sample length','INVALID_VOICE_INPUT',422)
                candidate = str(uuid.uuid4())
                digest = hashlib.sha256(json.dumps([prompt,sample],ensure_ascii=True).encode()).hexdigest()
                await conn.execute("""INSERT INTO voice_preview_candidates(candidate_uuid,chapter_uuid,character_uuid,
                    run_generation,base_voice_version,voice_instruct,reference_text,input_digest) VALUES(?,?,?,?,?,?,?,?)""",
                    (candidate,chapter_uuid,character_uuid,generation,character['voice_version'],prompt,sample,digest))
                await conn.execute('UPDATE chapters SET state_revision=state_revision+1 WHERE chapter_uuid=?',(chapter_uuid,))
                await conn.commit()
        return await self.get_voice_preview_candidate(chapter_uuid,candidate)

    async def update_voice_preview_candidate(self, candidate_uuid, status, data=None):
        data = data or {}
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute("""SELECT p.* FROM voice_preview_candidates p
                    JOIN chapters ch ON ch.chapter_uuid=p.chapter_uuid JOIN characters c ON c.character_uuid=p.character_uuid
                    WHERE p.candidate_uuid=? AND p.status IN ('PENDING','GENERATING') AND ch.review_phase='AWAITING_APPROVAL'
                    AND ch.run_generation=p.run_generation AND c.voice_version=p.base_voice_version
                    AND c.work_id=ch.work_id AND c.voice_scope=ch.voice_scope""",(candidate_uuid,))
                candidate = await cursor.fetchone()
                if not candidate:
                    return False
                await conn.execute("""UPDATE voice_preview_candidates SET status=?,embedding_pt_path=?,preview_mp3_path=?,
                    duration_ms=?,artifact_provider=?,artifact_fingerprint=?,error_code=?,error_message=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE candidate_uuid=?""",
                    (status,data.get('embedding_path'),data.get('preview_audio_path'),data.get('duration_ms'),
                     data.get('artifact_provider'),data.get('artifact_fingerprint'),data.get('error_code'),data.get('error_message'),candidate_uuid))
                await conn.execute('UPDATE chapters SET state_revision=state_revision+1 WHERE chapter_uuid=?',(candidate['chapter_uuid'],))
                await conn.commit()
                return True

    async def apply_voice_preview_candidate(self, chapter_uuid, candidate_uuid, generation, revision):
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                chapter = await self._review_identity(conn,chapter_uuid,generation,revision,('AWAITING_APPROVAL',))
                cursor = await conn.execute('SELECT * FROM voice_preview_candidates WHERE candidate_uuid=? AND chapter_uuid=?',(candidate_uuid,chapter_uuid))
                candidate = await cursor.fetchone()
                if not candidate:
                    raise ReviewConflict('Candidate not found','NOT_FOUND',404)
                character = await self._owned_character(conn,chapter,candidate['character_uuid'])
                if (candidate['status'] != 'READY' or candidate['run_generation'] != generation
                        or candidate['base_voice_version'] != character['voice_version'] or not candidate['embedding_pt_path']
                        or not candidate['preview_mp3_path']):
                    raise ReviewConflict('Candidate is stale, unavailable or already applied','STALE_CANDIDATE')
                await conn.execute("""UPDATE characters SET voice_instruct=?,reference_text=?,voice_version=voice_version+1,
                    voice_status='READY',embedding_pt_path=?,preview_mp3_path=?,artifact_provider=?,artifact_fingerprint=?
                    WHERE character_uuid=?""",(candidate['voice_instruct'],candidate['reference_text'],candidate['embedding_pt_path'],
                    candidate['preview_mp3_path'],candidate['artifact_provider'],candidate['artifact_fingerprint'],character['character_uuid']))
                await conn.execute("UPDATE voice_preview_candidates SET status='REVOKED' WHERE chapter_uuid=? AND character_uuid=?",(chapter_uuid,character['character_uuid']))
                await conn.execute('UPDATE chapters SET review_revision=review_revision+1,state_revision=state_revision+1 WHERE chapter_uuid=?',(chapter_uuid,))
                await conn.commit()

    @staticmethod
    async def _review_identity(conn, chapter_uuid, generation, revision, phases=None):
        cursor = await conn.execute('SELECT * FROM chapters WHERE chapter_uuid=?', (chapter_uuid,))
        chapter = await cursor.fetchone()
        if not chapter:
            raise ReviewConflict('Chapter not found', 'NOT_FOUND', 404)
        if chapter['run_generation'] != generation or chapter['review_revision'] != revision:
            raise ReviewConflict('Review changed; refresh before trying again', 'STALE_REVIEW')
        if phases and chapter['review_phase'] not in phases:
            raise ReviewConflict('This operation is not allowed in the current phase', 'WRONG_PHASE')
        return chapter

    async def finalize_chapter_review(self, chapter_uuid, expected_generation):
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor = await conn.execute('SELECT * FROM chapters WHERE chapter_uuid=?', (chapter_uuid,))
                chapter = await cursor.fetchone()
                if not chapter or chapter['run_generation'] != expected_generation:
                    raise ReviewConflict('Analysis generation was replaced', 'STALE_GENERATION')
                if chapter['analysis_completed_at'] and chapter['review_phase'] == 'AWAITING_APPROVAL':
                    return False
                if chapter['review_phase'] != 'ANALYZING':
                    raise ReviewConflict('Analysis cannot finalize in this phase', 'WRONG_PHASE')
                await isolate_chapter_cast(conn, chapter_uuid)
                try:
                    count = await validate_complete_script(conn, chapter_uuid)
                except ValueError as exc:
                    raise ReviewConflict(str(exc), 'INCOMPLETE_ANALYSIS') from exc
                await conn.execute("""UPDATE chapters SET review_phase='AWAITING_APPROVAL',status='PENDING',
                    review_revision=review_revision+1,state_revision=state_revision+1,total_segments=?,
                    analysis_completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                    approved_run_generation=NULL,approved_review_revision=NULL,approved_at=NULL,error_message=NULL
                    WHERE chapter_uuid=?""", (count, chapter_uuid))
                await conn.commit()
                return True

    async def approve_chapter_review(self, chapter_uuid, expected_generation, expected_revision):
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                ch = await self._review_identity(conn, chapter_uuid, expected_generation, expected_revision)
                if (ch['approved_run_generation'] == expected_generation
                        and ch['approved_review_revision'] == expected_revision
                        and ch['approved_at'] and ch['review_phase'] in ('SYNTHESIZING','MASTERING','COMPLETED')):
                    return False
                if ch['review_phase'] != 'AWAITING_APPROVAL' or not ch['analysis_completed_at']:
                    raise ReviewConflict('Complete chapter review is required', 'NOT_APPROVABLE')
                try:
                    await validate_complete_script(conn, chapter_uuid)
                except ValueError as exc:
                    raise ReviewConflict(str(exc), 'INCOMPLETE_ANALYSIS') from exc
                await conn.execute("""UPDATE chapters SET review_phase='SYNTHESIZING',status='STREAMING',
                    approved_run_generation=run_generation,approved_review_revision=review_revision,
                    approved_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),state_revision=state_revision+1
                    WHERE chapter_uuid=?""", (chapter_uuid,))
                await conn.execute("UPDATE voice_preview_candidates SET status='REVOKED' WHERE chapter_uuid=? AND status!='REVOKED'", (chapter_uuid,))
                await conn.commit()
                return True

    async def return_chapter_to_review(self, chapter_uuid, expected_generation, expected_revision):
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                ch = await self._review_identity(conn, chapter_uuid, expected_generation, expected_revision,
                    ('AWAITING_APPROVAL','SYNTHESIZING','MASTERING','COMPLETED','FAILED'))
                if not ch['analysis_completed_at']:
                    raise ReviewConflict('Reload chapter to finish analysis', 'INCOMPLETE_ANALYSIS')
                if ch['review_phase'] == 'AWAITING_APPROVAL':
                    return False
                await isolate_chapter_cast(conn, chapter_uuid)
                try:
                    await validate_complete_script(conn, chapter_uuid)
                except ValueError as exc:
                    raise ReviewConflict(str(exc), 'INCOMPLETE_ANALYSIS') from exc
                await conn.execute("""UPDATE chapters SET review_phase='AWAITING_APPROVAL',status='PENDING',
                    run_generation=run_generation+1,review_revision=review_revision+1,state_revision=state_revision+1,
                    approved_run_generation=NULL,approved_review_revision=NULL,approved_at=NULL,
                    completed_segments=0,master_mp3_path=NULL,master_version=master_version+1,error_message=NULL
                    WHERE chapter_uuid=?""", (chapter_uuid,))
                await conn.execute("""UPDATE segments SET status='QUEUED',audio_path=NULL,duration_ms=NULL,
                    claimed_by=NULL,claimed_generation=NULL,claimed_at=NULL,retry_count=0,last_error_code=NULL,
                    error_message=NULL WHERE chapter_uuid=?""", (chapter_uuid,))
                await conn.execute("UPDATE voice_preview_candidates SET status='REVOKED' WHERE chapter_uuid=?", (chapter_uuid,))
                await conn.commit()
                return True

    async def retry_approved_chapter(self, chapter_uuid, segment_uuids=None):
        async with self.db_manager.write_lock:
            async with self.db_manager.get_connection() as conn:
                cursor=await conn.execute('SELECT * FROM chapters WHERE chapter_uuid=?',(chapter_uuid,))
                chapter=await cursor.fetchone()
                if not chapter:
                    raise ReviewConflict('Chapter not found','NOT_FOUND',404)
                try:
                    await validate_complete_script(conn,chapter_uuid)
                except ValueError as exc:
                    raise ReviewConflict(str(exc),'INCOMPLETE_ANALYSIS') from exc
                selected=set(segment_uuids or [])
                if segment_uuids and (len(selected)!=len(segment_uuids) or len(selected)>1000):
                    raise ReviewConflict('Retry selection must contain up to 1000 distinct segments','INVALID_SELECTION',422)
                if selected:
                    cursor=await conn.execute("SELECT segment_uuid FROM segments WHERE chapter_uuid=? AND status='FAILED'",(chapter_uuid,))
                    if not selected.issubset({r[0] for r in await cursor.fetchall()}):
                        raise ReviewConflict('Retry selection must contain failed chapter segments','INVALID_SELECTION')
                cursor = await conn.execute("""UPDATE chapters SET review_phase='SYNTHESIZING',status='STREAMING',
                    error_message=NULL,state_revision=state_revision+1 WHERE chapter_uuid=? AND review_phase IN ('FAILED','SYNTHESIZING')
                    AND analysis_completed_at IS NOT NULL AND approved_at IS NOT NULL
                    AND approved_run_generation=run_generation AND approved_review_revision=review_revision""", (chapter_uuid,))
                if cursor.rowcount != 1:
                    raise ReviewConflict('Retry requires unchanged approved analysis', 'NOT_AUTHORIZED')
                filter_sql = ' AND (status=\'SYNTHESIZING\' OR segment_uuid IN ('+','.join('?' for _ in selected)+'))' if selected else ''
                await conn.execute("""UPDATE segments SET status='QUEUED',retry_count=0,claimed_by=NULL,
                    claimed_generation=NULL,claimed_at=NULL,error_message=NULL,last_error_code=NULL
                    WHERE chapter_uuid=? AND status IN ('FAILED','SYNTHESIZING')"""+filter_sql, (chapter_uuid,*selected))
                await conn.commit()
