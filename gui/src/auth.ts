import {
  createClient,
  type AuthChangeEvent,
  type Session,
  type SupabaseClient,
} from "@supabase/supabase-js";

export interface AuthConfig {
  required: boolean;
  supabase_url: string;
  supabase_key: string;
}

export interface TeamPrincipal {
  user_id: string;
  email: string;
  role: "admin" | "researcher" | "viewer";
}

let client: SupabaseClient | null = null;
let config: AuthConfig | null = null;

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export function apiUrl(path: string): string {
  return `${apiBase}${path}`;
}

export async function initializeAuth(): Promise<{ config: AuthConfig; session: Session | null }> {
  const response = await fetch(apiUrl("/api/auth/config"));
  if (!response.ok) throw new Error(`Authentication configuration failed: ${response.status}`);
  config = (await response.json()) as AuthConfig;
  if (!config.required) return { config, session: null };
  if (!config.supabase_url || !config.supabase_key) {
    throw new Error("Supabase browser authentication is incomplete");
  }
  client ??= createClient(config.supabase_url, config.supabase_key, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  const { data, error } = await client.auth.getSession();
  if (error) throw error;
  return { config, session: data.session };
}

export function onAuthChange(
  callback: (event: AuthChangeEvent, session: Session | null) => void,
): () => void {
  if (!client) return () => undefined;
  const { data } = client.auth.onAuthStateChange((event, session) => callback(event, session));
  return () => data.subscription.unsubscribe();
}

export async function signIn(email: string, password: string): Promise<Session> {
  if (!client) throw new Error("Authentication is not initialized");
  const { data, error } = await client.auth.signInWithPassword({ email, password });
  if (error) throw error;
  if (!data.session) throw new Error("Supabase did not return a session");
  return data.session;
}

export async function requestPasswordReset(email: string): Promise<void> {
  if (!client) throw new Error("Authentication is not initialized");
  const redirectTo = `${window.location.origin}/reset-password`;
  const { error } = await client.auth.resetPasswordForEmail(email, { redirectTo });
  if (error) throw error;
}

export async function updatePassword(password: string): Promise<void> {
  if (!client) throw new Error("Authentication is not initialized");
  const { error } = await client.auth.updateUser({ password });
  if (error) throw error;
}

export async function loadPrincipal(session: Session): Promise<TeamPrincipal> {
  const response = await fetch(apiUrl("/api/auth/me"), {
    headers: { Authorization: `Bearer ${session.access_token}` },
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(detail.detail ?? "This account is not an active Popper team member");
  }
  return response.json() as Promise<TeamPrincipal>;
}

export async function signOut(): Promise<void> {
  if (!client) return;
  const { error } = await client.auth.signOut();
  if (error) throw error;
}

export async function accessToken(): Promise<string | null> {
  if (!config?.required || !client) return null;
  const { data, error } = await client.auth.getSession();
  if (error) throw error;
  return data.session?.access_token ?? null;
}
