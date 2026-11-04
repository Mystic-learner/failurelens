import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listSignatures } from "../api";
import { EmptyState, ErrorBox, Loading } from "../components";
import { formatTime, shorten } from "../format";
import { useLoad } from "../useLoad";

export default function SignaturesPage() {
  const [sort, setSort] = useState<"count" | "last_seen">("count");
  const [service, setService] = useState("");
  const [applied, setApplied] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setApplied(service.trim()), 300);
    return () => clearTimeout(t);
  }, [service]);

  const { data, error, loading, reload } = useLoad(
    () => listSignatures({ sort, service: applied || undefined, limit: 100 }),
    [sort, applied],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-slate-100">Recurring failures</h1>
        <div className="flex gap-2">
          <input
            aria-label="Filter by service"
            value={service}
            onChange={(e) => setService(e.target.value)}
            placeholder="service"
            className="w-32 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm"
          />
          <select
            aria-label="Sort"
            value={sort}
            onChange={(e) => setSort(e.target.value as "count" | "last_seen")}
            className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm"
          >
            <option value="count">most frequent</option>
            <option value="last_seen">most recent</option>
          </select>
        </div>
      </div>

      {error ? <ErrorBox message={error} onRetry={reload} /> : null}
      {loading && !data ? <Loading /> : null}
      {data && data.items.length === 0 ? (
        <EmptyState title="No signatures yet" hint="Ingest some logs and they appear here." />
      ) : null}

      {data && data.items.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="bg-slate-900 text-slate-400">
              <tr>
                <th className="p-2">Service</th>
                <th className="p-2">Level</th>
                <th className="p-2 text-right">Count</th>
                <th className="p-2">First seen</th>
                <th className="p-2">Last seen</th>
                <th className="p-2">Fingerprint</th>
                <th className="p-2">Incident</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((s) => (
                <tr key={s.id} className="border-t border-slate-800">
                  <td className="p-2">{s.service}</td>
                  <td className="p-2">{s.level ?? "-"}</td>
                  <td className="p-2 text-right font-mono">{s.count}</td>
                  <td className="p-2">{formatTime(s.first_seen)}</td>
                  <td className="p-2">{formatTime(s.last_seen)}</td>
                  <td className="p-2 font-mono text-xs text-slate-500">{shorten(s.fingerprint, 12)}</td>
                  <td className="p-2">
                    {s.latest_incident_id !== null ? (
                      <Link to={`/incidents/${s.latest_incident_id}`} className="text-sky-300 hover:underline">
                        #{s.latest_incident_id}
                      </Link>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}