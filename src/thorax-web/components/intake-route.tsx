'use client';

import { Intake } from '@/features/intake/Intake';
import { AppShell, DesktopNotice, RoleGate, useDesktop } from './shell';

/** The X-ray technician registers patients and pushes studies; the assigned doctor then finds them on the worklist. */
export function IntakeRoute() {
  const desktop = useDesktop();
  return <RoleGate role="technician">{user => <AppShell>{desktop
    ? <main id="main-content" className="page-wrap"><div className="page-heading"><div><h1>Patient intake</h1><p>Find the patient or register a new one, then add the chest X-ray study. The assigned doctor reads it from their worklist.</p></div></div><Intake actor={user.id} /></main>
    : <DesktopNotice showWorklist={false} />}</AppShell>}</RoleGate>;
}
