'use client';

import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { ArrowRight, ChevronDown, ChevronRight, ClipboardList, FolderOpen, Search, X } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { AppShell, DemoBadge, DesktopNotice, RoleGate, useAuth, useDesktop } from '@/components/shell';
import { Badge, EmptyState, formatDate, Notice, readingLabel, Skeleton } from '@/components/ui';
import { api, ApiError } from '@/lib/api';

export function Worklist({ localDemo }: { localDemo: boolean }) {
  const desktop = useDesktop();
  return <RoleGate role="doctor">{user => <AppShell>{desktop ? <AssignedPatients key={user.id} actor={user.id} localDemo={localDemo} /> : <DesktopNotice />}</AppShell>}</RoleGate>;
}

function AssignedPatients({ actor, localDemo }: { actor: number; localDemo: boolean }) {
  const { expire, refresh } = useAuth();
  const [input, setInput] = useState('');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  useEffect(() => { void refresh(true).catch(() => {}); }, [refresh]);
  useEffect(() => {
    const value = input.trim();
    if (value === search) return;
    const timer = window.setTimeout(() => { setExpanded(null); setSearch(value); void refresh(true).catch(() => {}); }, 300);
    return () => window.clearTimeout(timer);
  }, [input, search, refresh]);

  const list = useInfiniteQuery({
    queryKey: ['actor', actor, 'patients', search], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.patients(search, pageParam, signal),
    getNextPageParam: page => page.next_cursor ?? undefined,
  });
  useEffect(() => { if (list.error instanceof ApiError && list.error.status === 401) expire(); }, [list.error, expire]);
  const rows = list.data?.pages.flatMap(page => page.items) ?? [];

  function touch() { void refresh(true).catch(() => {}); }
  function searchFor(value: string) { setInput(value); setSearch(value.trim()); setExpanded(null); touch(); }
  return <main className="page-wrap worklist" id="main-content">
    <div className="page-heading"><div><h1>Assigned patients</h1><p>Open a chest X-ray study to start or continue your reading.</p></div><DemoBadge mode={localDemo ? 'mock' : 'real'} /></div>
    <section className="surface worklist-table" aria-label="Patient worklist">
      <div className="worklist-toolbar">
        <div className="section-heading"><ClipboardList size={20} aria-hidden /><h2>Patient worklist</h2></div>
        <form className="worklist-search" onSubmit={event => { event.preventDefault(); searchFor(input); }}>
          <label className="sr-only" htmlFor="patient-search">Search assigned patients</label>
          <Search size={18} aria-hidden /><input id="patient-search" type="search" maxLength={100} placeholder="Search by name or patient code" value={input} onChange={event => setInput(event.target.value)} />
          {input && <button type="button" className="icon-button" aria-label="Clear search" onClick={() => searchFor('')}><X size={17} aria-hidden /></button>}
        </form>
      </div>
      {list.isPending ? <Skeleton label="Loading assigned patients" rows={5} /> : list.error ? <EmptyState title="Could not load assigned patients" action={<button className="button" onClick={() => { touch(); void list.refetch(); }}>Retry</button>}>Check your connection and try again.</EmptyState> : rows.length === 0 ?
        <EmptyState title={search ? 'No assigned patients match this search' : 'No patients are assigned to you'} action={search && <button className="button" onClick={() => searchFor('')}>Clear search</button>} /> : <>
          <table className="data-table">
            <thead><tr><th scope="col">Patient</th><th scope="col">Patient code</th><th scope="col">Birth year</th><th scope="col">Sex</th><th scope="col">Studies</th><th scope="col"><span className="sr-only">Expand studies</span></th></tr></thead>
            {rows.map(patient => <tbody key={patient.id}>
              <tr className={expanded === patient.id ? 'row-expanded' : ''}>
                <th scope="row"><span className="patient-cell"><span aria-hidden className="avatar">{patient.name.split(/\s+/).slice(0, 2).map(part => part[0]).join('')}</span>{patient.name}</span></th>
                <td className="mono">{patient.code}</td><td className="mono">{patient.birth_year ?? '—'}</td><td>{patient.sex ?? '—'}</td><td className="mono">{patient.study_count}</td>
                <td className="align-right"><button className="button ghost" aria-expanded={expanded === patient.id} aria-label={`${expanded === patient.id ? 'Hide' : 'Show'} studies for ${patient.name}`} aria-controls={`patient-studies-${patient.id}`} onClick={() => { setExpanded(expanded === patient.id ? null : patient.id); void refresh(true).catch(() => {}); }}>{expanded === patient.id ? <ChevronDown size={17} aria-hidden /> : <ChevronRight size={17} aria-hidden />}{expanded === patient.id ? 'Hide studies' : 'Show studies'}</button></td>
              </tr>
              {expanded === patient.id && <tr><td colSpan={6} className="study-cell"><PatientStudies key={patient.id} actor={actor} patientId={patient.id} /></td></tr>}
            </tbody>)}
          </table>
          <div className="worklist-footer"><span role="status">{rows.length} {rows.length === 1 ? 'patient' : 'patients'} shown</span>{list.hasNextPage && <button className="button" disabled={list.isFetchingNextPage} onClick={() => { touch(); void list.fetchNextPage(); }}>{list.isFetchingNextPage ? 'Loading…' : 'Load more'}</button>}</div>
        </>}
    </section>
  </main>;
}

function PatientStudies({ actor, patientId }: { actor: number; patientId: number }) {
  const { expire } = useAuth();
  const result = useInfiniteQuery({
    queryKey: ['actor', actor, 'patient', patientId, 'studies'], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.studies(patientId, pageParam, signal),
    getNextPageParam: page => page.next_cursor ?? undefined,
  });
  useEffect(() => { if (result.error instanceof ApiError && result.error.status === 401) expire(); }, [result.error, expire]);
  const studies = result.data?.pages.flatMap(page => page.items) ?? [];
  return <div className="study-list" id={`patient-studies-${patientId}`}>
    <div className="section-heading"><FolderOpen aria-hidden size={17} /><h3>Studies</h3></div>
    {result.isPending ? <Skeleton label="Loading studies" rows={2} /> : result.error ? <><Notice tone="error">Could not load this study list.</Notice><button className="button" onClick={() => void result.refetch()}>Retry</button></> : studies.length === 0 ? <p>No studies are available for this patient.</p> :
      <table className="data-table nested-table"><thead><tr><th scope="col">Study date</th><th scope="col">Indication</th><th scope="col">Images</th><th scope="col">Study status</th><th scope="col">Reading status</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
        <tbody>{studies.map(study => {
          const href = `/patients/${patientId}/studies/${study.id}`;
          return <tr key={study.id}><th scope="row" className="mono">{formatDate(study.study_date)}</th><td>{study.indication || '—'}</td><td className="mono">{study.image_count}</td><td><Badge>{study.status === 'final' ? 'Final' : study.status === 'in_progress' ? 'In progress' : 'New'}</Badge></td>
            <td><Badge tone={study.own_session_status === 'final' || study.own_session_status === 'findings_approved' ? 'approved' : study.own_session_status === 'active' ? 'info' : 'neutral'}>{readingLabel(study.own_session_status)}</Badge></td>
            <td className="align-right"><Link className="button" prefetch={false} href={study.own_session_status === 'final' ? `${href}/report` : href}>{study.own_session_status === 'final' ? 'View final report' : study.own_session_status ? 'Continue' : 'Open'}<ArrowRight size={16} aria-hidden /></Link></td></tr>;
        })}</tbody></table>}
    {result.hasNextPage && <button className="button" disabled={result.isFetchingNextPage} onClick={() => void result.fetchNextPage()}>{result.isFetchingNextPage ? 'Loading…' : 'Load more studies'}</button>}
  </div>;
}
