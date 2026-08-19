import { accessToken, apiUrl } from "./auth";
import type {
  AnalyticsResponse,
  Architecture,
  DataSourcesResponse,
  HypothesisRecord,
  Hypothesis,
  FeatureGap,
  MapResponse,
  MonteCarloCone,
  MonteCarloDiagnosticResult,
  DiagnosticStatus,
  PhasesResponse,
  ResearchRunRequest,
  ResearchEvent,
  ResearchRunSummary,
  Stats,
  SurvivorTest,
  TimelinePoint,
  VerdictFeedItem,
  FleetResponse,
} from "./lib/types";

async function authorizedFetch(url: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  const token = await accessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(apiUrl(url), { ...init, headers });
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authorizedFetch(url, init);
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

async function download(url: string): Promise<void> {
  const res = await authorizedFetch(url);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail || `${res.status} ${res.statusText}`);
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] ?? "popper-download";
  const href = URL.createObjectURL(await res.blob());
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}

export const api = {
  health: () => get<{ ok: boolean; version: string; store: string }>("/api/health"),
  phases: () => get<PhasesResponse>("/api/phases"),
  architecture: () => get<Architecture>("/api/architecture"),
  dataSources: () => get<DataSourcesResponse>("/api/data-sources"),
  stats: () => get<Stats>("/api/stats"),
  analytics: () => get<AnalyticsResponse>("/api/analytics"),
  survivors: () => get<{ survivors: SurvivorTest[] }>("/api/survivors"),
  llmConfig: () => get<{
    backend: string;
    policy: string;
    batch_size: number;
    fidelity_mode: string;
    survivor_audit: boolean;
    routes: Record<string, string>;
  }>("/api/llm-config"),
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
  diagnostics: (id: string) =>
    get<DiagnosticStatus>(`/api/hypotheses/${encodeURIComponent(id)}/diagnostics`),
  runMonteCarlo: (id: string) =>
    post<MonteCarloDiagnosticResult>(
      `/api/hypotheses/${encodeURIComponent(id)}/diagnostics/monte-carlo`,
      {}
    ),
  downloadForge: (id: string) =>
    download(`/api/hypotheses/${encodeURIComponent(id)}/forge-download`),
  fleet: () => get<FleetResponse>("/api/fleet"),
  setNodeSwitch: (machineId: string, switchVal: "on" | "off") =>
    post<{ machine_id: string; switch: string; ok: boolean }>(
      `/api/fleet/${encodeURIComponent(machineId)}/switch`,
      { switch: switchVal }
    ),
  setNodeExploit: (machineId: string, exploit: "on" | "off") =>
    post<{ machine_id: string; exploit: string; ok: boolean }>(
      `/api/fleet/${encodeURIComponent(machineId)}/exploit`,
      { exploit }
    ),
  setNodeProviders: (machineId: string, providersOff: string[]) =>
    post<{ machine_id: string; providers_off: string[]; ok: boolean }>(
      `/api/fleet/${encodeURIComponent(machineId)}/providers`,
      { providers_off: providersOff }
    ),
  requeueNode: (machineId: string) =>
    post<{ machine_id: string; requeue_requested: boolean; ok: boolean }>(
      `/api/fleet/${encodeURIComponent(machineId)}/requeue`,
      {}
    ),
  verdicts: (limit = 20) => get<{ verdicts: VerdictFeedItem[] }>(`/api/verdicts?limit=${limit}`),
  timeline: (id: string) =>
    get<{ experiments: TimelinePoint[] }>(`/api/timeline?hypothesis_id=${encodeURIComponent(id)}`),
};
