import { useState } from 'react';
import { useAppStore } from '../store/useAppStore';
import { apiClient } from '../services/apiClient';
import { reviewIdentity, runReviewMutation } from '../services/reviewActions';

export function SpeakerAssignmentControl() {
  const selected=useAppStore(s=>s.selectedSegmentIds);
  const cast=useAppStore(s=>s.characters);
  const busy=useAppStore(s=>s.reviewBusy);
  const [target,setTarget]=useState('');
  return <div className="p-3 border-b border-slate-800 bg-slate-900 flex flex-wrap gap-3 text-xs items-center">
    <span>{selected.length} segments selected. Full chapter remains visible.</span>
    <label>Assign speaker <select aria-label="Assign selected segments to speaker" value={target} onChange={e=>setTarget(e.target.value)} className="bg-slate-950 border border-slate-700 rounded p-1">
      <option value="">Choose speaker</option>{Object.values(cast).map(c=><option key={c.character_uuid} value={c.character_uuid}>{c.canonical_name} ({c.character_uuid.slice(0,6)})</option>)}
    </select></label>
    <button disabled={busy || !target || !selected.length || selected.length>1000} onClick={async()=>{
      const id=useAppStore.getState().metadata!.chapter_id;
      if (await runReviewMutation(()=>apiClient.reviewMutation(id,'speakers',reviewIdentity(),{segment_uuids:selected,target_character_uuid:target}))) useAppStore.getState().clearSegmentSelection();
    }}>Apply Speaker</button>
    <button onClick={()=>useAppStore.getState().clearSegmentSelection()}>Clear Selection</button>
    {selected.length>1000 && <span role="alert">Select at most 1000 segments per deliberate batch.</span>}
  </div>;
}
