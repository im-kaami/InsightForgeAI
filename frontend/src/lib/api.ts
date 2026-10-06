import type { components } from "./api-types";

export type User = components["schemas"]["UserOut"];
export type Dataset = components["schemas"]["DatasetOut"];
export type Connection = components["schemas"]["ConnectionOut"];
export type Session = components["schemas"]["SessionOut"];
export type Run = components["schemas"]["RunOut"];
export type Schedule = components["schemas"]["ScheduleOut"];
export type DatasetVersion = components["schemas"]["VersionOut"];
export type DataProfile = components["schemas"]["DataProfile"];
export type ImportOptions = components["schemas"]["ImportOptions"];
export type SalesDefinition = components["schemas"]["SalesDefinition"];
export type ReportDefinition = components["schemas"]["DefinitionOut"];
export type Suggestions = components["schemas"]["Suggestions"];
export type RecipeStep = NonNullable<components["schemas"]["CleaningRecipe"]["steps"]>[number];
export type ValidationRule = NonNullable<components["schemas"]["RuleSet"]["rules"]>[number];
export type AppliedRecipe = components["schemas"]["AppliedRecipe"];
export type ValidationReport = components["schemas"]["ValidationReport"];
export type SavedModel = components["schemas"]["SavedModelOut"];
export type Relationship = components["schemas"]["Relationship"];
export type RelationshipSuggestions = components["schemas"]["RelationshipSuggestions"];
export type ModelScore = components["schemas"]["ScoreOut"];
export type ModelSchedule = components["schemas"]["ModelScheduleOut"];
export type ModelScoring = components["schemas"]["ScoringOut"];
export type ModelExplanation = components["schemas"]["ExplanationOut"];
export type Metric = components["schemas"]["Metric"];
export type MetricFilter = components["schemas"]["MetricFilter"];
export type MetricQuery = components["schemas"]["MetricQuery"];
export type MetricSuggestions = components["schemas"]["MetricSuggestions"];
export type MetricPreview = components["schemas"]["MetricPreviewOut"];
export type ApprovedQuery = components["schemas"]["ApprovedQuery"];
export type MetricReportCreate = components["schemas"]["MetricReportCreate"];
export type FollowIn = components["schemas"]["FollowIn"];
export type Follow = components["schemas"]["FollowOut"];
export type MetricCheck = components["schemas"]["MetricCheckOut"];
export type SqlQueryResult = components["schemas"]["SqlQueryOut"];

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const value = item as { loc?: unknown[]; msg?: string };
        const location = value.loc?.slice(1).join(".");
        return `${location ? `${location}: ` : ""}${value.msg ?? fallback}`;
      })
      .join("; ");
  }
  return fallback;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = typeof window !== "undefined" ? localStorage.getItem("if_token") : null;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("if_token");
      if (!location.pathname.startsWith("/login"))
        window.location.assign(new URL("/login", window.location.origin));
    }
    throw new ApiError(response.status, errorMessage(body.detail, response.statusText));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const health = () => apiFetch<components["schemas"]["HealthOut"]>("/health");

export const auth = {
  register: (email: string, password: string) =>
    apiFetch<User>("/auth/register", json({ email, password })),
  login: (email: string, password: string) =>
    apiFetch<components["schemas"]["Token"]>("/auth/login", json({ email, password })),
  me: () => apiFetch<User>("/auth/me"),
};

export type ApiToken = components["schemas"]["ApiTokenOut"];
export type ApiTokenCreated = components["schemas"]["ApiTokenCreated"];

export const apiTokens = {
  list: () => apiFetch<ApiToken[]>("/auth/tokens"),
  create: (body: components["schemas"]["ApiTokenIn"]) =>
    apiFetch<ApiTokenCreated>("/auth/tokens", json(body)),
  revoke: (id: string) => apiFetch<void>(`/auth/tokens/${id}`, { method: "DELETE" }),
};

export const datasets = {
  list: () => apiFetch<Dataset[]>("/datasets"),
  get: (id: string) => apiFetch<Dataset>(`/datasets/${id}`),
  upload: (files: File[], name?: string, options?: ImportOptions[], review = false) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    if (name) body.append("name", name);
    if (options) body.append("options_json", JSON.stringify(options));
    if (review) body.append("review", "true");
    return apiFetch<Dataset>("/datasets/upload", { method: "POST", body });
  },
  versions: (id: string) => apiFetch<DatasetVersion[]>(`/datasets/${id}/versions`),
  replaceVersion: (id: string, files: File[], options?: ImportOptions[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    if (options) body.append("options_json", JSON.stringify(options));
    return apiFetch<DatasetVersion>(`/datasets/${id}/versions`, { method: "POST", body });
  },
  confirmVersion: (id: string, version: DatasetVersion) =>
    apiFetch<Dataset>(
      `/datasets/${id}/versions/${version.id}/confirm`,
      json({ confirmed: true, expected_current_version_id: version.base_version_id }),
    ),
  setPrivacy: (id: string, mode: Dataset["llm_policy"]) =>
    apiFetch<Dataset>(`/datasets/${id}/privacy`, {
      method: "PATCH",
      body: JSON.stringify({ mode, acknowledged: true }),
    }),
  refresh: (id: string) => apiFetch<Dataset>(`/datasets/${id}/refresh`, { method: "POST" }),
  saveNotes: (id: string, notes: components["schemas"]["DatasetNotes"]) =>
    apiFetch<Dataset>(`/datasets/${id}/notes`, { method: "PUT", body: JSON.stringify(notes) }),
  suggestions: (id: string) => apiFetch<Suggestions>(`/datasets/${id}/suggestions`),
  saveRecipe: (id: string, recipe: components["schemas"]["CleaningRecipe"]) =>
    apiFetch<Dataset>(`/datasets/${id}/recipe`, { method: "PUT", body: JSON.stringify(recipe) }),
  applyRecipe: (id: string) =>
    apiFetch<DatasetVersion>(`/datasets/${id}/recipe/apply`, { method: "POST" }),
  metricSuggestions: (id: string) =>
    apiFetch<MetricSuggestions>(`/datasets/${id}/metrics/suggestions`),
  saveMetrics: (id: string, metrics: Metric[]) =>
    apiFetch<Dataset>(`/datasets/${id}/metrics`, {
      method: "PUT",
      body: JSON.stringify({ metrics }),
    }),
  previewMetric: (id: string, metric: Metric, query?: MetricQuery) =>
    apiFetch<MetricPreview>(
      `/datasets/${id}/metrics/preview`,
      json({ metric, query: query ?? null }),
    ),
  metricReport: (id: string, body: MetricReportCreate) =>
    apiFetch<Run>(`/datasets/${id}/reports/metric`, json(body)),
  saveQueries: (id: string, queries: ApprovedQuery[], approvedOnly: boolean) =>
    apiFetch<Dataset>(`/datasets/${id}/queries`, {
      method: "PUT",
      body: JSON.stringify({ queries, approved_only: approvedOnly }),
    }),
  saveQueryFromRun: (id: string, runId: string, position: number) =>
    apiFetch<Dataset>(`/datasets/${id}/queries/from-run`, json({ run_id: runId, position })),
  runSql: (id: string, sql: string, versionId?: string) =>
    apiFetch<SqlQueryResult>(`/datasets/${id}/sql`, json({ sql, version_id: versionId ?? null })),
  downloadSqlCsv: (id: string, sql: string, versionId?: string) =>
    downloadBlob(
      `/datasets/${id}/sql/csv`,
      "query.csv",
      json({ sql, version_id: versionId ?? null }),
    ),
  relationshipSuggestions: (id: string) =>
    apiFetch<RelationshipSuggestions>(`/datasets/${id}/relationships/suggestions`),
  saveRelationships: (id: string, relationships: Relationship[]) =>
    apiFetch<Dataset>(`/datasets/${id}/relationships`, {
      method: "PUT",
      body: JSON.stringify({ relationships }),
    }),
  saveRules: (id: string, rules: components["schemas"]["RuleSet"]) =>
    apiFetch<Dataset>(`/datasets/${id}/rules`, { method: "PUT", body: JSON.stringify(rules) }),
  validateVersion: (id: string, versionId: string) =>
    apiFetch<DatasetVersion>(`/datasets/${id}/versions/${versionId}/validate`, { method: "POST" }),
  refreshProfile: (id: string, versionId: string) =>
    apiFetch<DatasetVersion>(`/datasets/${id}/versions/${versionId}/profile`, { method: "POST" }),
  fromUrl: (url: string, name?: string, sheets?: string[]) =>
    apiFetch<Dataset>("/datasets/from-url", json({ url, name, sheets })),
  fromConnection: (value: {
    connection_id?: string;
    uri?: string;
    name: string;
    tables?: string[];
  }) => apiFetch<Dataset>("/datasets/from-connection", json(value)),
  addSource: (id: string, source: File | string) => {
    if (typeof source === "string")
      return apiFetch<Dataset>(`/datasets/${id}/sources`, json({ url: source }));
    const body = new FormData();
    body.append("files", source);
    return apiFetch<Dataset>(`/datasets/${id}/sources`, { method: "POST", body });
  },
  preview: (id: string, table: string, versionId?: string) => {
    const version = versionId ? `&version_id=${encodeURIComponent(versionId)}` : "";
    return apiFetch<{ columns: string[]; rows: Record<string, unknown>[] }>(
      `/datasets/${id}/preview?table=${encodeURIComponent(table)}&limit=50${version}`,
    );
  },
  remove: (id: string) => apiFetch<void>(`/datasets/${id}`, { method: "DELETE" }),
};

export const connections = {
  list: () => apiFetch<Connection[]>("/connections"),
  create: (name: string, uri: string) => apiFetch<Connection>("/connections", json({ name, uri })),
  remove: (id: string) => apiFetch<void>(`/connections/${id}`, { method: "DELETE" }),
};

export const sessions = {
  list: () => apiFetch<Session[]>("/sessions"),
  get: (id: string) => apiFetch<Session>(`/sessions/${id}`),
  runs: (id: string) => apiFetch<Run[]>(`/sessions/${id}/runs`),
  create: (dataset_id: string, title?: string) =>
    apiFetch<Session>("/sessions", json({ dataset_id, title })),
  remove: (id: string) => apiFetch<void>(`/sessions/${id}`, { method: "DELETE" }),
  createRun: (id: string, goal: string, mode: "quick" | "deep" = "quick", clarified = false) =>
    apiFetch<Run>(`/sessions/${id}/runs`, json({ goal, mode, clarified })),
};

export async function downloadBlob(path: string, filename: string, init: RequestInit = {}) {
  const token = localStorage.getItem("if_token");
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, errorMessage(body.detail, response.statusText));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export const api = { downloadBlob };

export const runs = {
  get: (id: string) => apiFetch<Run>(`/runs/${id}`),
  reportUrl: (id: string, format: string) => `${API_BASE}/runs/${id}/report?format=${format}`,
  downloadReport: (id: string, format: string) =>
    downloadBlob(`/runs/${id}/report?format=${format}`, `insightforge-${id}.${format}`),
};

export const verifiedReports = {
  list: (datasetId: string) => apiFetch<ReportDefinition[]>(`/datasets/${datasetId}/reports`),
  create: (datasetId: string, body: components["schemas"]["DefinitionCreate"]) =>
    apiFetch<ReportDefinition>(`/datasets/${datasetId}/reports`, json(body)),
  run: (datasetId: string, definitionId: string, body: components["schemas"]["ReportRunCreate"]) =>
    apiFetch<Run>(`/datasets/${datasetId}/reports/${definitionId}/runs`, json(body)),
};

export type Dashboard = components["schemas"]["DashboardOut"];
export type DashboardItem = components["schemas"]["DashboardItemOut"];
export type Workspace = components["schemas"]["WorkspaceOut"];
type Role = "viewer" | "editor" | "owner";

export const workspaces = {
  list: () => apiFetch<Workspace[]>("/workspaces"),
  create: (name: string) => apiFetch<Workspace>("/workspaces", json({ name })),
  remove: (id: string) => apiFetch<void>(`/workspaces/${id}`, { method: "DELETE" }),
  invite: (id: string, email: string, role: Role) =>
    apiFetch<components["schemas"]["InviteCreated"]>(
      `/workspaces/${id}/invites`,
      json({ email, role }),
    ),
  revokeInvite: (id: string, inviteId: string) =>
    apiFetch<void>(`/workspaces/${id}/invites/${inviteId}`, { method: "DELETE" }),
  accept: (secret: string) => apiFetch<Workspace>("/workspaces/invites/accept", json({ secret })),
  setRole: (id: string, userId: string, role: Role) =>
    apiFetch<Workspace>(`/workspaces/${id}/members/${userId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),
  removeMember: (id: string, userId: string) =>
    apiFetch<void>(`/workspaces/${id}/members/${userId}`, { method: "DELETE" }),
  shareDataset: (datasetId: string, workspaceId: string | null) =>
    apiFetch<Dataset>(`/datasets/${datasetId}/workspace`, {
      method: "PUT",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),
  shareDashboard: (dashboardId: string, workspaceId: string | null) =>
    apiFetch<Dashboard>(`/dashboards/${dashboardId}/workspace`, {
      method: "PUT",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),
};

export type ShareLink = components["schemas"]["ShareOut"];
export type ShareCreated = components["schemas"]["ShareCreated"];
export type SharedView = {
  title: string;
  expires_at: string;
  content: Record<string, unknown>;
};

export const shares = {
  list: () => apiFetch<ShareLink[]>("/shares"),
  create: (kind: "dashboard" | "run", targetId: string, days: number) =>
    apiFetch<ShareCreated>(
      "/shares",
      json({ kind, target_id: targetId, expires_in_days: days, acknowledged: true }),
    ),
  revoke: (id: string) => apiFetch<void>(`/shares/${id}`, { method: "DELETE" }),
  // Public: no sign-in token is sent, and a 401 never redirects to the login page.
  view: async (secret: string): Promise<SharedView> => {
    const response = await fetch(`${API_BASE}/public/shares/view`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secret }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(response.status, errorMessage(body.detail, "Not found"));
    return body as SharedView;
  },
};

export const shareUrl = (secret: string) =>
  `${typeof window === "undefined" ? "" : window.location.origin}/share#${secret}`;

// Fields with server-side defaults are optional when sending.
export type DashboardItemIn = Partial<components["schemas"]["DashboardItemIn"]> &
  Pick<components["schemas"]["DashboardItemIn"], "kind">;

export const dashboards = {
  list: () => apiFetch<Dashboard[]>("/dashboards"),
  get: (id: string) => apiFetch<Dashboard>(`/dashboards/${id}`),
  create: (name: string, description = "") =>
    apiFetch<Dashboard>("/dashboards", json({ name, description })),
  update: (id: string, name: string, description = "") =>
    apiFetch<Dashboard>(`/dashboards/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name, description }),
    }),
  remove: (id: string) => apiFetch<void>(`/dashboards/${id}`, { method: "DELETE" }),
  addItem: (id: string, body: DashboardItemIn) =>
    apiFetch<DashboardItem>(`/dashboards/${id}/items`, json(body)),
  refresh: (id: string) => apiFetch<Dashboard>(`/dashboards/${id}/refresh`, { method: "POST" }),
  refreshItem: (id: string, itemId: string) =>
    apiFetch<DashboardItem>(`/dashboards/${id}/items/${itemId}/refresh`, { method: "POST" }),
  move: (id: string, itemId: string, direction: -1 | 1) =>
    apiFetch<Dashboard>(`/dashboards/${id}/items/${itemId}/move?direction=${direction}`, {
      method: "POST",
    }),
  removeItem: (id: string, itemId: string) =>
    apiFetch<void>(`/dashboards/${id}/items/${itemId}`, { method: "DELETE" }),
};

export const follows = {
  list: (datasetId: string) => apiFetch<Follow[]>(`/datasets/${datasetId}/follows`),
  create: (datasetId: string, body: FollowIn) =>
    apiFetch<Follow>(`/datasets/${datasetId}/follows`, json(body)),
  update: (id: string, body: FollowIn) =>
    apiFetch<Follow>(`/follows/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  remove: (id: string) => apiFetch<void>(`/follows/${id}`, { method: "DELETE" }),
  check: (id: string) => apiFetch<MetricCheck>(`/follows/${id}/check`, { method: "POST" }),
  acknowledge: (id: string, checkId: string) =>
    apiFetch<MetricCheck>(`/follows/${id}/checks/${checkId}/acknowledge`, { method: "POST" }),
  alerts: () => apiFetch<MetricCheck[]>("/metric-alerts"),
};

export const models = {
  save: (runId: string, position: number, name: string) =>
    apiFetch<SavedModel>(`/runs/${runId}/artifacts/${position}/model`, json({ name })),
  list: (datasetId: string) => apiFetch<SavedModel[]>(`/datasets/${datasetId}/models`),
  score: (id: string, versionId?: string) =>
    apiFetch<ModelScore>(`/models/${id}/score`, json({ version_id: versionId ?? null })),
  explain: (id: string, values: Record<string, unknown>) =>
    apiFetch<ModelExplanation>(`/models/${id}/explain`, json({ values })),
  downloadScores: (id: string, name: string) =>
    downloadBlob(`/models/${id}/scores/latest.csv`, `${name.replace(/[^\w-]+/g, "_")}-scores.csv`),
  remove: (id: string) => apiFetch<void>(`/models/${id}`, { method: "DELETE" }),
  saveSchedule: (id: string, body: { cron: string; timezone: string; enabled: boolean }) =>
    apiFetch<ModelSchedule>(`/models/${id}/schedule`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  removeSchedule: (id: string) => apiFetch<void>(`/models/${id}/schedule`, { method: "DELETE" }),
  runScheduleNow: (id: string) =>
    apiFetch<ModelScoring>(`/models/${id}/schedule/run-now`, { method: "POST" }),
  scorings: (id: string) => apiFetch<ModelScoring[]>(`/models/${id}/scorings`),
  acknowledge: (id: string, scoringId: string) =>
    apiFetch<ModelScoring>(`/models/${id}/scorings/${scoringId}/acknowledge`, { method: "POST" }),
  alerts: () => apiFetch<ModelScoring[]>("/model-alerts"),
};

export const schedules = {
  list: () => apiFetch<Schedule[]>("/schedules"),
  create: (body: Record<string, unknown>) => apiFetch<Schedule>("/schedules", json(body)),
  update: (id: string, body: Record<string, unknown>) =>
    apiFetch<Schedule>(`/schedules/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => apiFetch<void>(`/schedules/${id}`, { method: "DELETE" }),
  runNow: (id: string) => apiFetch<Run>(`/schedules/${id}/run-now`, { method: "POST" }),
};
