import type { ReactNode } from "react";
import { splitContext, trimContext } from "./context";
import type { Confidence, DiagnosisResult, IncidentStatus } from "./types";

const CONFIDENCE_STYLE: Record<Confidence, string> = {
  high: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  medium: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
  low: "bg-amber-500/15 text-amber-300 ring-amber-500/40",
  inconclusive: "bg-slate-500/20 text-slate-300 ring-slate-500/40",
};

const STATUS_STYLE: Record<IncidentStatus, string> = {
  open: "bg-rose-500/15 text-rose-300 ring-rose-500/40",
  diagnosing: "bg-sky-500/15 text-sky-300 ring-sky-500/40",
  diagnosed: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/40",
  resolved: "bg-slate-500/20 text-slate-300 ring-slate-500/40",
};

const PILL = "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1";

export function ConfidenceBadge({ value }: { value: Confidence }) {
  return <span className={`${PILL} ${CONFIDENCE_STYLE[value]}`}>{value}</span>;
}

export function StatusBadge({ value }: { value: IncidentStatus }) {
  return <span className={`${PILL} ${STATUS_STYLE[value]}`}>{value}</span>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-8 text-slate-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-sky-400" />
      {label}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-700 p-8 text-center">
      <p className="font-medium text-slate-300">{title}</p>
      {hint ? <p className="mt-1 text-sm text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-4">
      <p className="text-sm text-rose-200">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 rounded-md bg-rose-500/20 px-3 py-1 text-sm text-rose-100 hover:bg-rose-500/30"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/60 p-4">
      {title ? <h2 className="mb-2 text-sm font-semibold text-slate-300">{title}</h2> : null}
      {children}
    </section>
  );
}

/** Log content is untrusted: it is only ever rendered as React text, never as HTML. */
export function ChunkContext({ text, quote }: { text: string; quote: string }) {
  const lines = trimContext(splitContext(text, quote));
  return (
    <pre className="overflow-x-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-300">
      {lines.map((line, i) =>
        line.gap ? (
          <div key={i} className="italic text-slate-500">
            {line.text}
          </div>
        ) : (
          <div
            key={i}
            className={line.hit ? "-mx-3 border-l-2 border-amber-400 bg-amber-400/20 px-3 text-amber-100" : ""}
          >
            {line.text || "\u00a0"}
          </div>
        ),
      )}
    </pre>
  );
}

export function EvidenceView({
  result,
  chunks,
}: {
  result: DiagnosisResult;
  chunks: Record<string, string | undefined>;
}) {
  return (
    <div className="space-y-4">
      <Card title="Root cause">
        <div className="mb-2">
          <ConfidenceBadge value={result.confidence} />
        </div>
        <p className="whitespace-pre-wrap text-slate-100">{result.root_cause}</p>
      </Card>

      <Card title={`Evidence (${result.evidence.length})`}>
        {result.evidence.length === 0 ? (
          <p className="text-sm text-slate-500">No verified evidence lines.</p>
        ) : (
          <ul className="space-y-4">
            {result.evidence.map((ev, i) => {
              const text = chunks[ev.chunk_id];
              return (
                <li key={`${ev.chunk_id}-${i}`}>
                  <p className="mb-1 text-xs text-slate-500">chunk {ev.chunk_id}</p>
                  {text !== undefined ? (
                    <ChunkContext text={text} quote={ev.quoted_line} />
                  ) : (
                    <pre className="overflow-x-auto rounded-md bg-slate-950 p-3 text-xs text-amber-100">
                      {ev.quoted_line}
                    </pre>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {result.suggested_fix ? (
        <Card title="Suggested fix">
          <p className="whitespace-pre-wrap text-slate-200">{result.suggested_fix}</p>
        </Card>
      ) : null}

      {result.next_checks.length > 0 ? (
        <Card title="Next checks">
          <ul className="list-inside list-disc space-y-1 text-slate-200">
            {result.next_checks.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}