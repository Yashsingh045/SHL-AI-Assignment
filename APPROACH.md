# Approach — SHL Conversational Assessment Recommender

## 1. Design: a stateless two-step agent, guarded by code

Each `POST /chat` is stateless — everything is re-derived from the `messages` array.
Every turn runs **two LLM steps at most**: a **router** that classifies intent
(`clarify | recommend | refine | compare | refuse_partial | smalltalk_close`) and
extracts state (facts, aspects, edits, compare targets), then a single **intent
branch**. `refine` and `smalltalk_close` are fully deterministic (0 extra LLM calls),
so the budget is always ≤ 2 calls/request.

An explicit state machine plus deterministic guardrails beats one mega-prompt because
the behavior probes demand *reliability*, not eloquence: a single prompt asked to
"remember the shortlist, never invent URLs, refuse off-topic, honor edits, and close
cleanly" fails intermittently, and each failure is silent. Splitting classification
from action, and enforcing the invariants in Python, makes the failure modes
impossible rather than unlikely. Two structural guarantees carry the hard evals:

- **Catalog-whitelist firewall.** Every recommendation the LLM proposes is resolved by
  name against the committed catalog; `url` and `test_type` are taken **only** from the
  matched record, unresolved items are dropped, the list is deduped and clamped to 10.
  A non-catalog URL is therefore structurally impossible — confirmed by 0 non-catalog-URL
  turns across every replay run.
- **Code-level shortlist persistence.** The current shortlist is recovered
  deterministically each turn by parsing the last assistant table, and re-attached at
  *every* exit path — clarify, compare, refusal, and even the LLM-error fallback. The
  LLM never gets a chance to drop it, so the final scored turn can never lose the list.

`/chat` never 500s: malformed bodies, validation errors, and LLM failures all return a
schema-valid fallback with HTTP 200.

## 2. Data

The SHL-provided catalog JSON (377 records) is downloaded **once** and committed; the
service loads it at startup and never fetches shl.com or the catalog URL at runtime.
Each record is normalized to `{name, url (from the raw "link" field), description,
keys, test_type, job_levels, languages, duration_minutes, …}`. `test_type` is derived
by mapping each `keys` phrase to its letter (A/B/C/D/E/K/P/S). One record's name was
corrupted by unescaped control characters in the source JSON ("Excel" destroyed); it
is repaired by URL at load time. All 43 ground-truth shortlist items resolve to the
catalog by both URL and name.

## 3. Retrieval

Hybrid, pure-Python, no vector DB (~500 records don't need one): **BM25** over a
per-record search document + **MiniLM** (`all-MiniLM-L6-v2`) dense embeddings, fused by
**Reciprocal Rank Fusion** (k=60). The agent issues **multi-aspect** queries — one
retrieval string per named skill plus one for the population/level — and fuses across
aspects. A **keyword boost** promotes catalog records whose name contains an extracted
skill token above their RRF rank.

Retrieval alone is weak (naive single-query baseline: Recall@10 = 0.48). The official
traces revealed a **default-battery heuristic** that retrieval cannot learn: OPQ32r
appears in 8/10 official shortlists yet almost never surfaces from a role query, so the
agent **injects** OPQ32r + SHL Verify Interactive G+ as selectable defaults, and matches
level variants to seniority (senior → Advanced, graduate/entry → Entry-Level).

## 4. Evaluation

Two harnesses. **`evals/replay.py`** mirrors SHL's evaluation: a Groq-simulated
hiring-stakeholder (temperature 0) built from each trace's facts drives a multi-turn
conversation against the agent (≤ 8 turns, 30s/call), and the final response is scored
Recall@10 against the labeled shortlist. **`evals/probes.py`** runs 10 targeted
behavior probes (no premature recs, refusals, prompt-injection, surgical edits, duration
caps, honest no-match, clean close).

Progression (Recall@10, mean over the 10 traces unless noted):

| stage | mean | notes |
|-------|------|-------|
| naive retrieval baseline | 0.48 | single-query, no agent |
| agent baseline (pre-fix) | 0.32 | C1/C9/C10 = 0.00 (lost shortlist / dead fallback) |
| **clean baseline after A+B** | **0.54** | Fix A (LLM resilience) + Fix B (shortlist persistence) |
| combined levers c1–c4 | mixed | see below |

Per-lever `--runs 3` was infeasible (see below), so the four selection levers were
measured **in combination** once. On the 8 fully-completed traces they moved the mean
from 0.466 → 0.500: large wins (C1 0.33→1.00, C5 0.20→0.60, C4 0.40→0.60 from
report/SJT injection) partly offset by regressions (C7 0.60→0.20, C8 0.60→0.00).
Probes: **10/10** after fixing two failures (off-topic advice now refused; duration caps
now honored). Across all runs: 0 schema violations, 0 non-catalog URLs.

**What didn't work (honest):**
1. **Padding is double-edged.** Padding toward 8–10 items delivered the biggest wins
   *and* the worst regressions — on C8 it distracted the recommender into generic
   "MS Office literacy" + report products and dropped the OPQ32r default. Net only
   +0.034 on measured traces. (A follow-up prompt fix secures the core battery before
   padding; it is unvalidated because the token budget was spent.)
2. **Free-tier token budget capped the method.** Groq free tier is ~100k tokens/day per
   account and one `--runs 3` replay is ~1.25M tokens, so the specified per-lever
   `--runs 3` protocol was impossible; even a single `--runs 1` sometimes truncated the
   last traces. Measurements use `--runs 1`.
3. **No per-lever attribution.** Because the levers were measured only in combination,
   c1/c3/c4's individual contributions are unknown; c2's harm is inferable only from the
   C7/C8 transcripts.

## 5. AI-tools disclosure

Built with **Claude** (Anthropic) via **Claude Code** for planning and agentic coding.
Every design decision, measurement, and dead-end is recorded in [CLAUDE.md](CLAUDE.md)'s
running Decisions log and is defensible from the numbers there.

## 6. Stack justification

**FastAPI** (async, Pydantic v2 contract validation, trivial Render deploy). **Groq
`llama-3.3-70b-versatile`** as the primary LLM — a 70B model fast enough to fit two
sequential calls inside the 30s/turn budget — with **Gemini 2.5 Flash** as a
cross-provider fallback for resilience. **Pure-Python retrieval** (BM25 + MiniLM + RRF):
at ~500 records a vector DB adds operational weight for no benefit, and the precomputed
embedding matrix keeps the fully-loaded service at ≈ 410 MB RSS — within the free tier.
