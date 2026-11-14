# FailureLens

Real-time log ingestion, failure detection and RAG root-cause diagnosis. Logs stream in, get redacted, chunked and fingerprinted into failure signatures, and are embedded for hybrid search (pgvector + Postgres full-text). A live dashboard shows incidents as they happen.

Runs on free tools only: embeddings are computed locally (bge-small-en-v1.5 on CPU), so no API key is needed for ingest, search or the dashboard.

## Demo

Describe a failure in plain words and FailureLens finds the log lines that explain it. When an error is new or spiking, it opens an incident and an LLM writes a root cause backed by quoted log lines.

![A diagnosed incident](docs/incident.png)

### Try it in 2 minutes

1. `docker compose up -d`, then open http://localhost:8000/
2. Click **Upload a log file** and add a log (tested with the OpenStack_2k sample).
3. Try these searches:
   - `database has 1 instance but hypervisor has 0`
   - `server was deleted`
   - `how long did it take to build the VM`
   - `base file removed`
   - `no instances found for event`
   - `banana bread recipe` (should answer "I don't know")
4. Open **Break it** in the menu and press the button. It streams fake errors for a pretend service, an incident opens, and the **Incidents** page shows the diagnosis with evidence.

### Free setup

- Embeddings run locally (bge-small ONNX), so there are no embedding costs.
- Diagnosis uses any OpenAI-compatible API. The defaults in `.env.example` use Google's free Gemini tier. Get a key at https://aistudio.google.com, then put it in your local `.env` as `LLM_API_KEY=` (never commit it). Without a key, search still works and incidents show "No diagnosis yet".

## Components

| Path | What it is |
|---|---|
| `app/` | FastAPI backend: ingest queue, worker pool, fingerprinting, retrieval, incidents, WebSocket events, `/metrics` |
| `shipper/` | Log shipper CLI: tails files, spools to disk, forwards with backoff, resumes from committed keys |
| `frontend/` | Live dashboard (Vite dev server on port 5173) |
| `alembic/` | Database migrations (Postgres 16 + pgvector) |
| `eval/` | Retrieval eval harness and load test |

Main endpoints: `POST /ingest/stream`, `GET /signatures`, `GET /incidents`, `WS /ws/events`, `GET /health`, `GET /metrics`, `POST /diagnose`. Search and document upload routers are in `app/api/`.

## Quick start (Docker)

1. Put the embedding model files in `models/bge-small-en-v1.5/` (see [Embedding model](#embedding-model)).
2. Start everything:

```bash
docker compose up -d --build
curl http://localhost:8000/health
```

`/health` should report `"status": "ok"` and `"embedding": {"provider": "local", "configured": true}`.

Compose mounts `./models` read-only at `/models` and sets `LOCAL_MODEL_DIR=/models/bge-small-en-v1.5`. The image is Python 3.12 and installs the `local` extra.

## Local development

Python 3.12+ is required for the `local` extra (the numpy pin needs it).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows; on Linux/macOS: source .venv/bin/activate
pip install -e ".[dev,local]"
docker compose up -d db           # Postgres on host port 55432
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

Ship logs with `python -m shipper --help`.

## Embedding model

The default provider is `local`: `BAAI/bge-small-en-v1.5` (384 dims) run through ONNX Runtime on CPU. It needs two files in `models/bge-small-en-v1.5/` (the folder is gitignored):

```powershell
New-Item -ItemType Directory -Force models\bge-small-en-v1.5 | Out-Null
Invoke-WebRequest https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main/onnx/model.onnx -OutFile models\bge-small-en-v1.5\model.onnx
Invoke-WebRequest https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main/tokenizer.json -OutFile models\bge-small-en-v1.5/tokenizer.json
```

Notes:
- Long chunks are embedded as several windows and grouped back per chunk, so local embeddings are no longer truncated.
- Local vectors live in their own table (`chunk_embeddings_local`).
- `EMBEDDING_PROVIDER=local` is the default and is free. `fake` is for tests.
- Point at a different model folder with `LOCAL_MODEL_DIR`.
- `MIN_SIMILARITY` (default 0.65) is the least vector similarity a vector-only hit needs to count as evidence. It was tuned on 11 eval cases for bge-small only (answerable 0.708 to 0.851, off-topic 0.594 to 0.689), so treat it as provisional. A request can override it with `min_similarity`.

## Configuration

Copy `.env.example` to `.env`. Every setting is optional and the file lists the defaults.

LLM diagnosis (`POST /diagnose`) uses any OpenAI-compatible chat API. Set `LLM_API_KEY`, `LLM_BASE_URL` and `LLM_MODEL` in `.env`; the defaults in `.env.example` use Google's free Gemini tier. Without a key the diagnosis runner is disabled and `/diagnose` answers 503. Everything else works without a key.

## Tests

```bash
pytest -q -m "not integration"                       # unit tests, no database needed
docker compose --profile test up -d --wait db-test   # throwaway Postgres on port 55433
pytest -q -m integration                             # DB-backed tests
ruff check .
```

## Evaluation

Retrieval eval over Loghub samples (HDFS, OpenStack). It refuses any database whose name does not end in `_eval`:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://failurelens:failurelens@localhost:55433/failurelens_eval"
$env:EMBEDDING_PROVIDER = "local"
python -m eval.retrieval_eval
```

Committed results (`eval/results/`), 6 cases from 3 problems:

| Mode | Hits |
|---|---|
| Vector, windowed | 6/6 |
| Hybrid (vector + full-text, RRF) | 5/6 |
| Full-text only | 3/6 |
| Vector, truncated (old behaviour) | 4/6 |

Six cases from three problems are too few to rank the modes. Read this as "windowing fixed truncation", not "vector beats hybrid".

A load test is in `eval/load_test.py` (1000 lines/s for 60 s, 0 lost in the committed run).

## Measured search quality

Test set: `eval/fixtures/webshop/webshop-incident-day.log` (572 lines, 8 injected failures) with 8 plain-words queries, for example "server disk is full". A query passes when the right log line is in the top 3 results. Searches run over all uploaded files, including an unrelated OpenStack log.

| Change | Top-3 hits (of 8) |
|---|---|
| Baseline (hybrid vector + full-text) | 5 |
| ERROR/WARN level boost | 6 |
| Redaction fix: plain words after `token:` are no longer hidden | 7 |

- Searching one file only (the "All files / this file" dropdown) scored 7 of 8 when measured before the redaction fix. It has not been re-measured since.
- The redaction fix mattered because the line `failed to store session token: Redis is out of memory` was stored as `<SECRET> is out of memory`, so no search could find it.

Tried and dropped:
- Rewriting the query with a free Gemini model: +1 query on this log, but one model call per search, and no gain once the search was limited to one file.
- Removing filler words from the full-text query: net zero. One query got better and another got worse.

Caveats:
- Eight queries on one synthetic log is a small sample. Treat the numbers as a regression check, not a general accuracy claim.
- The level boost changed one result for the demo query "server was deleted" (an unrelated nginx WARN line entered the top 3).
- The 8 queries are committed as eval cases (`eval/fixtures/cases.jsonl`, source `synthetic`), but the eval has not been run yet because it needs a scratch `_eval` database.

## Known limitations

- **Full-text search does not stem.** It uses the `simple` Postgres config on purpose, so identifiers like `blk_123` or `nova.virt.libvirt` and words like "not" stay intact. A query using a different word form than the log (`instance` vs `instances`) can lose its vector hit during hybrid fusion. A separate stemmed column would fix this without touching the existing one, but needs more eval cases first.
- **Diagnosis needs an LLM key** (the free Gemini tier works, see Configuration). Without one, incidents still open but show "No diagnosis yet".
- Diagnosis only looks inside the failing service. If service A fails because of something in service B, the answer is "inconclusive" or wrong (seen on the `cart-api` scenario).
- Diagnosis can claim a cause when nothing was logged. If the logs show only the failure and no line that explains it, the model sometimes restates the symptom instead of answering "inconclusive". In 3 repeated runs of the `email-digest` scenario, 3 of 6 diagnoses were correct.
- One failure can open more than one incident. Each distinct error message is its own signature and each new signature opens its own incident, so one Break it click can produce two incidents.
- The "before the error" context is small: at most 4 distinct lines from the same service in the 30 minutes before the error. An older cause, or one buried under many newer lines, can be missed.
- Diagnoses wait when the hourly token cap is reached (`LLM_HOURLY_TOKEN_CAP`). At about 1.7k tokens per diagnosis, a cap of 50,000 allows roughly 28 per hour.
- The diagnosis scores come from a few synthetic scenarios (eval/scenario_run.py), each run twice, plus one hand-written log. Treat them as a trend, not an accuracy rate.
