'use client';

import { X } from 'lucide-react';
import { useMemo } from 'react';
import { api } from '@/lib/api';
import type { CaseMatch, ImageDTO } from '@/lib/contracts';
import StudyViewer from '@/features/viewer/StudyViewer';
import './cases.css';

const noop = () => {};
const resolved = async () => {};

export function CaseCompare({ match, onClose }: { match: CaseMatch; onClose: () => void }) {
  // StudyViewer checks the decoded blob's type and size, so an unexpected media_type shows its "image unavailable" state.
  const images = useMemo<ImageDTO[]>(() => [{ id: match.case_id, study_id: 0, ...match.image, media_type: match.image.media_type as ImageDTO['media_type'], sha256: '', content_url: '' }], [match]);
  return <section className="case-compare" aria-label="Comparison case">
    <div className="case-compare-banner"><strong>{match.patient ? 'Assigned patient — for comparison only' : 'De-identified case — for comparison only'}</strong>
      <button type="button" className="button ghost" onClick={onClose}><X size={16} aria-hidden />Close compare</button></div>
    <StudyViewer images={images} imageId={match.case_id} boxes={[]} selectedBoxId={null} canAnnotate={false} canAskRegion={false} readOnly loadImage={api.caseImage}
      onImageChange={noop} onBoxSelect={noop} onBoxSave={resolved} onBoxDelete={resolved} onAskRegion={noop} />
    <div className="case-compare-report">
      <h3>Findings</h3>{match.findings.length ? <ul>{match.findings.map((line, i) => <li key={i}>{line}</li>)}</ul> : <p>—</p>}
      <h3>Impression</h3><p>{match.impression || '—'}</p>
    </div>
  </section>;
}
