'use client';

import { ArrowRight, Eye, EyeOff, ShieldCheck } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Brand, DemoBadge, homeFor, useAuth } from '@/components/shell';
import { Notice, Skeleton, Spinner } from '@/components/ui';
import { api, ApiError } from '@/lib/api';

export function Login({ localDemo }: { localDemo: boolean }) {
  const auth = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [visible, setVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [retryUntil, setRetryUntil] = useState(0);
  const pending = useRef(false);
  const summary = useRef<HTMLDivElement>(null);

  useEffect(() => { if (auth.user) router.replace(homeFor(auth.user)); }, [auth.user, router]);
  useEffect(() => { if (error) summary.current?.focus(); }, [error]);
  useEffect(() => {
    if (!retryUntil) return;
    const timer = window.setTimeout(() => { setRetryUntil(0); setError(''); }, Math.max(0, retryUntil - Date.now()));
    return () => window.clearTimeout(timer);
  }, [retryUntil]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current || retryUntil || !email.trim() || !password) return;
    pending.current = true;
    setSubmitting(true);
    setError('');
    try {
      await api.login(email.trim(), password);
      const user = await api.me();
      auth.accept(user);
      router.replace(homeFor(user));
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 429) {
        const seconds = Math.max(1, failure.retryAfter ?? 60);
        setRetryUntil(Date.now() + seconds * 1000);
        setError(`Too many sign-in attempts. Try again in ${seconds} seconds.`);
      } else if (failure instanceof ApiError && [400, 401, 403, 422].includes(failure.status)) setError('Email or password is incorrect.');
      else setError('Could not reach the server. Try again.');
    } finally {
      setPassword('');
      setVisible(false);
      setSubmitting(false);
      pending.current = false;
    }
  }

  return <div className="login-shell">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <header className="login-header"><Brand /><span className="app-descriptor">Chest X-ray Reading Assistant</span></header>
    <main className="login-main" id="main-content">
      <section className="login-card surface" aria-labelledby="login-heading">
        {auth.loading || auth.user ? <Skeleton label="Checking session" rows={4} /> : <>
          <DemoBadge mode={localDemo ? 'mock' : 'real'} />
          <div className="login-title"><h1 id="login-heading">Sign in to Thorax AI</h1><p>Use your assigned account to access the clinical workspace.</p></div>
          {auth.expired && <Notice tone="pending">Your session expired. Sign in again.</Notice>}
          {error && <div ref={summary} tabIndex={-1} id="login-error"><Notice tone="error">{error} {error.startsWith('Email or password') && <><a href="#email">Email</a> · <a href="#password">Password</a></>}</Notice></div>}
          <form className="login-form" onSubmit={submit} noValidate>
            <label className="field" htmlFor="email">Email<input id="email" autoComplete="username" type="email" placeholder="you@example.org" required value={email} onChange={event => setEmail(event.target.value)} aria-describedby={error ? 'login-error' : undefined} /></label>
            <label className="field" htmlFor="password">Password<span className="password-field"><input id="password" autoComplete="current-password" required type={visible ? 'text' : 'password'} value={password} onChange={event => setPassword(event.target.value)} aria-describedby={error ? 'login-error' : undefined} /><button type="button" className="icon-button" aria-label={visible ? 'Hide password' : 'Show password'} onClick={() => setVisible(!visible)}>{visible ? <EyeOff size={19} aria-hidden /> : <Eye size={19} aria-hidden />}</button></span></label>
            <button className="button primary login-submit" disabled={submitting || Boolean(retryUntil) || !email.trim() || !password}>{submitting ? <Spinner /> : <ArrowRight size={17} aria-hidden />}{submitting ? 'Signing in…' : 'Sign in'}</button>
          </form>
          <div className="login-foot"><ShieldCheck size={17} aria-hidden /> For authorized accounts</div>
        </>}
      </section>
    </main>
  </div>;
}
