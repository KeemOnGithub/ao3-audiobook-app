import {
  ChapterIngestRequest,
  ChapterLoadResponse,
  ChapterSnapshotResponse,
  CharacterVoiceUpdatePayload,
  CharacterVoiceUpdateResponse,
  ForceRegenerateOption,
  PlaybackSeekPayload,
  PlaybackSeekResponse,
  RetryFailedRequest,
  LibraryResponse,
  ReviewIdentity,
} from '../types/api';

const BASE_API_URL = '/api/v1';

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
}

class ApiClient {
  async getLibrary(): Promise<LibraryResponse> {
    return this.request<LibraryResponse>('/library');
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${BASE_API_URL}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...(options.headers || {}),
    };

    const res = await fetch(url, { ...options, headers });
    if (!res.ok) {
      let errorMsg = `HTTP Error ${res.status}: ${res.statusText}`;
      let code: string | undefined;
      try {
        const errorData = await res.json();
        code = errorData.code;
        if (errorData.message) errorMsg = errorData.message;
        if (errorData.detail) {
          errorMsg = typeof errorData.detail === 'string'
            ? errorData.detail
            : JSON.stringify(errorData.detail);
        }
      } catch {
        // use fallback text
      }
      throw new ApiError(errorMsg,res.status,code);
    }

    return res.json() as Promise<T>;
  }

  async ingestChapter(
    url: string,
    forceRegenerate: ForceRegenerateOption = 'NONE'
  ): Promise<ChapterLoadResponse> {
    const payload: ChapterIngestRequest = {
      url,
      force_regenerate: forceRegenerate,
    };
    return this.request<ChapterLoadResponse>('/chapters/load', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async getChapterSnapshot(chapterId: string): Promise<ChapterSnapshotResponse> {
    return this.request<ChapterSnapshotResponse>(`/chapters/${chapterId}/state`);
  }

  async reviewMutation(chapterId: string, path: string, identity: ReviewIdentity, fields: Record<string, unknown> = {}, method = 'POST'): Promise<ChapterSnapshotResponse> {
    return this.request(`/chapters/${chapterId}/review/${path}`,{method,body:JSON.stringify({...identity,...fields})});
  }

  async createVoicePreview(chapterId: string, characterId: string, identity: ReviewIdentity, voice_instruct: string, reference_text: string): Promise<{candidate_uuid: string; snapshot: ChapterSnapshotResponse}> {
    return this.request(`/chapters/${chapterId}/review/characters/${characterId}/voice-previews`,{
      method:'POST',body:JSON.stringify({...identity,voice_instruct,reference_text})});
  }

  async seekPlayback(
    chapterId: string,
    targetOrder: number
  ): Promise<PlaybackSeekResponse> {
    const payload: PlaybackSeekPayload = {
      target_segment_order: targetOrder,
    };
    return this.request<PlaybackSeekResponse>(`/chapters/${chapterId}/seek`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  async updateCharacterVoice(
    characterUuid: string,
    payload: CharacterVoiceUpdatePayload,
    currentPlaybackOrder = 0
  ): Promise<CharacterVoiceUpdateResponse> {
    const body = {
      chapter_id: payload.chapter_id,
      current_chapter_uuid: payload.chapter_id,
      voice_instruct: payload.voice_instruct,
      current_playback_order: currentPlaybackOrder,
    };
    return this.request<CharacterVoiceUpdateResponse>(
      `/characters/${characterUuid}/voice`,
      {
        method: 'PATCH',
        body: JSON.stringify(body),
      }
    );
  }

  async retryFailed(
    chapterId: string,
    segmentUuids?: string[]
  ): Promise<ChapterSnapshotResponse> {
    const payload: RetryFailedRequest = {
      segment_uuids: segmentUuids,
    };
    return this.request<ChapterSnapshotResponse>(
      `/chapters/${chapterId}/retry-failed`,
      {
        method: 'POST',
        body: JSON.stringify(payload),
      }
    );
  }

  getMasterDownloadUrl(chapterId: string): string {
    return `${BASE_API_URL}/chapters/${chapterId}/download`;
  }
}

export const apiClient = new ApiClient();

