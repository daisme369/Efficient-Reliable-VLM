'use client';

import { useQuery } from '@tanstack/react-query';
import { Columns2 } from 'lucide-react';
import { useDeferredValue, useState } from 'react';
import { formatDate } from '@/components/ui';
import { api } from '@/lib/api';
import type { CaseMatch } from '@/lib/contracts';
import { Highlight } from './Highlight';
import './cases.css';

// Other patients are de-identified: identity is shown only when the backend sends it (patients assigned to the caller).
function caseLabel(c: CaseMatch) {
  return (c.patient ? [c.patient.name, c.patient.code, c.study_date && formatDate(c.study_date)] : [c.study_month, c.age_band, c.sex]).filter(Boolean).join(' · ');
}

export function SimilarCases({ actor, studyId, seed, onCompare }: { actor: number; studyId: number; seed: string; onCompare: (c: CaseMatch) => void }) {
  const [search, setSearch] = useState(seed);
  const term = useDeferredValue(search.trim());
  const { data, isError } = useQuery({
    queryKey: ['actor', actor, 'case-search', studyId, term],
    queryFn: ({ signal }) => api.caseSearch(studyId, term, signal),
    placeholderData: previous => previous,
  });
  const labels = new Map(data?.concepts.map(c => [c.id, c.label]));
  return <div className="similar-cases">
    <label className="prior-search"><span className="sr-only">Search similar cases</span>
      <input type="search" value={search} maxLength={500} onChange={event => setSearch(event.target.value)} placeholder="Search similar cases" autoComplete="off" /></label>
    {data && data.concepts.length > 0 && <p className="case-concepts"><span>Searching for:</span>{data.concepts.map(c => <span key={c.id} className="case-chip">{c.label}</span>)}</p>}
    {isError ? <p className="muted" role="alert">Similar cases could not be loaded.</p>
      : !data ? <p className="muted">Loading…</p>
      : !term ? <p className="muted">Type a finding to search signed reports.</p>
      : !data.concepts.length ? <p className="muted">Nothing to search for: the text names no finding, or only negated ones.</p>
      : data.cases.length ? <ul className="case-list">{data.cases.map(c => <li key={c.case_id} className="case-row">
          <div><p className="case-concepts">{c.concepts.map(id => <span key={id} className="case-chip">{labels.get(id) ?? id}</span>)}</p>
            <p className="case-meta mono">{caseLabel(c)}</p>
            <p className="prior-snippet"><Highlight text={c.snippet} spans={c.spans as [number, number][]} /></p></div>
          <button type="button" className="button" aria-label={`Compare with ${caseLabel(c)}`} onClick={() => onCompare(c)}><Columns2 size={16} aria-hidden />Compare</button>
        </li>)}</ul>
      : <p className="muted">No similar signed cases.</p>}
    <p className="muted case-note">Only signed reports are shown. Other patients are de-identified.</p>
  </div>;
}
