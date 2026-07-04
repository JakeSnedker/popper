import type {
  Architecture,
  HypothesisRecord,
  Hypothesis,
  MapResponse,
  PhasesResponse,
  Stats,
  TimelinePoint,
  VerdictFeedItem,
} from "./lib/types";

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<{ ok: boolean; version: string; store: string }>("/api/health"),
  phases: () => get<PhasesResponse>("/api/phases"),
  architecture: () => get<Architecture>("/api/architecture"),
  stats: () => get<Stats>("/api/stats"),
  hypotheses: (status?: string) =>
    get<{ hypotheses: Hypothesis[] }>(
      "/api/hypotheses" + (status ? `?status=${status}` : "")
    ),
  record: (id: string) => get<HypothesisRecord>(`/api/hypotheses/${id}`),
  map: () => get<MapResponse>("/api/map"),
  verdicts: (limit = 20) => get<{ verdicts: VerdictFeedItem[] }>(`/api/verdicts?limit=${limit}`),
  timeline: (id: string) =>
    get<{ experiments: TimelinePoint[] }>(`/api/timeline?hypothesis_id=${encodeURIComponent(id)}`),
};
