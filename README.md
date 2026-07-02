# SHL Conversational Assessment Recommender

A stateless FastAPI service that recommends SHL Individual Test Solutions through a
short, multi-turn conversation. Given the full message history on every call, it
clarifies only when necessary, builds an assessment battery, honors surgical edits,
and returns a schema-valid response with catalog-verified URLs.

> The authoritative spec — API contract, behavioral rules, engineering rules, and the
> running decisions log — lives in [CLAUDE.md](CLAUDE.md). Read it first.
> The design write-up is in [APPROACH.md](APPROACH.md).

## Layout
```
├── CLAUDE.md / APPROACH.md         # source of truth / design write-up
├── data/
│   ├── shl_product_catalog.json    # committed catalog (377 records) — loaded at runtime
│   ├── index/                      # committed retrieval index (BM25 + MiniLM embeddings)
│   ├── traces/C1..C10.md           # official sample conversations
│   └── ground_truth.json           # parsed traces (labels for Recall@10)
├── app/  main.py catalog.py retrieval.py agent.py llm.py schemas.py
├── evals/  replay.py  probes.py  retrieval_check.py  results/
├── scripts/  download_catalog.py  parse_traces.py  build_index.py  smoke.py
├── tests/   requirements.txt  render.yaml  Dockerfile
```

## Local run
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export GROQ_API_KEY=...        # primary LLM (Groq llama-3.3-70b-versatile)
export GEMINI_API_KEY=...      # fallback LLM (Gemini 2.5 Flash)
# optional: GEMINI_MODEL (default gemini-2.5-flash), GROQ_MODEL, LLM_TIMEOUT
# a .env file with these keys is auto-loaded (python-dotenv); .env is gitignored.

uvicorn app.main:app --host 0.0.0.0 --port 8000   # GET /health, POST /chat
```
On startup the service loads the committed catalog + retrieval index (zero shl.com /
catalog network calls) and warms the sentence-transformers model. The MiniLM model
weights (~90 MB) download from HuggingFace on first startup and are cached in-instance;
set `SHL_WARMUP=0` to skip warming (tests do this). Fully-loaded RSS ≈ 410 MB.

## Required env vars
| var | purpose | default |
|-----|---------|---------|
| `GROQ_API_KEY` | primary LLM | — (required) |
| `GEMINI_API_KEY` | fallback LLM | — (required) |
| `GEMINI_MODEL` | fallback model id | `gemini-2.5-flash` |

## Tests / evals
```bash
pytest -q                                   # unit + integration (mocked LLM; 1 live test if key set)
python evals/replay.py --runs 3             # LLM-simulated-user replay -> Recall@10 + contract asserts
python evals/probes.py                      # 10 behavior probes (P1-P10), PASS/FAIL each
python evals/replay.py --http <url> --runs 1  # replay against a running/deployed instance
python scripts/smoke.py <url>               # health + one full C9 conversation against a live URL
```
Free-tier note: Groq allows ~100k tokens/day per account; one full `--runs 3` replay
exceeds that. Add `--pace 3` (sleep between turns) to stay under per-minute limits, and
use `--runs 1` if the daily budget is tight.

## Data provenance
- Catalog downloaded **once** and committed. Reproduce: `python scripts/download_catalog.py`.
  The service **never** fetches the catalog URL or shl.com at runtime — committed file only.
- Retrieval index committed under `data/index/`. Rebuild: `python scripts/build_index.py`.
- Ground truth regenerated from traces: `python scripts/parse_traces.py`.

## Deploy to Render (free tier)
1. Push this repo to GitHub (ensure `.env` is **not** committed — it is gitignored).
2. Render → **New +** → **Blueprint** → select the repo. `render.yaml` configures a free
   Python web service: build `pip install -r requirements.txt`, start
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`.
3. In the service's **Environment**, set the two secret vars: `GROQ_API_KEY` and
   `GEMINI_API_KEY` (`GEMINI_MODEL=gemini-2.5-flash` is set by the blueprint).
4. Deploy. First boot downloads the MiniLM model, so the initial cold start is slower;
   subsequent requests are fast (model is warmed at startup, before requests are served).
5. Validate:
   ```bash
   python scripts/smoke.py https://<your-app>.onrender.com
   python evals/replay.py --http https://<your-app>.onrender.com --runs 1
   ```

## API
- `GET /health` → `{"status": "ok"}` (HTTP 200)
- `POST /chat` → `{"reply", "recommendations": null|[{name,url,test_type}], "end_of_conversation"}`.
  Full contract in [CLAUDE.md](CLAUDE.md). Stateless: send the full message history each call.
