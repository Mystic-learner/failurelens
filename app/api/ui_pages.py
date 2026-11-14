# Embedded HTML/CSS/JS has long lines by nature.
# ruff: noqa: E501
"""Sub-pages (Documents, Incidents, Signatures, Status) sharing one HTML shell."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FailureLens - __TITLE__</title>
<style>
  :root { --bg: #121316; --panel: #1b1d21; --panel2: #22252a; --text: #f1f2f4; --muted: #98a2ad; --line: #30353c; --coral: #ff6f61; --coral-soft: #ffa28f; --ok: #5fd49a; }
  * { box-sizing: border-box; }
  html { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font-family: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; -webkit-font-smoothing: antialiased; overflow-x: hidden; }
  .bg { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
  .fl-grid { position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px); background-size: 48px 48px; -webkit-mask-image: radial-gradient(ellipse at 50% 30%, #000 20%, transparent 75%); mask-image: radial-gradient(ellipse at 50% 30%, #000 20%, transparent 75%); }
  .fl-scan { position: absolute; left: 0; right: 0; top: -160px; height: 160px; background: linear-gradient(transparent, rgba(255,111,97,0.07)); border-bottom: 1px solid rgba(255,111,97,0.28); animation: flscan 9s linear infinite; }
  @keyframes flscan { to { transform: translateY(calc(100vh + 160px)); } }
  .top { position: relative; z-index: 2; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 14px; max-width: 1240px; margin: 0 auto; padding: 22px clamp(18px, 4vw, 40px) 0; }
  .brand { font-size: 1.5rem; font-weight: 800; letter-spacing: -0.03em; color: var(--text); text-decoration: none; }
  .brand span { color: var(--coral); text-shadow: 0 0 24px rgba(255,111,97,0.6); }
  .nav { display: flex; flex-wrap: wrap; gap: 8px; }
  .nav a { color: var(--muted); text-decoration: none; padding: 8px 16px; border: 1px solid var(--line); border-radius: 999px; font-size: 0.92rem; background: rgba(255,255,255,0.03); transition: all 0.25s; }
  .nav a:hover { color: var(--text); border-color: rgba(255,111,97,0.6); box-shadow: 0 0 22px -4px rgba(255,111,97,0.55); }
  .nav a.active { color: #fff; border-color: rgba(255,111,97,0.7); background: rgba(255,111,97,0.12); }
  .nav a[href="/ui/demo"] { color: var(--coral-soft); border-color: rgba(255,111,97,0.7); }
  main { position: relative; z-index: 1; max-width: 1100px; margin: 0 auto; padding: 34px clamp(18px, 4vw, 40px) 90px; }
  h1 { margin: 0; font-size: clamp(1.8rem, 4vw, 2.6rem); font-weight: 800; letter-spacing: -0.03em; }
  .sub { color: var(--muted); margin: 8px 0 28px; }
  .note { color: var(--muted); font-size: 0.85rem; margin-top: 18px; }
  .list { display: grid; gap: 12px; }
  .row { display: flex; align-items: center; gap: 16px; background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 16px 20px; transition: all 0.3s; }
  .row:hover { border-color: rgba(255,111,97,0.45); box-shadow: 0 0 40px -10px rgba(255,111,97,0.45); }
  .grow { flex: 1; min-width: 0; }
  .name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .meta { color: var(--muted); font-size: 0.85rem; margin-top: 3px; }
  button { font: inherit; cursor: pointer; }
  .danger { background: transparent; color: var(--coral-soft); border: 1px solid rgba(255,111,97,0.5); border-radius: 12px; padding: 9px 16px; transition: all 0.2s; }
  .danger:hover { background: var(--coral); color: #1a0f0d; }
  .danger:disabled { opacity: 0.6; cursor: wait; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 20px; padding: 20px; min-width: 0; transition: all 0.3s; }
  .card:hover { border-color: rgba(255,111,97,0.45); box-shadow: 0 0 50px -10px rgba(255,111,97,0.45); }
  .card h3 { margin: 0 0 10px; font-size: 1.05rem; overflow: hidden; text-overflow: ellipsis; }
  .tags { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
  .badge { font-size: 0.75rem; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); }
  .badge.hot { color: var(--coral-soft); border-color: rgba(255,111,97,0.5); }
  pre { margin: 8px 0 0; white-space: pre-wrap; word-break: break-word; font: 0.82rem/1.65 ui-monospace, "Cascadia Code", Consolas, monospace; color: #d9dde2; background: rgba(0,0,0,0.22); border: 1px solid rgba(255,255,255,0.04); border-radius: 12px; padding: 12px 14px; max-height: 220px; overflow: auto; scrollbar-width: thin; scrollbar-color: #4a5460 transparent; }
  .lbl { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 12px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 16px; }
  .tile { background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 18px; }
  .tile .lbl { margin: 0 0 8px; }
  .val { font-size: 1.8rem; font-weight: 700; }
  .tile.ok .val { color: var(--ok); }
  .tile.bad .val { color: var(--coral); }
  .tsub { color: var(--muted); font-size: 0.82rem; margin-top: 6px; }
  .empty { max-width: 560px; margin: 30px auto; text-align: center; background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 40px 28px; box-shadow: 0 0 60px -14px rgba(255,111,97,0.35); }
  .empty strong { display: block; font-size: 1.25rem; margin-bottom: 8px; }
  .empty p { margin: 0; color: var(--muted); line-height: 1.6; }
  .empty.warn { border-color: rgba(255,111,97,0.6); }
  .demo { display: flex; flex-direction: column; align-items: center; gap: 22px; }
  .breakbtn { background: var(--coral); color: #1a0f0d; border: 0; border-radius: 18px; padding: 22px 64px; font-size: 1.5rem; font-weight: 800; letter-spacing: 0.02em; box-shadow: 0 0 50px -4px rgba(255,111,97,0.8); transition: transform 0.2s, box-shadow 0.3s; }
  .breakbtn:hover { transform: translateY(-2px); box-shadow: 0 0 70px 0 rgba(255,111,97,0.95); }
  .breakbtn:disabled { opacity: 0.6; cursor: wait; transform: none; }
  .stage { color: var(--muted); text-align: center; min-height: 1.4em; }
  .stage.hot { color: var(--coral-soft); }
  .console { width: 100%; max-width: 820px; height: 300px; overflow: auto; background: rgba(0,0,0,0.3); border: 1px solid var(--line); border-radius: 16px; padding: 14px 16px; font: 0.8rem/1.7 ui-monospace, "Cascadia Code", Consolas, monospace; color: #b6bec7; scrollbar-width: thin; scrollbar-color: #4a5460 transparent; white-space: pre-wrap; word-break: break-word; }
  .console .err { color: var(--coral); }
  .gobtn { display: inline-block; margin-top: 16px; padding: 11px 22px; border-radius: 12px; border: 1px solid rgba(255,111,97,0.7); color: #fff; text-decoration: none; background: rgba(255,111,97,0.12); }
  .result { width: 100%; }
  @media (max-width: 600px) { .row { flex-wrap: wrap; } }
  @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; transition: none !important; } }
</style>
<script src="/ui/auth.js"></script>
</head>
<body>
<div class="bg"><i class="fl-grid"></i><i class="fl-scan"></i></div>
<header class="top"><a class="brand" href="/">Failure<span>Lens</span></a><nav class="nav" id="nav"></nav></header>
<main>
  <h1>__TITLE__</h1>
  <p class="sub" id="sub"></p>
  <div id="out"></div>
</main>
<script>
const PAGE = "__PAGE__";
const out = document.getElementById("out");
const sub = document.getElementById("sub");

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

const NAV = [["/", "Search", "search"], ["/ui/documents", "Documents", "documents"], ["/ui/incidents", "Incidents", "incidents"], ["/ui/signatures", "Signatures", "signatures"], ["/ui/status", "Status", "status"], ["/ui/demo", "Break it", "demo"]];
NAV.forEach((n) => {
  const a = el("a", n[2] === PAGE ? "active" : "", n[1]);
  a.href = n[0];
  document.getElementById("nav").appendChild(a);
});

function fmt(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleString();
}
function num(v, d) {
  return typeof v === "number" ? v.toLocaleString(undefined, { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }) : "-";
}
function show(v) {
  if (v === null || v === undefined) return "-";
  if (typeof v === "string") return v;
  if (Array.isArray(v) && v.every((x) => typeof x !== "object")) return v.join(", ");
  return JSON.stringify(v, null, 2);
}
async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(url + " returned " + r.status);
  const t = await r.text();
  try { return t ? JSON.parse(t) : null; } catch (e) { return null; }
}
function empty(title, msg) {
  const c = el("div", "empty");
  c.appendChild(el("strong", "", title));
  c.appendChild(el("p", "", msg));
  out.replaceChildren(c);
}
function fail(err) {
  const c = el("div", "empty warn");
  c.appendChild(el("strong", "", "Could not load this page"));
  c.appendChild(el("p", "", String(err.message || err)));
  out.replaceChildren(c);
}

async function pageDocuments() {
  sub.textContent = "Logs you uploaded. Deleting one also removes its searchable chunks.";
  const d = await api("/documents");
  const items = (d && d.items) || [];
  if (!items.length) { empty("No documents yet", "Upload a log file on the Search page and it will show up here."); return; }
  const list = el("div", "list");
  items.forEach((it) => {
    const row = el("div", "row");
    const main = el("div", "grow");
    main.appendChild(el("div", "name", it.filename));
    main.appendChild(el("div", "meta", num(it.chunks) + " chunks - uploaded " + fmt(it.created_at)));
    row.appendChild(main);
    const btn = el("button", "danger", "Delete");
    btn.type = "button";
    let armed = false;
    let timer = null;
    btn.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        btn.textContent = "Click again to confirm";
        timer = setTimeout(() => { armed = false; btn.textContent = "Delete"; }, 3000);
        return;
      }
      clearTimeout(timer);
      btn.disabled = true;
      btn.textContent = "Deleting...";
      try { await api("/documents/" + it.id, { method: "DELETE" }); await load(); }
      catch (err) { btn.disabled = false; btn.textContent = "Delete failed"; }
    });
    row.appendChild(btn);
    list.appendChild(row);
  });
  out.replaceChildren(list);
  if (d.next_before_id) out.appendChild(el("p", "note", "Showing the first " + items.length + " documents."));
}

async function pageIncidents() {
  sub.textContent = "Failures FailureLens has recorded.";
  const d = await api("/incidents");
  const items = (d && d.items) || [];
  if (!items.length) { empty("No incidents yet", "When FailureLens records an incident it will appear here."); return; }
  const grid = el("div", "grid");
  items.forEach((it) => {
    const card = el("article", "card");
    card.appendChild(el("h3", "", "#" + it.id + (it.service ? " - " + it.service : "")));
    const tags = el("div", "tags");
    const dstat = it.diagnosis && typeof it.diagnosis === "object" ? it.diagnosis.status : null;
    const label = dstat === "answered" ? "diagnosed" : dstat === "failed" ? "diagnosis failed" : it.status;
    if (label) tags.appendChild(el("span", dstat === "failed" ? "badge" : "badge hot", label));
    if (it.level) tags.appendChild(el("span", "badge", it.level));
    if (it.diagnosis_deferred) tags.appendChild(el("span", "badge", "deferred"));
    if (it.degraded) tags.appendChild(el("span", "badge", "degraded"));
    card.appendChild(tags);
    card.appendChild(el("div", "meta", "Seen " + num(it.signature_count) + " times - opened " + fmt(it.created_at) + (it.resolved_at ? " - resolved " + fmt(it.resolved_at) : "")));
    if (it.trigger) card.appendChild(el("pre", "", show(it.trigger)));
    if (it.diagnosis && typeof it.diagnosis === "object") {
      const dg = it.diagnosis;
      const res = dg.result && typeof dg.result === "object" ? dg.result : null;
      if (res) {
        if (res.confidence) tags.appendChild(el("span", "badge", "confidence: " + res.confidence));
        if (res.root_cause) {
          card.appendChild(el("div", "lbl", "Root cause"));
          card.appendChild(el("p", "", res.root_cause));
        }
        if (Array.isArray(res.evidence) && res.evidence.length) {
          card.appendChild(el("div", "lbl", "Evidence"));
          res.evidence.forEach((ev) => card.appendChild(el("pre", "", ev.quoted_line || show(ev))));
        }
        if (Array.isArray(res.next_checks) && res.next_checks.length) {
          card.appendChild(el("div", "lbl", "Next checks"));
          const ul = el("ul", "");
          res.next_checks.forEach((c) => ul.appendChild(el("li", "", c)));
          card.appendChild(ul);
        }
        if (res.suggested_fix) {
          card.appendChild(el("div", "lbl", "Suggested fix"));
          card.appendChild(el("p", "", res.suggested_fix));
        }
      } else if (dg.status === "failed" || dg.error) {
        card.appendChild(el("div", "lbl", "Diagnosis failed"));
        card.appendChild(el("pre", "", show(dg.error)));
      } else {
        card.appendChild(el("div", "lbl", "No diagnosis yet"));
      }
    } else {
      card.appendChild(el("div", "lbl", "No diagnosis yet"));
    }
    grid.appendChild(card);
  });
  out.replaceChildren(grid);
}

async function pageSignatures() {
  sub.textContent = "Known failure patterns FailureLens has seen.";
  const d = await api("/signatures");
  const items = (d && d.items) || [];
  if (!items.length) { empty("No signatures yet", "Patterns will appear here as FailureLens learns them from your logs."); return; }
  const TITLE_KEYS = ["template", "pattern", "sample", "fingerprint", "name", "text"];
  const grid = el("div", "grid");
  items.forEach((it) => {
    const card = el("article", "card");
    const tk = TITLE_KEYS.find((k) => typeof it[k] === "string" && it[k]);
    card.appendChild(el("h3", "", tk ? it[tk] : "Signature #" + it.id));
    const tags = el("div", "tags");
    Object.keys(it).forEach((k) => {
      const v = it[k];
      if (k === tk || v === null || typeof v === "object") return;
      const s = String(v);
      if (s.length <= 40) tags.appendChild(el("span", "badge", k.replace(/_/g, " ") + ": " + s));
    });
    card.appendChild(tags);
    grid.appendChild(card);
  });
  out.replaceChildren(grid);
}

function tile(label, value, note, state) {
  const t = el("div", "tile" + (state ? " " + state : ""));
  t.appendChild(el("div", "lbl", label));
  t.appendChild(el("div", "val", value));
  if (note) t.appendChild(el("div", "tsub", note));
  return t;
}
async function pageStatus() {
  sub.textContent = "Live health of the system. Refreshes every 5 seconds.";
  async function tick() {
    try {
      const res = await Promise.all([api("/health"), api("/metrics")]);
      const h = res[0] || {};
      const m = res[1] || {};
      const db = h.database || {};
      const emb = h.embedding || {};
      const q = m.queue || {};
      const ing = m.ingest || {};
      const dg = m.diagnosis || {};
      const inc = m.incidents || {};
      const llm = m.llm || {};
      const grid = el("div", "tiles");
      grid.appendChild(tile("System", h.status === "ok" ? "Healthy" : String(h.status || "Unknown"), "overall status", h.status === "ok" ? "ok" : "bad"));
      grid.appendChild(tile("Database", db.ok ? "Connected" : "Down", num(db.latency_ms, 1) + " ms response", db.ok ? "ok" : "bad"));
      grid.appendChild(tile("Embeddings", emb.provider || "-", emb.configured ? "ready" : "not configured", emb.configured ? "ok" : "bad"));
      grid.appendChild(tile("Queue", num(q.depth) + " / " + num(q.max_size), "waiting / capacity"));
      grid.appendChild(tile("Logs per second", num(ing.lines_per_s_1m, 1), "recent average"));
      grid.appendChild(tile("Batches saved", num(ing.batches_committed_1h), "in the last hour"));
      grid.appendChild(tile("Processed", num(ing.worker_processed), num(ing.worker_failed) + " failed", ing.worker_failed > 0 ? "bad" : "ok"));
      grid.appendChild(tile("Open incidents", num(inc.open), num(inc.diagnosing) + " being diagnosed, " + num(inc.deferred) + " deferred"));
      grid.appendChild(tile("Diagnoses (24h)", num(dg.diagnosed_24h), "median " + num(dg.p50_s, 1) + " s, p95 " + num(dg.p95_s, 1) + " s"));
      grid.appendChild(tile("AI tokens this hour", num(llm.tokens_used_this_hour), "limit " + num(llm.hourly_cap)));
      out.replaceChildren(grid, el("p", "note", "Updated " + new Date().toLocaleTimeString()));
    } catch (err) {
      fail(err);
    }
  }
  await tick();
  setInterval(tick, 5000);
}

const SCEN = [
  { svc: "payments-api", title: "database connection pool exhausted",
    info: ["POST /checkout 200 in 84ms", "GET /cart 200 in 31ms", "GET /prices 200 in 12ms"],
    err: ["db.pool timeout waiting for a free connection, pool exhausted (50 of 50 in use)", "checkout failed to charge the card: no database connection available"] },
  { svc: "auth-service", title: "login tokens failing to verify",
    info: ["GET /health 200", "POST /login 200 in 120ms", "POST /refresh 200 in 45ms"],
    err: ["token verification failed: signing key not found in key store", "login rejected: upstream key service unreachable"] },
  { svc: "orders-worker", title: "worker running out of memory",
    info: ["picked up order batch", "order batch saved", "heartbeat ok"],
    err: ["worker crashed while processing order batch: out of memory", "consumer lag keeps growing, queue is not draining"] },
  { svc: "search-index", title: "disk full on index node",
    info: ["segment merged", "query served in 22ms", "snapshot started"],
    err: ["write to index segment failed: no space left on device", "index node switched to read only mode"] },
  { svc: "notify-service", title: "email provider timing out",
    info: ["queued welcome email", "queued receipt email", "template cache refreshed"],
    err: ["smtp provider request timed out after retries", "notification dropped: provider returned service unavailable"] },
];

async function pageDemo() {
  sub.textContent = "Crash a pretend service on purpose and watch FailureLens catch it.";
  const wrap = el("div", "demo");
  const btn = el("button", "breakbtn", "Break it");
  btn.type = "button";
  const stage = el("div", "stage", "Ready. Click the button to crash a pretend service.");
  const con = el("div", "console", "Live log stream will appear here.");
  const result = el("div", "result");
  wrap.append(btn, stage, con, result);
  out.replaceChildren(wrap);

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const stamp = (off) => new Date(Date.now() + off).toISOString().replace("T", " ").replace("Z", "") + "000";
  let n = 0;
  try { n = parseInt(localStorage.getItem("fl_demo_n") || "0", 10) || 0; } catch (e) { n = 0; }

  async function post(svc, src, seq, text) {
    for (let tries = 0; tries < 5; tries++) {
      const r = await fetch("/ingest/stream", {
        method: "POST",
        headers: { "X-Service": svc, "X-Source-Id": src, "Idempotency-Key": src + "-" + seq, "X-Sequence": String(seq) },
        body: text,
      });
      if (r.status === 429) { await sleep(1000); continue; }
      if (!r.ok) throw new Error("The server said " + r.status + " when sending logs");
      return;
    }
    throw new Error("The server is busy right now. Try again in a moment.");
  }

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    result.replaceChildren();
    con.replaceChildren();
    const sc = SCEN[n % SCEN.length];
    const svc = sc.svc + "-" + Date.now().toString(36).slice(-5);
    n += 1;
    try { localStorage.setItem("fl_demo_n", String(n)); } catch (e) { /* ignore */ }
    const src = "demo-" + Date.now().toString(36);
    const t0 = Date.now();
    stage.className = "stage hot";
    stage.textContent = "Crashing " + svc + ": " + sc.title + "...";
    try {
      for (let seq = 1; seq <= 6; seq++) {
        const lines = [];
        for (let k = 0; k < 3; k++) lines.push(stamp(k * 40) + " INFO " + sc.info[(seq + k) % sc.info.length]);
        sc.err.forEach((m, k) => lines.push(stamp(150 + k * 40) + " ERROR " + m));
        await post(svc, src, seq, lines.join("\n") + "\n");
        for (const l of lines) {
          con.appendChild(el("div", l.indexOf(" ERROR ") > -1 ? "err" : "", svc + " " + l));
          con.scrollTop = con.scrollHeight;
          await sleep(80);
        }
        await sleep(500);
      }
      stage.textContent = "Logs sent. Waiting for FailureLens to spot the failure...";
      let found = null;
      const wait0 = Date.now();
      while (!found && Date.now() - wait0 < 45000) {
        await sleep(1500);
        try {
          const d = await api("/incidents");
          found = ((d && d.items) || []).find((it) => it.service === svc) || null;
        } catch (e) { /* keep waiting */ }
      }
      const c = el("div", "empty");
      if (found) {
        stage.textContent = "Caught it.";
        c.appendChild(el("strong", "", "Incident #" + found.id + " opened"));
        c.appendChild(el("p", "", "FailureLens spotted " + svc + " failing (" + sc.title + ") about " + Math.round((Date.now() - t0) / 1000) + " seconds after you pressed the button. Trigger: " + (found.trigger || "new failure") + "."));
      } else {
        stage.textContent = "No incident yet.";
        c.appendChild(el("strong", "", "Still waiting"));
        c.appendChild(el("p", "", "The logs were sent but no incident showed up yet. Check the Incidents page in a moment."));
      }
      const a = el("a", "gobtn", "See it on the Incidents page");
      a.href = "/ui/incidents";
      c.appendChild(a);
      result.replaceChildren(c);
    } catch (err) {
      stage.textContent = "Something went wrong.";
      const c = el("div", "empty warn");
      c.appendChild(el("strong", "", "Could not send the logs"));
      c.appendChild(el("p", "", String(err.message || err)));
      result.replaceChildren(c);
    } finally {
      btn.disabled = false;
    }
  });
}

async function load() {
  const pages = { documents: pageDocuments, incidents: pageIncidents, signatures: pageSignatures, status: pageStatus, demo: pageDemo };
  try { await pages[PAGE](); } catch (err) { fail(err); }
}
load();
</script>
</body>
</html>
"""

TITLES = {
    "documents": "Documents",
    "incidents": "Incidents",
    "signatures": "Signatures",
    "status": "Status",
    "demo": "Break it",
}


def _page(name: str) -> HTMLResponse:
    html = PAGE.replace("__PAGE__", name).replace("__TITLE__", TITLES[name])
    return HTMLResponse(html)


@router.get("/ui/documents", response_class=HTMLResponse, include_in_schema=False)
async def ui_documents() -> HTMLResponse:
    return _page("documents")


@router.get("/ui/incidents", response_class=HTMLResponse, include_in_schema=False)
async def ui_incidents() -> HTMLResponse:
    return _page("incidents")


@router.get("/ui/signatures", response_class=HTMLResponse, include_in_schema=False)
async def ui_signatures() -> HTMLResponse:
    return _page("signatures")


@router.get("/ui/demo", response_class=HTMLResponse, include_in_schema=False)
async def ui_demo() -> HTMLResponse:
    return _page("demo")


@router.get("/ui/status", response_class=HTMLResponse, include_in_schema=False)
async def ui_status() -> HTMLResponse:
    return _page("status")
