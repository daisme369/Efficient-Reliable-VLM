'use client';

import { useEffect, useId, useRef, type ReactNode } from 'react';
import { AlertCircle, ClipboardList, LoaderCircle } from 'lucide-react';

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'info' | 'pending' | 'approved' | 'error' }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'pending' | 'error' }) {
  return <div className={`notice ${tone}`} role={tone === 'error' ? 'alert' : 'status'}><AlertCircle aria-hidden size={17} /><div>{children}</div></div>;
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-icon"><ClipboardList aria-hidden size={21} /></span><h2>{title}</h2>{children && <p>{children}</p>}{action}</div>;
}

export function Skeleton({ label, rows = 4 }: { label: string; rows?: number }) {
  return <div className="skeleton" role="status"><span className="sr-only">{label}</span>{Array.from({ length: rows }, (_, index) => <div key={index} />)}</div>;
}

export function Spinner() {
  return <LoaderCircle className="spinner" size={16} aria-hidden />;
}

export function Confirm({ title, children, cancel = 'Cancel', action, pending = false, danger = false, onCancel, onConfirm }: {
  title: string; children: ReactNode; cancel?: string; action: string; pending?: boolean; danger?: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const description = useId();
  useEffect(() => {
    const dialog = ref.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);
  return <dialog ref={ref} className="confirm" aria-labelledby={heading} aria-describedby={description} onCancel={event => { event.preventDefault(); if (!pending) onCancel(); }}>
    <h2 id={heading}>{title}</h2><div id={description} className="confirm-copy">{children}</div>
    <div className="confirm-actions"><button className="button" autoFocus disabled={pending} onClick={onCancel}>{cancel}</button>
      <button className={`button ${danger ? 'danger' : 'primary'}`} disabled={pending} onClick={onConfirm}>{pending && <Spinner />}{pending ? 'Saving…' : action}</button></div>
  </dialog>;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return Number.isNaN(date.valueOf()) ? '—' : new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeZone: 'UTC' }).format(date);
}

export function formatTime(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date) + ' UTC';
}

export const aiSuggestion = 'AI suggestion — needs doctor review';

export function readingLabel(status: string | null | undefined) {
  switch (status) {
    case 'active': return 'In progress';
    case 'findings_approved': return 'Findings approved';
    case 'final': return 'Final — read-only';
    default: return 'Not started';
  }
}
