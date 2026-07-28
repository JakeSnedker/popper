import { createClient, type Session, type SupabaseClient } from "@supabase/supabase-js";

export interface AuthConfig {
  required: boolean;
  supabase_url: string;
  supabase_key: string;
}

let client: SupabaseClient | null = null;
let config: AuthConfig | null = null;

export async function initializeAuth(): Promise<{ config: AuthConfig; session: Session | null }> {
  const response = await fetch("/api/auth/config");
  if (!response.ok) throw new Error(`Authentication configuration failed: ${response.status}`);
  config = (await response.json()) as AuthConfig;
  if (!config.required) return { config, session: null };
  if (!config.supabase_url || !config.supabase_key) {
    throw new Error("Supabase browser authentication is incomplete");
  }
  client ??= createClient(config.supabase_url, config.supabase_key, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
  });
  const { data, error } = await client.auth.getSession();
  if (error) throw error;
  return { config, session: data.session };
}

export function onAuthChange(callback: (session: Session | null) => void): () => void {
  if (!client) return () => undefined;
  const { data } = client.auth.onAuthStateChange((_event, session) => callback(session));
  return () => data.subscription.unsubscribe();
}

export async function signIn(email: string, password: string): Promise<Session> {
  if (!client) throw new Error("Authentication is not initialized");
  const { data, error } = await client.auth.signInWithPassword({ email, password });
  if (error) throw error;
  if (!data.session) throw new Error("Supabase did not return a session");
  return data.session;
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
