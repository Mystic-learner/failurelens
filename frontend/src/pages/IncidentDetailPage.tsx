import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getChunk, getIncident } from "../api";
import { Card, ErrorBox, EvidenceView, Loading, StatusBadge } from "../components";
import { useEventSubscription } from "../events";
import { formatTime } from "../format";
import { useLoad } from "../useLoad";

export default function IncidentDetailPage() {
  const { id = "" } = useParams();
  const { data, error, loading, reload } = useLoad(() => getIncident(id), [id]);
  const [chunks, setChunks] = useState<Record<string, string | undefined>>({});

  useEventSubscription((e) => {
    if (String(e.payload.incident_id) === id) reload();
  });

  const evidenceIds = (data?.diagnosis?.result?.evidence ?? []).map((e) => e.chunk_id);
  const idsKey = [...new Set(evidenceIds)].join(",");

  useEffect(() => {
    let cancelled = false;
    const ids = idsKey ? idsKey.split(",") : [];
    void Promise.all(
      ids.map((cid) =>
        getChunk(cid)
          .then((c) => [cid, c.text] as const)
          .catch(() => [cid, undefined] as const),
      ),
    ).then((pairs) => {
      if (!cancelled) setChunks(Object.fromEntries(pairs));
    });
    return () => {
      cancelled = true;
    };
  }, [idsKey]);

  if (loading && !data) return <Loading />;
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />;
  if (!data) return null;

  const doc = data.diagnosis;
  return (
    <div className="space-y-4">
      <Link to="/incidents" className="text-sm text-sky-300 hover:underline">
        ← All incidents
      </Link>
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold text-slate-100">Incident #{data.id}</h1>
        <StatusBadge value={data.status} />
      </div>
      <Card>
        <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-slate-500">Service</dt>
            <dd>{data.service}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Trigger</dt>
            <dd>{data.trigger.replace("_", " ")}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Opened</dt>
            <dd>{formatTime(data.created_at)}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Occurrences</dt>
            <dd>{data.signature_count}</dd>
          </div>
        </dl>
      </Card>

      {data.status === "open" || data.status === "diagnosing" ? (
        <Card>
          <p className="text-sm text-slate-400">
            {data.diagnosis_deferred
              ? "Diagnosis is waiting for the hourly LLM budget to reset."
              : "Diagnosis in progress. This page updates automatically."}
          </p>
          {doc?.last_error ? (
            <p className="mt-1 text-xs text-slate-500">
              Attempt {doc.attempts}: {doc.last_error}
            </p>
          ) : null}
        </Card>
      ) : null}

      {doc?.status === "failed" ? (
        <ErrorBox message={`Diagnosis failed after ${doc.attempts ?? "several"} attempts: ${doc.error ?? "unknown error"}`} />
      ) : null}

      {doc?.status === "inconclusive" || (doc?.result && doc.result.confidence === "inconclusive") ? (
        <div className="rounded-lg border border-slate-600 bg-slate-800/50 p-4 text-sm text-slate-300">
          Inconclusive: the retrieved logs do not contain verifiable evidence for a cause.
        </div>
      ) : null}
      {doc?.degraded ? (
        <p className="text-sm text-amber-200">Diagnosed with keyword search only (embeddings were down).</p>
      ) : null}

      {doc?.result ? <EvidenceView result={doc.result} chunks={chunks} /> : null}

      {doc?.result && doc.result.similar_incidents.length > 0 ? (
        <Card title="Similar past incidents">
          <ul className="flex flex-wrap gap-2">
            {doc.result.similar_incidents.map((sid) => (
              <li key={sid}>
                <Link to={`/incidents/${sid}`} className="rounded-md bg-slate-800 px-2 py-1 text-sm text-sky-300 hover:bg-slate-700">
                  Incident #{sid}
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}