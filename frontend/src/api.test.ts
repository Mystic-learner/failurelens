import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, diagnose, errorMessage, extractMessage, listIncidents } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("extractMessage", () => {
  it("handles the common server error shapes", () => {
    expect(extractMessage({ detail: "nope" }, "x")).toBe("nope");
    expect(extractMessage({ detail: [{ msg: "bad field" }] }, "x")).toBe("bad field");
    expect(extractMessage({ error: { message: "deep" } }, "x")).toBe("deep");
    expect(extractMessage({ error: "flat" }, "x")).toBe("flat");
    expect(extractMessage({ message: "top" }, "x")).toBe("top");
    expect(extractMessage(null, "fallback")).toBe("fallback");
    expect(extractMessage({ other: 1 }, "fallback")).toBe("fallback");
  });
});

describe("request errors", () => {
  it("surfaces a 429 with its Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "budget exhausted" }), {
          status: 429,
          headers: { "Retry-After": "30" },
        }),
      ),
    );
    const err = await diagnose("x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const api = err as ApiError;
    expect([api.status, api.retryAfter, api.message]).toEqual([429, 30, "budget exhausted"]);
    expect(errorMessage(api)).toContain("30s");
  });

  it("turns a network failure into status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("boom"))));
    const err = (await diagnose("x").catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(0);
  });

  it("copes with a non-JSON error body", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    const err = (await diagnose("x").catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe("Request failed (502)");
  });
});

describe("requests", () => {
  it("builds the incidents query string without empty params", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], next_before_id: null })));
    vi.stubGlobal("fetch", fetchMock);
    await listIncidents({ status: "open", limit: 50, beforeId: undefined });
    expect(fetchMock).toHaveBeenCalledWith("/api/incidents?status=open&limit=50", undefined);
  });

  it("posts the query as JSON", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ status: "inconclusive" })));
    vi.stubGlobal("fetch", fetchMock);
    await diagnose("hello");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/diagnose");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ query: "hello" });
  });
});