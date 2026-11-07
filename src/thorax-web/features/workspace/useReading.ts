'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { actionKey, api, ApiError, isTerminal, type MutationOptions } from '@/lib/api';
import type { AICommand, AIRequest, Workspace } from '@/lib/contracts';
import { useAuth } from '@/components/shell';

type Mutation<T> = (options: MutationOptions) => Promise<T>;

export function errorText(error: unknown) {
  if (!(error instanceof ApiError)) return 'The request could not be completed. Try again.';
  switch (error.code) {
    case 'RESOURCE_NOT_FOUND': return 'This study is unavailable.';
    case 'AI_BUSY': return 'Another AI request is running for this study.';
    case 'GATE1_REQUIRED': return 'Approve the current findings before generating a report.';
    case 'ANALYSIS_REQUIRED': return 'Analyze this study before asking questions or editing regions and findings.';
    case 'UNRESOLVED_REVIEW': return 'Review the remaining findings and AI regions before approving.';
    case 'REPORT_STALE': return 'Findings changed after this draft was created. Approve them and generate a new report.';
    case 'FINAL_READ_ONLY': return 'This reading is final and read-only.';
    case 'PHI_IN_MODEL_INPUT': return 'Remove the patient name or patient code from the text before sending it to AI.';
    case 'CONTEXT_CHANGED': return 'The original context changed. Review the current selection and submit again.';
    default: return error.status === 422 && error.details.length ? error.details.map(detail => detail.reason).join(' ') : error.status >= 500 ? 'The service could not complete this request. Try again.' : error.message;
  }
}

function unavailable() { return new ApiError(404, 'RESOURCE_NOT_FOUND', 'This study is unavailable.'); }

export function useReading(actor: number, patientId: number, studyId: number) {
  const queryClient = useQueryClient();
  const { expire } = useAuth();
  const key = ['actor', actor, 'patient', patientId, 'study', studyId, 'workspace'] as const;
  const session = useRef<number | undefined>(undefined);

  function check(value: Workspace) {
    if (value.patient.id !== patientId || value.study.id !== studyId || value.study.patient_id !== patientId || value.session.doctor_id !== actor || value.session.patient_id !== patientId || value.session.study_id !== studyId || (session.current !== undefined && value.session.id !== session.current)) throw unavailable();
    return value;
  }

  const query = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) => {
      if (!Number.isSafeInteger(patientId) || !Number.isSafeInteger(studyId) || patientId < 1 || studyId < 1) throw unavailable();
      if (session.current !== undefined) return check(await api.workspace(session.current, signal));
      const study = await api.study(studyId, signal);
      if (study.id !== studyId || study.patient_id !== patientId) throw unavailable();
      if (signal.aborted) throw signal.reason;
      const data = check(await api.openStudy(studyId));
      if (signal.aborted) throw signal.reason;
      session.current = data.session.id;
      void queryClient.invalidateQueries({ queryKey: ['actor', actor, 'patient', patientId, 'studies'] });
      return data;
    },
  });
  useEffect(() => { if (query.error instanceof ApiError && query.error.status === 401) expire(); }, [expire, query.error]);
  return query;
}

export function useReadingActions(data: Workspace, reload: () => Promise<Workspace>, onAnalyze: () => void, onDenied: () => void, onCommit: (from: number, to: number) => void) {
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState<Workspace | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [job, setJob] = useState<AIRequest | null>(data.latest_ai_request);
  const [pollError, setPollError] = useState(false);
  const [visible, setVisible] = useState(true);
  const [checking, setChecking] = useState(false);
  const active = useRef(false);
  const inFlight = useRef(false);
  const pollActive = useRef(false);
  const latest = useRef(data);
  const recovery = useRef<(() => Promise<unknown>) | null>(null);
  const completionId = useRef<number | null>(null);
  const queryClient = useQueryClient();
  const { expire, refresh } = useAuth();

  useEffect(() => { latest.current = data; }, [data]);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  const track = useCallback((next: AIRequest | null) => {
    setJob(current => !next || (current && (next.request_id < current.request_id || (next.request_id === current.request_id && isTerminal(current.status) && !isTerminal(next.status)))) ? current : next);
  }, []);
  useEffect(() => { track(data.latest_ai_request); }, [data.latest_ai_request, track]);

  const applyError = useCallback((failure: unknown) => {
    if (!active.current) return;
    if (failure instanceof ApiError && failure.status === 401) { expire(); return; }
    if (failure instanceof ApiError && failure.status === 0) setOffline(true);
    setError(errorText(failure));
  }, [expire]);

  const sync = useCallback(async () => {
    try {
      const result = await reload();
      if (active.current) {
        setOffline(false);
        track(result.latest_ai_request);
      }
      return result;
    } catch (failure) {
      if (failure instanceof ApiError && [403, 404].includes(failure.status)) onDenied();
      applyError(failure);
      throw failure;
    }
  }, [reload, track, applyError, onDenied]);

  useEffect(() => {
    const visibility = () => {
      const shown = document.visibilityState === 'visible';
      setVisible(shown);
      if (shown) void refresh().then(user => user && sync()).catch(() => {});
    };
    const lost = () => setOffline(true);
    const reconnect = () => { if (!recovery.current) void sync().catch(() => {}); };
    setVisible(document.visibilityState === 'visible');
    setOffline(!navigator.onLine);
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('focus', visibility);
    window.addEventListener('offline', lost);
    window.addEventListener('online', reconnect);
    return () => {
      document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('focus', visibility);
      window.removeEventListener('offline', lost);
      window.removeEventListener('online', reconnect);
    };
  }, [refresh, sync]);

  const poll = useCallback(async (requestId: number, signal?: AbortSignal) => {
    if (pollActive.current) return;
    pollActive.current = true;
    try {
      const next = await api.request(latest.current.session.id, requestId, signal);
      if (!active.current || signal?.aborted) return;
      setPollError(false);
      track(next);
      if (isTerminal(next.status) && completionId.current !== requestId) {
        completionId.current = requestId;
        await sync();
        if (active.current && next.status === 'succeeded' && next.operation === 'analyze') onAnalyze();
      }
    } catch (failure) {
      if (!active.current || signal?.aborted) return;
      if (failure instanceof ApiError && [403, 404].includes(failure.status)) { void sync().catch(() => {}); return; }
      if (failure instanceof ApiError && failure.status === 401) { expire(); return; }
      setPollError(true);
    } finally { pollActive.current = false; }
  }, [expire, onAnalyze, sync, track]);

  const liveId = job && !isTerminal(job.status) ? job.request_id : data.live_request_id;
  useEffect(() => {
    if (liveId === null || !visible) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => { void poll(liveId, controller.signal); }, 1000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [liveId, visible, poll]);

  const transact = useCallback(async <T,>(mutation: Mutation<T>, complete?: (value: T, result: Workspace) => void, expectedRevision?: number): Promise<Workspace> => {
    if (inFlight.current || recovery.current || conflict || !navigator.onLine) throw new ApiError(409, 'BUSY', 'Finish the current action before continuing.');
    const options = { revision: expectedRevision ?? latest.current.session.revision, key: actionKey() };
    let receipt: { value: T } | undefined;
    const execute = async () => {
      if (!receipt) receipt = { value: await mutation(options) };
      const result = await sync();
      if (!active.current) return result;
      recovery.current = null;
      setUncertain(false);
      void queryClient.invalidateQueries({ queryKey: ['actor', result.session.doctor_id, 'patient', result.patient.id, 'studies'] });
      void refresh().catch(() => {});
      if (typeof receipt.value === 'object' && receipt.value && 'session_revision' in receipt.value && typeof receipt.value.session_revision === 'number') onCommit(options.revision, receipt.value.session_revision);
      complete?.(receipt.value, result);
      return result;
    };
    async function run() {
      inFlight.current = true;
      setBusy(true);
      setError('');
      try { return await execute(); }
      catch (failure) {
        if (!active.current) throw failure;
        if (failure instanceof ApiError && failure.status === 0) {
          recovery.current = run;
          setUncertain(true);
          setOffline(true);
          setError('The connection was interrupted. Check whether the action was saved.');
        } else {
          recovery.current = null;
          setUncertain(false);
          if (failure instanceof ApiError && failure.status === 412) {
            setConflict(latest.current);
            setError('This reading changed in another tab. Reload before saving.');
            await sync().catch(() => {});
          } else {
            applyError(failure);
            if (failure instanceof ApiError && [403, 404, 409, 428].includes(failure.status)) void sync().catch(() => {});
          }
        }
        throw failure;
      } finally {
        inFlight.current = false;
        if (active.current) setBusy(false);
      }
    }
    return run();
  }, [applyError, conflict, onCommit, queryClient, refresh, sync]);

  async function reconnect() {
    if (inFlight.current || checking) return;
    setChecking(true);
    try {
      if (recovery.current) await recovery.current();
      else { await sync(); setError(''); }
    } catch { /* The action and sync handlers retain the visible recovery state. */ }
    finally { if (active.current) setChecking(false); }
  }

  function submit(command: AICommand) {
    return transact(options => api.submit(data.session.id, command, options), track);
  }

  function retry() {
    if (!job?.submitted_input || !job.error?.retryable) return Promise.resolve();
    return submit({ ...job.submitted_input, retry_of: job.request_id });
  }

  function resolveConflict() { setConflict(null); setError(''); }

  return {
    busy, offline, uncertain, error, conflict, job, live: liveId !== null,
    pollError, checking, reconnect, resolveConflict, transact, submit, retry,
    checkStatus: () => liveId === null ? Promise.resolve() : poll(liveId),
  };
}

export type ReadingActions = ReturnType<typeof useReadingActions>;
