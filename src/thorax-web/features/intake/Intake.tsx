'use client';

import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, Search, Upload, UserPlus } from 'lucide-react';
import { useDeferredValue, useState, type FormEvent } from 'react';
import { Badge, Notice, formatDate } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import type { AdminPatient } from '@/lib/contracts';
import { UploadStudy } from '@/features/uploads/UploadStudy';
import { PatientStudies } from '@/features/worklist/Worklist';
import '@/features/admin/admin.css';
import './intake.css';

const problem = (failure: unknown) => failure instanceof ApiError ? failure.message : 'Something went wrong. Try again.';
const label = (patient: Pick<AdminPatient, 'name' | 'code'>) => patient.name ? `${patient.name} (${patient.code})` : patient.code;

/** The X-ray technician's one job: find or register the patient, then add the chest X-ray study. */
export function Intake({ actor }: { actor: number }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const term = useDeferredValue(search.trim());
  const query = useInfiniteQuery({
    queryKey: ['actor', actor, 'admin', 'patient-catalog', term], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.adminPatients(term, pageParam, signal),
    getNextPageParam: page => page.next_cursor ?? undefined,
  });
  const patients = query.data?.pages.flatMap(page => page.items) ?? [];
  const [registering, setRegistering] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: 'info' | 'error'; text: string } | null>(null);
  const [upload, setUpload] = useState<{ id: number; label: string; assigned: boolean } | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['actor', actor] });

  async function register(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const year = String(data.get('birth_year') ?? '').trim();
    const body = { name: String(data.get('name') ?? '').trim(), birth_year: year ? Number(year) : null, sex: String(data.get('sex') ?? '') || null };
    setBusy(true); setFeedback(null);
    try {
      const created = await api.adminCreatePatient(body);
      setRegistering(false); setSearch('');
      setFeedback({ tone: 'info', text: `Registered ${body.name} as ${created.code}.` });
      setUpload({ id: Number(created.resource_id), label: `${body.name} (${created.code})`, assigned: false });
      await refresh();
    } catch (failure) { setFeedback({ tone: 'error', text: problem(failure) }); }
    finally { setBusy(false); }
  }

  return <div className="admin-stack intake">
    <div className="intake-toolbar">
      <label className="intake-search"><Search size={18} aria-hidden />
        <span className="sr-only">Search patients</span>
        <input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search by name or patient code" autoComplete="off" />
      </label>
      {!registering && <button className="button primary" onClick={() => { setFeedback(null); setRegistering(true); }}><UserPlus size={18} aria-hidden />Register new patient</button>}
    </div>

    {registering && <form className="admin-panel admin-form intake-register" onSubmit={event => void register(event)} aria-labelledby="intake-register-title">
      <header className="admin-panel-heading"><h2 id="intake-register-title"><UserPlus size={20} aria-hidden /> Register new patient</h2><p>The patient code is assigned automatically. Next you add the X-ray study.</p></header>
      <fieldset disabled={busy}>
        <div className="intake-register-fields">
          <div><label htmlFor="intake-name">Full name</label><input id="intake-name" name="name" required maxLength={120} autoComplete="off" autoFocus defaultValue={patients.length === 0 ? search.trim() : ''} /></div>
          <div><label htmlFor="intake-year">Birth year</label><input id="intake-year" name="birth_year" type="number" min={1900} max={new Date().getFullYear()} placeholder="Optional" /></div>
          <div><label htmlFor="intake-sex">Sex</label><select id="intake-sex" name="sex" defaultValue=""><option value="">Not recorded</option><option>Female</option><option>Male</option><option>Other</option></select></div>
        </div>
        <div className="intake-register-actions">
          <button type="submit" className="button primary">{busy ? 'Saving…' : 'Register and add study'}</button>
          <button type="button" className="button" onClick={() => setRegistering(false)}>Cancel</button>
        </div>
      </fieldset>
    </form>}

    {feedback && <Notice tone={feedback.tone}>{feedback.text}</Notice>}
    {upload && <UploadStudy patientId={upload.id} patientLabel={upload.label} onClose={() => setUpload(null)} onUploaded={(study, complete) => {
      void refresh();
      if (!complete) return;
      setUpload(null);
      setFeedback({ tone: 'info', text: upload.assigned
        ? `Study of ${formatDate(study.study_date)} added for ${upload.label}. The assigned doctor sees it on their worklist.`
        : `Study of ${formatDate(study.study_date)} added for ${upload.label}. No doctor is assigned yet: ask an administrator to assign one.` });
    }} />}

    <section className="admin-panel" aria-labelledby="intake-patients-title">
      <header className="admin-panel-heading"><h2 id="intake-patients-title">Patients</h2><p>Newest first. Check the name and birth year before adding a study.</p></header>
      {query.isPending ? <p className="intake-empty">Loading patients…</p>
        : query.error ? <div className="intake-empty"><Notice tone="error">Could not load patients.</Notice><button className="button" onClick={() => void query.refetch()}>Retry</button></div>
        : patients.length === 0 ? <div className="intake-empty"><p>{term ? `No patient matches “${term}”.` : 'No patients yet.'}</p>
            {!registering && <button className="button primary" onClick={() => { setFeedback(null); setRegistering(true); }}><UserPlus size={18} aria-hidden />Register new patient</button>}</div>
        : <div className="admin-table-wrap"><table className="admin-table intake-table">
          <thead><tr><th scope="col">Patient</th><th scope="col">Born · sex</th><th scope="col">Last study</th><th scope="col">Reading doctor</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          {patients.map(patient => {
            const assigned = patient.assigned_doctor_ids.length > 0;
            return <tbody key={patient.id}>
              <tr>
                <th scope="row"><span className="intake-name">{patient.name ?? patient.code}</span><small className="admin-sub mono">{patient.code}</small></th>
                <td>{[patient.birth_year, patient.sex].filter(Boolean).join(' · ') || '—'}</td>
                <td>{patient.last_study_date ? formatDate(patient.last_study_date) : <span className="muted">No studies yet</span>}</td>
                <td>{assigned ? <Badge tone="approved">Assigned</Badge> : <Badge tone="pending">No doctor assigned</Badge>}</td>
                <td><div className="admin-row-actions">
                  <button className="button primary" aria-label={`Add a study for ${label(patient)}`} onClick={() => { setFeedback(null); setUpload({ id: patient.id, label: label(patient), assigned }); }}><Upload size={16} aria-hidden />Add study</button>
                  <button className="button ghost" aria-expanded={open === patient.id} aria-controls={`intake-studies-${patient.id}`} onClick={() => setOpen(open === patient.id ? null : patient.id)}>
                    {open === patient.id ? <ChevronUp size={16} aria-hidden /> : <ChevronDown size={16} aria-hidden />}Studies</button>
                </div></td>
              </tr>
              {open === patient.id && <tr id={`intake-studies-${patient.id}`}><td colSpan={5} className="study-cell"><PatientStudies actor={actor} patientId={patient.id} patientLabel={label(patient)} canUpload admin /></td></tr>}
            </tbody>;
          })}
        </table>
          {query.hasNextPage && <div className="admin-panel-footer"><button className="button" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? 'Loading…' : 'Load more patients'}</button></div>}
        </div>}
    </section>
  </div>;
}
