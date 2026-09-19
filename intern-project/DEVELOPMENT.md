# DEVELOPMENT

Working notes for this repository: architecture choices, verification state, known issues and honest limits. PRD.md, TRD.md, schemaDB.md and refrence.md (in the repository root above `intern-project`) remain the authority for requirements and contracts. This file describes work actually done, not planned intent.

## Implementation state

Implemented and offline-tested in this work stream:

- `streamlit_app.py`: Streamlit UI. Three areas (Account, New decision, History), HTTP-only via `src/ui_client.py`.
- `src/ui_client.py`: session-bound client. Per-call Bearer headers, Decimal money serialization, explicit nulls, 130-second timeout, redirect/automatic-retry prevention, 401 token clearing, injectable transport. The live `requests` path was not exercised against a running server.
- `eval/run_eval.py`: `python -m eval.run_eval --cases data/sample_test_cases.json` or `--csv data/tickets.csv`. Lazily imports the backend and calls the real `DecisionService(Settings.load()).decide(TicketInput)`. An exact allowlist prevents labels/IDs/identity fields from entering inputs. Errors count toward the total denominator; accuracy is `correct/total`.
- `tests/test_ui_client.py` and `tests/test_eval.py`: 17 offline tests. No keys, server, or database required.

Observed full-suite state (2026-09-17, `python -m pytest -q` from `intern-project`, global interpreter): collection was interrupted by **2 errors; no full-suite tests executed**. Both `tests/test_auth.py` and `tests/test_tickets.py` failed with `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`, consistent with incompatible installed FastAPI/Starlette versions. An earlier collection attempt also found retrieval not yet present; that module now exists. These modules were not modified here. Run inside a clean virtual environment before judging integration. The end-to-end journey against a running API remains unverified.

## Architecture and structure

Request flow: Streamlit form → `APIClient` (Bearer, timeout, no redirects/retries) → FastAPI route → auth dependency → validation (`src/schemas.py`) → retrieval → Gemini generation with validation and one repair pass → atomic SQLite insert of ticket and decision → 201 response. History reads are owner-scoped through the JWT identity. The frontend never imports `src.config`, `src.database`, or the Gemini SDK; its only configuration is `API_BASE_URL` from the environment.

Data storage uses raw SQLite via `data/schema.sql`: `users` (Argon2 password hashes), `tickets` (message plus full facts JSON, nulls preserved; money stored as a decimal string), `decisions` (action, confidence, reason, sources), and `policy_index`/`policy_chunks` (embeddings plus model/dimension/corpus/chunker metadata). No write transaction stays open during a Gemini call. There is no deletion endpoint.

## Testing approach

Offline tests use an injectable `requests`-compatible transport for the HTTP client and injected decide callables for evaluation; Streamlit runs under `streamlit.testing.v1.AppTest`. Fakes verify plumbing, validation and error handling only — not Gemini retrieval relevance or generation quality. No live accuracy claim is made. Live evidence would require a configured key and should be recorded separately, with the exact command, date and measured output.

## Security decisions

Passwords are hashed with Argon2; raw hashes never appear in API responses. JWTs use HS256 with an explicit algorithm allowlist, string `sub` user IDs, and verified expiry; missing, malformed, expired or unknown-user tokens return 401. Every ticket query filters on both ticket ID and the token-derived owner ID; cross-owner reads return 404, not 403, to avoid existence leaks. Error responses use the `{"detail":{"code","message"}}` envelope and the client also accepts framework string/list details. Provider exceptions are summarized, never printed raw. The UI holds its token in `st.session_state`, keeps no cache decorators over private data, clears all private state on logout and 401, and does not auto-retry POSTs. No secrets, tokens, hashes, customer rows or internal exceptions are logged or committed. Historical identities were not used to seed app users.

## Known issues and technical debt

- A retry after an uncertain POST timeout can duplicate a saved ticket; idempotency keys are out of scope.
- Semantic policy grounding is not provable; citation membership is necessary but insufficient.
- The committed model names in `.env.example` are placeholders pending verification of currently supported identifiers; verify before first live run.
- No CI pipeline; tests are run manually.
- `data/tickets.csv` label agreement is diagnostic only; some historical rows omit facts the policies require (for example, wrong-item identities) and some labels conflict with the written policies.
- The evaluator's exit-code convention (1 on any error/mismatch) is convenient for scripted checks but means a single flaky provider call fails the run; rerun to confirm.

## Ambiguities and decisions made

Documented interpretations implemented on the UI/eval side:

- Dedicated functional-defect policy takes precedence over `returns.md`'s broad routing sentence; cosmetic/transit damage follows `damaged_goods.md`.
- Wrong-item cases with missing ordered/received identities are clarified rather than force-matched to historical labels.
- The refund-only branch for unavailable original items uses the proposed `OFFER_REFUND` action; it is an extension beyond the 15 historical labels.
- Shipping days 0–5: `WAIT_AND_TRACK` with an expected-delivery explanation, rather than `NEEDS_MORE_INFORMATION` solely because the day is early.
- Missing facts are accepted as null and explained by the decision, not rejected as validation errors; only explicitly contradictory structured facts (such as a processing order with a positive dispatch age) are rejected upstream.
- Mixed product baskets have no dedicated policy; the decision may need to ask which item is affected.
- Historical labels were deliberately excluded from runtime prompts, indexes and the evaluation input allowlist to prevent leakage.

## Agent usage disclosure

This work stream was produced with an AI coding agent under human direction. The agent read the parent planning documents, implemented the assigned UI/evaluation files with test-first verification, ran only the offline tests above, and did not run live Gemini calls, ingestion, the full journey, or any Git operation. Humans remain responsible for reviewing the code, verifying model identifiers, supplying local secrets, and running live evaluation. Other modules were built by separate agents working to the same contracts; integration responsibility is shared.

## Verification log

- 2026-09-17: `python -m pytest tests/test_ui_client.py tests/test_eval.py -q` — 17 passed (offline, no keys; includes Streamlit AppTest session flow and evaluator CLI paths).
- 2026-09-17: `python -m pytest -q` (full suite, global interpreter) — 13 passed, 2 collection errors (`test_retrieval.py` import: missing `src/retrieval.py`; `test_auth.py`: FastAPI/Starlette incompatibility `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`). Retest inside a fresh virtualenv before judging application code.
- Not yet run: live ingestion, `python -m eval.run_eval --cases data/sample_test_cases.json` against real Gemini, browser/manual UI walkthrough against `uvicorn src.api:app`, and the complete `pytest` suite in a clean venv. No accuracy results are claimed.
