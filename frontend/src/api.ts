import type {
  Chunk,
  DiagnoseResponse,
  HealthInfo,
  Incident,
  IncidentsPage,
  Metrics,
  Signature,
} from "./types";

export const BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  status: number;
  retryAfter: number | null;

  constructor(message: string, status: number, retryAfter: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** Pull a human message out of whatever JSON error shape the server sent. */
export function extractMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.detail === "string") return b.detail;
    if (Array.isArray(b.detail) && b.detail.length > 0) {
      const first = b.detail[0] as Record<string, unknown> | null;
      if (first && typeof first.msg === "string") return first.msg;
    }
    const err = b.error;
    if (typeof err === "string") return err;
    if (err && typeof err === "object") {
      const m = (err as Record<string, unknown>).message;
      if (typeof m === "string") return m;
    }
    if (typeof b.message === "string") return b.message;
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the server", 0, null);
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    const ra = Number(res.headers.get("retry-after"));
    throw new ApiError(
      extractMessage(body, `Request failed (${res.status})`),
      res.status,
      Number.isFinite(ra) && ra > 0 ? ra : null,
    );
  }
  return (await res.json()) as T;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.retryAfter !== null ? `${err.message} (retry in ${err.retryAfter}s)` : err.message;
  }
  if (err instanceof Error) return err.message;
  return "Unexpected error";
}

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export function diagnose(query: string): Promise<DiagnoseResponse> {
  return request<DiagnoseResponse>("/diagnose", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
}

export function listIncidents(opts: {
  status?: string;
  limit?: number;
  beforeId?: number;
}): Promise<IncidentsPage> {
  return request<IncidentsPage>(
    `/incidents${qs({ status: opts.status, limit: opts.limit, before_id: opts.beforeId })}`,
  );
}

export function getIncident(id: number | string): Promise<Incident> {
  return request<Incident>(`/incidents/${encodeURIComponent(String(id))}`);
}

export function listSignatures(opts: {
  service?: string;
  sort?: "count" | "last_seen";
  limit?: number;
}): Promise<{ items: Signature[] }> {
  return request<{ items: Signature[] }>(`/signatures${qs(opts)}`);
}

export function getChunk(id: number | string): Promise<Chunk> {
  return request<Chunk>(`/chunks/${encodeURIComponent(String(id))}`);
}

export function getMetrics(): Promise<Metrics> {
  return request<Metrics>("/metrics");
}

/** /health answers 503 with a valid body when the database is down, so the body is read either way. */
export async function getHealth(): Promise<HealthInfo> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/health`);
  } catch {
    throw new ApiError("Cannot reach the server", 0, null);
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const b = body as Partial<HealthInfo> | null;
  if (b && typeof b.status === "string" && b.database && b.queue && b.embedding) {
    return b as HealthInfo;
  }
  throw new ApiError(extractMessage(body, `Request failed (${res.status})`), res.status, null);
}