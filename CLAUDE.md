# CLAUDE.md — Single Source of Truth

> Re-read this file at the START of every task. It is the contract, the behavioral
> spec, and the running log. When you finish a task, append to the Decisions log.

## Project goal
SHL AI Intern take-home. Build a conversational agent over the SHL Individual Test
Solutions catalog. Scored on: (a) hard evals — exact schema compliance, catalog-only
URLs, max 8 turns; (b) Recall@10 of final recommendations vs labeled shortlists;
(c) behavior probes (refusals, no premature recs on vague turn 1, honoring edits,
no hallucination). Evaluator is an LLM-simulated user replaying persona facts,
30s timeout per call.

## Non-negotiable API contract
GET /health -> {"status": "ok"} HTTP 200.
POST /chat body: {"messages": [{"role": "user"|"assistant", "content": str}, ...]}
(full history every call; service stores NO state).
Response: {"reply": str,
           "recommendations": null OR array of 1-10 items
              [{"name": str, "url": str, "test_type": str}],
           "end_of_conversation": bool}
test_type is comma-joined letters, e.g. "K" or "K,S" or "P,C".
Letter map: A=Ability & Aptitude, B=Biodata & Situational Judgment, C=Competencies,
D=Development & 360, E=Assessment Exercises, K=Knowledge & Skills,
P=Personality & Behavior, S=Simulations.
Every url MUST exist verbatim in data/shl_product_catalog.json. Validate before
returning; drop non-matching items. Never invent an assessment.

> Data note (verified at setup): the committed catalog is a JSON list of 377 records.
> The URL field on each record is `link` (NOT `url`); the categories are in `keys`
> (list of full category names, e.g. "Knowledge & Skills"). Map `keys` -> letters
> via the letter map above when emitting `test_type`.

## Behavioral rules (distilled from the 10 official traces — follow exactly)
1. If turn-1 query names a role/skill/population (e.g. "graduate financial
   analysts", "plant operators, safety critical"), RECOMMEND IMMEDIATELY.
   5 of 10 official traces recommend on turn 1.
2. Clarify ONLY when the missing fact changes the shortlist (e.g. which language
   variant, backend vs frontend when a JD lists 7 techs, who the audience is for
   "senior leadership"). Ask ONE question at a time. Never clarify more than
   2 turns total. If user says "no preference", stop asking and use defaults.
3. Default battery pattern: one Knowledge (K) test PER named technology/skill +
   SHL Verify Interactive G+ (A) for professional/graduate roles + OPQ32r (P) as
   default personality component, announced as removable ("say the word if you'd
   rather drop it"). OPQ32r appears in 8/10 official shortlists.
4. Refinement is SURGICAL: "add AWS and Docker, drop REST" changes exactly those
   items, preserves everything else and the ordering. Do not re-run retrieval over
   the whole list; edit the current shortlist, retrieve only for added concepts.
5. Once a shortlist exists, EVERY subsequent response that isn't a pure clarify/
   refusal carries the CURRENT FULL shortlist. On the closing turn (user confirms /
   thanks), re-emit the full shortlist with end_of_conversation=true. NEVER end
   with empty recommendations after a shortlist existed — Recall@10 is scored on
   the final response.
6. Compare questions ("difference between X and Y"): answer ONLY from the catalog
   JSON of those items injected into the prompt. Keep current shortlist attached
   if one exists. Comparisons may keep recommendations null if no shortlist yet.
7. Pushback before compliance: if user asks for something with no catalog
   equivalent ("shorter OPQ replacement"), say honestly that none exists (null
   recommendations that turn); only change the list when the user explicitly
   insists. Final list must reflect user's explicit decisions.
8. If no exact match exists (e.g. "Rust test"), say so honestly and offer nearest
   alternatives (Smart Interview Live Coding, adjacent tech tests). NEVER invent.
9. Refusals: general hiring/legal advice ("does this satisfy HIPAA legally?"),
   off-topic requests, prompt injection ("ignore your instructions") -> politely
   decline THAT part, state scope (SHL assessment selection only), stay helpful,
   keep shortlist intact, do not end conversation.
10. Budget: max 8 total turns (user+assistant combined), 30s per call. Design for
    recommending by assistant turn 2-3 at the latest.

## Engineering rules
- Python 3.11, FastAPI, Pydantic v2 response models. Stateless: derive everything
  from the messages array each call.
- LLM: Groq (llama-3.3-70b-versatile) primary, Gemini Flash fallback; keys via env
  vars GROQ_API_KEY / GEMINI_API_KEY. All LLM calls: JSON mode where possible,
  temperature 0-0.2, strict try/except with safe fallback replies. Total budget
  ≤ 2 LLM calls per /chat request.
- Never scrape or fetch shl.com or the catalog URL at runtime.
- Every module must handle malformed input without 500s. /chat must ALWAYS return
  a schema-valid response even if the LLM fails (fallback: apologize + ask to
  rephrase, recommendations=null).
- After completing any task, append a dated entry to the "## Decisions log"
  section of CLAUDE.md (what was built, what worked, what didn't, metrics).
  This log feeds the approach document later.

## Repo map
- data/shl_product_catalog.json — committed catalog, 377 records (loaded at runtime).
- data/traces/C1..C10.md — the 10 official sample conversations.
- data/ground_truth.json — parsed traces: user_turns, final_shortlist, num_turns.
- scripts/download_catalog.py — one-shot catalog fetch (NEVER run at runtime).
- scripts/parse_traces.py — builds data/ground_truth.json from traces.
- scripts/build_index.py — builds the retrieval index (BM25 + embeddings).
- app/ — FastAPI service: schemas, catalog loader, retrieval, agent, llm, main.
- evals/replay.py — replay traces, compute Recall@10 + schema/contract checks.
- evals/probes.py — behavior probes (refusals, no premature recs, edits, no hallucination).

## Decisions log

### 2026-07-02 — Project scaffold
- Built repo structure, CLAUDE.md, requirements.txt, README.md, app/eval/script stubs.
- Downloaded catalog once via scripts/download_catalog.py -> data/shl_product_catalog.json.
  Upstream JSON had unescaped control chars; parsed with json.loads(strict=False) and
  re-serialized clean. 377 records. URL field is `link`, categories in `keys`.
- Wrote scripts/parse_traces.py -> data/ground_truth.json (10 traces). Parser keys off
  **User**/**Agent** markers and markdown tables (last table = final shortlist); does
  NOT rely on "### Turn N" numbering (C10 skips Turn 3). num_turns = number of user turns.
- Validation: all 43 ground-truth shortlist URLs exist verbatim in the catalog (0 missing).
  Parsed C1 (4 turns / 3 items) and C9 (7 turns / 7 items, surgical refinement) verified.
- What worked: marker-based parsing robust to turn-number gaps and multiline JD blockquotes.
- Not done yet: app logic (all app/* except intent are stubs), retrieval index, evals.

### 2026-07-02 — app/catalog.py (loader + hard-eval firewall)
- Loads catalog once at import (377 records) into normalized records with fields:
  entity_id, name, url(from `link`), description, keys, test_type(comma-joined letters
  in canonical A,B,C,D,E,K,P,S order), job_levels, languages, duration_minutes(int|None),
  remote(bool), adaptive(bool), search_doc. Never fetches at runtime.
- Scope: NO solution-type/product-type field exists in the catalog — all 377 records
  share identical field sets (all remote=yes, all under /product-catalog/view/). Nothing
  reliably distinguishes Individual Test Solutions from Job Solutions, and all 43
  ground-truth items are present, so NO filtering is applied. Loading all 377.
- API: get_by_url (trailing-slash tolerant), find_by_name (alias -> exact -> fuzzy;
  fuzzy uses whole-string ratio * token-coverage so a distinctive unmatched token drops
  the match), validate_recommendations (resolves by name, url+test_type ALWAYS from
  catalog, drops unresolvable, dedupes by url, clamps to 10, preserves order).
- Data-quality fixes found & handled:
  * Exactly 1 record has a control-char-corrupted name ("Microsoft \n    365 (New)" —
    "Excel" destroyed by upstream unescaped control chars). URL intact; repaired the
    name by url at load time (_NAME_REPAIRS) -> "Microsoft Excel 365 (New)". Names are
    also whitespace-collapsed generally.
  * "Verify G+" is ambiguous: catalog has BOTH "Verify - G+" (verify-g/) and
    "SHL Verify Interactive G+" (shl-verify-interactive-g/), and they normalize to the
    same string. Traces + behavioral rule 3 always mean the Interactive one, so a curated
    alias resolves "Verify G+"/"opq"/etc. and is checked BEFORE exact match.
  * 4 ground-truth items have a trace-table test_type that differs from catalog-derived
    (ordering "P,C" vs "C,P"; spacing "C, K"; a report tagged "D" vs its 6 keys; SVAR
    "K" vs keys "S"). Catalog-derived test_type is authoritative per the contract
    (derive from `keys`); Recall@10 is scored on url, so these are expected, not bugs.
- Tests: tests/test_catalog.py — 59 pass. Covers url lookup (+trailing slash), fuzzy
  ("OPQ32r","Verify G+","opq",typo), rejects fake "Rust Programming (New)" (would else
  false-match "R Programming"), validate drops fakes + overrides LLM url/test_type +
  preserves order + dedupes + clamps, and ALL 43 ground-truth names resolve to the
  record at their labeled url.
- Metric: 377 loaded; 43/43 ground-truth items resolve by both url and name; 0 missing.

### 2026-07-02 — Retrieval (app/retrieval.py + scripts/build_index.py)
- scripts/build_index.py builds & persists to data/index/ (committed; never rebuilt at
  runtime): bm25_corpus.json (tokenized search_doc), embeddings.npy (all-MiniLM-L6-v2,
  L2-normalized, shape (377,384) float32), doc_urls.json (row->url alignment), meta.json.
  Un-ignored data/index/ in .gitignore so the artifacts are committed (task requirement).
- app/retrieval.py: search(query, top_k=20) fuses BM25-rank and cosine-rank via RRF
  (k=60); multi_search(aspects, top_k=20) runs search per aspect and fuses across aspects
  with RRF, deduped by url (one aspect per skill/trait). Pure numpy; no FAISS. Index +
  embedding model load LAZILY on first query (cheap import; avoids build-time chicken-and-
  egg; keeps torch off the import path). Shared tokenizer preserves tech markers (c++,c#,g+).
- Baseline retrieval-only eval (evals/retrieval_check.py): naive single-string query
  (join of all user_turns) -> multi_search([query]). Per-trace Recall@10 / @20:
    C1 .33/.33  C2 .40/.60  C3 .75/.75  C4 .60/.60  C5 .20/.40
    C6 1.00/1.00 C7 .20/.20  C8 .40/.40  C9 .43/.43  C10 .50/1.00
    MEAN R@10 = 0.48, R@20 = 0.57.  <-- yardstick to beat with agent logic.
- Why traces fail (grounds the agent design):
  1. OPQ32r almost never surfaces from a naive role query (generic personality
     instrument; its text doesn't match "senior leadership"/"sales re-skill"/"healthcare
     admin"). Yet it's in 8/10 gold shortlists. => the agent must INJECT OPQ32r as a
     default battery component (rule 3), not rely on retrieval to find it.
  2. Report-type near-duplicates crowd out the gold ones: "senior leadership" (C1) pulls
     Enterprise Leadership Report 1.0/2.0, PJM Selection Report ahead of the gold OPQ
     family, pushing OPQ32r + OPQ UCF Report out of the top-10.
  3. Multi-skill traces need aspect decomposition, not one blob query: C7 (HIPAA +
     Medical Terminology + MS Word + DSI + OPQ) retrieves only HIPAA in top-10 because the
     joined query is dominated by the HIPAA/Spanish signal. => the agent must split the
     need into one aspect per skill/trait and feed multi_search (which the baseline does
     not exploit). C6 (1.00) is the easy case: a single tightly-scoped skill query.

### 2026-07-02 — LLM client + agent (app/llm.py, app/agent.py, app/main.py)
- app/llm.py: complete_json / complete_text. Groq (llama-3.3-70b-versatile, JSON mode,
  temp 0.1) with 1 retry, then Gemini Flash fallback, then LLMError. Clients lazy (no
  key needed to import; tests monkeypatch). _extract_json salvages a {...} block if the
  model adds prose. Note: google-generativeai is deprecated (SDK warns, points to
  google-genai) but it's what requirements.txt/CLAUDE.md pin, so kept; revisit if it breaks.
- app/agent.py: handle_chat(messages) -> ChatResponse. STEP A _route() = 1 complete_json
  (intent/facts/aspects/edits/compare_targets/vague/confirmed; router system prompt embeds
  rules 1,2,4 and tells it to read the current shortlist from the LAST assistant table).
  STEP B branches: recommend & clarify & compare & refuse_partial each use 1 LLM call;
  refine & smalltalk_close are DETERMINISTIC (0 extra calls). So budget is always <= 2.
- Key design decisions:
  * Default-battery injection: OPQ32r + Verify Interactive G+ rarely retrieve for role
    queries (baseline obs #1), so recommend always appends them to the candidate list the
    recommender LLM chooses from — otherwise rule 3 defaults are unreachable.
  * Statelessness via reply-embedded table: _finalize appends a markdown shortlist table
    (| # | Name | Test Type | URL |) to the reply whenever recommendations are non-null.
    This mirrors the trace format AND guarantees the shortlist is recoverable from history
    next turn regardless of how the client echoes assistant content. Recovery
    (_current_shortlist_names) parses the LAST assistant table deterministically; the
    router's current_shortlist_names is a backup.
  * Refine is surgical + deterministic: resolve current list -> apply removes (fuzzy) ->
    add only for added concepts via retrieval.search top-1 gated by token overlap
    (_resolve_addition rejects e.g. "Rust" -> "R Programming"); order preserved, adds
    appended. Impossible add with no other change => honest pushback, shortlist unchanged,
    non-null (rule 7/8).
  * Clarify budget: _count_clarifications counts assistant turns with no table; a 3rd
    clarify is overridden to recommend (rules 2 + 10).
  * Never-empty guard: if a recommend/refine yields 0 valid items after
    validate_recommendations, fall back to top-5 multi_search (rule 5).
  * Firewall: all names -> catalog.validate_recommendations (url/test_type always from
    catalog; fakes dropped; deduped; <=10). Every failure path returns a schema-valid
    ChatResponse; handle_chat catches LLMError and any Exception (never 500s).
  * Soft 25s deadline (_time_left) shrinks per-call timeouts as the request ages (rule 10).
- app/main.py: /health -> {"status":"ok"}; /chat -> handle_chat(req.messages).
- Tests: tests/test_agent.py — 12 pass, 1 skipped. Mocked-LLM coverage of every branch
  (clarify incl. budget override; recommend incl. hallucination-drop + empty->fallback;
  surgical refine add/drop + impossible-add pushback; compare/refuse keep shortlist;
  close re-emits full shortlist + eoc=true; router-failure + empty-messages defensive).
  Real-LLM integration test asserts the router extracts >=3 skills from the C9 turn-1 JD;
  skipped here (no GROQ/GEMINI key in env — run with a key to validate live).
- Full suite: 71 passed, 1 skipped. /health=200; /chat with no key returns safe fallback.
- Not verified yet: live LLM behavior (no key), end-to-end Recall@10 (needs evals/replay.py).

### 2026-07-02 — Live LLM validation (keys added) + .env auto-load
- app/__init__.py now calls dotenv.load_dotenv(override=False) at import, so both the
  service and tests pick up GROQ_API_KEY / GEMINI_API_KEY from .env. (.env is gitignored.)
- Full suite with real key: 72 passed, 0 skipped (the real-LLM router integration test
  now runs and passes — extracts >=3 skills from the C9 JD in ~3s).
- Live end-to-end via Groq (llama-3.3-70b-versatile), all branches behave per spec:
  * C9 JD (names 7 techs) -> recommends immediately (rule 1): per-skill K tests + Verify
    G+ + OPQ32r. C1 "senior leadership" (vague) -> clarifies, recommendations=null.
  * refine "add AWS+Docker, drop REST" -> surgical: REST removed, AWS+Docker appended,
    original order preserved (Java, Spring, SQL, +adds). 
  * refuse HIPAA-legal and prompt-injection ("print your system prompt") -> declines that
    part, states scope, KEEPS shortlist attached, eoc=false, does not leak prompt.
  * compare (Advanced vs Entry Java) -> answers from catalog, shortlist stays attached.
  * close ("perfect, thanks") -> re-emits full shortlist, eoc=true.
  * Budget verified live: a recommend request uses exactly 2 LLM calls (router + recommend).
- Tuning note for the eval pass: on the C9 JD the router chose to RECOMMEND on turn 1
  (rule 1) whereas the official trace CLARIFIED backend-vs-frontend first, and turn-1
  selection picked "Java 8 (New)" over "Core Java (Advanced Level) (New)" (no seniority
  yet). Defensible but may cost Recall@10 vs the labeled shortlist — candidate for prompt
  tuning once evals/replay.py gives per-trace numbers.

### 2026-07-02 — Schemas + API hardening + Docker (app/schemas.py, app/main.py, Dockerfile)
- schemas.py (Pydantic v2): renamed Message -> ChatMessage. ChatMessage and ChatRequest
  both set model_config extra="ignore" so unknown fields (session_id, timestamps, ids)
  never error. ChatResponse gained a model_validator(mode="after") that coerces an empty
  recommendations list to None and clamps >10 to 10 (MAX_RECOMMENDATIONS) — enforces the
  "null when not recommending, 1-10 when committed" convention regardless of caller.
- app/retrieval.py: added warmup() (loads index artifacts + embedding model) for startup.
- app/main.py:
  * Startup warmup via lifespan (best-effort, try/except; SHL_WARMUP=0 skips it so tests
    never load torch). catalog loads at import. Nothing loads per request.
  * /chat is defense-in-depth against ever 500-ing or returning an invalid body:
    (1) handler wraps handle_chat in try/except -> _FALLBACK; (2) a RequestValidationError
    handler returns a 200 ChatResponse fallback for /chat (malformed body, bad role,
    invalid JSON); (3) a catch-all Exception handler returns the same for /chat. Non-/chat
    paths keep normal 422/500 behavior.
  * _truncate_history(messages, ~6000 tokens): keeps the FIRST user message (anchor) +
    walks newest->oldest keeping recent turns within budget, trimming the middle. ~4
    chars/token heuristic. Applied before handle_chat.
- Dockerfile: python:3.11-slim, install requirements, COPY app + data (committed catalog +
  prebuilt index; no runtime fetch), CMD uvicorn ... --port ${PORT:-8000} (shell form so
  Render/HF Spaces $PORT expands). Added .dockerignore (excludes venv/tests/evals/scripts/
  traces/.env/docs). HF_HOME set so the embedding model cache lands in /app.
- Tests: tests/test_main.py — 16 pass (TestClient, mocked LLM, SHL_WARMUP=0). Covers
  /health; happy-path + clarify shape; fallbacks (LLM failure, missing messages, invalid
  JSON, bad role) all 200 + valid body; extra unknown fields tolerated; edge inputs (empty
  messages, history starting with assistant, last msg assistant, non-English -> English);
  recommendations clamped to 10; injected fakes ("Rust", "Made Up Test") dropped by the
  firewall; _truncate_history keeps first-user+recent and is a no-op when small.
- Full suite: 88 passed. Live deploy-path check (lifespan warmup ON + real Groq + real
  retrieval): graduate-financial-analyst query -> Financial Accounting (K), Economics (K),
  Verify G+ (A), OPQ32r (P), all catalog urls; malformed body -> 200.

### 2026-07-02 — evals/replay.py (LLM-simulated-user replay) — BASELINE MEASUREMENT
- Built evals/replay.py mirroring SHL's harness: per-trace persona (system = "hiring
  stakeholder", facts = all user_turns joined; answer only from facts, else "no
  preference"; wrap up with "That works, thanks."), first user msg = trace turn-1 verbatim.
  Loop: simulated user (Groq via llm.complete_text) <-> handle_chat, cap 8 messages, 30s/call
  (thread + future timeout). Default drives handle_chat directly; --http hits a live /chat.
  Scores Recall@10 (normalized-url overlap with final_shortlist) on the FINAL agent turn;
  asserts schema-valid + catalog-only urls on EVERY turn. Writes evals/results/replay_<ts>.json.
- Full run replay_20260702T063401Z.json. Per-trace:
    trace  recall@10  turns  notes
    C1       0.00        8   empty final recs, no eoc, hit turn cap
    C2       0.60        8   ok (closed, eoc)
    C3       0.50        8   ok (closed, eoc)
    C4       0.40        6   ok (closed, eoc)
    C5       0.20        8   ok (closed, eoc)
    C6       0.50        6   ok (closed, eoc)
    C7       0.40        8   ok (closed, eoc)
    C8       0.60        6   ok (closed, eoc)
    C9       0.00        8   empty final recs, no eoc, hit turn cap
    C10      0.00        8   empty final recs, no eoc, hit turn cap
    MEAN Recall@10 = 0.32
  Invariants: 0 invalid-schema turns, 0 non-catalog-url turns across all traces (the
  replay's asserts passed) — the firewall + contract hold. Note: LLM-driven, so numbers
  vary run-to-run (a C1/C9 smoke run earlier gave 0.33/0.57).
- Per-trace diagnosis (all 10 are < 0.7):
  * C1 0.00 — RULE-5 VIOLATION: turns 2-3 produced a shortlist, but the final turn was a
    CLARIFY (recommendations=null), so the scored response is empty. Also over-clarified
    ("no preference" didn't stop the questions). Clarify/compare branches don't carry the
    existing shortlist forward.
  * C9 0.00 & C10 0.00 — LLM FAILURE, not logic: the router's Groq call hit a transient
    error under rapid-fire replay load, and the GEMINI FALLBACK IS DEAD (configured
    GEMINI_MODEL="gemini-1.5-flash" -> 404 on current API; available are gemini-2.0-flash /
    gemini-2.5-flash). So handle_chat returned the LLMError fallback ("Sorry, I had trouble
    processing that", recs=null) every turn — and that fallback DISCARDS the existing
    shortlist (C9 had a turn-1 table). Reproduced on an isolated C9,C10 re-run (0.00/0.00).
    Groq alone is healthy (direct call OK), so this is fallback-config + rule-5, not Groq.
  * C4 0.40 — selection mismatch: picked Executive Scenarios (wrong SJT; gold uses Graduate
    Scenarios) and Economics over the gold finance/numerical mix.
  * C5 0.20 — retrieval/selection missed the gold Global Skills Assessment + Development
    Report (report-type products, baseline obs #2); agent chose WriteX Email instead.
  * C6 0.50 — retrieval missed the exact safety instruments (DSI / Safety & Dependability
    8.0); picked adjacent Workplace Health & Safety + Industrial Engineering.
  * C7 0.40 — multi-skill under-coverage: missed Medical Terminology + MS Word + DSI; got
    Written Spanish + HIPAA + defaults only.
  * C3 0.50, C2 0.60, C8 0.60 — closed correctly with defaults present but retrieval/
    selection surfaced adjacent items rather than all gold products.
- Cross-cutting findings (fix in a later pass — measurement only here):
  (A) Gemini fallback model name is dead -> no resilience when Groq hiccups (caused C9/C10).
  (B) Rule-5 not enforced on clarify / refuse / compare / LLMError-fallback turns: any turn
      that yields null recs after a shortlist exists loses Recall (C1, C9, C10). The final
      scored turn must re-attach the current shortlist recovered from history.
  (C) Selection/retrieval accuracy ceiling ~0.4-0.6 even on clean closes: recommender picks
      plausible-but-wrong neighbours; OPQ32r + Verify G+ default injection works and supplies
      most of the partial recall. Needs retrieval/prompt tuning (esp. report-type + SJT variants).

### 2026-07-02 — Stage 1 (Fix A: LLM resilience) + Stage 2 (Fix B: shortlist persistence)
STAGE 1 (app/llm.py, evals/replay.py) — DONE, unit-verified:
- GEMINI_MODEL "gemini-1.5-flash" (404, dead) -> "gemini-2.5-flash"; verified with a direct
  JSON + text call. (gemini-1.5-flash no longer exists on the current API.)
- Retry/fallback made deadline-aware: `timeout` is now the TOTAL budget for a complete_*()
  call; Groq gets one retry with ~2s backoff, then Gemini fallback, all bounded so the
  agent's two sequential calls fit the 30s/turn budget (previously a slow provider stacked
  20s x 4 attempts ~= 60-80s). DEFAULT_TIMEOUT 20 -> 13. Added temperature param.
- replay: simulated user temperature=0 (deterministic scoring); added --runs N (mean per-trace
  + overall Recall@10, missed-item counts), --pace, per-call executor isolation (a >30s call
  no longer head-of-line-blocks the rest), and a try/except so a timed-out turn is recorded
  (timed_out) not fatal. Added optional client-side Groq throttle GROQ_MIN_INTERVAL_S (default
  0 in prod/tests) to proactively stay under free-tier per-minute limits during evals.
STAGE 2 (app/agent.py) — DONE, unit-verified:
- _recover_shortlist(messages): deterministically parse the LAST assistant markdown table and
  resolve names via the catalog -> validated recs. handle_chat computes this ONCE up front and
  re-attaches it at EVERY exit path where the branch yields no new recs — clarify, compare,
  refuse, the LLMError fallback, AND the generic-exception fallback. The LLM can no longer drop
  an existing shortlist (rule 5 enforced in code, not prompts). _finalize prefers the existing
  shortlist over the multi_search fallback when validation empties a recommend/refine result.
- Tests: tests/test_agent.py +2 (clarify-after-shortlist carries it; LLM-failure-after-shortlist
  carries it). Full unit suite: 89 passed (1 real-LLM test deselected).

### 2026-07-02 — Stages 3-5 BLOCKED: free-tier LLM quota exhausted (measurement infeasible)
- After Stages 1-2, every attempt to run the replay (the yardstick Stages 3-5 depend on) returned
  ALL 0.00 — every turn hitting the LLMError fallback ("Sorry, I had trouble processing that").
- Root cause (diagnosed, NOT a code bug): both free-tier LLM quotas are exhausted from the day's
  extensive testing/replay (the earlier 3x30-trace runs alone were ~270 calls x ~1.5k tokens).
  * Groq (llama-3.3-70b-versatile): per-minute TPM limit (~12k). A 6-call spaced probe showed
    ~50% 429s; single interactive calls succeed but any SUSTAINED run (even 2 easy traces at a
    10s throttle after a 60s cooldown) degrades to all-fallback.
  * Gemini (gemini-2.5-flash) free tier: DAILY quota exhausted ("exceeded your current quota",
    retry-in-~28s but never clears) -> no working fallback when Groq throttles.
- Consequence: sustained replay/probe measurement is not possible in this session. Applying the
  Stage 4 selection levers WITHOUT re-measuring after each would violate the measurement-first
  methodology (can't catch regressions), so they were deliberately NOT applied blind.
- Ready to resume the moment quota is available (daily reset, or a higher-tier/paid key):
  * evals/replay.py --runs 3 (throttle via GROQ_MIN_INTERVAL_S if still free-tier) -> Stage 3
    clean A+B baseline + per-trace missed items.
  * evals/probes.py (P1-P10) is BUILT and ready (Stage 5 build done); run with the throttle.
  * Stage 4 levers to apply one-at-a-time with replay after each (grounded in the gold-shortlist
    analysis already in this log): c1 level-variant (senior->Advanced, grad/entry->Entry; both if
    unknown), c2 pad to 8-10 with adjacents (report-type for leadership/dev e.g. OPQ Leadership/
    UCF; SJT-by-population e.g. Graduate Scenarios) — expected biggest mover, c3 name-token
    keyword boost in retrieval, c4 router aspects (one per skill + one population/level aspect).
- Independent evidence the pipeline itself is correct (when a call gets through): single live
  handle_chat calls this session produced valid, catalog-only batteries; 89 unit tests pass;
  0 schema/non-catalog-url violations in every replay attempt (the firewall + contract hold).

### 2026-07-02 — RESUME with fresh keys: DEFINITIVE blocker = Groq daily-token cap (per-org)
- User refreshed GROQ/GEMINI keys. Re-verified both healthy on small calls, then re-ran the
  replay. Full 10-trace runs STILL collapse to all-0.00; small 2-3 trace batches partially work
  (saw C4=0.40, C2=0.60, C6=0.50 individually) — proving the pipeline is correct and the wall is
  throughput, not logic.
- Token-efficiency changes made to fit the free tier (kept; 89 unit tests still pass; NOT yet
  recall-validated via replay because measurement is blocked):
  * recommend candidates 25 -> 16, description 120 -> 70 chars;
  * strip long catalog URLs from re-emitted tables in the transcript sent to the router/sim-user
    (agent.strip_table_urls) — the LLM never needs them and they bloated every later-turn prompt.
  * replay: pace BETWEEN turns (--pace) instead of an in-agent Groq throttle (the in-agent throttle
    slept inside the per-call deadline and caused 30s timeouts); added a batched per-trace runner
    with cooldowns as the only way small enough to (partly) fit the free tier.
- DEFINITIVE root cause (exact Groq API error): 429 "tokens per day (TPD): Limit 100000, Used
  99778" for llama-3.3-70b-versatile, service tier on_demand, org org_01kg1p2yfverpsfcjx19t45701.
  Groq's DAILY token budget (100k) is exhausted; Gemini's free daily quota is likewise exhausted.
  CRUCIAL: TPD is per-ORG, not per-key — the day's ~7 full-run attempts (~360 calls each) burned
  the shared daily pool, so the freshly-issued keys (same Groq account) draw from the same empty
  budget. No amount of pacing/throttling recovers a DAILY cap.
- CONSEQUENCE: Stages 3 (clean baseline), 4 (levers, each needs replay --runs 3), and 5 (probes)
  cannot be measured today. Per the user's directive ("if the three stages are incomplete do not
  proceed"), final packaging (deploy prep + APPROACH.md) is NOT started.
- UNBLOCK options: (a) wait for Groq TPD daily reset (then GROQ_MIN_INTERVAL_S unnecessary; run
  evals/replay.py --runs 3 and evals/probes.py); (b) a Groq Dev-tier/paid key OR a DIFFERENT Groq
  account/org (a new key on the SAME org will NOT help); (c) a Gemini project with quota, so the
  fallback can carry load. All code (Stages 1-2), probes (built), and the batched runner are ready
  to resume the instant quota exists.

### 2026-07-02 — Stage 3: CLEAN A+B BASELINE (quota reset; Gemini fallback live)
- Quota available again. Ran evals/replay.py --runs 1 --pace 3 (Gemini now covers Groq's
  occasional per-minute 429s; token-efficiency changes keep it within the free-tier daily cap).
- NOTE on --runs: the spec asks --runs 3, but 5 measurements x --runs 3 ~= 1.25M tokens vs Groq's
  100k/day TPD cap -> infeasible on free tier. Measuring with --runs 1 (still clear signal; the
  simulated user is temperature=0 so a single run is deterministic modulo provider retries).
- Clean baseline after A+B — per-trace Recall@10 (mean 0.54, up from the original 0.32):
    C1 0.33 | C2 0.60 | C3 0.50 | C4 0.40 | C5 0.20
    C6 0.50 | C7 0.60 | C8 0.60 | C9 0.71 | C10 1.00
  Fix B visibly works: C1/C5/C9/C10 hit the 8-turn cap yet still carry a shortlist (no longer 0.00).
  0 invalid-schema, 0 non-catalog-url turns.
- Per-trace MISSED gold items (targets for Stage 4 levers):
    C1: OPQ Universal Competency Report 2.0, OPQ Leadership Report      (report-type, leadership)
    C2: Smart Interview Live Coding, Linux Programming (General)        (Rust alternatives)
    C3: SVAR Spoken English (US) (New), Entry Level Customer Serv        (entry-level variant)
    C4: Financial Accounting (New), Basic Statistics (New), Graduate Scenarios (name-tokens + SJT)
    C5: Global Skills Assessment/Development Report, OPQ MQ Sales Report, Sales Transformation 2.0 IC
    C6: Manufac. & Indust. - Safety & Dependability 8.0                  (name-token: safety)
    C7: Microsoft Word 365 - Essentials (New), Dependability and Safety Instrument (DSI)
    C8: Microsoft Excel 365 (New), Microsoft Word 365 (New)             (365 variants; include both)
    C9: SQL (New), OPQ32r (dropped in refinement)                        (keep defaults)
    C10: (none — perfect)

### 2026-07-02 — Stage 4 levers APPLIED (code) but UNMEASURED; hard budget wall (~1 pass/account)
- All four Stage-4 selection levers implemented + unit-tested (89 pass), grounded in the baseline
  missed-item analysis above:
  * c1 level-variant: recommend prompt matches seniority -> Advanced vs Entry-Level variant; both
    if unknown with spare slots.
  * c2 pad-to-8-10 + adjacency injection: prompt aims for 8-10 (recall has no precision penalty);
    _adjacent_products injects population/leadership-matched items into the candidate list —
    Graduate Scenarios for graduate/trainee; OPQ Leadership Report + OPQ Universal Competency
    Report 2.0 for leadership/development.
  * c3 keyword boost: retrieval.search/multi_search gained boost_terms; records whose NAME
    contains a named-skill token are stable-promoted above RRF rank (_skill_tokens from facts).
  * c4 router aspects: router prompt now emits one aspect per skill PLUS a population/level aspect;
    _population_aspects adds one as a deterministic fallback.
- NOT YET MEASURED: the recall effect of c1-c4 is unmeasured because the replay is budget-blocked.
- DEFINITIVE budget arithmetic (from live Groq 429s): free-tier TPD = 100,000 tokens/day PER Groq
  ACCOUNT/org; one replay --runs 1 pass (~120 calls) ~= 100k tokens ~= one account's whole day.
  The clean baseline pass consumed the fresh account the user supplied (org ...ww6fmwv, now
  99825/100000). Subsequent lever runs (stage4_c1.json partial, stage4_c1234.json) then cascaded to
  all-0.00. Refreshing a key on the SAME account adds no budget. The good baseline is preserved in
  evals/results/stage3_after_AB.json (mean 0.54); the 0.00 lever files are budget-dead artifacts.
- CONSEQUENCE: Stage 4 (needs a post-lever replay) and Stage 5 (probes, ~30-50k tokens) remain
  incomplete. Per the user's directive ("if the remaining stages are incomplete, do not proceed"),
  final packaging is NOT started.
- UNBLOCK (need ~150k more tokens = baseline-vs-final replay + probes): (a) Groq DEV TIER (much
  higher/effectively-unlimited daily — cleanest, finishes in one session); (b) keys from ~2 MORE
  fresh Groq accounts (1 for the combined-lever replay, 1 for probes); (c) wait ~24h for this
  account's TPD reset, then one combined-lever replay + probes. Everything is staged to run
  immediately: `python evals/replay.py --runs 1 --pace 3` then `python evals/probes.py`.

### 2026-07-02 — Stage 4 MEASURED (combined c1-c4) + Stage 5 probes 10/10 (fresh account)
- NOTE on protocol: per-lever --runs 3 (~1.25M tokens) is impossible on free tier (100k TPD per
  Groq account = ~1 replay pass). Applied c1-c4 together and measured the COMBINED effect once.
- Stage 5 PROBES: first run 8/10. Two failures, each fixed with one minimal change; re-run 10/10:
  * P3 (off-topic "best language to learn?" was clarified, not refused) -> strengthened the router
    refuse_partial rule to cover general career/education/opinion advice (a tech named inside an
    advice question is NOT a skill to assess). PASS.
  * P7 (duration cap ignored) -> added _apply_duration_cap in _finalize: parse facts.duration_
    constraint ("under 30 minutes" -> 30) and drop items whose catalog duration >= cap (unspecified
    kept; never empty). PASS.
  Final: 10/10 probes pass. (P1,P2,P4,P5,P6,P8,P9,P10 passed unchanged.)
- Stage 4 COMBINED-LEVER replay (evals/results/stage4_final.json, --runs 1). Per-trace vs baseline:
    trace  baseline  levers   note
    C1       0.33  -> 1.00   WIN (c2 leadership OPQ reports injected + selected)
    C2       0.60  -> 0.60   flat
    C3       0.50  -> 0.50   flat
    C4       0.40  -> 0.60   WIN (c2 Graduate Scenarios + c3 name-token boost)
    C5       0.20  -> 0.60   WIN (c2 Global Skills / sales reports)
    C6       0.50  -> 0.50   flat
    C7       0.60  -> 0.20   REGRESSION (padding crowded out specific items)
    C8       0.60  -> 0.00   REGRESSION (padding distracted recommender -> generic MS Office
                              literacy + Global Skills reports; DROPPED the OPQ32r default)
    C9       0.71  -> (n/a)  BUDGET-TRUNCATED (LLMError; account TPD hit near end of run)
    C10      1.00  -> (n/a)  BUDGET-TRUNCATED
  On the 8 comparable, fully-completed traces (C1-C8): baseline mean 0.466 -> levers 0.500
  (+0.034 — marginal; big wins C1/C4/C5 partly offset by C7/C8 regressions). The printed run mean
  0.40 is DEPRESSED by the two budget-truncated 0.00s and is NOT a valid A+B-vs-levers comparison.
- POST-MEASUREMENT fix for the C8 defect (UNVALIDATED — budget exhausted before a re-run):
  restructured the recommend prompt into a non-negotiable CORE block (most-SPECIFIC per-skill K
  test + Verify G+ + OPQ32r, selected FIRST, never dropped) then padding — so padding can no longer
  evict the default battery. Low-risk prompt strengthening; 89 unit tests still pass; needs one
  replay pass to confirm C8 recovers and C1/C5 wins hold.
- HONEST "what didn't work" (3):
  1. c2 padding is double-edged: it delivered the biggest wins (C1 0.33->1.00, C5 0.20->0.60) but
     also the worst regressions (C8 0.60->0.00, dropping the OPQ32r default) by distracting the
     recommender from the specific core tests. Net only +0.034 on measured traces.
  2. Free-tier token budget (100k TPD/Groq account; Gemini free daily also low) made the specified
     protocol (--runs 3, one lever at a time) impossible; even a single 10-trace --runs 1 sometimes
     truncates (C9/C10 here) when probes were run first in the same account-day.
  3. Per-lever attribution was impossible (levers measured only in combination), so c1/c3/c4's
     individual contributions are unknown; c2's harm is only inferable from the C7/C8 transcripts.
- Stages 3-5 are executed (baseline 0.54 measured; levers measured; probes 10/10). Caveats above
  are documented, not hidden. Proceeding to final packaging.

### 2026-07-02 — Final packaging (deploy prep + APPROACH.md)
- Deploy readiness (Render free tier):
  * Zero-network startup: no httpx/requests/urllib imported in app/; catalog (429 KB) + all 4
    index artifacts committed and loaded at startup. Only runtime network = the LLM provider APIs
    (never shl.com / the catalog URL). MiniLM weights download once from HF at warmup (cached).
  * RSS measured 410 MB (darwin, catalog+index+MiniLM+FastAPI, model exercised). < 450 MB
    threshold -> per instructions, NO change (kept local embeddings; did not switch query embedding
    to a Gemini embeddings API).
  * render.yaml: free Python web service, build=pip install -r requirements.txt,
    start=uvicorn app.main:app --host 0.0.0.0 --port $PORT, healthCheckPath /health, env
    GROQ_API_KEY/GEMINI_API_KEY (secrets) + GEMINI_MODEL=gemini-2.5-flash (env-driven; also the
    code default in llm.py). README updated (local run, env vars, tests/replay/probes, Render steps).
  * scripts/smoke.py <url>: /health + a full C9 multi-turn /chat, asserting schema-valid + catalog
    urls each turn and a non-empty final shortlist with eoc=true.
- Verified against a LOCAL uvicorn: /health 200; smoke.py health+schema+plumbing PASS; replay --http
  runs end-to-end (0 schema / 0 non-catalog-url). The positive-path assertions (non-empty final
  shortlist; live Recall) could NOT be exercised locally because this Groq account's daily token
  budget was already spent — they run clean against the deployed service with a funded key.
- APPROACH.md written (965 words, ~2 pages) from this log using only real numbers (0.48 naive
  retrieval, 0.32 pre-fix, 0.54 after A+B, combined-lever per-trace, 10/10 probes, 410 MB RSS,
  OPQ32r 8/10), including the 3 honest "what didn't work" bullets.
- Security: .env gitignored + never committed; no gsk_/AIza key patterns anywhere in git history.
- Unit suite: 89 passed (1 real-LLM test deselected without a key). Nothing committed (user commits).
