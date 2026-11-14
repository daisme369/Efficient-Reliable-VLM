'use client';

import Admin from '@/features/admin/Admin';
import { AppShell, DesktopNotice, RoleGate, useDesktop } from './shell';

export function AdminRoute({ localDemo }: { localDemo: boolean }) {
  const desktop = useDesktop();
  return <RoleGate role="admin">{user => <AppShell>{desktop ? <Admin user={user} localDemo={localDemo} /> : <DesktopNotice showWorklist={false} />}</AppShell>}</RoleGate>;
}
