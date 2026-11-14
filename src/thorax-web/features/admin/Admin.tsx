"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Database, KeyRound, UserPlus, Users } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { useAuth } from "@/components/shell";
import { Confirm } from "@/components/ui";
import { actionKey, api, ApiError } from "@/lib/api";
import type { AdminDoctor, AdminPatient, AuthUser } from "@/lib/contracts";
import "./admin.css";

const tabs = [
  { id: "doctors", label: "Doctors", icon: Users },
  { id: "assignments", label: "Assignments", icon: KeyRound },
  { id: "fixtures", label: "Mock fixtures", icon: Database },
] as const;
const fixtureKeys = ["thorax-demo-v1"];
type Tab = (typeof tabs)[number]["id"];

function useActivity() {
  const { refresh } = useAuth();
  return useCallback(() => { void refresh(true).catch(() => {}); }, [refresh]);
}

type AdminWork = {
  run: () => Promise<unknown>;
  message: string;
  onSuccess?: () => void;
};
type Feedback = { tone: "error" | "success"; text: string; field?: string };

function useAdminAction(refresh: () => Promise<unknown>) {
  const inFlight = useRef(false);
  const [pending, setPending] = useState(false);
  const [retry, setRetry] = useState<AdminWork | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  async function submit(work: AdminWork) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(true);
    setFeedback(null);
    try {
      await work.run();
    } catch (error) {
      if (error instanceof TypeError || (error instanceof ApiError && error.status === 0)) {
        setRetry(work);
        setFeedback({ tone: "error", text: "The connection was interrupted. Check whether the action was saved." });
      } else {
        setRetry(null);
        const code = error instanceof ApiError ? error.code : "";
        if (code === "EMAIL_IN_USE") {
          setFeedback({ tone: "error", text: "A doctor already uses this email address.", field: "email" });
        } else if (error instanceof ApiError && error.status === 404) {
          setFeedback({ tone: "error", text: "This selection is unavailable. Refresh the list and choose again." });
        } else if (code === "FIXTURE_CONFLICT") {
          setFeedback({ tone: "error", text: "The fixture conflicts with a previously imported bundle." });
        } else {
          setFeedback({ tone: "error", text: "The action could not be saved. Try again." });
        }
      }
      inFlight.current = false;
      setPending(false);
      return;
    }

    setRetry(null);
    setFeedback({ tone: "success", text: work.message });
    work.onSuccess?.();
    try {
      await refresh();
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return { pending, retry, feedback, submit, setFeedback };
}

function ActionFeedback({ action, showRetry = true }: { action: ReturnType<typeof useAdminAction>; showRetry?: boolean }) {
  if (!action.feedback) return null;
  return (
    <div
      className={`notice admin-feedback ${action.feedback.tone === "error" ? "error" : "admin-success"}`}
      role={action.feedback.tone === "error" ? "alert" : "status"}
    >
      {action.feedback.tone === "success" && <Check size={17} aria-hidden="true" />}
      <span>{action.feedback.text}</span>
      {showRetry && action.retry && (
        <button type="button" className="button" disabled={action.pending} onClick={() => {
          if (action.retry) void action.submit(action.retry);
        }}>
          Check outcome
        </button>
      )}
    </div>
  );
}

function AdminConfirm({ title, children, confirmLabel, action, onConfirm, onClose }: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  action: ReturnType<typeof useAdminAction>;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const retry = action.retry;
  return (
    <Confirm title={title} action={retry ? "Check outcome" : confirmLabel} danger={!retry} pending={action.pending}
      onCancel={onClose} onConfirm={retry ? () => void action.submit(retry) : onConfirm}>
      <p className="admin-confirm-copy">{children}</p>
      <ActionFeedback action={action} showRetry={false} />
    </Confirm>
  );
}

function useDoctors(actor: number, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: ["actor", actor, "admin", "doctors"],
    queryFn: ({ pageParam, signal }) => api.adminDoctors(pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled,
  });
}

function ListState({ title, loading, failed, retry, children }: {
  title: string;
  loading: boolean;
  failed: boolean;
  retry: () => void;
  children: ReactNode;
}) {
  const touch = useActivity();
  if (loading) return (
    <div className="admin-loading" role="status" aria-label={`Loading ${title}`}>
      <span /><span /><span />
    </div>
  );
  if (failed) return (
    <div className="admin-list-error" role="alert">
      <p>Could not load {title}.</p>
      <button type="button" className="button" onClick={() => { touch(); retry(); }}>Retry</button>
    </div>
  );
  return children;
}

function LoadMore({ label, pending, failed, load }: {
  label: string;
  pending: boolean;
  failed?: boolean;
  load: () => void;
}) {
  const touch = useActivity();
  return (
    <button type="button" className="button" disabled={pending} onClick={() => { touch(); load(); }}>
      {pending ? "Loading…" : failed ? `Retry loading ${label}` : `Load more ${label}`}
    </button>
  );
}

function Doctors({ actor, active, refresh }: { actor: number; active: boolean; refresh: () => Promise<unknown> }) {
  const query = useDoctors(actor, active);
  const doctors = query.data?.pages.flatMap((page) => page.items) ?? [];
  const form = useRef<HTMLFormElement>(null);
  const email = useRef<HTMLInputElement>(null);
  const password = useRef<HTMLInputElement>(null);
  const create = useAdminAction(refresh);
  const change = useAdminAction(refresh);
  const [disableDoctor, setDisableDoctor] = useState<AdminDoctor | null>(null);

  const invalidEmail = create.feedback?.field === "email";
  const invalidPassword = create.feedback?.field === "password";
  useEffect(() => {
    if (invalidEmail) email.current?.focus();
    if (invalidPassword) password.current?.focus();
  }, [invalidEmail, invalidPassword]);

  function createDoctor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (create.pending || create.retry) return;
    const data = new FormData(event.currentTarget);
    const input = {
      name: String(data.get("name") ?? "").trim(),
      email: String(data.get("email") ?? "").trim(),
      password: String(data.get("password") ?? ""),
    };
    if (!input.name) {
      create.setFeedback({ tone: "error", text: "Enter the doctor’s display name.", field: "name" });
      event.currentTarget.querySelector<HTMLInputElement>("#admin-doctor-name")?.focus();
      return;
    }
    const length = new TextEncoder().encode(input.password).length;
    if (length < 12 || length > 72) {
      create.setFeedback({ tone: "error", text: "Use an initial password between 12 and 72 UTF-8 bytes.", field: "password" });
      return;
    }
    const key = actionKey();
    void create.submit({
      run: () => api.createDoctor(input, key),
      message: "Doctor account created.",
      onSuccess: () => form.current?.reset(),
    });
  }

  function setActive(doctor: AdminDoctor, isActive: boolean) {
    const key = actionKey();
    void change.submit({
      run: () => api.setDoctorActive(doctor.id, isActive, key),
      message: isActive ? "Doctor account reactivated." : "Doctor account disabled.",
      onSuccess: () => setDisableDoctor(null),
    });
  }

  return (
    <div className="admin-grid">
      <section className="admin-panel" aria-labelledby="doctor-accounts-heading">
        <header className="admin-panel-heading">
          <h2 id="doctor-accounts-heading">Doctor accounts</h2>
          <p>Manage access for doctors in this workspace.</p>
        </header>
        <ListState title="doctor accounts" loading={query.isPending} failed={query.isLoadingError} retry={() => void query.refetch()}>
          {doctors.length ? (
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead><tr><th scope="col">Display name</th><th scope="col">Email</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead>
                <tbody>{doctors.map((doctor) => (
                  <tr key={doctor.id}>
                    <th scope="row">{doctor.name}</th>
                    <td>{doctor.email}</td>
                    <td><span className={`badge ${doctor.is_active ? "admin-active" : "admin-inactive"}`}>{doctor.is_active ? "Active" : "Disabled"}</span></td>
                    <td><button type="button" className="button ghost" aria-label={`${doctor.is_active ? "Disable" : "Reactivate"} ${doctor.name}`}
                      disabled={change.pending || !!change.retry} onClick={() => {
                        change.setFeedback(null);
                        if (doctor.is_active) setDisableDoctor(doctor);
                        else setActive(doctor, true);
                      }}>
                      {doctor.is_active ? "Disable" : "Reactivate"}
                    </button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          ) : <p className="admin-empty">There are no doctor accounts yet. Create an account to grant study access.</p>}
          {query.hasNextPage && <div className="admin-panel-footer"><LoadMore label="doctors" pending={query.isFetchingNextPage} failed={query.isFetchNextPageError} load={() => void query.fetchNextPage()} /></div>}
        </ListState>
        {!disableDoctor && <ActionFeedback action={change} />}
      </section>
      <form ref={form} className="admin-panel admin-form" onSubmit={createDoctor} aria-labelledby="create-doctor-heading">
        <header className="admin-panel-heading">
          <h2 id="create-doctor-heading"><UserPlus size={20} aria-hidden="true" /> Create doctor</h2>
          <p>The account is created with the Doctor role.</p>
        </header>
        <fieldset disabled={create.pending || !!create.retry}>
          <label htmlFor="admin-doctor-name">Display name</label>
          <input id="admin-doctor-name" name="name" required maxLength={100} autoComplete="off" aria-invalid={create.feedback?.field === "name" || undefined} />
          <label htmlFor="admin-doctor-email">Email</label>
          <input ref={email} id="admin-doctor-email" name="email" type="email" required maxLength={254} autoComplete="off"
            aria-invalid={invalidEmail || undefined} aria-describedby={invalidEmail ? "admin-email-error" : undefined} />
          {invalidEmail && <span id="admin-email-error" className="admin-field-error">{create.feedback?.text}</span>}
          <label htmlFor="admin-doctor-password">Initial password</label>
          <input ref={password} id="admin-doctor-password" name="password" type="password" required autoComplete="new-password"
            aria-invalid={invalidPassword || undefined} aria-describedby="admin-password-help" />
          <p id="admin-password-help" className={invalidPassword ? "admin-field-error" : "admin-field-help"}>Use 12–72 UTF-8 bytes.</p>
          <button type="submit" className="button primary">{create.pending ? "Creating…" : "Create doctor"}</button>
        </fieldset>
        <ActionFeedback action={create} />
      </form>
      {disableDoctor && <AdminConfirm title="Disable doctor account?" confirmLabel="Disable account" action={change}
        onClose={() => setDisableDoctor(null)} onConfirm={() => setActive(disableDoctor, false)}>
        Disabling {disableDoctor.name} revokes their access and signs them out of active sessions.
      </AdminConfirm>}
    </div>
  );
}

function Assignments({ actor, active, refresh }: { actor: number; active: boolean; refresh: () => Promise<unknown> }) {
  const doctorQuery = useDoctors(actor, active);
  const patientQuery = useInfiniteQuery({
    queryKey: ["actor", actor, "admin", "patient-catalog"],
    queryFn: ({ pageParam, signal }) => api.adminPatients("", pageParam, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: active,
  });
  const doctors = doctorQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const patients = patientQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const patientById = new Map(patients.map((patient) => [patient.id, patient]));
  const pairs = doctors.flatMap((doctor) => doctor.assigned_patient_ids.flatMap((id) => {
    const patient = patientById.get(id);
    return patient?.assigned_doctor_ids.includes(doctor.id) ? [{ doctor, patient }] : [];
  }));
  const grant = useAdminAction(refresh);
  const revoke = useAdminAction(refresh);
  const [doctorId, setDoctorId] = useState("");
  const [patientId, setPatientId] = useState("");
  const [remove, setRemove] = useState<{ doctor: AdminDoctor; patient: AdminPatient } | null>(null);
  const selectedDoctor = doctors.find((doctor) => String(doctor.id) === doctorId && doctor.is_active);
  const selectedPatient = patients.find((patient) => String(patient.id) === patientId);
  const alreadyAssigned = !!(selectedDoctor && selectedPatient && selectedDoctor.assigned_patient_ids.includes(selectedPatient.id));
  const loading = doctorQuery.isPending || patientQuery.isPending;
  const failed = doctorQuery.isLoadingError || patientQuery.isLoadingError;

  function grantAccess(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedDoctor || !selectedPatient || alreadyAssigned || grant.pending || grant.retry || revoke.pending || revoke.retry) return;
    const key = actionKey();
    void grant.submit({
      run: () => api.assignPatient(selectedDoctor.id, selectedPatient.id, key),
      message: "Patient access granted.",
      onSuccess: () => setPatientId(""),
    });
  }

  function revokeAccess(doctor: AdminDoctor, patient: AdminPatient) {
    void revoke.submit({
      run: () => api.revokeAssignment(doctor.id, patient.id),
      message: "Patient access revoked.",
      onSuccess: () => setRemove(null),
    });
  }

  return (
    <div className="admin-grid">
      <section className="admin-panel" aria-labelledby="assignments-heading">
        <header className="admin-panel-heading">
          <h2 id="assignments-heading">Patient assignments</h2>
          <p>Review the patient codes each doctor can access.</p>
        </header>
        <ListState title="assignments" loading={loading} failed={failed} retry={() => {
          void doctorQuery.refetch();
          void patientQuery.refetch();
        }}>
          {pairs.length ? <div className="admin-table-wrap">
            <table className="admin-table">
              <thead><tr><th scope="col">Doctor</th><th scope="col">Patient code</th><th scope="col">Action</th></tr></thead>
              <tbody>{pairs.map(({ doctor, patient }) => (
                <tr key={`${doctor.id}-${patient.id}`}>
                  <th scope="row">{doctor.name}</th>
                  <td className="mono">{patient.code}</td>
                  <td><button type="button" className="button ghost" disabled={revoke.pending || !!revoke.retry || grant.pending || !!grant.retry}
                    aria-label={`Revoke ${doctor.name} access to ${patient.code}`} onClick={() => { revoke.setFeedback(null); setRemove({ doctor, patient }); }}>
                    Revoke access
                  </button></td>
                </tr>
              ))}</tbody>
            </table>
          </div> : <p className="admin-empty">{doctorQuery.hasNextPage || patientQuery.hasNextPage ? "No assignments in the loaded pages." : "No patient access has been granted yet."}</p>}
          {(doctorQuery.hasNextPage || patientQuery.hasNextPage) && <div className="admin-panel-footer">
            {doctorQuery.hasNextPage && <LoadMore label="doctors" pending={doctorQuery.isFetchingNextPage} failed={doctorQuery.isFetchNextPageError} load={() => void doctorQuery.fetchNextPage()} />}
            {patientQuery.hasNextPage && <LoadMore label="patient codes" pending={patientQuery.isFetchingNextPage} failed={patientQuery.isFetchNextPageError} load={() => void patientQuery.fetchNextPage()} />}
          </div>}
        </ListState>
        {!remove && <ActionFeedback action={revoke} />}
      </section>
      <form className="admin-panel admin-form" onSubmit={grantAccess} aria-labelledby="grant-access-heading">
        <header className="admin-panel-heading">
          <h2 id="grant-access-heading"><KeyRound size={20} aria-hidden="true" /> Grant access</h2>
          <p>Choose an active doctor and a patient code from the catalogue.</p>
        </header>
        <fieldset disabled={loading || failed || grant.pending || !!grant.retry}>
          <label htmlFor="admin-assignment-doctor">Doctor</label>
          <select id="admin-assignment-doctor" required value={doctorId} onChange={(event) => setDoctorId(event.target.value)}>
            <option value="">Select a doctor</option>
            {doctors.filter((doctor) => doctor.is_active).map((doctor) => <option key={doctor.id} value={doctor.id}>{doctor.name} ({doctor.email})</option>)}
          </select>
          <label htmlFor="admin-assignment-patient">Patient code</label>
          <select id="admin-assignment-patient" required value={patientId} onChange={(event) => setPatientId(event.target.value)}>
            <option value="">Select a patient code</option>
            {patients.map((patient) => <option key={patient.id} value={patient.id}>{patient.code}</option>)}
          </select>
          {alreadyAssigned && <p className="admin-field-help" role="status">This doctor already has access to the selected patient.</p>}
          {!loading && !failed && !doctors.some((doctor) => doctor.is_active) && <p className="admin-field-help">Create or reactivate a doctor before granting access.</p>}
          {!loading && !failed && !patients.length && <p className="admin-field-help">Import the mock fixture to populate the patient catalogue.</p>}
          <button type="submit" className="button primary" disabled={!selectedDoctor || !selectedPatient || alreadyAssigned || revoke.pending || !!revoke.retry}>
            {grant.pending ? "Granting…" : "Grant access"}
          </button>
        </fieldset>
        <ActionFeedback action={grant} />
      </form>
      {remove && <AdminConfirm title="Revoke patient access?" confirmLabel="Revoke access" action={revoke}
        onClose={() => setRemove(null)} onConfirm={() => revokeAccess(remove.doctor, remove.patient)}>
        {remove.doctor.name} will lose access to patient {remove.patient.code} and its studies.
      </AdminConfirm>}
    </div>
  );
}

function Fixtures({ actor, active, refresh }: { actor: number; active: boolean; refresh: () => Promise<unknown> }) {
  const [key, setKey] = useState(fixtureKeys[0]);
  const query = useQuery({
    queryKey: ["actor", actor, "admin", "fixtures", key],
    queryFn: ({ signal }) => api.fixtureStatus(key, signal),
    enabled: active,
  });
  const action = useAdminAction(refresh);
  const status = query.data;

  function importFixture(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!status || query.isError || action.pending || action.retry) return;
    const idempotencyKey = actionKey();
    void action.submit({
      run: () => api.importFixture(key, idempotencyKey),
      message: "Mock fixture import verified.",
    });
  }

  return (
    <div className="admin-grid">
      <section className="admin-panel" aria-labelledby="fixture-status-heading">
        <header className="admin-panel-heading">
          <h2 id="fixture-status-heading">Fixture status</h2>
          <p>Inspect the prepared synthetic bundle and its import status.</p>
        </header>
        <ListState title="fixture status" loading={query.isPending} failed={query.isError} retry={() => void query.refetch()}>
          {status && <dl className="admin-fixture-details">
            <div><dt>Fixture key</dt><dd className="mono">{status.fixture_key}</dd></div>
            <div><dt>Status</dt><dd><span className={`badge ${status.status === "loaded" ? "admin-active" : "admin-inactive"}`}>{status.status === "loaded" ? "Imported" : "Not imported"}</span></dd></div>
            <div><dt>Checksum</dt><dd className="mono">{status.checksum || "Unavailable"}</dd></div>
            <div><dt>Patients imported</dt><dd>{status.patient_count}</dd></div>
            <div><dt>Studies imported</dt><dd>{status.study_count}</dd></div>
            <div><dt>Images imported</dt><dd>{status.image_count}</dd></div>
          </dl>}
        </ListState>
      </section>
      <form className="admin-panel admin-form" onSubmit={importFixture} aria-labelledby="fixture-import-heading">
        <header className="admin-panel-heading">
          <h2 id="fixture-import-heading"><Database size={20} aria-hidden="true" /> Import mock fixture</h2>
          <p>Import a prepared academic demo bundle. Reimporting the same checksum preserves existing records.</p>
        </header>
        <fieldset disabled={action.pending || !!action.retry}>
          <label htmlFor="admin-fixture-key">Allowlisted fixture</label>
          <select id="admin-fixture-key" value={key} onChange={(event) => setKey(event.target.value)}>
            {fixtureKeys.map((fixtureKey) => <option key={fixtureKey}>{fixtureKey}</option>)}
          </select>
          <button type="submit" className="button primary" disabled={!status || query.isError}>{action.pending ? "Importing…" : "Import fixture"}</button>
        </fieldset>
        <ActionFeedback action={action} />
      </form>
    </div>
  );
}

export default function Admin({ user }: { user: AuthUser }) {
  const queryClient = useQueryClient();
  const { refresh: refreshSession } = useAuth();
  const touch = useActivity();
  const [selected, setSelected] = useState<Tab>("doctors");
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(touch, [touch]);

  function selectTab(tab: Tab) {
    setSelected(tab);
    touch();
  }

  function moveTab(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    switch (event.key) {
      case "ArrowLeft": next = (index + tabs.length - 1) % tabs.length; break;
      case "ArrowRight": next = (index + 1) % tabs.length; break;
      case "Home": next = 0; break;
      case "End": next = tabs.length - 1; break;
      default: return;
    }
    event.preventDefault();
    selectTab(tabs[next].id);
    tabButtons.current[next]?.focus();
  }

  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["actor", user.id, "admin"] }),
    refreshSession().catch(() => {}),
  ]);

  return (
    <main className="admin-workspace" id="main-content" tabIndex={-1}>
      <header className="admin-page-heading">
        <h1>Administration</h1>
        <p>Manage doctor accounts, patient assignments, and mock fixtures.</p>
      </header>
      <div className="admin-tabs" role="tablist" aria-label="Administration sections">
        {tabs.map(({ id, label, icon: Icon }, index) => (
          <button key={id} type="button" role="tab" id={`admin-tab-${id}`} aria-selected={selected === id}
            aria-controls={`admin-panel-${id}`} tabIndex={selected === id ? 0 : -1}
            ref={(node) => { tabButtons.current[index] = node; }} onClick={() => selectTab(id)} onKeyDown={(event) => moveTab(event, index)}>
            <Icon size={18} aria-hidden="true" />{label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="admin-panel-doctors" aria-labelledby="admin-tab-doctors" hidden={selected !== "doctors"} tabIndex={0}>
        <Doctors actor={user.id} active={selected === "doctors"} refresh={refresh} />
      </div>
      <div role="tabpanel" id="admin-panel-assignments" aria-labelledby="admin-tab-assignments" hidden={selected !== "assignments"} tabIndex={0}>
        <Assignments actor={user.id} active={selected === "assignments"} refresh={refresh} />
      </div>
      <div role="tabpanel" id="admin-panel-fixtures" aria-labelledby="admin-tab-fixtures" hidden={selected !== "fixtures"} tabIndex={0}>
        <Fixtures actor={user.id} active={selected === "fixtures"} refresh={refresh} />
      </div>
    </main>
  );
}
