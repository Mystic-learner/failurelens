import { useEffect, useRef, useState } from "react";
import { errorMessage, getHealth, getMetrics } from "../api";
import { Card, EmptyState, ErrorBox, Loading } from "../components";
import type { HealthInfo, Metrics } from "../types";

const POLL_MS = 5000;

interface Poll<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  lastOk: Date | null;
}

/** Poll `fn` every `ms`. Old data stays on screen while a refresh runs or after it fails. */
function usePoll<T>(fn: () => Promise<T>, ms: number): Poll<T> & { reload: () => void } {
  const [state, setState] = useState<Poll<T>>({ data: null, error: null, loading: true, lastOk: null });
  const [nonce, setNonce] = useState(0);
  const latest = useRef(fn);
  latest.current = fn;

  useEffect(() => {
    let cancelled = false;
    const run = () => {
      latest
        .current()
        .then((data) => {
          if (!cancelled) setState({ data, error: null, loading: false, lastOk: new Date() });
        })
        .catch((err: unknown) => {
          if (!cancelled) setState((s) => ({ ...s, error: errorMessage(err), loading: false }));
        });
    };
    run();
    const timer = setInterval(run, ms);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [ms, nonce]);

  return { ...state, reload: () => setNonce((n) => n + 1) };
}

function fmtSeconds(v: number | null): string {
  if (v === null) return "-";
  if (v < 1) return `${Math.round(v * 1000)} ms`;
  if (v < 120) return `${v.toFixed(1)} s`;
  return `${(v / 60).toFixed(1)} min`;
}

function fmtPercent(v: number | null): string {
  return v === null ? "-" : `${(v * 100).toFixed(0)}%`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-1 font-mono text-xl text-slate-100">{value}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

function Banner({ health }: { health: HealthInfo }) {
  if (health.status === "ok") return null;
  const down = health.status === "unavailable";
  const text = down
    ? "Database unavailable. Ingestion and diagnosis cannot work until it is back."
    : !health.embedding.configured
      ? `Degraded: embedding provider "${health.embedding.provider}" is not configured, so search is keyword-only.`
      : "Degraded.";
  const style = down
    ? "border-rose-500/40 bg-rose-500/10 text-rose-200"
    : "border-amber-500/40 bg-amber-500/10 text-amber-200";
  return (
    <div role="alert" className={`rounded-lg border p-3 text-sm ${style}`}>
      {text}
    </div>
  );
}

function MetricsView({ m }: { m: Metrics }) {
  const noDiagnoses = m.diagnosis.diagnosed_24h === 0;
  const cap = m.llm.hourly_cap;
  const used = m.llm.tokens_used_this_hour;
  const usedPct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  return (
    <div className="space-y-4">
      <Card title="Ingest">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="Queue depth" value={`${m.queue.depth} / ${m.queue.max_size}`} />
          <Stat label="Ingest rate" value={`${m.ingest.lines_per_s_1m.toFixed(1)} /s`} hint="lines, last minute" />
          <Stat label="Batches (1h)" value={String(m.ingest.batches_committed_1h)} />
          <Stat
            label="Queue to commit p50"
            value={fmtSeconds(m.ingest.queue_to_commit.p50_s)}
            hint={`${m.ingest.queue_to_commit.count} samples since start`}
          />
          <Stat label="Queue to commit p95" value={fmtSeconds(m.ingest.queue_to_commit.p95_s)} />
          <Stat
            label="Worker failures"
            value={`${m.ingest.worker_failed} / ${m.ingest.worker_processed + m.ingest.worker_failed}`}
            hint="since start"
          />
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Queue-to-commit latency is kept in memory and resets when the server restarts.
        </p>
      </Card>

      <Card title="Diagnosis (last 24 hours)">
        {noDiagnoses ? (
          <EmptyState title="No diagnoses yet" hint="Latency and inconclusive rate appear after the first one." />
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Diagnosed" value={String(m.diagnosis.diagnosed_24h)} />
            <Stat label="p50" value={fmtSeconds(m.diagnosis.p50_s)} />
            <Stat label="p95" value={fmtSeconds(m.diagnosis.p95_s)} />
            <Stat label="Inconclusive" value={fmtPercent(m.diagnosis.inconclusive_rate)} />
          </div>
        )}
      </Card>

      <Card title="Incidents">
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Open" value={String(m.incidents.open)} />
          <Stat label="Diagnosing" value={String(m.incidents.diagnosing)} />
          <Stat label="Deferred" value={String(m.incidents.deferred)} hint="waiting for LLM budget" />
        </div>
      </Card>

      <Card title="LLM budget (this hour)">
        <p className="font-mono text-slate-100">
          {used.toLocaleString()} / {cap.toLocaleString()} tokens
        </p>
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            className={`h-full ${usedPct >= 90 ? "bg-rose-500" : "bg-sky-400"}`}
            style={{ width: `${usedPct}%` }}
          />
        </div>
      </Card>
    </div>
  );
}

export default function HealthPage() {
  const metrics = usePoll(getMetrics, POLL_MS);
  const health = usePoll(getHealth, POLL_MS);

  const stale = (metrics.error !== null && metrics.data !== null) || (health.error !== null && health.data !== null);
  const firstLoad = metrics.loading && health.loading && !metrics.data && !health.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-slate-100">System health</h1>
        {metrics.lastOk ? (
          <span className="text-xs text-slate-500">Updated {metrics.lastOk.toLocaleTimeString()}</span>
        ) : null}
      </div>

      {health.data ? <Banner health={health.data} /> : null}

      {stale ? (
        <div role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-200">
          Reconnecting... showing the last numbers received.
        </div>
      ) : null}

      {firstLoad ? <Loading /> : null}
      {!metrics.data && metrics.error ? <ErrorBox message={metrics.error} onRetry={metrics.reload} /> : null}
      {metrics.data ? <MetricsView m={metrics.data} /> : null}

      {health.data ? (
        <p className="text-xs text-slate-500">
          Database {health.data.database.ok ? "ok" : "down"}
          {health.data.database.latency_ms !== null ? ` (${health.data.database.latency_ms} ms)` : ""} · embeddings:{" "}
          {health.data.embedding.provider} ({health.data.embedding.configured ? "configured" : "not configured"})
        </p>
      ) : null}
    </div>
  );
}