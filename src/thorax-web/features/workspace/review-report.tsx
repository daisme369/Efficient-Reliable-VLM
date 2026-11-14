'use client';

import { ArrowLeft, ArrowRight, Check, ClipboardCheck, FileCheck, FileText, LockKeyhole, Plus, Save, ShieldCheck, Undo2 } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { AppShell, DemoBadge, useAuth } from '@/components/shell';
import { aiSuggestion, Badge, Confirm, EmptyState, formatDate, formatTime, Notice, Spinner } from '@/components/ui';
import { actionKey, api, ApiError } from '@/lib/api';
import type { BoxDTO, FindingItem, FindingsEdit, ReportDTO, ReportNarrative, Workspace } from '@/lib/contracts';
import { regionName as sharedRegionName } from '@/features/viewer/RegionEditor';
import type { Panel } from './StudyWorkspace';
import { AIStatus, imageLabel, SourceDetails } from './analysis-chat';
import type { ReadingActions } from './useReading';

type ReviewProps = {
  data: Workspace; actions: ReadingActions; disabled: boolean; otherDirty: boolean; baseRevision?: number;
  onDirty: (dirty: boolean) => void; onDraftText: (text: string[]) => void; onPanel: (panel: Panel) => void;
};
const included = (item: FindingItem) => item.action === 'accepted' || item.action === 'edited' || item.action === 'added';
const actionLabels = { pending: 'Needs review', accepted: 'Accepted', edited: 'Doctor edited', rejected: 'Rejected', added: 'Doctor added' } as const;

export function FindingsPanel({ data, actions, disabled, baseRevision, otherDirty, onDirty, onDraftText, onPanel, onBox, onBoxReview }: ReviewProps & { onBox: (id: number) => void; onBoxReview: (id: number, action: 'accepted' | 'rejected') => Promise<void> }) {
  const [draft, setDraft] = useState<FindingsEdit | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [confirmSave, setConfirmSave] = useState(false);
  const [saved, setSaved] = useState('');
  const [showExcluded, setShowExcluded] = useState(false);
  const findings = data.latest_findings;
  const items = draft?.items ?? findings?.items ?? [];
  const pending = items.filter(item => item.action === 'pending').length;
  const countIncluded = items.filter(included).length;
  const boxes = data.boxes.filter(box => !box.deleted_at);
  const aiBoxes = boxes.filter(box => box.source === 'ai');
  const pendingBoxes = aiBoxes.filter(box => box.review_action === 'pending').length;
  const normal = draft?.no_abnormality_confirmed ?? findings?.no_abnormality_confirmed ?? false;
  const readOnly = data.session.status === 'final';
  const approved = findings?.status === 'approved';
  const dirty = draft !== null;
  const blockers = data.blockers.filter(item => item.gate === 'gate1');
  const canEdit = !readOnly && !disabled;
  const invalidDraft = items.some(item => !item.text.trim() || item.text.length > 2000 || (item.location?.length ?? 0) > 200 || item.box_ids.some(id => !boxes.some(box => box.id === id && box.image_id === item.image_id && box.review_action !== 'rejected')));
  const canApprove = !readOnly && !disabled && !otherDirty && !dirty && findings?.status === 'in_review' && blockers.length === 0 && pending === 0 && pendingBoxes === 0 && (countIncluded > 0 || normal);
  const visibleItems = items.filter(item => item.action !== 'rejected' || showExcluded);
  const excluded = items.length - items.filter(item => item.action !== 'rejected').length;

  useEffect(() => { onDirty(!readOnly && dirty); }, [dirty, onDirty, readOnly]);
  useEffect(() => { onDraftText(draft?.items.map(item => item.text).filter(Boolean) ?? []); }, [draft, onDraftText]);
  useEffect(() => { if (readOnly) { setDraft(null); setEditing(null); setConfirm(false); } }, [readOnly]);

  function replace(nextItems: FindingItem[], noAbnormality = normal) {
    setSaved('');
    setDraft({ items: nextItems, no_abnormality_confirmed: noAbnormality && !nextItems.some(item => included(item) || item.action === 'pending') });
  }
  function update(id: string, patch: Partial<FindingItem>) { replace(items.map(item => item.id === id ? { ...item, ...patch } : item)); }
  function changeAction(item: FindingItem, action: FindingItem['action']) {
    const original = findings?.ai_items.find(source => source.id === item.id);
    const fields = original && (action === 'accepted' || action === 'pending') ? { text: original.text, location: original.location } : {};
    update(item.id, { action, ...fields, ...(action === 'rejected' ? { box_ids: [] } : {}) });
    setEditing(null);
  }
  function addFinding() {
    if (items.length >= 50 || !data.images.length) return;
    const id = actionKey();
    replace([...items, { id, text: '', location: null, image_id: data.images[0].id, source: 'doctor', action: 'added', box_ids: [] }]);
    setEditing(id);
  }
  function save() {
    if (!findings || !canEdit || invalidDraft) return;
    if (approved) { setConfirmSave(true); return; }
    commitSave();
  }
  function commitSave() {
    if (!findings || !canEdit || invalidDraft) return;
    setConfirmSave(false);
    const payload = { items, no_abnormality_confirmed: normal };
    void actions.transact(options => api.saveFindings(data.session.id, payload, options), () => {
      setDraft(null); setEditing(null); setSaved('Findings saved.'); onDirty(false);
    }, baseRevision).catch(() => {});
  }
  function approve() {
    if (!findings || !canApprove) return;
    void actions.transact(options => api.approveFindings(data.session.id, findings.version, options), () => {
      setConfirm(false); setSaved(`Findings revision ${findings.version} approved.`);
    }).catch(() => { setConfirm(false); });
  }
  const regionName = (box: BoxDTO) => sharedRegionName(boxes, box);
  function linkOptions(item: FindingItem) { return boxes.filter(box => box.image_id === item.image_id && box.review_action !== 'rejected'); }

  if (!findings) return <div className="panel-content"><div className="section-heading"><ClipboardCheck size={20} aria-hidden /><h2>Findings review</h2></div><EmptyState title="Analysis required" action={<button className="button" onClick={() => onPanel('analysis')}>Go to Analyze <ArrowRight size={16} aria-hidden /></button>}>Analyze the selected image before reviewing findings and regions.</EmptyState></div>;
  const analyzedImage = findings.ai_items[0]?.image_id ?? aiBoxes[0]?.image_id;
  const statusText = dirty ? 'Unsaved changes' : saved || (approved ? `Approved ${formatTime(findings.approved_at)} by ${data.session.doctor_id === findings.approved_by ? 'you' : `doctor ${findings.approved_by ?? '—'}`}` : 'All changes saved');
  const hints = invalidDraft ? ['Enter all finding text and remove links to rejected regions before saving.'] : dirty || otherDirty ? ['Save or discard changes before approval.'] : blockers.length ? [...new Set(blockers.map(item => item.message))] : canApprove ? [] : ['Complete review and save before approving.'];
  return <div className="panel-content findings-panel">
    <div className="review-summary">
      <div><h2>Findings</h2><Badge tone={approved ? 'approved' : 'pending'}>{approved ? `Approved · rev. ${findings.version}` : `${findings.status === 'ai_proposed' ? 'AI proposed' : 'In review'} · rev. ${findings.version}`}</Badge></div>
      <p className="muted dense">AI analysed {analyzedImage ? imageLabel(data.images, analyzedImage) : 'one image'} · {findings.ai_items.length} {findings.ai_items.length === 1 ? 'suggestion' : 'suggestions'} · {aiBoxes.length} {aiBoxes.length === 1 ? 'region' : 'regions'} · <button className="text-button" onClick={() => onPanel('analysis')}>Analysis details</button></p>
    </div>
    {approved && !readOnly && (dirty || editing) && <Notice tone="pending">Saving this change creates a new findings version and makes any draft report stale.</Notice>}
    <ul className="review-list" aria-label="Findings">
    {visibleItems.map((item, index) => {
      const original = findings.ai_items.find(source => source.id === item.id);
      const isEditing = canEdit && editing === item.id;
      const sameImageBoxes = linkOptions(item);
      return <li key={item.id} className={`review-row ${item.action}`}>
        <div className="review-row-top"><span className="row-index mono">{String(index + 1).padStart(2, '0')}</span><div><strong>{item.location || 'Finding'}</strong><small>{imageLabel(data.images, item.image_id)}</small></div><Badge tone={item.action === 'pending' ? 'pending' : included(item) ? 'approved' : 'neutral'}>{item.source === 'ai' && item.action === 'pending' ? 'AI · Needs review' : actionLabels[item.action]}</Badge></div>
        {isEditing ? <div className="finding-fields">
          <label className="field">Finding text<textarea autoFocus value={item.text} maxLength={2000} rows={3} onChange={event => update(item.id, { text: event.target.value, action: item.source === 'ai' ? 'edited' : 'added' })} /></label>
          <label className="field">Location<input maxLength={200} value={item.location ?? ''} onChange={event => update(item.id, { location: event.target.value || null, action: item.source === 'ai' ? 'edited' : 'added' })} /></label>
          {item.source === 'doctor' && <label className="field">Image<select value={item.image_id} onChange={event => update(item.id, { image_id: Number(event.target.value), box_ids: [] })}>{data.images.map(image => <option key={image.id} value={image.id}>{imageLabel(data.images, image.id)}</option>)}</select></label>}
          <fieldset className="finding-links"><legend>Linked regions</legend>{sameImageBoxes.length ? <><label><input type="checkbox" checked={item.box_ids.length === 0} onChange={() => update(item.id, { box_ids: [] })} /> No linked region</label>{sameImageBoxes.map(box => <label key={box.id}><input type="checkbox" checked={item.box_ids.includes(box.id)} onChange={event => update(item.id, { box_ids: event.target.checked ? [...item.box_ids, box.id] : item.box_ids.filter(id => id !== box.id) })} />{regionName(box)} {box.label || ''}</label>)}</> : <p>No linked region</p>}</fieldset>
          {item.box_ids.some(id => !sameImageBoxes.some(box => box.id === id)) && <button className="text-button" onClick={() => update(item.id, { box_ids: item.box_ids.filter(id => sameImageBoxes.some(box => box.id === id)) })}>Remove unavailable region links</button>}
          <button className="button" disabled={!item.text.trim()} onClick={() => { if (item.source === 'ai' && item.action === 'pending') update(item.id, { action: original?.text === item.text && original.location === item.location ? 'accepted' : 'edited' }); setEditing(null); }}>Done editing</button>
        </div> : <><p>{item.text || 'Enter finding text.'}</p>{item.box_ids.length > 0 && <div className="finding-links-inline">{item.box_ids.map(id => { const box = boxes.find(candidate => candidate.id === id && candidate.review_action !== 'rejected'); return box ? <button className="text-button" key={id} onClick={() => onBox(id)}>{regionName(box)}</button> : <span key={id} className="link-gone"><span className="muted">Linked region was rejected or removed</span>{canEdit && <button className="text-button" onClick={() => update(item.id, { box_ids: item.box_ids.filter(other => other !== id) })}>Remove link</button>}</span>; })}</div>}</>}
        {original && item.action === 'edited' && <details className="source-details"><summary>Original AI suggestion</summary><p>{original.text}</p></details>}
        {!isEditing && canEdit && <div className="row-actions">
          {item.source === 'ai' && item.action !== 'accepted' && item.action !== 'rejected' && <button className="button small" onClick={() => changeAction(item, 'accepted')}><Check size={15} aria-hidden />Accept</button>}
          {item.source === 'ai' && item.action === 'accepted' && <button className="button small ghost" onClick={() => changeAction(item, 'pending')}><Undo2 size={15} aria-hidden />Undo</button>}
          {item.action !== 'rejected' && <><button className="button small ghost" onClick={() => setEditing(item.id)}>Edit</button><button className="button small ghost" onClick={() => changeAction(item, 'rejected')}>Reject</button></>}
          {item.action === 'rejected' && <button className="button small" onClick={() => changeAction(item, item.source === 'ai' ? 'pending' : 'added')}>{item.source === 'ai' ? 'Mark for review' : 'Restore finding'}</button>}
        </div>}
      </li>;
    })}
    </ul>
    <div className="row-actions">
      {!readOnly && <button className="button small ghost" disabled={!canEdit || items.length >= 50} onClick={addFinding}><Plus size={16} aria-hidden />Add finding</button>}
      {excluded > 0 && <button className="text-button" onClick={() => setShowExcluded(!showExcluded)} aria-expanded={showExcluded}>{showExcluded ? 'Hide' : 'Show'} rejected ({excluded})</button>}
    </div>
    {items.length === 0 && <p className="muted">No findings have been entered.</p>}
    {aiBoxes.length > 0 && <section className="review-group"><h3>AI regions</h3><ul className="review-list">{aiBoxes.map(box => <RegionReviewRow key={box.id} box={box} name={regionName(box)} onBox={onBox} onReview={canEdit && !actions.busy ? action => void onBoxReview(box.id, action).catch(() => {}) : undefined} />)}</ul></section>}
    {!readOnly && (normal || (!pending && !pendingBoxes && !countIncluded)) && <label className="checkbox-line"><input type="checkbox" checked={normal} disabled={!canEdit || pending > 0 || pendingBoxes > 0 || countIncluded > 0} onChange={event => replace(items, event.target.checked)} />No abnormal findings confirmed</label>}
    {!readOnly && <div className="panel-footer sticky-save">
      <div className="footer-status"><span role="status">{statusText}</span>{!approved && (pending + pendingBoxes > 0) && <span>{pending + pendingBoxes} left to review</span>}</div>
      {!approved && hints.length > 0 && <ul className="gate-hints" aria-label="Before you can approve">{hints.map(message => <li key={message}>{message}</li>)}</ul>}
      <div className="footer-actions">
        {(dirty || !approved) && <button className="button" disabled={!canEdit || invalidDraft || (!dirty && findings.status !== 'ai_proposed')} onClick={save}>{actions.busy ? <Spinner /> : <Save size={16} aria-hidden />}Save</button>}
        {dirty && <button className="button ghost" disabled={disabled} onClick={() => { setDraft(null); setEditing(null); }}>Discard</button>}
        {approved ? <button className="button primary" onClick={() => onPanel('report')}>Go to report <ArrowRight size={17} aria-hidden /></button>
          : <button className="button primary" disabled={!canApprove} onClick={() => setConfirm(true)}><ShieldCheck size={17} aria-hidden />Approve findings</button>}
      </div>
    </div>}
    {confirmSave && <Confirm title="Save changes to approved findings" action="Save as new version" cancel="Keep editing" pending={actions.busy} onCancel={() => setConfirmSave(false)} onConfirm={commitSave}><p>This creates findings revision {findings.version + 1}, withdraws the approval of revision {findings.version} (Gate 1), and marks any draft report stale.</p></Confirm>}
    {confirm && <Confirm title="Approve findings (Gate 1)" action="Approve findings" pending={actions.busy} onCancel={() => setConfirm(false)} onConfirm={approve}><p>Approve these findings and reviewed regions for report drafting?</p><p><strong>{data.patient.name}</strong> · {formatDate(data.study.study_date)} · Findings revision {findings.version}</p></Confirm>}
  </div>;
}

function RegionReviewRow({ box, name, onBox, onReview }: { box: BoxDTO; name: string; onBox: (id: number) => void; onReview?: (action: 'accepted' | 'rejected') => void }) {
  return <li className={`review-row ${box.review_action}`}>
    <div className="review-row-top"><span className="row-index mono region">{name}</span><div><button className="text-button" onClick={() => onBox(box.id)}>{box.label || `Region ${box.id}`}</button></div><Badge tone={box.review_action === 'pending' ? 'pending' : box.review_action === 'rejected' ? 'neutral' : 'approved'}>{box.review_action === 'pending' ? 'AI · Needs review' : actionLabels[box.review_action]}</Badge></div>
    {box.review_action === 'pending' && onReview && <div className="row-actions"><button className="button small" onClick={() => onReview('accepted')}><Check size={15} aria-hidden />Accept</button><button className="button small ghost" onClick={() => onReview('rejected')}>Reject</button></div>}
  </li>;
}

const fields = [ ['comparison', 'Comparison'], ['findings', 'Findings'], ['impression', 'Impression'], ['recommendation', 'Recommendation'] ] as const;
function narrative(content: ReportNarrative): ReportNarrative {
  return { comparison: content.comparison, findings: content.findings, impression: content.impression, recommendation: content.recommendation };
}

export function ReportPanel({ data, actions, disabled, baseRevision, otherDirty, onDirty, onDraftText, onPanel, onFinal }: ReviewProps & { onFinal: () => void }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [edited, setEdited] = useState<{ id: number; value: ReportNarrative } | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [confirmGenerate, setConfirmGenerate] = useState(false);
  const [saved, setSaved] = useState('');
  const current = data.reports.find(report => report.status === 'final') ?? [...data.reports].sort((a, b) => b.id - a.id)[0];
  const report = data.reports.find(item => item.id === selected) ?? current;
  const approved = data.latest_findings?.status === 'approved' ? data.latest_findings : null;
  const readOnly = data.session.status === 'final';
  const dirty = !readOnly && edited !== null && edited.id === report?.id;
  const draft = Boolean(report?.status === 'draft' && approved && report.findings_version === approved.version);
  const copy = report ? dirty ? edited?.value : narrative(report.content) : undefined;
  const blockers = data.blockers.filter(item => item.gate === 'gate2');
  const canApprove = Boolean(draft && !dirty && !otherDirty && !disabled && !readOnly && blockers.length === 0 && report?.content.findings.trim() && report?.content.impression.trim());
  const job = actions.job?.operation === 'draft_report' ? actions.job : null;
  const href = `/patients/${data.patient.id}/studies/${data.study.id}/report`;

  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  useEffect(() => { onDraftText(edited ? fields.map(([field]) => edited.value[field]).filter(Boolean) : []); }, [edited, onDraftText]);
  useEffect(() => { if (readOnly) { setEdited(null); setConfirm(false); } }, [readOnly]);
  function generate() {
    if (!approved || disabled || otherDirty || dirty || readOnly) return;
    setConfirmGenerate(false);
    setSelected(null);
    void actions.submit({ operation: 'draft_report', findings_version: approved.version }).catch(() => {});
  }
  function save() {
    if (!report || !copy || !draft || !dirty || disabled) return;
    void actions.transact(options => api.saveReport(data.session.id, report.id, copy, options), () => { setEdited(null); setSaved('Report saved.'); onDirty(false); }, baseRevision).catch(() => {});
  }
  function approve() {
    if (!report || !canApprove) return;
    void actions.transact(options => api.approveReport(data.session.id, report.id, report.findings_version, options), () => { setConfirm(false); onFinal(); }).catch(() => { setConfirm(false); });
  }
  return <div className="panel-content report-panel">
    <div className="review-summary">
      <div><h2>Report</h2>{readOnly ? <Badge tone="approved"><LockKeyhole size={14} aria-hidden />Final</Badge> : report && draft ? <Badge tone="pending">Draft · {formatTime(report.updated_at)}</Badge> : report?.status === 'stale' ? <Badge tone="pending">Stale</Badge> : null}</div>
      {report && <p className="muted dense">{report.content.patient.name} · {formatDate(report.content.study.study_date)} · {report.content.study.indication || 'Chest X-ray'} · from approved findings revision {report.findings_version}</p>}
    </div>
    {job && <AIStatus job={job} actions={actions} />}
    {!report && !approved && <EmptyState title="Approve findings before drafting a report" action={<button className="button" onClick={() => onPanel('findings')}>Go to Review <ArrowRight size={16} aria-hidden /></button>}>Approving the findings fixes the revision the report is drafted from.</EmptyState>}
    {!report && approved && <div className="report-setup"><Badge tone="approved">Findings approved · rev. {approved.version}</Badge><p>Generate a report from the saved approved findings. Review and edit the draft before final approval.</p></div>}
    {report && copy && <>
      {data.reports.length > 1 && <label className="field">Report version<select disabled={dirty} value={report.id} onChange={event => { setSelected(Number(event.target.value)); setSaved(''); }}>{[...data.reports].reverse().map(item => <option key={item.id} value={item.id}>{item.status === 'draft' ? 'Draft' : item.status === 'final' ? 'Final' : 'Stale draft'} from {formatTime(item.updated_at)} · findings rev. {item.findings_version}</option>)}</select></label>}
      {report.status === 'stale' && <Notice tone="pending"><strong>Stale — findings have changed.</strong>{approved ? <><p>This draft came from findings revision {report.findings_version}. Revision {approved.version} is now approved; {current && current.id !== report.id && current.findings_version === approved.version ? 'a newer draft already exists.' : 'generate a new report from it.'}</p>{current && current.id !== report.id && current.findings_version === approved.version && <button className="text-button" onClick={() => setSelected(current.id)}>Open the current draft</button>}</> : <><p>This draft came from findings revision {report.findings_version}. Approve the current findings, then generate a new report.</p><button className="text-button" onClick={() => onPanel('findings')}>Review findings</button></>}</Notice>}
      {dirty && !draft && <><details className="source-details"><summary>Your unsaved text</summary>{fields.map(([field, label]) => <section className="report-section" key={field}><h3>{label}</h3><p>{copy[field] || '—'}</p></section>)}</details><button className="button" onClick={() => setEdited(null)}>Discard unsaved report changes</button></>}
      {readOnly && report.status === 'final' && <Link className="button primary" href={href} prefetch={false}>View final report <ArrowRight size={16} aria-hidden /></Link>}
      {draft && <p className="report-ai-note"><Badge tone="pending">{aiSuggestion}</Badge> AI-drafted text. Edit it before final approval.</p>}
      {fields.map(([field, label]) => draft && !readOnly ? <div className="field" key={field}><label htmlFor={`report-${field}`}>{label}</label><textarea id={`report-${field}`} maxLength={8000} rows={field === 'findings' || field === 'impression' ? 4 : 2} readOnly={disabled} value={copy[field]} onChange={event => { setSaved(''); setEdited({ id: report.id, value: { ...copy, [field]: event.target.value } }); }} /></div> : <section className="report-section" key={field}><h3>{label}</h3><p>{report.content[field] || '—'}</p></section>)}
      {draft && !readOnly && <div className="panel-footer sticky-save">
        <div className="footer-status"><span role="status">{dirty ? 'Unsaved changes' : saved || 'Draft saved'}</span><button className="text-button" onClick={() => onPanel('findings')}>Back to Review</button></div>
        {!canApprove && <ul className="gate-hints" aria-label="Before you can sign"><li>{dirty || otherDirty ? 'Save or discard changes first.' : blockers[0]?.message || 'Save a Findings and an Impression section before signing.'}</li></ul>}
        <div className="footer-actions"><button className="button" disabled={!dirty || disabled} onClick={save}>{actions.busy ? <Spinner /> : <Save size={16} aria-hidden />}Save</button>{dirty && <button className="button ghost" disabled={disabled} onClick={() => setEdited(null)}>Discard</button>}
          <button className="button primary" disabled={!canApprove} onClick={() => setConfirm(true)}><FileCheck size={17} aria-hidden />Sign report</button></div>
      </div>}
    </>}
    {approved && !draft && !readOnly && <><button className="button primary full-action" disabled={disabled || otherDirty || dirty} onClick={() => report ? setConfirmGenerate(true) : generate()}><FileText size={17} aria-hidden />{report ? 'Generate new report' : 'Generate report'}</button>{(otherDirty || dirty) && <p className="muted dense">Save or discard your changes before drafting.</p>}</>}
    {confirmGenerate && report && <Confirm title="Generate a new report" action="Generate new report" cancel="Keep current text" pending={actions.busy} onCancel={() => setConfirmGenerate(false)} onConfirm={generate}><p>The new draft starts from findings revision {approved?.version}. Text you wrote in the current draft is not carried over; it stays available under Report version.</p></Confirm>}
    {confirm && report && <Confirm title="Sign the final report (Gate 2)" action="Sign report" cancel="Continue editing" pending={actions.busy} onCancel={() => setConfirm(false)} onConfirm={approve}><p>This makes the report and this reading session read-only.</p><p><strong>{data.patient.name}</strong> · {formatDate(data.study.study_date)} · Findings revision {report.findings_version}</p></Confirm>}
  </div>;
}

function ReportSource({ data, report }: { data: Workspace; report: ReportDTO }) {
  return <dl className="report-source">
    <div><dt>Patient</dt><dd>{report.content.patient.name} · {report.content.patient.code}</dd></div>
    <div><dt>Study</dt><dd>{formatDate(report.content.study.study_date)} · {report.content.study.indication || 'Chest X-ray'}</dd></div>
    <div><dt>Source</dt><dd>Approved findings, revision {report.findings_version}</dd></div>
    <div><dt>Initial AI analysis image</dt><dd>{imageLabel(data.images, report.content.study.analyzed_image_id)}</dd></div>
  </dl>;
}

export function FinalReport({ data, reload, onDenied }: { data: Workspace; reload: () => Promise<Workspace>; onDenied: () => void }) {
  const { user, expire } = useAuth();
  const [error, setError] = useState(false);
  const report = data.reports.find(item => item.status === 'final');
  const href = `/patients/${data.patient.id}/studies/${data.study.id}`;
  async function check() {
    try { await reload(); setError(false); }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) expire();
      else if (failure instanceof ApiError && [403, 404].includes(failure.status)) onDenied();
      else setError(true);
    }
  }
  useEffect(() => {
    const verify = () => { if (document.visibilityState === 'visible') void check(); };
    document.addEventListener('visibilitychange', verify); window.addEventListener('focus', verify);
    return () => { document.removeEventListener('visibilitychange', verify); window.removeEventListener('focus', verify); };
  }, [reload, onDenied, expire]);
  return <AppShell><main id="main-content" className="final-page">
    <div className="final-top"><Link className="button ghost" href={href} prefetch={false}><ArrowLeft size={16} aria-hidden />Return to workspace</Link><DemoBadge mode={data.capabilities.provider_mode} /></div>
    {error ? <Notice tone="error">Could not reload this report. <button className="text-button" onClick={() => void check()}>Reload</button></Notice> : !report ?
      <div className="surface"><EmptyState title="No final report is available for this reading" action={<Link className="button" href={href} prefetch={false}>Return to workspace</Link>} /></div> : <article className="surface final-document">
        <header className="final-header"><div className="section-heading"><FileCheck size={23} aria-hidden /><h1>Final chest X-ray report</h1></div><Badge tone="approved"><LockKeyhole size={14} aria-hidden />Final — read-only</Badge></header>
        <ReportSource data={data} report={report} />
        {fields.map(([field, label]) => <section className="report-section" key={field}><h2>{label}</h2><p>{report.content[field] || '—'}</p></section>)}
        <footer className="report-signature"><ShieldCheck size={22} aria-hidden /><div><strong>Approved by {report.approved_by === user?.id ? user?.name : `doctor ${report.approved_by ?? '—'}`}</strong><p>{formatTime(report.approved_at)}</p></div></footer>
        <SourceDetails data={data} provenance={data.latest_ai_request?.operation === 'draft_report' ? data.latest_ai_request.provenance : undefined} approval={report.approved_by === user?.id ? user?.name : undefined} />
      </article>}
  </main></AppShell>;
}
