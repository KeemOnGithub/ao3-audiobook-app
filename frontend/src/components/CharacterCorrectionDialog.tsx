import { useEffect, useRef, useState } from 'react';
import { useAppStore } from '../store/useAppStore';
import { apiClient } from '../services/apiClient';
import { reviewIdentity, runReviewMutation } from '../services/reviewActions';

export type CorrectionMode = 'add' | 'rename' | 'merge' | 'delete';

export function CharacterCorrectionDialog({mode, characterId, onClose}: {mode: CorrectionMode; characterId?: string; onClose: () => void}) {
  const cast = useAppStore(s => s.characters);
  const busy = useAppStore(s => s.reviewBusy);
  const error = useAppStore(s => s.reviewError);
  const character = characterId ? cast[characterId] : undefined;
  const [name,setName] = useState(character?.canonical_name || '');
  const [prompt,setPrompt] = useState('A clear expressive speaking voice with natural pacing.');
  const [target,setTarget] = useState('');
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    root.current?.querySelector<HTMLElement>('input,select,button')?.focus();
    return () => previous?.focus();
  },[]);
  const title = {add:'Add Character',rename:'Rename Character',merge:'Merge Characters',delete:'Delete Unused Character'}[mode];
  return <div className="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-4" onKeyDown={e => {
    if (e.key === 'Escape' && !busy) onClose();
    if (e.key === 'Tab') {
      const nodes = root.current?.querySelectorAll<HTMLElement>('input,select,textarea,button:not(:disabled)');
      if (!nodes?.length) return;
      const first=nodes[0],last=nodes[nodes.length-1];
      if (e.shiftKey && document.activeElement === first) {e.preventDefault();last.focus();}
      if (!e.shiftKey && document.activeElement === last) {e.preventDefault();first.focus();}
    }
  }}>
    <div ref={root} role="dialog" aria-modal="true" aria-labelledby="correction-title" className="w-full max-w-md bg-slate-900 border border-slate-700 rounded-xl p-5 space-y-4">
      <h2 id="correction-title" className="font-semibold">{title}</h2>
      <form className="space-y-4" onSubmit={async e => {
        e.preventDefault();
        const id=useAppStore.getState().metadata!.chapter_id;
        const path = mode === 'add' ? 'characters' : mode === 'merge' ? 'characters/merge' : `characters/${characterId}`;
        const fields = mode === 'add' ? {canonical_name:name,voice_instruct:prompt} : mode === 'rename' ? {canonical_name:name} : mode === 'merge' ? {source_character_uuid:characterId,target_character_uuid:target} : {};
        if (await runReviewMutation(() => apiClient.reviewMutation(id,path,reviewIdentity(),fields,mode === 'rename' ? 'PATCH' : mode === 'delete' ? 'DELETE' : 'POST'))) onClose();
      }}>
        {(mode === 'add' || mode === 'rename') && <label className="block text-sm">Display name<input autoFocus required minLength={1} maxLength={120} value={name} onChange={e=>setName(e.target.value)} className="block w-full bg-slate-950 border border-slate-700 rounded p-2 mt-1" /></label>}
        {mode === 'add' && <label className="block text-sm">Default voice description<textarea required minLength={10} maxLength={600} value={prompt} onChange={e=>setPrompt(e.target.value)} className="block w-full bg-slate-950 border border-slate-700 rounded p-2 mt-1" /></label>}
        {mode === 'merge' && <>
          <p className="text-sm">Move {character?.segment_count} segments from {character?.canonical_name}. The target keeps its voice. This cannot be undone.</p>
          <label className="block text-sm">Merge into<select required value={target} onChange={e=>setTarget(e.target.value)} className="block w-full bg-slate-950 border border-slate-700 rounded p-2 mt-1">
            <option value="">Choose target</option>{Object.values(cast).filter(c=>!c.is_narrator && c.character_uuid!==characterId).map(c=><option key={c.character_uuid} value={c.character_uuid}>{c.canonical_name} ({c.segment_count} segments; {c.character_uuid.slice(0,6)})</option>)}
          </select></label>
        </>}
        {mode === 'delete' && <p className="text-sm">Delete {character?.canonical_name}? Only unused non-narrator characters can be deleted. Files are retained conservatively.</p>}
        {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
        <div className="flex justify-end gap-4"><button type="button" disabled={busy} onClick={onClose}>Cancel</button><button disabled={busy} className="bg-indigo-600 px-3 py-2 rounded">{busy ? 'Saving...' : title}</button></div>
      </form>
    </div>
  </div>;
}
