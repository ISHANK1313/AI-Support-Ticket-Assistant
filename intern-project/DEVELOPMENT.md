# DEVELOPMENT

Working notes for this repository: architecture choices, verification state, known issues and honest limits. PRD.md, TRD.md, schemaDB.md and refrence.md (in the repository root above `intern-project`) remain the authority for requirements and contracts. This file describes work actually done, not planned intent.

## Implementation state

Implemented and offline-tested in this work stream:

- `streamlit_app.py`: Streamlit UI. Three areas (Account, New decision, History), HTTP-only via `src/ui_client.py`.
- `src/ui_client.py`: session-bound client. Per-call Bearer headers, Decimal money serialization, explicit nulls, 160-second timeout, redirect/automatic-retry prevention, 401 token clearing, injectable transport. The live `requests` path was exercised against a running `uvicorn` server through the Streamlit UI on 2026-09-19.
- `eval/run_eval.py`: `python -m eval.run_eval --cases data/sample_test_cases.json` or `--csv data/tickets.csv`. Lazily imports the backend and calls the real `DecisionService(Settings.load()).decide(TicketInput)`. An exact allowlist prevents labels/IDs/identity fields from entering inputs. Errors count toward the total denominator; accuracy is `correct/total`.
- `tests/test_ui_client.py` and `tests/test_eval.py`: 17 offline tests. No keys, server, or database required.
- `tests/test_auth.py` and `tests/test_database.py`: JWT rejection paths (expired, forged signature, `alg=none`, HS384, missing `sub`/`iat`/`exp`, non-numeric subject, deleted user, malformed header), owner-scoped repository reads, schema column guards, and ticket+decision atomicity with rollback on failure.

Integration state (2026-09-19): the complete offline suite runs and passes inside `intern-project/.venv` (Python 3.11.9, FastAPI 0.141.1, Starlette 1.6.0) — see the verification log below for the exact command and result. An earlier attempt with a globally installed interpreter reported `TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'`; that was an incompatible global FastAPI/Starlette pairing, not an application defect, and it does not reproduce in a clean virtual environment. The end-to-end journey against a running API was exercised manually on 2026-09-19 through the Streamlit UI.

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
- The `.env.example` model defaults were checked against the published Gemini API documentation on 2026-09-19 (`gemini-3.5-flash-lite` stable with structured-output support; `gemini-embedding-001` still available with task-type support). Availability is account- and date-dependent, so an unknown-model error on a live run means substituting an identifier the account can use.
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

This work stream was produced with AI coding agents under human direction. The agents read the parent planning documents, implemented the modules against the shared contracts, wrote tests first where possible, and ran only the offline suites recorded below. Live Gemini ingestion, the supplied-case evaluation and the manual browser walkthrough were performed separately by the human operator, and the repository was initialized, committed and pushed to GitHub on 2026-09-19 at the operator's explicit request. Humans remain responsible for reviewing the code, verifying model identifiers, supplying local secrets, and running live evaluation.

## Verification log

- 2026-09-19: `python -m pytest -q` from `intern-project` inside `.venv` (Python 3.11.9) — **96 passed, 0 failed, 0 errors** (42 s). Per file: `test_auth.py` 15, `test_database.py` 7, `test_decision.py` 21, `test_eval.py` 8, `test_provider.py` 2, `test_retrieval.py` 19, `test_schemas.py` 10, `test_tickets.py` 5, `test_ui_client.py` 9. Offline only: fake embedder/generator and injected `requests`-compatible transport, so no key, quota or network was used.
- 2026-09-19: `python -m src.retrieval ingest` — reused the existing valid index (`{"chunks": 6, "dimension": 3072, "corpus_hash": "229f0a76…", "reused": true}`), which confirms the corpus-hash/chunker/model compatibility check short-circuits a paid re-embed.
- 2026-09-19: live manual walkthrough — `python -m uvicorn src.api:app --reload` (port 8000) plus `python -m streamlit run streamlit_app.py` (port 8501); registration, sign-in, a real Gemini decision and reading the saved result back from History all succeeded in the browser.
- 2026-09-19: `python -m pytest tests/test_auth.py tests/test_database.py -q` — 22 passed, covering the JWT rejection paths (expired, forged signature, `alg=none`, HS384, missing `sub`/`iat`/`exp`, non-numeric subject, deleted user, malformed header) and repository atomicity/rollback.
- 2026-09-17: `python -m pytest tests/test_ui_client.py tests/test_eval.py -q` — 17 passed (offline, no keys; includes the Streamlit `AppTest` session flow and evaluator CLI paths).
- Superseded, kept as history: the 2026-09-17 global-interpreter attempt (13 passed, 2 collection errors). Cause was an incompatible globally installed FastAPI/Starlette pairing; it does not reproduce in the virtual environment.
- Known remaining verification gap: none of the live results above are held-out evidence. The five visible sample cases are limited end-to-end checks and `data/tickets.csv` agreement is diagnostic only.
