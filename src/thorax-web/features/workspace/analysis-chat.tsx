'use client';

import { Activity, ArrowDown, ImageIcon, Info, MessageSquare, ScanLine, Send, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { aiSuggestion, Badge, EmptyState, formatTime, Notice, Spinner } from '@/components/ui';
import { api, isTerminal } from '@/lib/api';
import type { AIProvenance, AIRequest, BoxDTO, ImageDTO, MessageDTO, Workspace } from '@/lib/contracts';
import { errorText, type ReadingActions } from './useReading';

type AnalysisProps = {
  data: Workspace; image?: ImageDTO; imageReady: boolean; actions: ReadingActions; disabled: boolean;
  reason?: string;
};

export function imageLabel(images: ImageDTO[], id: number | null | undefined) {
  const index = images.findIndex(image => image.id === id);
  return index < 0 ? 'Image unavailable' : `${images[index].view} · Image ${index + 1}`;
}

export function AIStatus({ job, actions }: { job: AIRequest; actions: ReadingActions }) {
  const [time, setTime] = useState(Date.now);
  const active = !isTerminal(job.status);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setTime(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  const started = job.provenance.started_at ? Date.parse(job.provenance.started_at) : null;
  const elapsed = started ? Math.max(0, Math.floor((time - started) / 1000)) : 0;
  const runningLabel = job.operation === 'analyze' ? 'Analyzing image…' : job.operation === 'ask' ? 'Preparing answer…' : 'Drafting report…';
  if (active) return <div className="job-status">
    <div className="job-line"><Spinner /><strong role="status">{job.status === 'queued' ? job.operation === 'analyze' ? 'Waiting to analyze…' : 'Waiting to start…' : runningLabel}</strong><span className="mono muted" aria-hidden>{elapsed}s</span></div>
    {job.operation === 'analyze' && <p className="muted">Analysis continues for this study if you leave.</p>}
    {elapsed >= 30 && <p className="muted">This is taking longer than the demo target.</p>}
    {actions.pollError && <><Notice tone="pending">Connection interrupted; checking the existing request.</Notice><button className="button" onClick={() => void actions.checkStatus()}>Check status</button></>}
  </div>;
  if (job.status === 'succeeded') return null;
  return <div className="job-status">
    <Notice tone="error"><strong>{job.status === 'timed_out' ? 'Request timed out' : job.status === 'cancelled' ? 'Request cancelled' : job.operation === 'analyze' ? 'Analysis failed' : 'AI request failed'}</strong>
      <p>{job.status === 'timed_out' ? 'The AI request exceeded 60 seconds.' : job.error?.code === 'PHI_IN_MODEL_INPUT' ? 'Remove the patient name or patient code from the text before sending it to AI.' : job.error?.code === 'AI_CONTEXT_TOO_LARGE' ? 'The question exceeds the AI context limit. Shorten it and submit again.' : 'The AI service could not complete this request.'}</p>
    </Notice>
    {job.error?.retryable && job.submitted_input && <button className="button" disabled={actions.busy || actions.offline || actions.uncertain || Boolean(actions.conflict)} onClick={() => void actions.retry().catch(() => {})}>Try again</button>}
    <details className="source-details"><summary>Request details</summary><p className="muted mono">Request {job.request_id}{job.error && ` · ${job.error.code}`}</p></details>
  </div>;
}

export function SourceDetails({ data, provenance, approval }: { data: Workspace; provenance?: AIProvenance; approval?: string }) {
  const artifacts = provenance ? [provenance.vlm_artifact, provenance.cv_artifact].filter(Boolean) : [];
  return <details className="source-details"><summary><Info size={15} aria-hidden />AI source</summary>
    <dl className="source-dl"><div><dt>Provider mode</dt><dd>{data.capabilities.provider_mode === 'mock' ? 'Mock AI' : 'Real AI'}</dd></div>
      {(artifacts.length ? artifacts : data.capabilities.model_artifacts).map(artifact => artifact && <div key={`${artifact.id}:${artifact.version}`}><dt>{artifact.id}</dt><dd className="mono">{artifact.version}</dd></div>)}
      {provenance?.prompt_version && <div><dt>Prompt version</dt><dd>{provenance.prompt_version}</dd></div>}
      {provenance?.preprocessing_version && <div><dt>Preprocessing</dt><dd>{provenance.preprocessing_version}</dd></div>}
      {provenance?.history_report_ids && <div><dt>History source IDs</dt><dd>{provenance.history_report_ids.length ? provenance.history_report_ids.join(', ') : 'None'}</dd></div>}
      {provenance?.image_sha256 && <div><dt>Source image SHA-256</dt><dd className="mono hash">{provenance.image_sha256}</dd></div>}
      {approval && <div><dt>Approved by</dt><dd>{approval}</dd></div>}
    </dl>
  </details>;
}

export function AnalysisPanel({ data, image, imageReady, actions, disabled, reason }: AnalysisProps) {
  const analysis = actions.job?.operation === 'analyze' ? actions.job : undefined;
  const active = analysis && !isTerminal(analysis.status);
  const failed = analysis?.error;
  return <div className="panel-content analysis-panel">
    <div className="section-heading"><Activity size={20} aria-hidden /><h2>AI analysis</h2></div>
    <p className="muted">Analyze one image to open Expert Discussion with a preliminary AI report, findings and detected regions.</p>
    <div className="analysis-input"><span className="empty-icon"><ScanLine size={22} aria-hidden /></span><div><span className="muted">Selected image</span><strong>{image ? imageLabel(data.images, image.id) : 'No image available'}</strong></div></div>
    <div className="analysis-sources"><div><span>Earlier reports</span><strong>{data.history.length ? `${data.history.length} available` : 'None'}</strong></div><div><span>Image coverage</span><strong>Current image only</strong></div></div>
    {analysis && <AIStatus job={analysis} actions={actions} />}
    {!active && !failed?.retryable && <button className="button primary full-action" disabled={disabled || !imageReady || !image || !data.capabilities.can_detect} onClick={() => image && void actions.submit({ operation: 'analyze', image_id: image.id }).catch(() => {})}><Activity size={17} aria-hidden />{actions.busy ? 'Submitting…' : 'Analyze selected image'}</button>}
    {!active && (reason || !imageReady || !data.capabilities.can_detect) && <p className="muted dense">{reason || (!data.capabilities.can_detect ? 'The configured detector is unavailable.' : 'Wait for the selected image to load.')}</p>}
    <SourceDetails data={data} provenance={analysis?.provenance} />
    <div className="panel-note"><Info size={16} aria-hidden /><p>Review the original image and any AI suggestions before recording your assessment.</p></div>
  </div>;
}

type ChatProps = {
  data: Workspace; image?: ImageDTO; actions: ReadingActions; disabled: boolean; reason?: string; attachment: BoxDTO | null;
  clearAttachment: () => void; onDirty: (dirty: boolean) => void; visible: boolean;
};

export function ChatPanel({ data, image, actions, disabled, reason, attachment, clearAttachment, onDirty, visible }: ChatProps) {
  const [question, setQuestion] = useState('');
  const [older, setOlder] = useState<MessageDTO[]>([]);
  const [cursor, setCursor] = useState<string | null | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [newAnswer, setNewAnswer] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const feed = useRef<HTMLDivElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const lastMessage = useRef<number | undefined>(data.messages.items.at(-1)?.id);
  const restored = useRef<number | undefined>(undefined);
  const beforeLoad = useRef<number | null>(null);
  const olderFetch = useRef<AbortController | null>(null);
  const currentJob = actions.job?.operation === 'ask' ? actions.job : null;
  const submitted = currentJob?.submitted_input?.operation === 'ask' ? currentJob.submitted_input : null;
  const liveQuestion = currentJob && !isTerminal(currentJob.status) ? submitted : null;
  const readOnly = data.session.status === 'final';
  const analyzed = Boolean(data.latest_findings);
  const messages = [...older.filter(item => !data.messages.items.some(message => message.id === item.id)), ...data.messages.items];
  const next = cursor === undefined ? data.messages.next_cursor : cursor;
  const canSend = analyzed && !readOnly && !disabled && Boolean(image) && Boolean(question.trim()) && question.trim().length <= 2000;

  useEffect(() => { onDirty(!readOnly && Boolean(question.trim()) && question.trim() !== liveQuestion?.question); }, [question, readOnly, liveQuestion?.question, onDirty]);
  useEffect(() => () => olderFetch.current?.abort(), []);
  useEffect(() => {
    if (!currentJob || !submitted || currentJob.request_id === restored.current) return;
    if (currentJob.status === 'succeeded') {
      restored.current = currentJob.request_id;
      setQuestion(value => value.trim() === submitted.question ? '' : value);
      setAnnouncement('AI answer saved.');
    } else if (isTerminal(currentJob.status)) {
      restored.current = currentJob.request_id;
      setQuestion(value => value || submitted.question);
    }
  }, [currentJob, submitted]);
  useEffect(() => {
    const last = data.messages.items.at(-1)?.id;
    const node = feed.current;
    if (!node || last === lastMessage.current) return;
    lastMessage.current = last;
    if (node.scrollHeight - node.scrollTop - node.clientHeight < 140 || !visible) node.scrollTop = node.scrollHeight;
    else setNewAnswer(true);
  }, [data.messages.items, visible]);
  useEffect(() => {
    if (beforeLoad.current !== null && feed.current) { feed.current.scrollTop += feed.current.scrollHeight - beforeLoad.current; beforeLoad.current = null; }
  }, [older]);
  useEffect(() => { if (visible && attachment) questionRef.current?.focus(); }, [visible, attachment]);

  async function loadOlder() {
    if (!next || loading) return;
    const controller = new AbortController();
    olderFetch.current = controller;
    setLoading(true); setError('');
    try {
      const page = await api.messages(data.session.id, next, controller.signal);
      beforeLoad.current = feed.current?.scrollHeight ?? null;
      setOlder(items => [...page.items, ...items]);
      setCursor(page.next_cursor);
    } catch (failure) { if (!controller.signal.aborted) setError(errorText(failure)); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }
  function send() {
    if (!canSend || !image) return;
    setError('');
    void actions.submit({ operation: 'ask', image_id: image.id, question: question.trim(), ...(attachment ? { box_id: attachment.id } : {}) }).catch(failure => { if (errorText(failure).includes('patient')) questionRef.current?.focus(); });
  }
  return <div className="panel-content chat-panel">
    <div className="panel-heading"><div className="section-heading"><MessageSquare size={20} aria-hidden /><h2>Chat with AI</h2></div>{readOnly && <Badge tone="approved">Read-only</Badge>}</div>
    {data.preliminary_report ? <details className="preliminary-report" open>
      <summary>Preliminary AI report</summary>
      <Badge tone="pending">{aiSuggestion}</Badge> <Badge>{imageLabel(data.images, data.preliminary_report.image_id)}</Badge>
      <p>{data.preliminary_report.text}</p>
      <SourceDetails data={data} provenance={data.initial_analysis?.provenance} />
    </details> : analyzed && <p className="muted">Preliminary report unavailable for this saved reading.</p>}
    <p className="muted">Discuss this image or a saved region, then record any corrections in Findings.</p>
    {image && <Badge><ImageIcon size={14} aria-hidden />{imageLabel(data.images, image.id)}</Badge>}
    <div ref={feed} className="message-feed" aria-label="Saved study chat" tabIndex={0}>
      {next && <button className="button ghost" disabled={loading} onClick={() => void loadOlder()}>{loading ? 'Loading…' : 'Load earlier messages'}</button>}
      {error && <Notice tone="error">{error}</Notice>}
      {!messages.length && !liveQuestion && <EmptyState title="No saved messages">Ask a question about this X-ray or select a region.</EmptyState>}
      {messages.map(message => <article className={`message ${message.role === 'user' ? 'doctor' : 'assistant'}`} key={message.id}>
        <div className="message-meta"><strong>{message.role === 'user' ? 'Doctor' : 'AI assistant'}</strong><time dateTime={message.created_at}>{formatTime(message.created_at)}</time></div>
        {message.role === 'assistant' && <Badge tone="pending">{aiSuggestion}</Badge>}<p>{message.text}</p><Badge>{imageLabel(data.images, message.image_id)}</Badge>
        {message.box_snapshot && <details className="source-details"><summary>Region as submitted</summary><p className="mono">Region {message.box_snapshot.box_id}, revision {message.box_snapshot.revision}</p><p className="mono">{message.box_snapshot.bbox_xyxy.join(', ')} px</p></details>}
        {message.role === 'assistant' && message.model_version && <small className="muted">Model {message.model_version}</small>}
      </article>)}
      {liveQuestion && <article className="message doctor"><div className="message-meta"><strong>Doctor</strong><span>Pending</span></div><p>{liveQuestion.question}</p><Badge>{imageLabel(data.images, liveQuestion.image_id)}</Badge></article>}
    </div>
    {newAnswer && <button className="button" onClick={() => { if (feed.current) feed.current.scrollTop = feed.current.scrollHeight; setNewAnswer(false); }}>New answer <ArrowDown size={15} aria-hidden /></button>}
    <div className="sr-only" role="status">{announcement}</div>
    {currentJob && <AIStatus job={currentJob} actions={actions} />}
    {!readOnly && <form className="chat-form" onSubmit={event => { event.preventDefault(); send(); }}>
      {attachment && <div className="attachment"><Badge tone="info">Region {attachment.id} · {imageLabel(data.images, attachment.image_id)}</Badge><button className="icon-button" type="button" aria-label="Remove region attachment" onClick={clearAttachment}><X size={16} aria-hidden /></button></div>}
      <label className="field">Doctor’s question<textarea ref={questionRef} rows={3} maxLength={2000} placeholder="Ask about the current image…" value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); send(); } }} /></label>
      <div className="chat-actions"><small className="mono muted">{question.length}/2000</small><button className="button primary" disabled={!canSend}><Send size={16} aria-hidden />Send</button></div>
      <p className="muted dense">{!analyzed ? 'Analyze this study before asking questions.' : reason || 'Do not enter patient names or patient codes.'}</p>
    </form>}
  </div>;
}
