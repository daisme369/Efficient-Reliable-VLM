'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { BoxSelect, Check, Eye, EyeOff, Maximize, Minus, MousePointer2, Plus, SlidersHorizontal, Trash2, Undo2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { AppShell, DesktopNotice, RoleGate, useDesktop } from '@/components/shell';
import { Badge, Confirm, EmptyState, Notice, Skeleton, Spinner } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import type { BoxCoordinates, LabelBox, LabelState, LabelTask, LabelVocabulary } from '@/lib/contracts';
import { BackendOnly, statusLabel, statusTone } from './LabelingQueue';
import './labeling.css';

const stateLabel: Record<LabelState, string> = { positive: 'Positive', negative: 'Negative', uncertain: 'Uncertain' };
const MIN_BOX = 4; // original-image pixels; smaller drags are treated as clicks

export function LabelingTaskRoute({ taskId, localDemo }: { taskId: number; localDemo: boolean }) {
  const desktop = useDesktop();
  return <RoleGate role="annotator">{() => <AppShell workspace={desktop && !localDemo}>{!desktop ? <DesktopNotice showWorklist={false} /> : localDemo ? <BackendOnly /> : <TaskLoader taskId={taskId} />}</AppShell>}</RoleGate>;
}

function TaskLoader({ taskId }: { taskId: number }) {
  const task = useQuery({ queryKey: ['labeling', 'task', taskId], queryFn: ({ signal }) => api.labelTask(taskId, signal), enabled: Number.isSafeInteger(taskId) && taskId > 0 });
  const vocabulary = useQuery({ queryKey: ['labeling', 'vocabulary'], queryFn: ({ signal }) => api.labelVocabulary(signal), staleTime: Infinity });
  if (task.error instanceof ApiError && task.error.status === 404 || !Number.isSafeInteger(taskId) || taskId < 1) {
    return <main id="main-content" className="page-wrap surface"><EmptyState title="This task is unavailable" action={<Link className="button" prefetch={false} href="/labeling">Back to the queue</Link>} /></main>;
  }
  if (task.error || vocabulary.error) return <main id="main-content" className="page-wrap surface"><EmptyState title="Could not load the task" action={<button className="button" onClick={() => { void task.refetch(); void vocabulary.refetch(); }}>Retry</button>}>Check your connection and try again.</EmptyState></main>;
  if (!task.data || !vocabulary.data) return <main id="main-content" className="page-wrap surface"><Skeleton label="Loading task" rows={6} /></main>;
  return <TaskWorkspace key={`${task.data.id}:${task.data.revision}`} task={task.data} vocabulary={vocabulary.data} />;
}

function TaskWorkspace({ task, vocabulary }: { task: LabelTask; vocabulary: LabelVocabulary }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [boxes, setBoxes] = useState<LabelBox[]>(task.boxes);
  const [selected, setSelected] = useState<number | null>(task.boxes.length ? 0 : null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [comment, setComment] = useState('');
  const [commentError, setCommentError] = useState(false);
  const [confirmReturn, setConfirmReturn] = useState(false);
  const last = useRef<Pick<LabelBox, 'region' | 'finding'>>({ region: vocabulary.regions[0], finding: vocabulary.findings[0] });
  const dirty = JSON.stringify(boxes) !== JSON.stringify(task.boxes);
  const editable = task.can_edit && !busy;
  const reviewing = task.role === 'reviewer' && task.status === 'submitted';
  const changedByReviewer = reviewing && dirty;

  useEffect(() => {
    function warn(event: BeforeUnloadEvent) { if (dirty) event.preventDefault(); }
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  function update(index: number, patch: Partial<LabelBox>) {
    setBoxes(current => current.map((box, i) => i === index ? { ...box, ...patch } : box));
    if (patch.region || patch.finding) last.current = { ...last.current, ...(patch.region ? { region: patch.region } : {}), ...(patch.finding ? { finding: patch.finding } : {}) };
  }
  function add(bbox: BoxCoordinates) {
    setSelected(boxes.length);
    setBoxes([...boxes, { ...last.current, state: 'positive', bbox_xyxy: bbox }]);
  }
  function remove(index: number) {
    setBoxes(current => current.filter((_, i) => i !== index));
    setSelected(null);
  }

  async function run(action: () => Promise<LabelTask | void>, after?: (result: LabelTask | void) => void) {
    setBusy(true); setError('');
    try {
      const result = await action();
      await queryClient.invalidateQueries({ queryKey: ['labeling'] });
      after?.(result);
    } catch (failure) {
      setError(failure instanceof ApiError && failure.code === 'REVISION_CONFLICT'
        ? 'This task changed in another window. Reload to see the latest version; your unsaved changes will be lost.'
        : failure instanceof ApiError ? failure.message : 'Could not reach the server. Try again.');
    } finally { setBusy(false); }
  }
  // Saving returns a new revision; later steps must use it.
  async function saveIfNeeded() { return dirty ? api.saveLabelBoxes(task.id, boxes, task.revision) : task; }
  const save = () => run(() => api.saveLabelBoxes(task.id, boxes, task.revision));
  const submit = () => run(async () => api.submitLabelTask(task.id, (await saveIfNeeded()).revision), () => router.push('/labeling'));
  const approve = () => run(async () => api.reviewLabelTask(task.id, 'approve', comment.trim(), (await saveIfNeeded()).revision), () => router.push('/labeling'));
  function sendBack() {
    if (!comment.trim()) { setCommentError(true); document.getElementById('review-comment')?.focus(); return; }
    setConfirmReturn(true);
  }
  const doReturn = () => run(async () => api.reviewLabelTask(task.id, 'return', comment.trim(), (await saveIfNeeded()).revision), () => router.push('/labeling'));

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (event.ctrlKey || event.metaKey || event.altKey || target?.closest('input, textarea, select') || document.querySelector('dialog[open]')) return;
      const key = event.key.toLowerCase();
      if (/^[1-9]$/.test(key) && Number(key) <= boxes.length) { setSelected(Number(key) - 1); event.preventDefault(); return; }
      if (!editable || selected === null) return;
      const states: Record<string, LabelState> = { p: 'positive', n: 'negative', u: 'uncertain' };
      if (states[key]) { update(selected, { state: states[key] }); event.preventDefault(); }
      if (key === 'delete' || key === 'backspace') { remove(selected); event.preventDefault(); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  return <main id="main-content" className="labeling-task">
    <div className="labeling-strip">
      <dl>
        <div><dt>{reviewing ? 'Reviewing task' : 'Task'}</dt><dd>{task.id}</dd></div>
        <div><dt>Image</dt><dd className="mono">{task.image_id} · {task.image.view} · {task.image.width} × {task.image.height} px</dd></div>
        <div><dt>Status</dt><dd><Badge tone={statusTone[task.status]}>{statusLabel[task.status]}</Badge></dd></div>
      </dl>
      <span className="muted dense">No patient details are shown in labeling.</span>
    </div>
    {task.status === 'returned' && task.review_comment && task.role === 'labeler' && <div className="labeling-notice"><Notice tone="pending"><strong>Returned by the reviewer:</strong> “{task.review_comment}”</Notice></div>}
    {error && <div className="labeling-notice"><Notice tone="error">{error}</Notice></div>}
    <div className="labeling-grid">
      <BoxCanvas task={task} boxes={boxes} selected={selected} editable={editable} onSelect={setSelected} onAdd={add} onMove={(index, bbox) => update(index, { bbox_xyxy: bbox })} />
      <aside className="surface labeling-panel" aria-label={reviewing ? 'Review' : 'Labels'}>
        <div className="labeling-panel-head">
          <div><h1>{reviewing ? 'Check and fix the boxes' : 'Boxes'}</h1><Badge>{boxes.length} on this image</Badge></div>
          <p className="muted dense">{!task.can_edit ? 'Read-only.' : reviewing ? 'You can fix any box before you approve. Your changes are recorded as reviewer edits.' : 'Draw on the image, then set each box’s region, finding and state.'}</p>
        </div>
        <ol className="label-list">
          {boxes.map((box, index) => {
            const open = selected === index;
            const original = task.boxes[index];
            const changed = reviewing && JSON.stringify(original) !== JSON.stringify(box);
            return <li key={index} className={open ? 'selected' : undefined}>
              <button className="label-row" aria-expanded={open} onClick={() => setSelected(open ? null : index)}>
                <span className="label-index">{index + 1}</span>
                <span><strong>{box.finding}</strong><small>{box.region} · {stateLabel[box.state]}</small></span>
                {changed && <Badge tone="info">Changed by you</Badge>}
              </button>
              {open && <div className="label-fields">
                <label className="field">Region<select value={box.region} disabled={!editable} onChange={event => update(index, { region: event.target.value })}>{vocabulary.regions.map(region => <option key={region}>{region}</option>)}</select></label>
                <label className="field">Finding<select value={box.finding} disabled={!editable} onChange={event => update(index, { finding: event.target.value })}>{vocabulary.findings.map(finding => <option key={finding}>{finding}</option>)}</select></label>
                <fieldset className="label-states" disabled={!editable}><legend>State</legend>
                  <div>{vocabulary.states.map(state => <button key={state} type="button" aria-pressed={box.state === state} onClick={() => update(index, { state })}>{stateLabel[state]}</button>)}</div>
                </fieldset>
                <div className="label-meta"><span className="mono muted">{box.bbox_xyxy.map(value => value.toFixed(0)).join(', ')} px</span>
                  {editable && <span className="labeling-row-actions">
                    {changed && original && <button className="button small ghost" onClick={() => update(index, original)}><Undo2 size={15} aria-hidden />Undo my change</button>}
                    <button className="button small ghost danger-text" onClick={() => remove(index)}><Trash2 size={15} aria-hidden />Delete box</button>
                  </span>}
                </div>
              </div>}
            </li>;
          })}
        </ol>
        {!boxes.length && <p className="muted dense labeling-empty">{task.can_edit ? 'No boxes yet. Submitting with no boxes means “no finding on this image”.' : 'No finding was marked on this image.'}</p>}
        <details className="labeling-shortcuts"><summary>Keyboard shortcuts</summary>
          <dl><dt>B</dt><dd>Draw a box</dd><dt>V</dt><dd>Select tool; drag a box to move it, drag a corner to resize</dd><dt>Arrows</dt><dd>Nudge the selected box (Shift: 10 px)</dd><dt>1–9</dt><dd>Select a box</dd><dt>P, N, U</dt><dd>Positive, negative, uncertain</dd><dt>Delete</dt><dd>Delete the selected box</dd></dl>
        </details>
        <div className="labeling-footer">
          {reviewing ? <>
            <label className="field" htmlFor="review-comment">Comment for the labeler<span className="muted dense">Required to return. Optional when you approve.</span>
              <textarea id="review-comment" rows={3} maxLength={1000} value={comment} aria-invalid={commentError || undefined} aria-describedby={commentError ? 'comment-error' : undefined} onChange={event => { setComment(event.target.value); setCommentError(false); }} />
            </label>
            {commentError && <p id="comment-error" className="labeling-error" role="alert">Say what to fix before returning the task.</p>}
            <div className="labeling-actions">
              <button className="button" disabled={busy} onClick={sendBack}>Return to labeler</button>
              <button className="button primary" disabled={busy} onClick={() => void approve()}>{busy ? <Spinner /> : <Check size={16} aria-hidden />}{changedByReviewer ? 'Approve with my changes' : 'Approve'}</button>
            </div>
          </> : task.can_edit ? <>
            <div className="labeling-footer-status"><span role="status">{dirty ? 'Unsaved changes' : 'All changes saved'}</span></div>
            <div className="labeling-actions">
              <button className="button" disabled={busy || !dirty} onClick={() => void save()}>Save</button>
              <button className="button primary" disabled={busy} onClick={() => void submit()}>{busy ? <Spinner /> : null}Submit for review</button>
            </div>
          </> : <div className="labeling-actions"><Link className="button" prefetch={false} href="/labeling">Back to the queue</Link></div>}
        </div>
      </aside>
    </div>
    {confirmReturn && <Confirm title="Return to the labeler?" action="Return task" pending={busy} onCancel={() => setConfirmReturn(false)} onConfirm={() => { setConfirmReturn(false); void doReturn(); }}>
      <p>The labeler sees your comment{changedByReviewer ? ' and your box changes' : ''} and fixes the task.</p><p>“{comment.trim()}”</p>
    </Confirm>}
  </main>;
}

type Handle = 'move' | 'nw' | 'ne' | 'sw' | 'se';

/** Moves the whole box or one corner by (dx, dy) image pixels, kept inside the image and at least MIN_BOX in size. */
export function moveBox(box: BoxCoordinates, handle: Handle, dx: number, dy: number, width: number, height: number): BoxCoordinates {
  let [x1, y1, x2, y2] = box;
  if (handle === 'move') {
    const mx = Math.min(Math.max(dx, -x1), width - x2), my = Math.min(Math.max(dy, -y1), height - y2);
    return [x1 + mx, y1 + my, x2 + mx, y2 + my].map(v => Math.round(v * 100) / 100) as BoxCoordinates;
  }
  if (handle.includes('w')) x1 = Math.min(Math.max(0, x1 + dx), x2 - MIN_BOX);
  if (handle.includes('e')) x2 = Math.max(Math.min(width, x2 + dx), x1 + MIN_BOX);
  if (handle.includes('n')) y1 = Math.min(Math.max(0, y1 + dy), y2 - MIN_BOX);
  if (handle.includes('s')) y2 = Math.max(Math.min(height, y2 + dy), y1 + MIN_BOX);
  return [x1, y1, x2, y2].map(v => Math.round(v * 100) / 100) as BoxCoordinates;
}

function BoxCanvas({ task, boxes, selected, editable, onSelect, onAdd, onMove }: {
  task: LabelTask; boxes: LabelBox[]; selected: number | null; editable: boolean;
  onSelect: (index: number | null) => void; onAdd: (bbox: BoxCoordinates) => void; onMove: (index: number, bbox: BoxCoordinates) => void;
}) {
  const { width, height } = task.image;
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [tool, setTool] = useState<'select' | 'draw'>(editable && !boxes.length ? 'draw' : 'select');
  const [zoom, setZoom] = useState(1);
  const [overlays, setOverlays] = useState(true);
  const [filters, setFilters] = useState(false);
  const [brightness, setBrightness] = useState(1);
  const [contrast, setContrast] = useState(1);
  const [draft, setDraft] = useState<BoxCoordinates | null>(null);
  const start = useRef<[number, number] | null>(null);
  const drag = useRef<{ index: number; handle: Handle; from: [number, number]; box: BoxCoordinates } | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState(0); // displayed width at 100% zoom: the whole image fits the stage

  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const measure = () => setFit(Math.max(120, Math.min(element.clientWidth - 32, (element.clientHeight - 32) * width / height)));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [width, height, url]);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | null = null;
    api.image(task.image_id, controller.signal).then(blob => { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [task.image_id]);
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (event.ctrlKey || event.metaKey || event.altKey || target?.closest('input, textarea, select') || document.querySelector('dialog[open]')) return;
      if (event.key.toLowerCase() === 'b' && editable) { setTool('draw'); setOverlays(true); }
      if (event.key.toLowerCase() === 'v') setTool('select');
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [editable]);

  // Pointer position -> original-image pixels, clamped to the image.
  function point(event: ReactPointerEvent): [number, number] {
    const rect = surface.current!.getBoundingClientRect();
    const clamp = (value: number, max: number) => Math.min(max, Math.max(0, value));
    return [clamp((event.clientX - rect.left) / rect.width * width, width), clamp((event.clientY - rect.top) / rect.height * height, height)];
  }
  function down(event: ReactPointerEvent<HTMLDivElement>) {
    if (!editable || event.button !== 0) return;
    if (tool === 'select') {
      // Drag a box to move it, or one of its corner handles to resize it.
      const target = (event.target as HTMLElement).closest<HTMLElement>('[data-handle], .label-box[data-index]');
      if (!target) return;
      const index = Number(target.closest<HTMLElement>('.label-box')!.dataset.index);
      onSelect(index);
      drag.current = { index, handle: (target.dataset.handle as Handle | undefined) ?? 'move', from: point(event), box: boxes[index].bbox_xyxy };
      event.currentTarget.setPointerCapture(event.pointerId);
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    start.current = point(event);
  }
  function move(event: ReactPointerEvent<HTMLDivElement>) {
    if (drag.current) {
      const [x, y] = point(event), { index, handle, from, box } = drag.current;
      onMove(index, moveBox(box, handle, x - from[0], y - from[1], width, height));
      return;
    }
    if (!start.current) return;
    const [x, y] = point(event), [x0, y0] = start.current;
    setDraft([Math.min(x0, x), Math.min(y0, y), Math.max(x0, x), Math.max(y0, y)]);
  }
  function up() {
    drag.current = null;
    if (draft && draft[2] - draft[0] >= MIN_BOX && draft[3] - draft[1] >= MIN_BOX) {
      onAdd(draft.map(value => Math.round(value * 100) / 100) as BoxCoordinates);
      setTool('select');
    }
    start.current = null; setDraft(null);
  }

  const pct = (value: number, total: number) => `${(value / total) * 100}%`;
  const rect = (b: BoxCoordinates) => ({ left: pct(b[0], width), top: pct(b[1], height), width: pct(b[2] - b[0], width), height: pct(b[3] - b[1], height) });
  return <section className="labeling-viewer" aria-label="Image viewer">
    <div className="labeling-toolbar" role="group" aria-label="Viewer tools">
      <button className="viewer-tool" aria-label="Select (V)" title="Select (V)" aria-pressed={tool === 'select'} onClick={() => setTool('select')}><MousePointer2 size={18} aria-hidden /></button>
      {editable && <button className="viewer-tool" aria-label="Draw box (B)" title="Draw box (B)" aria-pressed={tool === 'draw'} onClick={() => { setTool('draw'); setOverlays(true); }}><BoxSelect size={18} aria-hidden /></button>}
      <span className="viewer-divider" aria-hidden />
      <button className="viewer-tool" aria-label="Zoom out" title="Zoom out" disabled={zoom <= 0.5} onClick={() => setZoom(value => Math.max(0.5, value / 1.25))}><Minus size={18} aria-hidden /></button>
      <output aria-label="Zoom" className="viewer-zoom">{Math.round(zoom * 100)}%</output>
      <button className="viewer-tool" aria-label="Zoom in" title="Zoom in" disabled={zoom >= 4} onClick={() => setZoom(value => Math.min(4, value * 1.25))}><Plus size={18} aria-hidden /></button>
      <button className="viewer-tool" aria-label="Fit" title="Fit" onClick={() => setZoom(1)}><Maximize size={18} aria-hidden /></button>
      <span className="viewer-divider" aria-hidden />
      <button className="viewer-tool" aria-label={overlays ? 'Hide boxes' : 'Show boxes'} title={overlays ? 'Hide boxes' : 'Show boxes'} aria-pressed={overlays} onClick={() => setOverlays(!overlays)}>{overlays ? <Eye size={18} aria-hidden /> : <EyeOff size={18} aria-hidden />}</button>
      <button className="viewer-tool" aria-label="Brightness and contrast" title="Brightness and contrast" aria-expanded={filters} onClick={() => setFilters(!filters)}><SlidersHorizontal size={18} aria-hidden /></button>
    </div>
    {filters && <div className="labeling-filters">
      <label>Brightness<input type="range" min={0.5} max={2} step={0.05} value={brightness} onChange={event => setBrightness(event.target.valueAsNumber)} /><output>{brightness.toFixed(2)}</output></label>
      <label>Contrast<input type="range" min={0.5} max={2} step={0.05} value={contrast} onChange={event => setContrast(event.target.valueAsNumber)} /><output>{contrast.toFixed(2)}</output></label>
    </div>}
    <div ref={stage} className="labeling-stage">
      {failed ? <p className="labeling-stage-message">The image is unavailable. Reload the page.</p> : !url ? <p className="labeling-stage-message">Loading image…</p> :
        <div ref={surface} className="labeling-surface" data-tool={tool} style={{ width: fit * zoom, aspectRatio: `${width} / ${height}` }}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          {/* eslint-disable-next-line @next/next/no-img-element -- local blob URL of an access-checked image */}
          <img src={url} alt={`Chest X-ray, ${task.image.view}, ${width} by ${height} pixels`} draggable={false} style={{ filter: `brightness(${brightness}) contrast(${contrast})` }} />
          {overlays && boxes.map((box, index) => <button key={index} type="button" className="label-box" data-index={index} data-selected={selected === index} data-editable={editable && tool === 'select'} style={rect(box.bbox_xyxy)}
            aria-label={`Box ${index + 1}: ${box.finding}, ${box.region}`} tabIndex={tool === 'draw' ? -1 : 0} onClick={() => tool === 'select' && onSelect(index)}
            onKeyDown={event => {
              // Arrow keys nudge the selected box by 1 px (Shift: 10 px).
              const step = event.shiftKey ? 10 : 1;
              const delta = ({ ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] } as Record<string, [number, number]>)[event.key];
              if (!delta || !editable || selected !== index) return;
              event.preventDefault();
              onMove(index, moveBox(box.bbox_xyxy, 'move', delta[0], delta[1], width, height));
            }}>
            <span>{index + 1}</span>
            {editable && tool === 'select' && selected === index && (['nw', 'ne', 'sw', 'se'] as const).map(corner => <i key={corner} className="label-handle" data-handle={corner} aria-hidden />)}
          </button>)}
          {draft && <span className="label-box drafting" style={rect(draft)} aria-hidden />}
        </div>}
    </div>
    <div className="labeling-statusbar"><span>Coordinates in original image pixels</span><span>{tool === 'draw' ? 'Drag on the image to draw a box' : 'Click a box to select it'}</span></div>
  </section>;
}
