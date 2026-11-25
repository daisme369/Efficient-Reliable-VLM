"use client";

// Admin panels that need the real Thorax backend: overview, users of every role, AI monitoring
// with the pause switch, labeling oversight, and the admin activity log.
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Pause, Play, RefreshCw, UserPlus } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Badge, Confirm, Notice, formatTime } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { AdminUser, AuthUser, LabelTaskStatus, UserRole } from "@/lib/contracts";
import { PatientStudies } from "@/features/worklist/Worklist";
import "./admin.css"; // also used by the technician's intake page, which does not load Admin.tsx

const roleLabel: Record<UserRole, string> = { doctor: "Doctor", technician: "X-ray technician", annotator: "Annotator", admin: "Administrator" };
export const overviewKey = (actor: number) => ["actor", actor, "admin", "overview"] as const;
/** Labeling (annotator role) is off unless the backend sets THORAX_LABELING=1. */
export function useLabelingEnabled(actor: number, enabled = true) {
  const query = useQuery({ queryKey: overviewKey(actor), queryFn: ({ signal }) => api.adminOverview(signal), enabled });
  return query.data?.labeling_enabled ?? false;
}
const taskLabel: Record<LabelTaskStatus, string> = { open: "Unlabeled", in_progress: "In progress", submitted: "Awaiting review", returned: "Returned", approved: "Approved" };
const serviceLabel = { up: "Running", down: "Not responding", not_configured: "Not configured" } as const;
const actionLabel: Record<string, string> = {
  user_created: "Created account", user_disabled: "Disabled account", user_enabled: "Enabled account", password_reset: "Reset password",
  forced_sign_out: "Signed out user", access_granted: "Granted patient access", access_revoked: "Revoked patient access",
  ai_paused: "Paused AI", ai_resumed: "Resumed AI", labels_exported: "Exported approved labels",
  patient_created: "Created patient", study_uploaded: "Uploaded study", image_uploaded: "Uploaded image",
  study_edited: "Edited study", image_deleted: "Removed image", study_deleted: "Deleted study",
};
const ms = (value: number | null) => value === null ? "—" : value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`;
const problem = (failure: unknown) => failure instanceof ApiError ? failure.message : "Could not reach the server. Try again.";

function Panel({ title, description, action, children }: { title: string; description?: string; action?: ReactNode; children: ReactNode }) {
  return <section className="admin-panel" aria-label={title}>
    <header className="admin-panel-heading admin-console-heading"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</header>
    {children}
  </section>;
}

function Loading({ query, label, children }: { query: { isPending: boolean; isError: boolean; refetch: () => unknown }; label: string; children: () => ReactNode }) {
  if (query.isPending) return <p className="admin-empty">Loading {label}…</p>;
  if (query.isError) return <div className="admin-empty"><p>Could not load {label}.</p><button className="button" onClick={() => void query.refetch()}>Retry</button></div>;
  return <>{children()}</>;
}

function Refresh({ onClick, busy }: { onClick: () => void; busy: boolean }) {
  return <button type="button" className="button ghost" onClick={onClick} disabled={busy}><RefreshCw size={16} aria-hidden="true" />Refresh</button>;
}

function Stats({ items }: { items: { label: string; value: ReactNode; note?: ReactNode }[] }) {
  return <dl className="admin-stats">{items.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd>{item.note && <small>{item.note}</small>}</div>)}</dl>;
}

export function Overview({ actor, active }: { actor: number; active: boolean }) {
  const query = useQuery({ queryKey: overviewKey(actor), queryFn: ({ signal }) => api.adminOverview(signal), enabled: active, refetchInterval: active ? 30_000 : false });
  return <Panel title="System overview" description="Refreshes every 30 seconds." action={<Refresh onClick={() => void query.refetch()} busy={query.isFetching} />}>
    <Loading query={query} label="the overview">{() => {
      const data = query.data!;
      return <div className="admin-overview">
        {data.ai.paused && <Notice tone="pending">AI is paused{data.ai.changed_by ? ` by ${data.ai.changed_by}` : ""} since {formatTime(data.ai.changed_at)}. Doctors can still read, edit and sign by hand.</Notice>}
        <h3>Accounts</h3>
        <Stats items={[
          ...(Object.keys(roleLabel) as UserRole[]).map(role => ({ label: `${roleLabel[role]}s`, value: data.users[role].active, note: `${data.users[role].total - data.users[role].active} disabled` })),
          { label: "Signed in now", value: data.signed_in },
        ]} />
        <h3>AI</h3>
        <Stats items={[
          { label: "AI requests", value: data.ai.paused ? <Badge tone="pending">Paused</Badge> : <Badge tone="approved">Accepting</Badge> },
          { label: "Model service", value: serviceLabel[data.ai.model_service.status] },
          { label: "Requests logged", value: data.ai.requests },
          { label: "Failed or timed out", value: data.ai.failed },
        ]} />
        {data.over_reads && (() => {
          const { agree, minor, major } = data.over_reads;
          const graded = agree + minor + major;
          const rate = (n: number) => graded ? `${Math.round(n / graded * 100)}% of over-reads` : "No over-reads yet";
          return <><h3>Resident preliminary reports</h3>
            <Stats items={[
              { label: "Over-read by a doctor", value: graded },
              { label: "Minor change", value: minor, note: rate(minor) },
              { label: "Major change", value: major, note: rate(major) },
            ]} /></>;
        })()}
        <h3>Data and labeling</h3>
        <Stats items={[
          { label: "Patients", value: data.catalog.patients }, { label: "Studies", value: data.catalog.studies }, { label: "Images", value: data.catalog.images },
          ...(data.labeling ? [{ label: "Awaiting label review", value: data.labeling.submitted }, { label: "Approved labels", value: data.labeling.approved }] : []),
        ]} />
        <p className="admin-footnote">Backend running since {formatTime(data.backend.started_at)}.</p>
      </div>;
    }}</Loading>
  </Panel>;
}

export function Patients({ actor, active, intake = false }: { actor: number; active: boolean; intake?: boolean }) {
  const queryClient = useQueryClient();
  const query = useInfiniteQuery({
    queryKey: ["actor", actor, "admin", "patient-catalog"], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) => api.adminPatients("", pageParam, signal),
    getNextPageParam: page => page.next_cursor ?? undefined, enabled: active,
  });
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "info" | "error"; text: string } | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const patients = query.data?.pages.flatMap(page => page.items) ?? [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["actor", actor, "admin"] });

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const year = String(data.get("birth_year") ?? "").trim();
    const body = { name: String(data.get("name") ?? "").trim(), birth_year: year ? Number(year) : null, sex: String(data.get("sex") ?? "") || null };
    setBusy(true); setFeedback(null);
    try {
      const created = await api.adminCreatePatient(body);
      setFeedback({ tone: "info", text: intake ? `Created patient ${created.code}. Add the study, then ask an administrator to assign the reading doctor.` : `Created patient ${created.code}. Grant a doctor access under Assignments.` });
      form.reset();
      await refresh();
    } catch (failure) { setFeedback({ tone: "error", text: problem(failure) }); }
    finally { setBusy(false); }
  }

  return <div className="admin-stack">
    <Panel title="Patients" description="Patient codes in the catalogue. Manage studies to upload chest X-rays (synthetic or permitted images only), or to edit and delete uploads before a reading starts.">
      {feedback && <div className="admin-console-feedback"><Notice tone={feedback.tone}>{feedback.text}</Notice></div>}
      <Loading query={query} label="patients">{() => <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th scope="col">Patient code</th><th scope="col">Doctors with access</th><th scope="col">Studies</th></tr></thead>
        {patients.map(patient => <tbody key={patient.id}>
          <tr>
            <th scope="row" className="mono">{patient.code}</th>
            <td>{patient.assigned_doctor_ids.length}</td>
            <td><button className="button ghost" aria-expanded={open === patient.id} aria-controls={`patient-studies-${patient.id}`}
              onClick={() => { setFeedback(null); setOpen(open === patient.id ? null : patient.id); }}>{open === patient.id ? "Hide studies" : "Manage studies"}</button></td>
          </tr>
          {open === patient.id && <tr><td colSpan={3} className="study-cell"><PatientStudies actor={actor} patientId={patient.id} patientLabel={patient.code} canUpload admin /></td></tr>}
        </tbody>)}
      </table>
        {query.hasNextPage && <div className="admin-panel-footer"><button className="button" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? "Loading…" : "Load more patients"}</button></div>}
      </div>}</Loading>
    </Panel>
    <form className="admin-panel admin-form admin-create-user" onSubmit={event => void create(event)} aria-labelledby="admin-create-patient">
      <header className="admin-panel-heading"><h2 id="admin-create-patient"><UserPlus size={20} aria-hidden="true" /> Create patient</h2><p>A patient code is assigned automatically.</p></header>
      <fieldset disabled={busy}>
        <label htmlFor="admin-patient-name">Name</label><input id="admin-patient-name" name="name" required maxLength={120} autoComplete="off" />
        <label htmlFor="admin-patient-year">Birth year (optional)</label><input id="admin-patient-year" name="birth_year" type="number" min={1900} max={new Date().getFullYear()} />
        <label htmlFor="admin-patient-sex">Sex (optional)</label>
        <select id="admin-patient-sex" name="sex" defaultValue=""><option value="">Not recorded</option><option>Female</option><option>Male</option><option>Other</option></select>
        <button type="submit" className="button primary">{busy ? "Saving…" : "Create patient"}</button>
      </fieldset>
    </form>
  </div>;
}

export function UsersAccess({ actor, active, me }: { actor: number; active: boolean; me: AuthUser }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["actor", actor, "admin", "users"], queryFn: ({ signal }) => api.adminUsers(signal), enabled: active });
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ tone: "info" | "error"; text: string } | null>(null);
  const [confirm, setConfirm] = useState<{ title: string; body: string; label: string; run: () => Promise<unknown>; done: string } | null>(null);
  const [resetFor, setResetFor] = useState<AdminUser | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [role, setRole] = useState<UserRole>("doctor");
  const labelingOn = useLabelingEnabled(actor, active);
  const roles = (Object.keys(roleLabel) as UserRole[]).filter(r => r !== "annotator" || labelingOn);
  const [filter, setFilter] = useState<UserRole | "all">("all");

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true); setFeedback(null);
    try { await action(); setFeedback({ tone: "info", text: done }); await queryClient.invalidateQueries({ queryKey: ["actor", actor, "admin"] }); return true; }
    catch (failure) { setFeedback({ tone: "error", text: problem(failure) }); return false; }
    finally { setBusy(false); }
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const body = { name: String(data.get("name") ?? "").trim(), email: String(data.get("email") ?? "").trim(), role, password: String(data.get("password") ?? "") };
    if (await run(() => api.adminCreateUser(body), `Created ${roleLabel[role].toLowerCase()} account ${body.email}.`)) form.reset();
  }
  const users = (query.data?.items ?? []).filter(user => filter === "all" || user.role === filter);

  return <div className="admin-stack">
    <Panel title="Accounts" description="Every doctor, X-ray technician and administrator. Disabling an account or resetting its password also signs it out."
      action={<label className="admin-inline-field">Show<select aria-label="Filter accounts by role" value={filter} onChange={event => setFilter(event.target.value as UserRole | "all")}><option value="all">All roles</option>{roles.map(r => <option key={r} value={r}>{roleLabel[r]}s</option>)}</select></label>}>
      {feedback && <div className="admin-console-feedback"><Notice tone={feedback.tone}>{feedback.text}</Notice></div>}
      <Loading query={query} label="accounts">{() => <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Last sign-in</th><th scope="col">Failed sign-ins (24 h)</th><th scope="col">Actions</th></tr></thead>
        <tbody>{users.map(user => {
          const self = user.id === me.id;
          return <tr key={user.id}>
            <th scope="row">{user.name}{self && " (you)"}<small className="admin-sub">{user.email}</small></th>
            <td>{roleLabel[user.role]}</td>
            <td><span className={`badge ${user.is_active ? "admin-active" : "admin-inactive"}`}>{user.is_active ? (user.signed_in ? "Signed in" : "Active") : "Disabled"}</span></td>
            <td>{user.last_sign_in_at ? formatTime(user.last_sign_in_at) : "Never"}</td>
            <td>{user.failed_sign_ins_24h ? <Badge tone={user.failed_sign_ins_24h >= 5 ? "error" : "pending"}>{user.failed_sign_ins_24h}</Badge> : "0"}</td>
            <td><div className="admin-row-actions">
              {user.signed_in && !self && <button className="button ghost" disabled={busy} onClick={() => setConfirm({ title: `Sign out ${user.name}?`, body: "Their current session ends now. They can sign in again.", label: "Sign out", run: () => api.adminSignOutUser(user.id), done: `Signed out ${user.name}.` })}>Sign out</button>}
              <button className="button ghost" disabled={busy} onClick={() => { setResetFor(user); setResetPassword(""); }}>Reset password</button>
              {!self && (user.is_active
                ? <button className="button ghost danger-text" disabled={busy} onClick={() => setConfirm({ title: `Disable ${user.name}?`, body: "They are signed out and cannot sign in until an administrator enables the account.", label: "Disable account", run: () => api.adminUpdateUser(user.id, { is_active: false }), done: `Disabled ${user.name}.` })}>Disable</button>
                : <button className="button ghost" disabled={busy} onClick={() => void run(() => api.adminUpdateUser(user.id, { is_active: true }), `Enabled ${user.name}.`)}>Enable</button>)}
            </div></td>
          </tr>;
        })}</tbody>
      </table></div>}</Loading>
    </Panel>
    <form className="admin-panel admin-form admin-create-user" onSubmit={event => void create(event)} aria-labelledby="admin-create-user">
      <header className="admin-panel-heading"><h2 id="admin-create-user"><UserPlus size={20} aria-hidden="true" /> Create account</h2><p>Share the initial password privately. Users can change it after signing in.</p></header>
      <fieldset disabled={busy}>
        <label htmlFor="admin-user-role">Role</label>
        <select id="admin-user-role" value={role} onChange={event => setRole(event.target.value as UserRole)}>{roles.map(r => <option key={r} value={r}>{roleLabel[r]}</option>)}</select>
        <label htmlFor="admin-user-name">Display name</label><input id="admin-user-name" name="name" required maxLength={100} autoComplete="off" />
        <label htmlFor="admin-user-email">Email</label><input id="admin-user-email" name="email" type="email" required maxLength={254} autoComplete="off" />
        <label htmlFor="admin-user-password">Initial password</label><input id="admin-user-password" name="password" type="password" required minLength={12} maxLength={72} autoComplete="new-password" />
        <p className="admin-field-help">Use 12–72 UTF-8 bytes.</p>
        <button type="submit" className="button primary">{busy ? "Saving…" : `Create ${roleLabel[role].toLowerCase()}`}</button>
      </fieldset>
    </form>
    {confirm && <Confirm title={confirm.title} action={confirm.label} danger pending={busy} onCancel={() => setConfirm(null)} onConfirm={() => { const c = confirm; setConfirm(null); void run(c.run, c.done); }}><p>{confirm.body}</p></Confirm>}
    {resetFor && <Confirm title={`Reset password for ${resetFor.name}`} action="Reset password" pending={busy} onCancel={() => setResetFor(null)}
      onConfirm={() => { const user = resetFor; setResetFor(null); void run(() => api.adminUpdateUser(user.id, { password: resetPassword }), `Reset the password for ${user.name}${user.id === me.id ? "" : " and signed them out"}.`); }}>
      <label className="field">New password<input type="password" autoComplete="new-password" minLength={12} maxLength={72} value={resetPassword} onChange={event => setResetPassword(event.target.value)} /></label>
      <p className="admin-field-help">Use 12–72 UTF-8 bytes.{resetFor.id !== me.id && " Their current session ends."}</p>
    </Confirm>}
  </div>;
}

export function AiMonitor({ actor, active }: { actor: number; active: boolean }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["actor", actor, "admin", "ai"], queryFn: ({ signal }) => api.adminAi(signal), enabled: active, refetchInterval: active ? 15_000 : false });
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function setPaused(paused: boolean) {
    setBusy(true); setError("");
    try { await api.adminSetAiPaused(paused); await queryClient.invalidateQueries({ queryKey: ["actor", actor, "admin"] }); }
    catch (failure) { setError(problem(failure)); }
    finally { setBusy(false); setConfirm(false); }
  }
  return <Loading query={query} label="AI activity">{() => {
    const { pause, model_service: service, stats, items } = query.data!;
    return <div className="admin-stack">
      <Panel title="AI control" action={<Refresh onClick={() => void query.refetch()} busy={query.isFetching} />}>
        <div className="admin-ai-control">
          <div>
            <p className="admin-ai-state">{pause.paused ? <Badge tone="pending">AI paused</Badge> : <Badge tone="approved">AI accepting requests</Badge>}
              {pause.changed_at && <span className="admin-sub"> Last changed {formatTime(pause.changed_at)}{pause.changed_by ? ` by ${pause.changed_by}` : ""}.</span>}</p>
            <p className="admin-sub">{pause.paused ? "Doctors can still read, edit and sign by hand. New Analyze, Ask AI and report-draft requests are refused." : "Pause stops new AI requests for every doctor, for example while the model is being replaced or misbehaves."}</p>
          </div>
          {pause.paused
            ? <button className="button primary" disabled={busy} onClick={() => void setPaused(false)}><Play size={16} aria-hidden="true" />Resume AI</button>
            : <button className="button danger" disabled={busy} onClick={() => setConfirm(true)}><Pause size={16} aria-hidden="true" />Pause AI</button>}
        </div>
        {error && <div className="admin-console-feedback"><Notice tone="error">{error}</Notice></div>}
        <Stats items={[
          { label: "Model service", value: serviceLabel[service.status], note: service.url ?? "Set THORAX_VLM_URL on the backend" },
          { label: "Requests logged", value: stats.total },
          { label: "Failed / timed out", value: `${stats.failed} / ${stats.timed_out}`, note: `of the last ${stats.shown}` },
          { label: "Average response", value: ms(stats.average_ms), note: "successful requests" },
          { label: "Slowest response", value: ms(stats.slowest_ms) },
        ]} />
      </Panel>
      <Panel title="Recent AI requests" description="The latest 100 requests, newest first. Questions and answers are not shown here.">
        {items.length ? <div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th scope="col">Time</th><th scope="col">Operation</th><th scope="col">User</th><th scope="col">Status</th><th scope="col">Duration</th><th scope="col">Model</th></tr></thead>
          <tbody>{items.map(row => <tr key={row.id}>
            <th scope="row">{formatTime(row.created_at)}</th>
            <td>{{ analyze: "Analyze image", ask: "Ask AI", draft_report: "Draft report" }[row.operation] ?? row.operation}</td>
            <td>{row.user_name}</td>
            <td><Badge tone={row.status === "succeeded" ? "approved" : row.status === "failed" || row.status === "timed_out" ? "error" : "neutral"}>{row.status.replace("_", " ")}</Badge>{row.error_code && <small className="admin-sub mono">{row.error_code}</small>}</td>
            <td className="mono">{ms(row.latency_ms)}</td>
            <td className="mono">{row.model_version ?? "—"}</td>
          </tr>)}</tbody>
        </table></div> : <p className="admin-empty">No AI requests yet. The backend logs them once reading sessions and the model service are connected.</p>}
      </Panel>
      {confirm && <Confirm title="Pause AI for everyone?" action="Pause AI" danger pending={busy} onCancel={() => setConfirm(false)} onConfirm={() => void setPaused(true)}>
        <p>New AI requests are refused until an administrator resumes AI. Requests already running finish. This is recorded in the activity log.</p>
      </Confirm>}
    </div>;
  }}</Loading>;
}

export function LabelingOversight({ actor, active }: { actor: number; active: boolean }) {
  const query = useQuery({ queryKey: ["actor", actor, "admin", "labeling"], queryFn: ({ signal }) => api.adminLabeling(signal), enabled: active });
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: "info" | "error"; text: string } | null>(null);
  async function exportLabels() {
    setBusy(true); setResult(null);
    try {
      const { manifest, labels_jsonl } = await api.adminExportLabels();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([labels_jsonl], { type: "application/x-ndjson" }));
      link.download = `thorax-labels-${String(manifest.created_at).replace(/[:]/g, "")}.jsonl`;
      link.click();
      URL.revokeObjectURL(link.href);
      setResult({ tone: "info", text: `Exported ${manifest.rows} approved images. SHA-256 ${String(manifest.sha256).slice(0, 12)}…` });
      await queryClient.invalidateQueries({ queryKey: ["actor", actor, "admin", "events"] });
    } catch (failure) { setResult({ tone: "error", text: problem(failure) }); }
    finally { setBusy(false); }
  }
  return <Loading query={query} label="labeling progress">{() => {
    const data = query.data!;
    const total = Object.values(data.counts).reduce((sum, value) => sum + value, 0);
    return <div className="admin-stack">
      <Panel title="Labeling progress" description={`${total} images in the labeling queue.`} action={<Refresh onClick={() => void query.refetch()} busy={query.isFetching} />}>
        {!data.vocabulary.verified && <div className="admin-console-feedback"><Notice tone="pending">The label list ({data.vocabulary.version}) is not fully verified. Check it before using these labels for research. {data.vocabulary.status}</Notice></div>}
        <Stats items={[...(Object.keys(taskLabel) as LabelTaskStatus[]).map(status => ({ label: taskLabel[status], value: data.counts[status] })),
          { label: "Approved with reviewer fixes", value: data.approved_with_reviewer_changes }]} />
        <div className="admin-progress" role="img" aria-label={`${data.counts.approved} of ${total} images approved`}>
          {(Object.keys(taskLabel) as LabelTaskStatus[]).map(status => data.counts[status] ? <span key={status} data-status={status} style={{ flexGrow: data.counts[status] }} title={`${taskLabel[status]}: ${data.counts[status]}`} /> : null)}
        </div>
      </Panel>
      <Panel title="Annotators" description="Returned counts how often a reviewer sent the person’s work back. Fixes counts boxes the person changed while reviewing.">
        {data.annotators.length ? <div className="admin-table-wrap"><table className="admin-table">
          <thead><tr><th scope="col">Annotator</th><th scope="col">In progress</th><th scope="col">Labeled</th><th scope="col">Approved</th><th scope="col">Returned</th><th scope="col">Reviews done</th><th scope="col">Fixes while reviewing</th></tr></thead>
          <tbody>{data.annotators.map(person => <tr key={person.id}>
            <th scope="row">{person.name}{!person.is_active && <small className="admin-sub">Disabled</small>}</th>
            <td>{person.in_progress}</td><td>{person.labeled}</td><td>{person.approved}</td><td>{person.times_returned}</td><td>{person.reviews}</td><td>{person.review_edits}</td>
          </tr>)}</tbody>
        </table></div> : <p className="admin-empty">No annotator accounts yet. Create one under Users &amp; access.</p>}
      </Panel>
      <Panel title="Export approved labels" description="Downloads approved labels as JSON Lines in the repository’s region format. Training on them needs an approved scope-change record.">
        <div className="admin-console-body">
          <button className="button primary" disabled={busy || !data.counts.approved} onClick={() => void exportLabels()}><Download size={16} aria-hidden="true" />{busy ? "Exporting…" : `Export ${data.counts.approved} approved images`}</button>
          {result && <Notice tone={result.tone}>{result.text}</Notice>}
        </div>
      </Panel>
    </div>;
  }}</Loading>;
}

const eventGroups: Record<string, { label: string; actions: string[] }> = {
  accounts: { label: "Accounts and access", actions: ["user_created", "user_disabled", "user_enabled", "password_reset", "forced_sign_out", "access_granted", "access_revoked"] },
  data: { label: "Patients and uploads", actions: ["patient_created", "study_uploaded", "image_uploaded", "study_edited", "image_deleted", "study_deleted"] },
  ai: { label: "AI and labeling", actions: ["ai_paused", "ai_resumed", "labels_exported"] },
};

export function ActivityLog({ actor, active }: { actor: number; active: boolean }) {
  const query = useQuery({ queryKey: ["actor", actor, "admin", "events"], queryFn: ({ signal }) => api.adminEvents(signal), enabled: active });
  const [group, setGroup] = useState("all");
  const items = (query.data?.items ?? []).filter(event => group === "all" || eventGroups[group].actions.includes(event.action));
  return <Panel title="Activity log" description="The latest 100 administrative actions and uploads, newest first."
    action={<div className="admin-row-actions"><label className="admin-inline-field">Show<select aria-label="Filter activity" value={group} onChange={event => setGroup(event.target.value)}>
      <option value="all">All actions</option>{Object.entries(eventGroups).map(([id, g]) => <option key={id} value={id}>{g.label}</option>)}</select></label>
      <Refresh onClick={() => void query.refetch()} busy={query.isFetching} /></div>}>
    <Loading query={query} label="the activity log">{() => items.length ? <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th scope="col">Time</th><th scope="col">By</th><th scope="col">Action</th><th scope="col">Details</th></tr></thead>
      <tbody>{items.map(event => <tr key={event.id}><th scope="row">{formatTime(event.created_at)}</th><td>{event.actor_name}<small className="admin-sub">{roleLabel[event.actor_role]}</small></td><td>{actionLabel[event.action] ?? event.action}</td><td>{event.detail}</td></tr>)}</tbody>
    </table></div> : <p className="admin-empty">{group === "all" ? "No activity yet." : "No activity of this kind in the latest 100 entries."}</p>}</Loading>
  </Panel>;
}
