import { beforeEach,describe,expect,it,vi } from 'vitest';
import { fireEvent,render,screen } from '@testing-library/react';
import { useAppStore } from '../store/useAppStore';
import { gaplessAudioEngine } from '../services/GaplessAudioEngine';
import { sseManager } from '../services/sseManager';
import { ChapterSnapshotResponse } from '../types/api';
import { ChapterReviewToolbar } from '../components/ChapterReviewToolbar';
import { PlayerControls } from '../components/PlayerControls';
import { apiClient,ApiError } from '../services/apiClient';
import { runReviewMutation } from '../services/reviewActions';

const snapshot:ChapterSnapshotResponse={chapter_uuid:'review',status:'PENDING',run_generation:1,state_revision:5,
  review_phase:'AWAITING_APPROVAL',review_revision:2,analysis_completed_at:'now',narration_authorized:false,can_approve:true,
  master_mp3_ready:false,master_mp3_url:null,total_segments:1,ready_segments:0,
  characters:[{character_uuid:'n',canonical_name:'Narrator',voice_instruct:'A warm narrator.',voice_version:1,preview_url:null,is_narrator:true}],
  segments:[{segment_uuid:'s',segment_order:0,character_uuid:'n',text:'"Exact, text!"',status:'QUEUED',audio_url:null,duration_ms:null,is_paragraph_end:true,voice_version:1}]};

describe('Durable chapter review frontend gates',()=>{
  beforeEach(()=>{vi.restoreAllMocks();sseManager.disconnect();useAppStore.getState().resetWork();sseManager.reconcileSnapshot(snapshot);});
  it('engine and keyboard cannot create narration intent in review',async()=>{
    render(<PlayerControls />);
    const seek=vi.spyOn(apiClient,'seekPlayback');
    await gaplessAudioEngine.play();gaplessAudioEngine.seekToSegment(0);gaplessAudioEngine.setAutoplayIntent();
    fireEvent.keyDown(window,{code:'Space'});fireEvent.keyDown(window,{code:'ArrowRight'});
    expect(gaplessAudioEngine.getPlayIntent()).toBe('NONE');expect(seek).not.toHaveBeenCalled();
    expect(screen.getByTitle('Play (Space)')).toBeDisabled();
  });
  it('dirty candidate drafts require explicit discard confirmation and never silently apply',()=>{
    useAppStore.getState().setVoiceDraft('n',{prompt:'An unapplied prompt.',sample:'An unapplied sample long enough.'});
    const confirmation=vi.spyOn(window,'confirm').mockReturnValue(false);
    const mutate=vi.spyOn(apiClient,'reviewMutation');
    render(<ChapterReviewToolbar />);fireEvent.click(screen.getByText('Approve & Generate Chapter'));
    expect(confirmation).toHaveBeenCalled();expect(mutate).not.toHaveBeenCalled();
    expect(useAppStore.getState().voiceDrafts.n.prompt).toBe('An unapplied prompt.');
  });
  it('rejects out-of-order snapshots and preserves recoverable drafts after conflict',async()=>{
    useAppStore.getState().setVoiceDraft('n',{prompt:'Keep this prompt.',sample:'A sufficiently long reference sample.'});
    sseManager.reconcileSnapshot({...snapshot,state_revision:8,review_revision:3,characters:[]});
    expect(sseManager.reconcileSnapshot(snapshot)).toBe(false);
    expect(useAppStore.getState().characterIds).toEqual([]);
    vi.spyOn(apiClient,'getChapterSnapshot').mockResolvedValue({...snapshot,state_revision:9,review_revision:4});
    const operation=vi.fn().mockRejectedValue(new ApiError('Stale review',409,'STALE_REVIEW'));
    expect(await runReviewMutation(operation)).toBe(false);
    expect(operation).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().voiceDrafts.n).toMatchObject({prompt:'Keep this prompt.',stale:true});
  });
  it('revocation clears active and pending narration while preview remains independent',async()=>{
    sseManager.reconcileSnapshot({...snapshot,state_revision:6,review_phase:'SYNTHESIZING',narration_authorized:true});
    await gaplessAudioEngine.play();expect(gaplessAudioEngine.getPlayIntent()).toBe('USER_PLAY');
    await gaplessAudioEngine.playCharacterPreview('candidate','/preview.mp3');
    expect(gaplessAudioEngine.getPlayIntent()).toBe('NONE');
    await gaplessAudioEngine.onSegmentReady(0);expect(useAppStore.getState().playbackStatus).toBe('PAUSED');
    sseManager.reconcileSnapshot({...snapshot,run_generation:2,state_revision:7});
    expect(gaplessAudioEngine.getPlayIntent()).toBe('NONE');
    expect(useAppStore.getState().activePreviewCharacterId).toBeNull();
  });
});
