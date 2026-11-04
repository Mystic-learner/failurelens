export type Confidence = "high" | "medium" | "low" | "inconclusive";
export type IncidentStatus = "open" | "diagnosing" | "diagnosed" | "resolved";

export interface Evidence {
  chunk_id: string;
  quoted_line: string;
}

export interface DiagnosisResult {
  root_cause: string;
  confidence: Confidence;
  evidence: Evidence[];
  similar_incidents: string[];
  suggested_fix: string;
  next_checks: string[];
}

export interface DiagnosisDoc {
  status?: "answered" | "inconclusive" | "failed";
  result?: DiagnosisResult | null;
  dropped_evidence?: number;
  degraded?: boolean;
  retrieval_reason?: string;
  error?: string | null;
  attempts?: number;
  last_error?: string;
}

export interface Incident {
  id: number;
  signature_id: number;
  trigger: string;
  status: IncidentStatus;
  degraded: boolean;
  diagnosis_deferred: boolean;
  created_at: string;
  updated_at: string;
  diagnosed_at: string | null;
  resolved_at: string | null;
  diagnosis: DiagnosisDoc | null;
  service: string;
  level: string | null;
  signature_count: number;
}

export interface IncidentsPage {
  items: Incident[];
  next_before_id: number | null;
}

export interface Signature {
  id: number;
  service: string;
  fingerprint: string;
  level: string | null;
  count: number;
  first_seen: string;
  last_seen: string;
  exemplar_chunk_id: number | null;
  latest_incident_id: number | null;
}

export interface Hit {
  chunk_id: number;
  service: string | null;
  level: string | null;
  score: number;
  fingerprint_match: boolean;
  text: string;
}

export interface DiagnoseResponse {
  status: "answered" | "inconclusive";
  degraded: boolean;
  retrieval_reason: string;
  result: DiagnosisResult | null;
  dropped_evidence: number;
  tokens: { prompt: number; completion: number };
  hits: Hit[];
}

export interface Chunk {
  id: number;
  text: string;
  service: string | null;
  level: string | null;
  ts: string | null;
}

export interface ServerEvent {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}