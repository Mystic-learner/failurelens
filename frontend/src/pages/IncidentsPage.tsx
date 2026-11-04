import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { errorMessage, listIncidents } from "../api";
import { ConfidenceBadge, EmptyState, ErrorBox, Loading, StatusBadge } from "../components";
import { useEventSubscription } from "../events";
import { formatTime } from "../format";
import type { Incident } from "../types";

const STATUSES = ["", "open", "diagnosing", "diagnosed", "resolved"];

function upsert(current: Incident[], incoming: Incident[]): Incident[] {
  const byId = new Map<number, Incident>();
  for (const i of current) byId.set(i.id, i);
  for (const i of incoming) byId.set(i.id, i);
  return [...byId.values()].sort((a, b) => b.id - a.id);
}

export default function IncidentsPage() {
  const [status, setStatus] = useState("");
  const [items, setItems] = useState<Incident[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadFirst = useCallback(
    async (replace: boolean) => {
      const mine = ++generation.current;
      if (replace) setLoading(true);
      try {
        const page = await listIncidents({ status: status || undefined, limit: 50 });
        if (mine !== generation.current) return;
        setItems((cur) => (replace ? page.items : upsert(cur, page.items)));
        if (replace) setNextBefore(page.next_before_id);
        setError(null);
      } catch (err) {
        if (mine === generation.current) setError(errorMessage(err));
      } finally {
        if (mine === generation.current) setLoading(false);
      }
    },
    [status],
  );

  useEffect(() => {
    setItems([]);
    void loadFirst(true);
    return () => {
      generation.current += 1;
    };
  }, [loadFirst]);

  useEffect(
    () => () => {
      if (debounce.current !== null) clearTimeout(debounce.current);
    },
    [],
  );

  // Live updates: any incident event triggers one debounced refetch of the first page.
  useEventSubscription((event) => {
    if (!event.type.startsWith("incident.")) return;
    if (debounce.current !== null) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => void loadFirst(false), 300);
  });

  async function loadMore() {
    if (nextBefore === null) return;
    try {
      const page = await listIncidents({
        status: status || undefined,
        limit: 50,
        beforeId: nextBefore,
      });
      setItems((cur) => upsert(cur, page.items));
      setNextBefore(page.next_before_id);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-lg font-semibold text-slate-100">Incidents</h1>
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s === "" ? "all statuses" : s}
            </option>
          ))}
        </select>
      </div>

      {error ? <ErrorBox message={error} onRetry={() => void loadFirst(true)} /> : null}
      {loading && items.length === 0 ? <Loading /> : null}
      {!loading && !error && items.length === 0 ? (
        <EmptyState
          title="No incidents yet"
          hint="Incidents open automatically when a new error signature or a rate spike is detected."
        />
      ) : null}

      <ul className="space-y-2">
        {items.map((i) => {
          const conf = i.diagnosis?.result?.confidence;
          return (
            <li key={i.id}>
              <Link
                to={`/incidents/${i.id}`}
                className="block rounded-lg border border-slate-800 bg-slate-900/60 p-3 hover:border-slate-600"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-slate-400">#{i.id}</span>
                  <StatusBadge value={i.status} />
                  {conf ? <ConfidenceBadge value={conf} /> : null}
                  {i.diagnosis_deferred ? (
                    <span className="text-xs text-amber-300">waiting for LLM budget</span>
                  ) : null}
                  <span className="ml-auto text-xs text-slate-500">{formatTime(i.created_at)}</span>
                </div>
                <p className="mt-1 text-sm text-slate-300">
                  {i.service} · {i.level ?? "?"} · {i.trigger.replace("_", " ")} ·{" "}
                  {i.signature_count} occurrences
                </p>
                {i.diagnosis?.result ? (
                  <p className="mt-1 line-clamp-2 text-sm text-slate-400">
                    {i.diagnosis.result.root_cause}
                  </p>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>

      {nextBefore !== null ? (
        <button
          type="button"
          onClick={() => void loadMore()}
          className="w-full rounded-md border border-slate-700 py-2 text-sm text-slate-300 hover:bg-slate-900"
        >
          Load older
        </button>
      ) : null}
    </div>
  );
}