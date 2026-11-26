'use client';

import { useQuery } from '@tanstack/react-query';
import { ChevronRight, FileText, History } from 'lucide-react';
import { useDeferredValue, useState } from 'react';
import { formatDate } from '@/components/ui';
import { api } from '@/lib/api';
import type { PriorMatch } from '@/lib/contracts';
import { Highlight } from './Highlight';
import './cases.css';

export function PriorStudies({ actor, studyId }: { actor: number; studyId: number }) {
  const [search, setSearch] = useState('');
  const term = useDeferredValue(search.trim());
  const { data, isError } = useQuery({
    queryKey: ['actor', actor, 'case-search', studyId, term],
    queryFn: ({ signal }) => api.caseSearch(studyId, term, signal),
    placeholderData: previous => previous,
  });
  const prior = data?.prior ?? [];
  return <section className="context-section history-section"><div className="section-heading"><History size={17} aria-hidden /><h2>Prior studies</h2></div><p className="muted">Up to 5 earlier reports</p>
    <label className="prior-search"><span className="sr-only">Search this patient&apos;s prior studies</span>
      <input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search this patient's prior studies" autoComplete="off" /></label>
    {isError ? <p className="muted" role="alert">Prior studies could not be loaded.</p>
      : !data ? <p className="muted">Loading…</p>
      : prior.length ? <ul className="history-list">{prior.map((p: PriorMatch, i) => <li key={`${p.source}:${p.study_id}:${p.date}:${i}`}><details><summary><FileText size={15} aria-hidden /><span><strong>{formatDate(p.date)}</strong><small>{p.source === 'reading' ? 'Thorax report' : 'Imported report'}</small>{p.snippet !== null && <small className="prior-snippet"><Highlight text={p.snippet} spans={p.spans as [number, number][]} /></small>}</span><ChevronRight size={15} aria-hidden /></summary><p className="source-label">Historical report — not current-image evidence</p><p className="history-text">{p.text}</p></details></li>)}</ul>
      : <p className="muted">{term ? `No earlier study mentions “${term}”.` : 'No earlier studies.'}</p>}
  </section>;
}
