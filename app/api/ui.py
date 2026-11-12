# Embedded HTML/CSS/JS has long lines by nature.
# ruff: noqa: E501
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FailureLens</title>
<style>
  :root {
    --bg: #121316; --panel: #1b1d21; --panel2: #22252a;
    --text: #f1f2f4; --muted: #98a2ad; --line: #30353c;
    --coral: #ff6f61; --coral-soft: #ffa28f; --slate: #7f95ab;
  }
  * { box-sizing: border-box; }
  html { font-size: 17px; scroll-behavior: smooth; }
  body {
    margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
    font-family: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased; overflow-x: hidden;
  }

  /* ---------- background: grid + scrolling log lines + scan line ---------- */
  .bg { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
  .fl-grid { position: absolute; inset: 0; background-image: linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px); background-size: 48px 48px; -webkit-mask-image: radial-gradient(ellipse at 50% 30%, #000 20%, transparent 75%); mask-image: radial-gradient(ellipse at 50% 30%, #000 20%, transparent 75%); }
  .fl-logs { position: absolute; top: 0; bottom: 0; width: 30%; overflow: hidden; opacity: 0.55; font: 11px/1.9 ui-monospace, Consolas, monospace; color: rgba(127,149,171,0.6); white-space: nowrap; -webkit-mask-image: linear-gradient(transparent, #000 25%, #000 75%, transparent); mask-image: linear-gradient(transparent, #000 25%, #000 75%, transparent); }
  .fl-left { left: 1.5%; }
  .fl-right { right: 1.5%; text-align: right; }
  .fl-logs > div { animation: flscroll 70s linear infinite; }
  .fl-right > div { animation-duration: 90s; animation-direction: reverse; }
  .fl-logs b { color: rgba(255,111,97,0.9); font-weight: 500; }
  @keyframes flscroll { to { transform: translateY(-50%); } }
  .fl-scan { position: absolute; left: 0; right: 0; top: -160px; height: 160px; background: linear-gradient(transparent, rgba(255,111,97,0.07)); border-bottom: 1px solid rgba(255,111,97,0.28); animation: flscan 9s linear infinite; }
  @keyframes flscan { to { transform: translateY(calc(100vh + 160px)); } }
  @media (max-width: 1000px) { .fl-logs { display: none; } }

  /* ---------- layout ---------- */
  .top { position: relative; z-index: 2; display: flex; justify-content: flex-end; padding: 22px clamp(18px, 4vw, 48px) 0; }
  main { position: relative; z-index: 1; max-width: 1240px; margin: 0 auto; padding: 0 clamp(18px, 4vw, 40px) 90px; }
  .hero { text-align: center; padding: clamp(36px, 9vh, 96px) 0 40px; transition: padding 0.5s ease; }
  body.searched .hero { padding-top: 12px; padding-bottom: 28px; }

  .logo { margin: 0; font-size: clamp(2.6rem, 7vw, 4.2rem); font-weight: 800; letter-spacing: -0.035em; transition: font-size 0.5s ease; }
  .logo span { color: var(--coral); text-shadow: 0 0 30px rgba(255,111,97,0.6), 0 0 70px rgba(255,111,97,0.3); }
  body.searched .logo { font-size: clamp(1.9rem, 4vw, 2.5rem); }
  .tag { margin: 12px auto 34px; max-width: 520px; color: var(--muted); font-size: 1.05rem; line-height: 1.55; transition: opacity 0.4s, margin 0.4s, max-height 0.4s; max-height: 80px; }
  body.searched .tag { opacity: 0; max-height: 0; margin: 0 auto 18px; overflow: hidden; }

  /* ---------- search box ---------- */
  form { max-width: 780px; margin: 0 auto; }
  .box { position: relative; border-radius: 24px; }
  .box::before {
    content: ""; position: absolute; inset: -2px; border-radius: 26px; z-index: 0;
    background: radial-gradient(60% 140% at 50% 50%, rgba(255,111,97,0.9), rgba(255,111,97,0));
    filter: blur(22px); opacity: 0.22; transition: opacity 0.5s ease;
  }
  .box:focus-within::before { opacity: 0.75; }
  .box.loading::before { opacity: 0.95; animation: pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.45; } }
  .inner {
    position: relative; z-index: 1; display: flex; align-items: center; gap: 12px;
    background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 10px 10px 10px 24px;
    transition: border-color 0.3s;
  }
  .box:focus-within .inner { border-color: rgba(255,111,97,0.55); }
  input[type=text] { flex: 1; min-width: 0; background: transparent; border: 0; outline: 0; color: var(--text); font: inherit; font-size: 1.15rem; padding: 14px 0; }
  input::placeholder { color: #70798379; color: #707983; }
  button { font: inherit; color: var(--text); cursor: pointer; }
  button:disabled { opacity: 0.6; cursor: wait; }
  .go {
    background: var(--coral); border: 0; border-radius: 14px; padding: 14px 28px; font-weight: 700; font-size: 1.02rem; color: #1a0f0d;
    box-shadow: 0 0 26px -4px rgba(255,111,97,0.7); transition: transform 0.2s, box-shadow 0.3s;
  }
  .go:hover { transform: translateY(-1px); box-shadow: 0 0 38px -2px rgba(255,111,97,0.95); }

  .dd { position: relative; }
  .ddbtn {
    background: var(--panel2); border: 1px solid var(--line); border-radius: 14px; padding: 13px 38px 13px 16px; min-width: 70px; text-align: left; font-size: 1.02rem;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='14' height='9' viewBox='0 0 14 9'><path d='M1 1l6 6 6-6' fill='none' stroke='%2398a2ad' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>");
    background-repeat: no-repeat; background-position: right 14px center;
  }
  .ddbtn:hover { border-color: #566170; }
  .ddmenu { display: none; position: absolute; top: calc(100% + 10px); right: 0; min-width: 100%; background: var(--panel2); border: 1px solid #4a5460; border-radius: 16px; padding: 6px; z-index: 10; box-shadow: 0 16px 50px rgba(0,0,0,0.6), 0 0 40px -10px rgba(255,111,97,0.35); }
  .ddmenu.open { display: block; }
  .ddmenu button { display: block; width: 100%; text-align: left; background: transparent; border: 0; border-radius: 10px; padding: 11px 20px; font-size: 1.15rem; }
  .ddmenu button:hover, .ddmenu button.sel { background: #323841; }
  .ddmenu button.sel { color: var(--coral-soft); }

  .chips { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-top: 22px; transition: opacity 0.4s; }
  body.searched .chips { display: none; }
  .chip { background: rgba(255,255,255,0.03); border: 1px solid var(--line); border-radius: 999px; padding: 9px 16px; font-size: 0.92rem; color: var(--muted); transition: all 0.25s; }
  .chip:hover { color: var(--text); border-color: rgba(255,111,97,0.6); box-shadow: 0 0 22px -4px rgba(255,111,97,0.55); transform: translateY(-1px); }

  .upload-wrap { display: flex; flex-direction: column; align-items: center; gap: 8px; margin-top: 26px; }
  body.searched .upload-wrap { margin-top: 14px; }
  .upload { background: rgba(255,111,97,0.10); border: 1px solid rgba(255,111,97,0.7); border-radius: 14px; padding: 15px 34px; font-size: 1.05rem; font-weight: 600; color: #fff; cursor: pointer; box-shadow: 0 0 28px -6px rgba(255,111,97,0.55); transition: all 0.25s; }
  .upload:hover { background: rgba(255,111,97,0.2); box-shadow: 0 0 38px -2px rgba(255,111,97,0.75); transform: translateY(-1px); }
  .upload-hint { font-size: 0.82rem; color: var(--muted); }
  input[type=file] { display: none; }
  #status { min-height: 1.4em; margin-top: 18px; font-size: 0.95rem; color: var(--muted); }

  /* ---------- confidence meter ---------- */
  .meter { grid-column: 1 / -1; background: var(--panel); border: 1px solid var(--line); border-radius: 18px; padding: 16px 20px; --mc: #f2b84b; }
  .meter.strong { --mc: #5fd49a; }
  .meter.none { --mc: #7f95ab; }
  .mrow { display: flex; align-items: center; gap: 10px; }
  .mdot { width: 10px; height: 10px; border-radius: 50%; background: var(--mc); box-shadow: 0 0 12px var(--mc); }
  .mrow b { color: var(--mc); font-size: 1.05rem; }
  .mnum { margin-left: auto; color: var(--muted); font-size: 0.85rem; }
  .mbar { height: 6px; border-radius: 99px; background: #2b2f35; margin: 12px 0 10px; overflow: hidden; }
  .mbar i { display: block; height: 100%; border-radius: 99px; background: var(--mc); box-shadow: 0 0 12px var(--mc); transform-origin: left; animation: mgrow 0.9s ease; }
  @keyframes mgrow { from { transform: scaleX(0); } }
  .mmsg { color: var(--muted); font-size: 0.9rem; }

  /* ---------- dark scrollbars ---------- */
  html { color-scheme: dark; }
  pre.open { scrollbar-width: thin; scrollbar-color: #4a5460 transparent; }

  /* ---------- top menu ---------- */
  .top { justify-content: center; }
  .nav { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }
  .nav a { color: var(--muted); text-decoration: none; padding: 8px 16px; border: 1px solid var(--line); border-radius: 999px; font-size: 0.92rem; background: rgba(255,255,255,0.03); transition: all 0.25s; }
  .nav a:hover { color: var(--text); border-color: rgba(255,111,97,0.6); box-shadow: 0 0 22px -4px rgba(255,111,97,0.55); }
  .nav a.active { color: #fff; border-color: rgba(255,111,97,0.7); background: rgba(255,111,97,0.12); }
  .nav a[href="/ui/demo"] { color: var(--coral-soft); border-color: rgba(255,111,97,0.7); }

  /* ---------- voice button ---------- */
  .mic { flex: 0 0 auto; display: grid; place-items: center; width: 46px; height: 46px; border-radius: 14px; background: rgba(255,255,255,0.04); border: 1px solid var(--line); color: var(--muted); cursor: pointer; transition: all 0.25s; }
  .mic:hover { color: var(--text); border-color: rgba(255,111,97,0.6); box-shadow: 0 0 22px -4px rgba(255,111,97,0.55); }
  .mic.on { color: #fff; background: var(--coral); border-color: var(--coral); animation: micpulse 1.1s ease-in-out infinite; }
  @keyframes micpulse { 50% { box-shadow: 0 0 0 10px rgba(255,111,97,0); } 0% { box-shadow: 0 0 0 0 rgba(255,111,97,0.6); } }

  /* ---------- results ---------- */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 26px; align-items: start; }
  .notice { grid-column: 1 / -1; max-width: 640px; margin: 20px auto 0; text-align: center; background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 34px 28px; color: var(--muted); line-height: 1.6; box-shadow: 0 0 60px -14px rgba(255,111,97,0.35); }
  .notice strong { display: block; color: var(--text); font-size: 1.25rem; margin-bottom: 8px; }
  .notice.warn { border-color: rgba(255,111,97,0.5); }

  .card {
    position: relative; background: var(--panel); border: 1px solid var(--line); border-radius: 22px; padding: 22px 22px 18px; min-width: 0;
    box-shadow: 0 0 44px -12px rgba(255,111,97,0.32), 0 18px 40px -24px rgba(0,0,0,0.8);
    transition: box-shadow 0.35s ease, border-color 0.35s ease, transform 0.35s ease;
    animation: rise 0.55s cubic-bezier(.2,.8,.2,1) both;
  }
  .card:hover { transform: translateY(-4px); border-color: rgba(255,111,97,0.45); box-shadow: 0 0 64px -8px rgba(255,111,97,0.5), 0 22px 44px -22px rgba(0,0,0,0.9); }
  @keyframes rise { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }

  .head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
  .rank { flex: none; width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; font-size: 0.9rem; color: var(--coral-soft); background: rgba(255,111,97,0.12); border: 1px solid rgba(255,111,97,0.4); box-shadow: 0 0 18px -2px rgba(255,111,97,0.5); }
  .who { min-width: 0; flex: 1; }
  .svc { font-weight: 600; font-size: 0.95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .when { color: var(--muted); font-size: 0.8rem; margin-top: 2px; }
  .score { text-align: right; flex: none; }
  .score b { font-size: 0.95rem; color: var(--text); }
  .bar { width: 64px; height: 5px; border-radius: 99px; background: #2b2f35; margin-top: 6px; overflow: hidden; }
  .bar i { display: block; height: 100%; border-radius: 99px; background: var(--coral); box-shadow: 0 0 10px rgba(255,111,97,0.8); }

  .tags { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
  .badge { font-size: 0.74rem; letter-spacing: 0.02em; padding: 4px 11px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); background: rgba(255,255,255,0.02); }
  .badge.vector { color: #a9bccf; border-color: rgba(127,149,171,0.5); }
  .badge.fulltext { color: var(--coral-soft); border-color: rgba(255,111,97,0.45); }
  .badge.fp { color: #9be3b0; border-color: rgba(120,200,150,0.4); }
  .badge.lvl { text-transform: uppercase; }

  pre { margin: 0; white-space: pre-wrap; word-break: break-word; font: 0.84rem/1.7 ui-monospace, "Cascadia Code", Consolas, monospace; color: #d9dde2; background: rgba(0,0,0,0.22); border: 1px solid rgba(255,255,255,0.04); border-radius: 14px; padding: 14px 16px; }
  pre.clamp { display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
  pre.open { max-height: 380px; overflow: auto; }
  mark { background: rgba(255,111,97,0.3); color: #fff; border-radius: 4px; padding: 0 3px; }
  .more { margin-top: 12px; background: transparent; border: 0; color: var(--coral-soft); font-size: 0.88rem; padding: 6px 2px; }
  .more:hover { text-decoration: underline; text-underline-offset: 4px; }

  .skel { height: 210px; animation: none; overflow: hidden; }
  .skel::after { content: ""; position: absolute; inset: 0; background: linear-gradient(100deg, transparent 20%, rgba(255,255,255,0.05) 50%, transparent 80%); transform: translateX(-100%); animation: shimmer 1.4s infinite; }
  @keyframes shimmer { to { transform: translateX(100%); } }

  /* ---------- responsive ---------- */
  @media (max-width: 640px) {
    html { font-size: 16px; }
    .inner { flex-wrap: wrap; padding: 8px 8px 8px 18px; }
    input[type=text] { flex: 1 1 100%; padding: 12px 0 6px; }
    .dd { flex: 0 0 auto; }
    .go { flex: 1; }
    .grid { grid-template-columns: minmax(0, 1fr); gap: 20px; }
    .ring.r3 { display: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
  <div class="bg">
    <i class="fl-grid"></i><i class="fl-scan"></i>
    <div class="fl-logs fl-left"><div>12:04:11 nova.compute <b>WARN</b>  1 instances in database, 0 on hypervisor<br>12:04:12 nova.api INFO  GET /v2/servers/detail 200<br>12:04:14 nova.scheduler INFO  filter pass: 3 hosts<br>12:04:17 neutron.agent <b>ERROR</b>  port binding failed<br>12:04:19 nova.compute INFO  instance build took 21.4s<br>12:04:22 cinder.volume INFO  volume attached<br>12:04:25 nova.compute <b>WARN</b>  base file removed<br>12:04:27 glance.api INFO  image cache hit<br>12:04:31 nova.api <b>ERROR</b>  no instances found for event<br>12:04:33 nova.compute INFO  server was deleted<br>12:04:36 keystone INFO  token validated<br>12:04:40 nova.conductor <b>WARN</b>  retrying request 2 of 3<br>12:04:43 nova.api INFO  POST /v2/servers 202<br>12:04:47 neutron.server INFO  network ready<br>12:04:50 nova.compute <b>ERROR</b>  hypervisor not responding<br>12:04:53 nova.scheduler INFO  host selected<br>12:04:11 nova.compute <b>WARN</b>  1 instances in database, 0 on hypervisor<br>12:04:12 nova.api INFO  GET /v2/servers/detail 200<br>12:04:14 nova.scheduler INFO  filter pass: 3 hosts<br>12:04:17 neutron.agent <b>ERROR</b>  port binding failed<br>12:04:19 nova.compute INFO  instance build took 21.4s<br>12:04:22 cinder.volume INFO  volume attached<br>12:04:25 nova.compute <b>WARN</b>  base file removed<br>12:04:27 glance.api INFO  image cache hit<br>12:04:31 nova.api <b>ERROR</b>  no instances found for event<br>12:04:33 nova.compute INFO  server was deleted<br>12:04:36 keystone INFO  token validated<br>12:04:40 nova.conductor <b>WARN</b>  retrying request 2 of 3<br>12:04:43 nova.api INFO  POST /v2/servers 202<br>12:04:47 neutron.server INFO  network ready<br>12:04:50 nova.compute <b>ERROR</b>  hypervisor not responding<br>12:04:53 nova.scheduler INFO  host selected</div></div>
    <div class="fl-logs fl-right"><div>12:04:53 nova.scheduler INFO  host selected<br>12:04:50 nova.compute <b>ERROR</b>  hypervisor not responding<br>12:04:47 neutron.server INFO  network ready<br>12:04:43 nova.api INFO  POST /v2/servers 202<br>12:04:40 nova.conductor <b>WARN</b>  retrying request 2 of 3<br>12:04:36 keystone INFO  token validated<br>12:04:33 nova.compute INFO  server was deleted<br>12:04:31 nova.api <b>ERROR</b>  no instances found for event<br>12:04:27 glance.api INFO  image cache hit<br>12:04:25 nova.compute <b>WARN</b>  base file removed<br>12:04:22 cinder.volume INFO  volume attached<br>12:04:19 nova.compute INFO  instance build took 21.4s<br>12:04:17 neutron.agent <b>ERROR</b>  port binding failed<br>12:04:14 nova.scheduler INFO  filter pass: 3 hosts<br>12:04:12 nova.api INFO  GET /v2/servers/detail 200<br>12:04:11 nova.compute <b>WARN</b>  1 instances in database, 0 on hypervisor<br>12:04:53 nova.scheduler INFO  host selected<br>12:04:50 nova.compute <b>ERROR</b>  hypervisor not responding<br>12:04:47 neutron.server INFO  network ready<br>12:04:43 nova.api INFO  POST /v2/servers 202<br>12:04:40 nova.conductor <b>WARN</b>  retrying request 2 of 3<br>12:04:36 keystone INFO  token validated<br>12:04:33 nova.compute INFO  server was deleted<br>12:04:31 nova.api <b>ERROR</b>  no instances found for event<br>12:04:27 glance.api INFO  image cache hit<br>12:04:25 nova.compute <b>WARN</b>  base file removed<br>12:04:22 cinder.volume INFO  volume attached<br>12:04:19 nova.compute INFO  instance build took 21.4s<br>12:04:17 neutron.agent <b>ERROR</b>  port binding failed<br>12:04:14 nova.scheduler INFO  filter pass: 3 hosts<br>12:04:12 nova.api INFO  GET /v2/servers/detail 200<br>12:04:11 nova.compute <b>WARN</b>  1 instances in database, 0 on hypervisor</div></div>
  </div>

  <div class="top"><nav class="nav"><a href="/" class="active">Search</a><a href="/ui/documents">Documents</a><a href="/ui/incidents">Incidents</a><a href="/ui/signatures">Signatures</a><a href="/ui/status">Status</a><a href="/ui/demo">Break it</a></nav>
    <input type="file" id="file">
  </div>

  <main>
    <section class="hero">
      <h1 class="logo">Failure<span>Lens</span></h1>
      <p class="tag">Describe what went wrong in plain words. FailureLens finds the log lines that explain it.</p>

      <form id="f">
        <div class="box" id="box">
          <div class="inner">
            <input type="text" id="q" placeholder="e.g. instance missing on hypervisor" autocomplete="off" autofocus>
            <button type="button" class="mic" id="mic" title="Search by voice" aria-label="Search by voice"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></button>
            <div class="dd" id="dd">
              <button type="button" class="ddbtn" id="ddbtn" title="Number of results"><span id="kval">8</span></button>
              <div class="ddmenu" id="ddmenu">
                <button type="button" data-v="4">4</button>
                <button type="button" data-v="6">6</button>
                <button type="button" data-v="8" class="sel">8</button>
                <button type="button" data-v="12">12</button>
                <button type="button" data-v="20">20</button>
              </div>
            </div>
            <button class="go" id="go" type="submit">Search</button>
          </div>
        </div>
      </form>

      <div class="chips" id="chips"></div>
  <div class="upload-wrap"><button type="button" class="upload" id="up">Upload a log file</button><span class="upload-hint">Add your own log, then search it in plain words</span></div>
      <div id="status"></div>
    </section>

    <section class="grid" id="results"></section>
  </main>

<script>
  const $ = (id) => document.getElementById(id);
  const box = $("box"), results = $("results"), statusEl = $("status");
  const CLAMP_LINES = 3;
  const EXAMPLES = [
    "database has 1 instance but hypervisor has 0",
    "server was deleted",
    "how long did it take to build the VM",
    "no instances found for event"
  ];

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function notice(cls, title, body) {
    const n = el("div", "notice " + cls);
    n.appendChild(el("strong", "", title));
    n.appendChild(document.createTextNode(body || ""));
    return n;
  }
  async function errorText(r) {
    let msg = "HTTP " + r.status;
    try {
      const j = await r.json();
      if (typeof j.detail === "string") msg += ": " + j.detail;
      else if (Array.isArray(j.detail)) msg += ": " + j.detail.map((d) => (d.loc ? d.loc.slice(1).join(".") + " " : "") + (d.msg || "")).join("; ");
    } catch (e) {}
    return msg;
  }
  function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

  function highlight(pre, text, query) {
    const words = query.toLowerCase().split(/\W+/).filter((w) => w.length > 3);
    const re = words.length ? new RegExp("(" + words.map(escRe).join("|") + ")", "ig") : null;
    text.split("\n").forEach((line, i, arr) => {
      const shown = line.length > 420 ? line.slice(0, 420) + "..." : line;
      if (re) {
        shown.split(re).forEach((part, pi) => {
          if (pi % 2 === 1) pre.appendChild(el("mark", "", part));
          else pre.appendChild(document.createTextNode(part));
        });
      } else pre.appendChild(document.createTextNode(shown));
      if (i < arr.length - 1) pre.appendChild(document.createTextNode("\n"));
    });
  }

  function skeletons(n) {
    results.replaceChildren();
    for (let i = 0; i < Math.min(n, 6); i++) results.appendChild(el("div", "card skel"));
  }

  const STRONG_AT = 0.75;
  function meter(data) {
    const hits = data.hits || [];
    const sims = hits.map((h) => h.similarity).filter((s) => typeof s === "number");
    const top = sims.length ? Math.max(...sims) : null;
    let state = "weak";
    let label = "Weak match";
    let msg = "These lines look related, but check them before trusting them.";
    if (data.inconclusive || hits.length === 0) {
      state = "none";
      label = "Not sure";
      msg = "Nothing in your logs matches this well, so FailureLens will not guess.";
    } else if (top === null) {
      msg = "Matched on exact words only, with no meaning score.";
    } else if (top >= STRONG_AT) {
      state = "strong";
      label = "Strong match";
      msg = "The top result is very close to what you described.";
    }
    const m = el("div", "meter " + state);
    const row = el("div", "mrow");
    row.appendChild(el("span", "mdot"));
    row.appendChild(el("b", "", label));
    if (top !== null) row.appendChild(el("span", "mnum", Math.round(top * 100) + "% similar"));
    m.appendChild(row);
    const bar = el("div", "mbar");
    const fill = document.createElement("i");
    const pct = state === "none" ? 8 : (top === null ? 50 : top * 100);
    fill.style.width = Math.max(8, Math.min(100, pct)) + "%";
    bar.appendChild(fill);
    m.appendChild(bar);
    m.appendChild(el("div", "mmsg", msg));
    return m;
  }

  function render(data, query) {
    results.replaceChildren();
    if (data.degraded) results.appendChild(notice("warn", "Partial results", "Some search sources were unavailable, so results may be incomplete."));
    results.appendChild(meter(data));
    if (data.inconclusive || !data.hits || data.hits.length === 0) {
      results.appendChild(notice("", "I don't know", data.reason || "Try describing the problem with different words, or upload the log you want to search."));
      return;
    }
    data.hits.forEach((h, i) => {
      const card = el("article", "card");
      card.style.animationDelay = (i * 70) + "ms";

      const head = el("div", "head");
      head.appendChild(el("div", "rank", String(i + 1)));
      const who = el("div", "who");
      who.appendChild(el("div", "svc", h.service || "log chunk"));
      if (h.ts) who.appendChild(el("div", "when", new Date(h.ts).toLocaleString()));
      head.appendChild(who);
      if (typeof h.similarity === "number") {
        const sc = el("div", "score");
        sc.appendChild(el("b", "", h.similarity.toFixed(3)));
        const bar = el("div", "bar");
        const fill = document.createElement("i");
        fill.style.width = Math.max(4, Math.min(100, h.similarity * 100)) + "%";
        bar.appendChild(fill);
        sc.appendChild(bar);
        head.appendChild(sc);
      }
      card.appendChild(head);

      const tags = el("div", "tags");
      (h.sources || []).forEach((s) => tags.appendChild(el("span", "badge " + s, s === "vector" ? "meaning match" : s === "fulltext" ? "keyword match" : s)));
      if (h.fingerprint_match) tags.appendChild(el("span", "badge fp", "known pattern"));
      if (h.level) tags.appendChild(el("span", "badge lvl", h.level));
      if (tags.childNodes.length) card.appendChild(tags);

      const text = h.text || "";
      const long = text.split("\n").length > CLAMP_LINES;
      const pre = el("pre", long ? "clamp" : "");
      highlight(pre, text, query);
      card.appendChild(pre);
      if (long) {
        const btn = el("button", "more", "Show full log");
        btn.type = "button";
        btn.addEventListener("click", () => {
          const collapsed = pre.classList.toggle("clamp");
          pre.classList.toggle("open", !collapsed);
          btn.textContent = collapsed ? "Show full log" : "Show less";
        });
        card.appendChild(btn);
      }
      results.appendChild(card);
    });
  }

  async function search(query) {
    document.body.classList.add("searched");
    box.classList.add("loading");
    $("go").disabled = true;
    statusEl.textContent = "Searching...";
    const k = parseInt($("kval").textContent, 10);
    const MAX_Q = 2000;
    const trimmed = query.length > MAX_Q;
    const sent = trimmed ? query.slice(0, MAX_Q) : query;
    skeletons(k);
    const t0 = performance.now();
    try {
      const r = await fetch("/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: sent, top_k: k }),
      });
      if (!r.ok) throw new Error(await errorText(r));
      const data = await r.json();
      render(data, sent);
      if (trimmed) results.prepend(notice("warn", "Long input trimmed", "Only the first " + MAX_Q + " characters were searched. For best results paste just the error line."));
      const n = data.hits ? data.hits.length : 0;
      statusEl.textContent = n + " result" + (n === 1 ? "" : "s") + " in " + Math.round(performance.now() - t0) + " ms";
    } catch (err) {
      results.replaceChildren(notice("warn", "Search failed", String(err.message || err)));
      statusEl.textContent = "";
    } finally {
      box.classList.remove("loading");
      $("go").disabled = false;
    }
  }

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const micBtn = $("mic");
  if (!SR) {
    micBtn.style.display = "none";
  } else {
    const rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    let heard = "";
    rec.onstart = () => { micBtn.classList.add("on"); statusEl.textContent = "Listening..."; };
    rec.onresult = (e) => {
      heard = Array.from(e.results).map((r) => r[0].transcript).join(" ");
      $("q").value = heard;
    };
    rec.onerror = (e) => { statusEl.textContent = "Mic problem: " + e.error; };
    rec.onend = () => {
      micBtn.classList.remove("on");
      const said = heard.trim().replace(/[.,!?]+$/, "");
      heard = "";
      if (said) search(said);
    };
    micBtn.addEventListener("click", () => {
      if (micBtn.classList.contains("on")) { rec.stop(); return; }
      heard = "";
      try { rec.start(); } catch (err) { statusEl.textContent = "Mic problem: " + err.message; }
    });
  }

  $("f").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("q").value.trim();
    if (q) search(q);
  });

  EXAMPLES.forEach((t) => {
    const b = el("button", "chip", t);
    b.type = "button";
    b.addEventListener("click", () => { $("q").value = t; search(t); });
    $("chips").appendChild(b);
  });

  const menu = $("ddmenu");
  $("ddbtn").addEventListener("click", (e) => { e.stopPropagation(); menu.classList.toggle("open"); });
  menu.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $("kval").textContent = b.dataset.v;
    menu.querySelectorAll("button").forEach((x) => x.classList.toggle("sel", x === b));
    menu.classList.remove("open");
  }));
  document.addEventListener("click", () => menu.classList.remove("open"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== $("q")) { e.preventDefault(); $("q").focus(); }
  });

  $("up").addEventListener("click", () => $("file").click());
  $("file").addEventListener("change", async () => {
    const f = $("file").files[0];
    if (!f) return;
    statusEl.textContent = "Uploading " + f.name + "...";
    try {
      const fd = new FormData();
      fd.append("file", f, f.name);
      const r = await fetch("/ingest", { method: "POST", body: fd });
      if (!r.ok) throw new Error(await errorText(r));
      const d = await r.json();
      statusEl.textContent = d.duplicate
        ? f.name + " was already uploaded."
        : "Uploaded " + d.filename + " (" + d.chunks + " chunks). It needs about a minute to be searchable.";
    } catch (err) {
      statusEl.textContent = "Upload failed: " + (err.message || err);
    }
    $("file").value = "";
  });
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> HTMLResponse:
    return HTMLResponse(PAGE)
