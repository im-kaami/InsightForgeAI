import type { components } from "./api-types";

export type User = components["schemas"]["UserOut"];
export type Dataset = components["schemas"]["DatasetOut"];
export type Connection = components["schemas"]["ConnectionOut"];
export type Session = components["schemas"]["SessionOut"];
export type Run = components["schemas"]["RunOut"];
export type Schedule = components["schemas"]["ScheduleOut"];

const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = typeof window !== "undefined" ? localStorage.getItem("if_token") : null;
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`${base}${path}`, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("if_token");
      if (!location.pathname.startsWith("/login"))
        window.location.assign(new URL("/login", window.location.origin));
    }
    throw new ApiError(response.status, body.detail ?? response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const auth = {
  register: (email: string, password: string) =>
    apiFetch<User>("/auth/register", json({ email, password })),
  login: (email: string, password: string) =>
    apiFetch<components["schemas"]["Token"]>("/auth/login", json({ email, password })),
  me: () => apiFetch<User>("/auth/me"),
};

export const datasets = {
  list: () => apiFetch<Dataset[]>("/datasets"),
  get: (id: string) => apiFetch<Dataset>(`/datasets/${id}`),
  upload: (files: File[], name?: string) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    if (name) body.append("name", name);
    return apiFetch<Dataset>("/datasets/upload", { method: "POST", body });
  },
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
  preview: (id: string, table: string) =>
    apiFetch<{ columns: string[]; rows: Record<string, unknown>[] }>(
      `/datasets/${id}/preview?table=${encodeURIComponent(table)}&limit=50`,
    ),
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
  create: (dataset_id: string, title?: string) =>
    apiFetch<Session>("/sessions", json({ dataset_id, title })),
  remove: (id: string) => apiFetch<void>(`/sessions/${id}`, { method: "DELETE" }),
  createRun: (id: string, goal: string) => apiFetch<Run>(`/sessions/${id}/runs`, json({ goal })),
};

export const runs = {
  get: (id: string) => apiFetch<Run>(`/runs/${id}`),
  reportUrl: (id: string, format: string) => `${base}/runs/${id}/report?format=${format}`,
  downloadReport: async (id: string, format: string) => {
    const token = localStorage.getItem("if_token");
    const response = await fetch(`${base}/runs/${id}/report?format=${format}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(response.status, body.detail ?? response.statusText);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `insightforge-${id}.${format}`;
    anchor.click();
    URL.revokeObjectURL(url);
  },
};

export const schedules = {
  list: () => apiFetch<Schedule[]>("/schedules"),
  create: (body: Record<string, unknown>) => apiFetch<Schedule>("/schedules", json(body)),
  update: (id: string, body: Record<string, unknown>) =>
    apiFetch<Schedule>(`/schedules/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => apiFetch<void>(`/schedules/${id}`, { method: "DELETE" }),
  runNow: (id: string) => apiFetch<Run>(`/schedules/${id}/run-now`, { method: "POST" }),
};
