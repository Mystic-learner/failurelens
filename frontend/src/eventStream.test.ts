import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EventStream } from "./eventStream";
import type { SocketLike, StreamOptions, StreamStatus } from "./eventStream";

class FakeSocket implements SocketLike {
  static all: FakeSocket[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  closedWith: number | undefined;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeSocket.all.push(this);
  }
  close(code?: number): void {
    this.closedWith = code;
  }
  send(obj: unknown): void {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  drop(code = 1006): void {
    this.onclose?.({ code });
  }
}

const ev = (id: number) => ({
  kind: "event",
  id,
  type: "incident.opened",
  payload: { incident_id: id },
  created_at: "2024-01-01T00:00:00Z",
});

let events: number[];
let statuses: StreamStatus[];

function make(extra: Partial<StreamOptions> = {}): EventStream {
  return new EventStream({
    urlFor: (c) => `ws://t/ws/events${c === null ? "" : `?last_event_id=${c}`}`,
    onEvent: (e) => events.push(e.id),
    onStatus: (s) => statuses.push(s),
    socketFactory: (u) => new FakeSocket(u),
    random: () => 1,
    baseDelayMs: 1000,
    maxDelayMs: 8000,
    rewind: 0,
    ...extra,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.all = [];
  events = [];
  statuses = [];
});
afterEach(() => vi.useRealTimers());

describe("EventStream", () => {
  it("delivers each id once, including a late lower id", () => {
    const s = make();
    s.start();
    const sock = FakeSocket.all[0]!;
    [1, 2, 2, 12, 11, 11].forEach((id) => sock.send(ev(id)));
    expect(events).toEqual([1, 2, 12, 11]);
  });

  it("goes connecting -> live on ready", () => {
    const s = make();
    s.start();
    FakeSocket.all[0]!.send({ kind: "ready" });
    expect(statuses).toEqual(["connecting", "live"]);
  });

  it("backs off exponentially and caps the delay", () => {
    make().start();
    const drop = (n: number) => FakeSocket.all[n]!.drop();
    drop(0);
    vi.advanceTimersByTime(999);
    expect(FakeSocket.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(2);
    drop(1);
    vi.advanceTimersByTime(1999);
    expect(FakeSocket.all).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(3);
    drop(2);
    vi.advanceTimersByTime(4000);
    expect(FakeSocket.all).toHaveLength(4);
    drop(3);
    vi.advanceTimersByTime(8000);
    expect(FakeSocket.all).toHaveLength(5);
    drop(4);
    vi.advanceTimersByTime(7999);
    expect(FakeSocket.all).toHaveLength(5);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(6); // capped at 8000, not 16000
  });

  it("resets the backoff after a successful ready", () => {
    make().start();
    FakeSocket.all[0]!.drop();
    vi.advanceTimersByTime(1000);
    FakeSocket.all[1]!.send({ kind: "ready" });
    FakeSocket.all[1]!.drop();
    vi.advanceTimersByTime(999);
    expect(FakeSocket.all).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(3);
  });

  it("resumes with last_event_id minus the rewind, clamped at zero", () => {
    make({ rewind: 50 }).start();
    const first = FakeSocket.all[0]!;
    expect(first.url).toBe("ws://t/ws/events");
    first.send(ev(100));
    first.drop();
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.all[1]!.url.endsWith("last_event_id=50")).toBe(true);
    FakeSocket.all[1]!.drop();
    vi.advanceTimersByTime(2000);
    FakeSocket.all[2]!.send(ev(30));
    expect(FakeSocket.all[2]!.url.endsWith("last_event_id=50")).toBe(true);

    FakeSocket.all = [];
    make({ rewind: 50 }).start();
    FakeSocket.all[0]!.send(ev(30));
    FakeSocket.all[0]!.drop();
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.all[1]!.url.endsWith("last_event_id=0")).toBe(true);
  });

  it("does not reconnect after a 1008 (bad cursor)", () => {
    make().start();
    FakeSocket.all[0]!.drop(1008);
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(statuses.at(-1)).toBe("failed");
  });

  it("reconnects at once to continue a truncated replay", () => {
    make().start();
    const first = FakeSocket.all[0]!;
    first.send(ev(1));
    first.send(ev(2));
    first.send({ kind: "replay_truncated", last_id: 2 });
    first.send({ kind: "ready" });
    expect(FakeSocket.all).toHaveLength(2);
    expect(first.closedWith).toBe(1000);
    expect(FakeSocket.all[1]!.url.endsWith("last_event_id=2")).toBe(true);
  });

  it("stop() prevents any reconnect", () => {
    const s = make();
    s.start();
    const sock = FakeSocket.all[0]!;
    s.stop();
    sock.drop();
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(statuses.at(-1)).toBe("stopped");
  });

  it("ignores malformed frames", () => {
    make().start();
    const sock = FakeSocket.all[0]!;
    sock.onmessage?.({ data: "not json" });
    sock.onmessage?.({ data: 123 });
    sock.send({ kind: "event", id: "x", type: "t" });
    sock.send({ kind: "mystery" });
    sock.send("just a string");
    expect(events).toEqual([]);
  });
});