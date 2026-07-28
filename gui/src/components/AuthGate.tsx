import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Session } from "@supabase/supabase-js";
import { initializeAuth, onAuthChange, signIn, signOut, type AuthConfig } from "../auth";
import { LoadingRows } from "./States";

interface AuthState {
  required: boolean;
  email: string | null;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState>({ required: false, email: null, logout: async () => undefined });

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

function Login({ onSession }: { onSession: (session: Session) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSession(await signIn(email.trim(), password));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-ink-900 flex items-center justify-center p-6">
      <form onSubmit={submit} className="card w-full max-w-sm p-6 space-y-5">
        <div>
          <div className="text-[10px] font-mono uppercase tracking-[0.24em] text-accent">Private research system</div>
          <h1 className="text-2xl font-semibold text-slate-100 mt-2">Popper team access</h1>
          <p className="text-sm text-slate-500 mt-1">Sign in with the Supabase account assigned to you.</p>
        </div>
        <label className="block text-xs text-slate-400">
          Email
          <input
            type="email" value={email} onChange={(event) => setEmail(event.target.value)} required
            autoComplete="email"
            className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-accent"
          />
        </label>
        <label className="block text-xs text-slate-400">
          Password
          <input
            type="password" value={password} onChange={(event) => setPassword(event.target.value)} required
            autoComplete="current-password"
            className="mt-1.5 w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-accent"
          />
        </label>
        {error && <p className="text-xs text-red-400">{error}</p>}
        <button
          type="submit" disabled={busy}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-white hover:brightness-110 disabled:opacity-50"
        >
          {busy ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    let unsubscribe: () => void = () => undefined;
    initializeAuth()
      .then((result) => {
        if (!active) return;
        setConfig(result.config);
        setSession(result.session);
        unsubscribe = onAuthChange(setSession);
      })
      .catch((reason: Error) => active && setError(reason.message));
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  if (error) {
    return <div className="min-h-screen bg-ink-900 flex items-center justify-center p-6 text-sm text-red-400">{error}</div>;
  }
  if (!config) {
    return <div className="min-h-screen bg-ink-900 flex items-center justify-center"><div className="w-64"><LoadingRows rows={3} /></div></div>;
  }
  if (config.required && !session) return <Login onSession={setSession} />;

  return (
    <AuthContext.Provider value={{
      required: config.required,
      email: session?.user.email ?? null,
      logout: async () => { await signOut(); setSession(null); },
    }}>
      {children}
    </AuthContext.Provider>
  );
}
