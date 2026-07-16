import type {
  Architecture,
  HypothesisRecord,
  Hypothesis,
  FeatureGap,
  MapResponse,
  MonteCarloCone,
  PhasesResponse,
  ResearchRunRequest,
  ResearchEvent,
  ResearchRunSummary,
  Stats,
  TimelinePoint,
  VerdictFeedItem,
} from "./lib/types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

async function get<T>(url: string): Promise<T> {
  return request<T>(url);
}

async function post<T>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const api = {
  health: () => get<{ ok: boolean; version: string; store: string }>("/api/health"),
  phases: () => get<PhasesResponse>("/api/phases"),
  architecture: () => get<Architecture>("/api/architecture"),
  stats: () => get<Stats>("/api/stats"),
  featureGaps: (limit = 12) => get<{ feature_gaps: FeatureGap[] }>(`/api/feature-gaps?limit=${limit}`),
  runResearch: (req: ResearchRunRequest) => post<ResearchRunSummary>("/api/research", req),
  researchEvents: (runId?: string, limit = 80) =>
    get<{ events: ResearchEvent[] }>(
      `/api/research-events?limit=${limit}` + (runId ? `&run_id=${encodeURIComponent(runId)}` : "")
    ),
  hypotheses: (status?: string) =>
    get<{ hypotheses: Hypothesis[] }>(
      "/api/hypotheses" + (status ? `?status=${status}` : "")
    ),
  record: (id: string) => get<HypothesisRecord>(`/api/hypotheses/${id}`),
  map: () => get<MapResponse>("/api/map"),
  mcCone: (id: string) =>
    get<MonteCarloCone>(`/api/hypotheses/${encodeURIComponent(id)}/mc-cone`),
  verdicts: (limit = 20) => get<{ verdicts: VerdictFeedItem[] }>(`/api/verdicts?limit=${limit}`),
  timeline: (id: string) =>
    get<{ experiments: TimelinePoint[] }>(`/api/timeline?hypothesis_id=${encodeURIComponent(id)}`),
};
