import { useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { diagnose, errorMessage } from "../api";
import { Card, ErrorBox, EvidenceView, Loading } from "../components";
import { shorten } from "../format";
import type { DiagnoseResponse } from "../types";

const MAX_CHARS = 20000;

export default function DiagnosePage() {
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<DiagnoseResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useRef(0);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    const mine = ++run.current;
    setPhase("loading");
    setError(null);
    try {
      const r = await diagnose(q);
      if (mine !== run.current) return;
      setResult(r);
      setPhase("done");
    } catch (err) {
      if (mine !== run.current) return;
      setError(errorMessage(err));
      setPhase("error");
    }
  }

  const chunks: Record<string, string> = {};
  for (const h of result?.hits ?? []) chunks[String(h.chunk_id)] = h.text;

  return (
    <div className="space-y-4">
      <form onSubmit={submit} className="space-y-2">
        <label htmlFor="log" className="block text-sm font-medium text-slate-300">
          Paste an error or log snippet
        </label>
        <textarea
          id="log"
          value={query}
          maxLength={MAX_CHARS}
          onChange={(e) => setQuery(e.target.value)}
          rows={8}
          placeholder="2024-01-01 10:00:00 ERROR payment-service: connection reset by peer (ECONNRESET)"
          className="w-full rounded-md border border-slate-700 bg-slate-900 p-3 font-mono text-sm text-slate-100 placeholder:text-slate-600 focus:border-sky-500 focus:outline-none"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-slate-500">
            {query.length}/{MAX_CHARS}
          </span>
          <button
            type="submit"
            disabled={phase === "loading" || query.trim().length === 0}
            className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {phase === "loading" ? "Diagnosing…" : "Diagnose"}
          </button>
        </div>
      </form>

      {phase === "loading" ? <Loading label="Searching logs and diagnosing…" /> : null}
      {phase === "error" && error ? <ErrorBox message={error} /> : null}

      {phase === "done" && result ? (
        <div className="space-y-4">
          {result.status === "inconclusive" ? (
            <div className="rounded-lg border border-slate-600 bg-slate-800/50 p-4">
              <p className="font-medium text-slate-100">Inconclusive</p>
              <p className="mt-1 text-sm text-slate-400">
                There is not enough verifiable evidence in the indexed logs to name a cause.
                {result.retrieval_reason ? ` (${result.retrieval_reason})` : ""}
              </p>
            </div>
          ) : null}
          {result.degraded ? (
            <p className="rounded-md bg-amber-500/10 p-3 text-sm text-amber-200">
              Embeddings were unavailable, so this used keyword search only.
            </p>
          ) : null}
          {result.dropped_evidence > 0 ? (
            <p className="text-sm text-slate-500">
              {result.dropped_evidence} quoted line(s) did not match the logs verbatim and were
              dropped.
            </p>
          ) : null}

          {result.result ? <EvidenceView result={result.result} chunks={chunks} /> : null}

          {result.result && result.result.similar_incidents.length > 0 ? (
            <Card title="Similar past incidents">
              <ul className="flex flex-wrap gap-2">
                {result.result.similar_incidents.map((id) => (
                  <li key={id}>
                    <Link
                      to={`/incidents/${id}`}
                      className="rounded-md bg-slate-800 px-2 py-1 text-sm text-sky-300 hover:bg-slate-700"
                    >
                      Incident #{id}
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          {result.hits.length > 0 ? (
            <details className="rounded-lg border border-slate-800 p-3">
              <summary className="cursor-pointer text-sm text-slate-400">
                Retrieved chunks ({result.hits.length})
              </summary>
              <ul className="mt-3 space-y-2">
                {result.hits.map((h) => (
                  <li key={h.chunk_id} className="rounded-md bg-slate-900 p-2 text-xs">
                    <span className="text-slate-500">
                      #{h.chunk_id} · {h.service ?? "?"} · {h.level ?? "?"} · score{" "}
                      {h.score.toFixed(3)}
                      {h.fingerprint_match ? " · same fingerprint" : ""}
                    </span>
                    <pre className="mt-1 overflow-x-auto text-slate-400">{shorten(h.text, 400)}</pre>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}