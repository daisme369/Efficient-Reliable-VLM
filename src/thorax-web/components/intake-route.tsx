'use client';

import { Patients } from '@/features/admin/AdminConsole';
import { AppShell, DesktopNotice, RoleGate, useDesktop } from './shell';

/** The X-ray technician registers patients and pushes studies; the assigned doctor then finds them on the worklist. */
export function IntakeRoute() {
  const desktop = useDesktop();
  return <RoleGate role="technician">{user => <AppShell>{desktop
    ? <main id="main-content" className="page-wrap"><div className="page-heading"><div><h1>Patient intake</h1><p>Register the patient, then push the chest X-ray study. The assigned doctor finds it on their worklist.</p></div></div><Patients actor={user.id} active intake /></main>
    : <DesktopNotice showWorklist={false} />}</AppShell>}</RoleGate>;
}
