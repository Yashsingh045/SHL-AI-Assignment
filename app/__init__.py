"""SHL conversational assessment recommender (FastAPI service).

See CLAUDE.md for the contract, behavioral rules, and engineering rules.
"""
# Load .env (GROQ_API_KEY / GEMINI_API_KEY) so env vars are set before any module
# reads them. No-op if python-dotenv or the file is absent; never overrides real env.
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except Exception:  # pragma: no cover - dotenv optional
    pass
