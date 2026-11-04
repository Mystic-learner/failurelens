import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { EventStream } from "./eventStream";
import type { StreamStatus } from "./eventStream";
import type { ServerEvent } from "./types";

type Listener = (event: ServerEvent) => void;

interface EventsValue {
  status: StreamStatus;
  subscribe: (fn: Listener) => () => void;
}

const EventsContext = createContext<EventsValue>({
  status: "stopped",
  subscribe: () => () => undefined,
});

function wsUrl(cursor: number | null): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const q = cursor === null ? "" : `?last_event_id=${cursor}`;
  return `${proto}//${window.location.host}/ws/events${q}`;
}

/** One shared WebSocket for the whole app. */
export function EventsProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const listeners = useRef(new Set<Listener>());

  useEffect(() => {
    const stream = new EventStream({
      urlFor: wsUrl,
      onEvent: (e) => listeners.current.forEach((fn) => fn(e)),
      onStatus: setStatus,
    });
    stream.start();
    return () => stream.stop();
  }, []);

  const subscribe = (fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  };

  return <EventsContext.Provider value={{ status, subscribe }}>{children}</EventsContext.Provider>;
}

export function useEventsStatus(): StreamStatus {
  return useContext(EventsContext).status;
}

export function useEventSubscription(fn: Listener): void {
  const { subscribe } = useContext(EventsContext);
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => subscribe((e) => latest.current(e)), [subscribe]);
}