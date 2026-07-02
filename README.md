# SHL Conversational Assessment Recommender

A stateless FastAPI service that recommends SHL Individual Test Solutions through a
short, multi-turn conversation. Given the full message history on every call, it
clarifies only when necessary, builds an assessment battery, honors surgical edits,
and returns a schema-valid response with catalog-verified URLs.

> The authoritative spec — API contract, behavioral rules, engineering rules, and the
> running decisions log — lives in [CLAUDE.md](CLAUDE.md). Read it first.

## Layout
```
SHL-Assignment/
├── CLAUDE.md                       # source of truth (contract + rules + log)
├── data/
│   ├── shl_product_catalog.json    # committed catalog (377 records) — loaded at runtime
│   ├── traces/C1..C10.md           # official sample conversations
│   └── ground_truth.json           # parsed traces (labels for Recall@10)
├── app/                            # FastAPI service
│   ├── main.py  catalog.py  retrieval.py  agent.py  llm.py  schemas.py
├── evals/
│   ├── replay.py                   # Recall@10 + contract compliance
│   ├── probes.py                   # behavior probes
│   └── results/
├── scripts/
│   ├── download_catalog.py         # one-shot catalog fetch (NEVER at runtime)
│   ├── parse_traces.py             # traces -> data/ground_truth.json
│   └── build_index.py              # build retrieval index
├── requirements.txt
└── README.md
```

## Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set API keys (see [CLAUDE.md](CLAUDE.md) engineering rules):
```bash
export GROQ_API_KEY=...        # primary LLM
export GEMINI_API_KEY=...      # fallback LLM
```

## Data provenance
- The catalog was downloaded **once** and committed. To reproduce:
  `python scripts/download_catalog.py`. The deployed service **never** fetches the
  catalog URL or shl.com at runtime — it loads the committed file only.
- Ground truth is regenerated from the traces: `python scripts/parse_traces.py`.

## Run (once implemented)
```bash
uvicorn app.main:app --reload          # serves GET /health and POST /chat
python evals/replay.py                 # Recall@10 + contract checks
python evals/probes.py                 # behavior probes
```

## API
- `GET /health` → `{"status": "ok"}`
- `POST /chat` → see the contract in [CLAUDE.md](CLAUDE.md#non-negotiable-api-contract).
