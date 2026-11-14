'use client';

import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { AppShell, DesktopNotice, RoleGate, useDesktop } from '@/components/shell';
import { Badge, EmptyState, formatTime, Notice, Skeleton, Spinner } from '@/components/ui';
import { api, ApiError } from '@/lib/api';
import type { LabelQueueRow, LabelTaskStatus } from '@/lib/contracts';
import './labeling.css';

export const statusLabel: Record<LabelTaskStatus, string> = {
  open: 'Unlabeled', in_progress: 'In progress', submitted: 'Awaiting review', returned: 'Returned', approved: 'Approved',
};
export const statusTone = { open: 'neutral', in_progress: 'info', submitted: 'neutral', returned: 'pending', approved: 'approved' } as const;

export function BackendOnly() {
  return <main id="main-content" className="page-wrap surface"><EmptyState title="Labeling needs the Thorax backend">
    The local demo API has no labeling. Start the backend (python scripts/serve_api.py) and run the web app with THORAX_API_ORIGIN set.
  </EmptyState></main>;
}

export function LabelingQueueRoute({ localDemo }: { localDemo: boolean }) {
  const desktop = useDesktop();
  return <RoleGate role="annotator">{() => <AppShell>{!desktop ? <DesktopNotice showWorklist={false} /> : localDemo ? <BackendOnly /> : <LabelingQueue />}</AppShell>}</RoleGate>;
}

function LabelingQueue() {
  const router = useRouter();
  const queue = useQuery({ queryKey: ['labeling', 'queue'], queryFn: ({ signal }) => api.labelQueue(signal) });
  const [claiming, setClaiming] = useState(false);
  const [error, setError] = useState('');

  async function next() {
    if (claiming) return;
    setClaiming(true); setError('');
    try {
      const task = await api.labelNext();
      router.push(`/labeling/tasks/${task.id}`);
    } catch (failure) {
      setError(failure instanceof ApiError && failure.code === 'NO_TASKS' ? 'There are no images left to label.' : 'Could not get the next image. Try again.');
      setClaiming(false);
    }
  }
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (event.key.toLowerCase() !== 'n' || event.ctrlKey || event.metaKey || event.altKey || target?.closest('input, textarea, select')) return;
      event.preventDefault(); void next();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (queue.isPending) return <main id="main-content" className="page-wrap surface"><Skeleton label="Loading labeling queue" rows={5} /></main>;
  if (queue.error) return <main id="main-content" className="page-wrap surface"><EmptyState title="Could not load the labeling queue" action={<button className="button" onClick={() => void queue.refetch()}>Retry</button>}>Check your connection and try again.</EmptyState></main>;
  const { my_tasks: mine, to_review: review, counts } = queue.data;
  const returned = mine.filter(task => task.status === 'returned').length;
  const nextText = returned ? `Next: fix ${returned} returned ${returned === 1 ? 'image' : 'images'}` : mine.length ? 'Next: continue your image' : counts.open ? 'Next: label a new image' : 'No images left to label';

  return <main id="main-content" className="labeling-page">
    <div className="page-heading"><div><h1>Labeling queue</h1><p>Mark findings on chest X-rays with region boxes. A second annotator reviews each image.</p></div><Badge tone="info">Demo · synthetic images</Badge></div>
    <section className="surface labeling-next">
      <div className="labeling-next-main">
        <h2>{nextText}</h2>
        <p className="muted dense">Returned and in-progress work comes first, then the oldest unlabeled image.</p>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="labeling-row-actions">
          <button className="button primary" disabled={claiming || (!mine.length && !counts.open)} onClick={() => void next()}>{claiming ? <Spinner /> : null}Label next image <ArrowRight size={16} aria-hidden /></button>
          <span className="muted dense">Shortcut <kbd>N</kbd></span>
        </div>
      </div>
      <dl className="labeling-counts">{(Object.keys(statusLabel) as LabelTaskStatus[]).map(status => <div key={status}><dt>{statusLabel[status]}</dt><dd>{counts[status]}</dd></div>)}</dl>
    </section>
    <div className="labeling-lists">
      <TaskTable title="Your work" empty="Nothing in progress." rows={mine} action={row => row.status === 'returned' ? 'Fix' : 'Continue'} note={row => row.review_comment ? `Reviewer: “${row.review_comment}”` : `Last change ${formatTime(row.updated_at)}`} />
      <TaskTable title="Waiting for your review" empty="Nothing to review. You can’t review your own labels." rows={review} action={() => 'Review'} note={row => `Submitted ${formatTime(row.updated_at)}`} />
    </div>
    <p className="muted dense">Only approved labels are exported. The label list is unverified. Training on these labels needs an approved scope-change record.</p>
  </main>;
}

function TaskTable({ title, empty, rows, action, note }: { title: string; empty: string; rows: LabelQueueRow[]; action: (row: LabelQueueRow) => string; note: (row: LabelQueueRow) => string }) {
  return <section className="surface labeling-list" aria-label={title}>
    <div className="labeling-list-head"><h2>{title}</h2><span className="muted dense">{rows.length}</span></div>
    {rows.length ? <table>
      <thead><tr><th scope="col">Task</th><th scope="col">Status</th><th scope="col">Note</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id}>
        <th scope="row">Task {row.id}<small className="mono">Image {row.image_id}</small></th>
        <td><Badge tone={statusTone[row.status]}>{statusLabel[row.status]}</Badge></td>
        <td className="muted">{note(row)}</td>
        <td className="cell-action"><Link className="button" prefetch={false} href={`/labeling/tasks/${row.id}`}>{action(row)}</Link></td>
      </tr>)}</tbody>
    </table> : <p className="muted dense labeling-empty">{empty}</p>}
  </section>;
}
