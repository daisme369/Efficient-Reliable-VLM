'use client';

import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ChevronRight, FlaskConical, LogOut, Monitor, ScanLine } from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { api, ApiError, authLostEvent, clearApiAuth } from '@/lib/api';
import { Badge, EmptyState, Notice, Skeleton } from './ui';

type AuthUser = Awaited<ReturnType<typeof api.me>>;
const authKey = ['auth', 'me'] as const;
const authContext = createContext<{
  user: AuthUser | undefined; loading: boolean; error: unknown; expired: boolean;
  refresh: (activity?: boolean) => Promise<AuthUser | undefined>;
  accept: (user: AuthUser) => void; expire: () => void; signOut: () => Promise<void>;
} | null>(null);

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 } } }));
  return <QueryClientProvider client={queryClient}><AuthProvider>{children}</AuthProvider></QueryClientProvider>;
}

function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const [expired, setExpired] = useState(false);
  const generation = useRef(0);
  const [logoutChannel, setLogoutChannel] = useState<BroadcastChannel | null>(null);
  const auth = useQuery({ queryKey: authKey, queryFn: async ({ signal }) => {
    try { return await api.me(false, signal); }
    catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; }
  }, staleTime: Infinity, gcTime: Infinity });
  const user = auth.data ?? undefined;
  const clear = useCallback((expired: boolean) => {
    generation.current += 1;
    clearApiAuth();
    queryClient.cancelQueries();
    queryClient.setQueryData(authKey, null);
    queryClient.removeQueries({ predicate: query => query.queryKey[0] !== 'auth' });
    setExpired(expired);
    router.replace('/login');
  }, [queryClient, router]);
  const expire = useCallback(() => clear(true), [clear]);
  useEffect(() => {
    window.addEventListener(authLostEvent, expire);
    return () => window.removeEventListener(authLostEvent, expire);
  }, [expire]);
  const accept = useCallback((next: AuthUser) => {
    const old = queryClient.getQueryData<AuthUser | null>(authKey);
    if (old?.id !== next.id || old?.role !== next.role) queryClient.removeQueries({ predicate: query => query.queryKey[0] !== 'auth' });
    queryClient.setQueryData(authKey, next);
    setExpired(false);
  }, [queryClient]);
  const refresh = useCallback(async (activity = false) => {
    const started = generation.current;
    try {
      const next = await api.me(activity);
      if (generation.current !== started) return;
      accept(next);
      return next;
    } catch (error) {
      if (generation.current !== started) return;
      if (error instanceof ApiError && error.status === 401) { expire(); return; }
      throw error;
    }
  }, [accept, expire]);
  const signOut = useCallback(async () => {
    try { await api.logout(); }
    catch (error) { if (!(error instanceof ApiError && error.status === 401)) throw error; }
    logoutChannel?.postMessage('signed-out');
    clear(false);
  }, [logoutChannel, clear]);

  useEffect(() => {
    if (!('BroadcastChannel' in window)) return;
    const channel = new BroadcastChannel('thorax-auth');
    channel.onmessage = event => { if (event.data === 'signed-out') clear(false); };
    setLogoutChannel(channel);
    return () => { channel.close(); };
  }, [clear]);

  useEffect(() => {
    if (!user || pathname === '/login') return;
    function check() { if (document.visibilityState === 'visible') void refresh().catch(() => {}); }
    document.addEventListener('visibilitychange', check);
    window.addEventListener('focus', check);
    return () => { document.removeEventListener('visibilitychange', check); window.removeEventListener('focus', check); };
  }, [user?.id, pathname, refresh]);

  useEffect(() => {
    if (!user?.expires_at) return;
    const delay = Date.parse(user.expires_at) - Date.now();
    const timer = window.setTimeout(() => { void refresh().catch(expire); }, Math.max(0, delay) + 250);
    return () => window.clearTimeout(timer);
  }, [user?.expires_at, refresh, expire]);

  return <authContext.Provider value={{ user, loading: auth.isPending, error: auth.error, expired, refresh, accept, expire, signOut }}>{children}</authContext.Provider>;
}

export function useAuth() {
  const auth = useContext(authContext);
  if (!auth) throw new Error('AuthProvider is required');
  return auth;
}

export function homeFor(user: AuthUser) { return user.role === 'admin' ? '/admin' : '/worklist'; }

export function RoleGate({ role, children }: { role: 'doctor' | 'admin'; children: (user: AuthUser) => ReactNode }) {
  const auth = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (auth.loading || auth.error) return;
    if (!auth.user) router.replace('/login');
    else if (auth.user.role !== role) router.replace(homeFor(auth.user));
  }, [auth.loading, auth.error, auth.user, role, router]);
  if (auth.error) return <main id="main-content" className="page-wrap"><EmptyState title="Could not reach the server" action={<button className="button" onClick={() => window.location.reload()}>Retry</button>}>Check your connection and try again.</EmptyState></main>;
  if (!auth.user || auth.user.role !== role) return <main id="main-content" className="page-wrap"><Skeleton label="Checking access" /></main>;
  return children(auth.user);
}

export function Brand() {
  return <span className="brand"><span className="brand-mark"><ScanLine size={22} aria-hidden /></span><strong>Thorax AI</strong></span>;
}

export function AppShell({ children, workspace, onWorklist, onSignOut }: { children: ReactNode; workspace?: boolean; onWorklist?: () => void; onSignOut?: () => void }) {
  const { user, signOut, refresh } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState('');
  const [warning, setWarning] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const pathname = usePathname();
  useEffect(() => {
    if (!user?.expires_at) return;
    const delay = Date.parse(user.expires_at) - Date.now() - 120_000;
    setWarning(delay <= 0);
    if (delay <= 0) return;
    const timer = window.setTimeout(() => setWarning(true), delay);
    return () => window.clearTimeout(timer);
  }, [user?.expires_at]);

  async function leave() {
    if (onSignOut) { onSignOut(); return; }
    setSigningOut(true);
    try { await signOut(); }
    catch { setError('Could not sign out. Check your connection and try again.'); }
    finally { setSigningOut(false); }
  }
  async function keepWorking() {
    setContinuing(true);
    try { setError(''); await refresh(true); }
    catch { setError('Could not reach the server. Try again.'); }
    finally { setContinuing(false); }
  }

  const initials = user?.name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
  return <div className={workspace ? 'app-shell app-shell-workspace' : 'app-shell'}>
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <header className="app-header">
      <Brand /><span className="app-descriptor">Chest X-ray Reading Assistant</span>
      {user && <nav aria-label="Primary navigation" className="main-nav">
        {user.role === 'doctor' ? <Link prefetch={false} href="/worklist" aria-current={pathname === '/worklist' ? 'page' : undefined} onClick={onWorklist ? event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); onWorklist(); } : undefined}>
          {pathname !== '/worklist' && <ArrowLeft size={16} aria-hidden />} Worklist
        </Link> : <Link prefetch={false} href="/admin" aria-current="page">Admin</Link>}
        {user.role === 'doctor' && pathname.startsWith('/patients/') && <><ChevronRight size={14} aria-hidden /><span>{pathname.endsWith('/report') ? 'Final report' : 'Study workspace'}</span></>}
      </nav>}
      <div className="header-account">
        {user && <><span className="avatar" aria-hidden>{initials}</span><span className="account-name"><strong>{user.name}</strong><span>{user.role === 'admin' ? 'Administrator' : 'Doctor'}</span></span></>}
        {user && <button className="button ghost" onClick={() => void leave()} disabled={signingOut}><LogOut size={16} aria-hidden /> {signingOut ? 'Signing out…' : 'Sign out'}</button>}
      </div>
    </header>
    {warning && <div className="session-warning"><Notice tone="pending">Your session expires soon. Save your changes or continue your session.</Notice><button className="button" disabled={continuing} onClick={() => void keepWorking()}>{continuing ? 'Checking…' : 'Continue session'}</button><button className="button ghost" onClick={() => void leave()}>Sign out</button></div>}
    {error && <Notice tone="error">{error}</Notice>}
    {children}
  </div>;
}

export function DemoBadge({ mode }: { mode?: 'mock' | 'real' }) {
  return mode === 'real' ? <Badge><FlaskConical size={14} aria-hidden />Academic demonstration</Badge> : <Badge><FlaskConical size={14} aria-hidden />Academic demonstration — mock data</Badge>;
}

const desktopMedia = '(min-width: 768px)';
function subscribeDesktop(callback: () => void) {
  const media = window.matchMedia(desktopMedia);
  media.addEventListener('change', callback);
  return () => media.removeEventListener('change', callback);
}
export function useDesktop() { return useSyncExternalStore(subscribeDesktop, () => window.matchMedia(desktopMedia).matches, () => false); }

export function DesktopNotice({ showWorklist = true }: { showWorklist?: boolean }) {
  return <main id="main-content" className="desktop-notice"><Monitor size={32} aria-hidden /><h1>Wider window required</h1><p>Expand this browser panel or open Thorax AI in a browser window at least 768 px wide.</p>{showWorklist && <Link className="button" prefetch={false} href="/worklist">Worklist</Link>}</main>;
}
