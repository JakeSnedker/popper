import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { AuthChangeEvent, Session } from "@supabase/supabase-js";
import {
  initializeAuth,
  loadPrincipal,
  onAuthChange,
  requestPasswordReset,
  signIn,
  signOut,
  updatePassword,
  type AuthConfig,
  type TeamPrincipal,
} from "../auth";
import { LoadingRows } from "./States";

interface AuthState {
  required: boolean;
  email: string | null;
  role: TeamPrincipal["role"] | null;
  canWrite: boolean;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  required: false,
  email: null,
  role: "admin",
  canWrite: true,
  logout: async () => undefined,
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-ink-900 flex items-center justify-center p-6">
      <div className="card w-full max-w-sm p-6 space-y-5">{children}</div>
    </div>
  );
}

function Heading({ title, detail }: { title: string; detail: string }) {
  return (
    <div>
      <div className="text-[10px] font-mono uppercase tracking-[0.24em] text-accent">
        Private research system
      </div>
      <h1 className="text-2xl font-semibold text-slate-100 mt-2">{title}</h1>
      <p className="text-sm text-slate-500 mt-1">{detail}</p>
    </div>
  );
}

function Login({ onSession }: { onSession: (session: Session) => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [resetMode, setResetMode] = useState(false);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submitLogin = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSession(await signIn(email.trim(), password));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  const submitReset = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await requestPasswordReset(email.trim());
      setSent(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Reset email failed");
    } finally {
      setBusy(false);
    }
  };

  if (resetMode) {
    return (
      <Shell>
        <Heading
          title="Reset your password"
          detail="Supabase will email a private link back to this Popper dashboard."
        />
        <form onSubmit={submitReset} className="space-y-4">
          <label className="block text-xs text-slate-400">
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              autoComplete="email"
              className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-accent"
            />
          </label>
          {sent && <p className="text-xs text-emerald-400">Reset email sent. You can close this page.</p>}
          {error && <p className="text-xs text-red-400">{error}</p>}
          <button
            type="submit"
            disabled={busy || sent}
            className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white hover:brightness-110 disabled:opacity-50"
          >
            {busy ? "Sending..." : "Send reset link"}
          </button>
          <button
            type="button"
            onClick={() => { setResetMode(false); setSent(false); setError(null); }}
            className="w-full text-xs text-slate-500 hover:text-slate-200"
          >
            Back to sign in
          </button>
        </form>
      </Shell>
    );
  }

  return (
    <Shell>
      <Heading title="Popper team access" detail="Sign in with the Supabase account assigned to you." />
      <form onSubmit={submitLogin} className="space-y-4">
        <label className="block text-xs text-slate-400">
          Email
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            autoComplete="email"
            className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-accent"
          />
        </label>
        <label className="block text-xs text-slate-400">
          Password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            autoComplete="current-password"
            className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-accent"
          />
        </label>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white hover:brightness-110 disabled:opacity-50"
        >
          {busy ? "Signing in..." : "Sign in"}
        </button>
        <button
          type="button"
          onClick={() => { setResetMode(true); setError(null); }}
          className="w-full text-xs text-slate-500 hover:text-slate-200"
        >
          Forgot password?
        </button>
      </form>
    </Shell>
  );
}

function PasswordRecovery({ onComplete }: { onComplete: () => void }) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (password.length < 12) {
      setError("Use at least 12 characters.");
      return;
    }
    if (password !== confirmation) {
      setError("The passwords do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await updatePassword(password);
      window.history.replaceState({}, "", "/");
      onComplete();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Password update failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell>
      <Heading title="Choose a new password" detail="This updates only your private Popper login." />
      <form onSubmit={submit} className="space-y-4">
        <label className="block text-xs text-slate-400">
          New password
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            autoComplete="new-password"
            className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-accent"
          />
        </label>
        <label className="block text-xs text-slate-400">
          Confirm password
          <input
            type="password"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            required
            autoComplete="new-password"
            className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-accent"
          />
        </label>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white hover:brightness-110 disabled:opacity-50"
        >
          {busy ? "Updating..." : "Set new password"}
        </button>
      </form>
    </Shell>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [principal, setPrincipal] = useState<TeamPrincipal | null>(null);
  const [recovering, setRecovering] = useState(window.location.pathname === "/reset-password");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const applySession = async (next: Session | null) => {
    setSession(next);
    setPrincipal(next ? await loadPrincipal(next) : null);
  };

  useEffect(() => {
    let active = true;
    let unsubscribe: () => void = () => undefined;
    const changed = (event: AuthChangeEvent, next: Session | null) => {
      if (!active) return;
      if (event === "PASSWORD_RECOVERY") setRecovering(true);
      void applySession(next).catch((reason: Error) => setError(reason.message));
    };
    initializeAuth()
      .then(async (result) => {
        if (!active) return;
        setConfig(result.config);
        await applySession(result.session);
        unsubscribe = onAuthChange(changed);
      })
      .catch((reason: Error) => active && setError(reason.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  if (error) {
    return (
      <div className="min-h-screen bg-ink-900 flex items-center justify-center p-6 text-sm text-red-400">
        {error}
      </div>
    );
  }
  if (loading || !config) {
    return (
      <div className="min-h-screen bg-ink-900 flex items-center justify-center">
        <div className="w-64"><LoadingRows rows={3} /></div>
      </div>
    );
  }
  if (recovering && session) return <PasswordRecovery onComplete={() => setRecovering(false)} />;
  if (config.required && !session) return <Login onSession={applySession} />;

  const role = principal?.role ?? (config.required ? null : "admin");
  const canWrite = !config.required || role === "admin" || role === "researcher";
  return (
    <AuthContext.Provider value={{
      required: config.required,
      email: session?.user.email ?? null,
      role,
      canWrite,
      logout: async () => { await signOut(); setSession(null); setPrincipal(null); },
    }}>
      {children}
    </AuthContext.Provider>
  );
}
