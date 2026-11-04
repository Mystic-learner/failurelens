import type { ServerEvent } from "./types";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "failed" | "stopped";

export interface SocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: { code: number }) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  close(code?: number, reason?: string): void;
}

export interface StreamOptions {
  /** Build the socket URL. cursor is null on the very first connection. */
  urlFor: (cursor: number | null) => string;
  onEvent: (event: ServerEvent) => void;
  onStatus?: (status: StreamStatus) => void;
  socketFactory?: (url: string) => SocketLike;
  random?: () => number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  /**
   * Reconnect cursor = highest seen id minus this. The server can commit event ids out of
   * order, so resuming exactly at the max could skip a lower id that had not arrived yet.
   * Overlap is harmless because events are deduplicated by id.
   */
  rewind?: number;
  maxSeen?: number;
}

const defaultFactory = (url: string): SocketLike => new WebSocket(url) as unknown as SocketLike;

export class EventStream {
  private readonly opts: StreamOptions;
  private ws: SocketLike | null = null;
  private lastId = 0;
  private readonly seen = new Set<number>();
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private truncated = false;
  private everLive = false;

  constructor(opts: StreamOptions) {
    this.opts = opts;
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.detach();
    this.setStatus("stopped");
  }

  private setStatus(status: StreamStatus): void {
    this.opts.onStatus?.(status);
  }

  private cursor(): number | null {
    if (this.lastId <= 0) return null;
    return Math.max(0, this.lastId - (this.opts.rewind ?? 50));
  }

  private detach(): void {
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onclose = null;
      ws.onerror = null;
      ws.close(1000);
    }
  }

  private connect(): void {
    this.setStatus(this.everLive ? "reconnecting" : "connecting");
    const factory = this.opts.socketFactory ?? defaultFactory;
    const ws = factory(this.opts.urlFor(this.cursor()));
    this.ws = ws;
    ws.onmessage = (ev) => {
      if (this.ws === ws) this.handle(ev.data);
    };
    ws.onerror = () => undefined; // a close event always follows
    ws.onclose = (ev) => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.onClosed(ev.code);
    };
  }

  private onClosed(code: number): void {
    if (this.stopped) return;
    if (code === 1008) {
      this.setStatus("failed"); // the server rejected our cursor; retrying cannot help
      return;
    }
    const base = this.opts.baseDelayMs ?? 1000;
    const cap = this.opts.maxDelayMs ?? 30000;
    const random = this.opts.random ?? Math.random;
    const delay = Math.min(cap, base * 2 ** this.attempt) * (0.5 + 0.5 * random());
    this.attempt += 1;
    this.setStatus("reconnecting");
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.stopped) this.connect();
    }, delay);
  }

  private handle(data: unknown): void {
    if (typeof data !== "string") return;
    let frame: unknown;
    try {
      frame = JSON.parse(data);
    } catch {
      return;
    }
    if (!frame || typeof frame !== "object") return;
    const f = frame as Record<string, unknown>;
    if (f.kind === "event") {
      if (typeof f.id !== "number" || typeof f.type !== "string") return;
      if (this.seen.has(f.id)) return;
      this.seen.add(f.id);
      const max = this.opts.maxSeen ?? 5000;
      if (this.seen.size > max) {
        const oldest = this.seen.values().next().value;
        if (oldest !== undefined) this.seen.delete(oldest);
      }
      if (f.id > this.lastId) this.lastId = f.id;
      this.opts.onEvent({
        id: f.id,
        type: f.type,
        payload: (f.payload && typeof f.payload === "object" ? f.payload : {}) as Record<
          string,
          unknown
        >,
        created_at: typeof f.created_at === "string" ? f.created_at : "",
      });
    } else if (f.kind === "replay_truncated") {
      this.truncated = true;
    } else if (f.kind === "ready") {
      this.attempt = 0;
      this.everLive = true;
      this.setStatus("live");
      if (this.truncated) {
        // More history is waiting: reconnect right away and keep replaying from our cursor.
        this.truncated = false;
        this.detach();
        this.connect();
      }
    }
  }
}