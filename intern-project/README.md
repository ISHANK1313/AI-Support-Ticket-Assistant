# Support Ticket Assistant

A small FastAPI + Streamlit support-ticket recommendation application. An authenticated user submits a complaint and known order facts, receives a policy-backed action, and views their own saved decisions. It does **not** execute refunds, replacements, cancellations or shipping operations.

## Current verification status

On 2026-09-19, `python -m pytest -q` (run from `intern-project` inside `.venv`, Python 3.11.9) passed **96 offline tests** with fake providers — no key or network needed — covering auth/ownership and every JWT rejection path, repository atomicity/rollback, ticket API round trip, retrieval index integrity, decision validation/repair, UI client, evaluator runner and schema guards.

Live Gemini verification with the project's own configured key: ingestion succeeded (6 policy documents, 3072-dim vectors stored locally) and `python -m eval.run_eval --cases data/sample_test_cases.json` scored **total=5 correct=5 incorrect=0 errors=0 accuracy=100.00%** after one prompt fix (prompt v2 clarifies that facts stated in the customer message count as known). The five visible cases are limited end-to-end checks, not proof of general accuracy; the optional `--csv` replay is a diagnostic of historical label agreement, not held-out accuracy. Live calls were also verified through the authenticated HTTP API (register/login + real decision round trip) using an isolated copy of the database, and on 2026-09-19 through a manual browser walkthrough of the Streamlit UI against the running backend (register, sign in, submit a decision, read it back from History).

## Local setup (Python 3.11+)

Run commands from the `intern-project` directory. Use a virtual environment rather than mixing globally installed FastAPI/Starlette versions.

### Windows PowerShell

```powershell
cd 'D:\java_springboot_pp\Claude Project\MaxorLabs Backend + AI intern Assignemnt\intern-project'
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of `python` without changing machine execution policy.

### Git Bash on Windows

```bash
cd 'D:/java_springboot_pp/Claude Project/MaxorLabs Backend + AI intern Assignemnt/intern-project'
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Copy `.env.example` only when `.env` does not already exist. Edit `.env` locally and manually:

- Set your own `GEMINI_API_KEY`; obtain it through Google AI Studio. Never paste keys into chat, source, screenshots or version control.
- Generate a strong `JWT_SECRET`, for example using `python -c "import secrets; print(secrets.token_urlsafe(48))"`, and paste that value locally.
- Review `GEMINI_MODEL` and `GEMINI_EMBEDDING_MODEL`. The `.env.example` defaults (`gemini-3.5-flash-lite` and `gemini-embedding-001`) were checked against the published Gemini API documentation on 2026-09-19. Availability is account- and date-dependent, so substitute an identifier your own account can use if a live run reports the model as unknown.
- Review `DATABASE_PATH`, `KNOWLEDGE_BASE_PATH`, and `JWT_EXPIRE_MINUTES`. Relative backend paths resolve against the project root.

The backend/evaluation `Settings.load()` reads `.env`. The UI **does not read `.env`, backend settings, the database, or Gemini credentials**. It reads only `API_BASE_URL` from its process environment, defaulting to `http://127.0.0.1:8000`. This URL is trusted operator configuration, not a customer input. Keep the local demo on loopback; use HTTPS and proper deployment controls before any remote use.

## Ingest and run

The following steps require configured local credentials. Ingestion and ticket generation call Gemini and can consume quota/cost money. The offline tests below do not.

```bash
python -m src.retrieval ingest
python -m uvicorn src.api:app --reload
```

Keep that terminal open. Open another terminal, enter `intern-project`, activate the same virtual environment, and run:

```bash
python -m streamlit run streamlit_app.py
```

For a nondefault trusted API address, set it in the **UI terminal** before starting Streamlit:

```powershell
$env:API_BASE_URL = 'http://127.0.0.1:8000'
```

Or in Git Bash:

```bash
export API_BASE_URL='http://127.0.0.1:8000'
```

Visit Streamlit at `http://localhost:8501`; API Swagger is at `http://127.0.0.1:8000/docs`. Register, sign in, submit known facts, and explicitly load a page under History. Empty numeric inputs become JSON null, not zero. Money is sent as a decimal string. Original-item availability supports true, false and unknown.

Forms submit only on button presses; a normal Streamlit rerender does not repeat a POST. Requests use a 160-second timeout, no automatic retry and no redirects. After an uncertain timeout, check History before manually retrying: there are no idempotency keys, so the first request may already have saved a ticket. Logout and HTTP 401 clear all private session state. Tokens and responses are not globally cached.

## API contract

JSON bodies; protected routes require `Authorization: Bearer <access_token>`.

| Method/path | Result |
| --- | --- |
| POST `/register` with email/password | 201: id, email, created_at |
| POST `/login` with email/password | access_token, token_type, expires_in |
| GET `/me` | Signed-in identity |
| POST `/tickets` | 201: id, message, facts, created_at, decision |
| GET `/tickets?limit=20&offset=0` | items (full details), limit, offset; owner-scoped |
| GET `/tickets/{id}` | One owned ticket; missing/non-owned returns 404 |

Example ticket JSON for Swagger or an HTTP client:

```json
{
  "message": "My order arrived damaged yesterday.",
  "order_value_inr": "3500.00",
  "days_since_delivery": 1,
  "days_since_dispatch": null,
  "product_type": "non_food",
  "opened_status": "opened",
  "order_status": "delivered",
  "ordered_item": null,
  "received_item": null,
  "original_item_available": null
}
```

Message is required. Values/days are nullable and nonnegative; days must be integers. Product type: food/non_food/mixed/unknown; opened status: opened/unopened/unknown; order status: processing/dispatched/delivered/unknown. Optional item names/availability clarify wrong-item cases. Do not send user identity, case IDs, issue_type or expected labels.

A decision has `action`, `confidence`, `reason`, and `sources`. Confidence is model-reported, not calibrated. Missing facts can yield `NEEDS_MORE_INFORMATION`; technical failures must remain errors, not fabricated decisions. Errors normally use `{"detail":{"code":"...","message":"..."}}`; the client also handles framework string and validation-list errors.

## Evaluation and tests

Offline checks:

```bash
python -m pytest tests/test_ui_client.py tests/test_eval.py -q
python -m pytest -q
```

After ingestion and local key setup, run the **real shared DecisionService path**, not a label lookup:

```bash
python -m eval.run_eval --cases data/sample_test_cases.json
# Optional, can make many provider calls:
python -m eval.run_eval --csv data/tickets.csv
```

The runner prints total, correct, incorrect, errors and accuracy. `accuracy = correct / total`; errors stay in the denominator. Exit status: 0 for all matches (including empty input), 1 for errors/mismatches, 2 for CLI/input-file failure. Service initialization failures count all loaded cases as errors. Raw provider exceptions, customer names and ticket messages are not printed.

The five visible samples are limited end-to-end checks. CSV mode measures **diagnostic historical label agreement, not held-out accuracy**. Historical rows have been inspected and may conflict with policies. An exact allowlist isolates ticket inputs; expected_action, resolved_action, issue_type, case/ticket IDs and identity fields never enter TicketInput. CSV blank facts become null. Neither dataset is runtime policy evidence.

## Components and storage

- `streamlit_app.py` and `src/ui_client.py`: HTTP-only, session-local UI.
- `src/api.py`, `src/auth.py`, `src/schemas.py`: routes, authentication and validated contracts.
- `src/decision.py`, `src/retrieval.py`: Gemini generation, policy retrieval and ingestion.
- `src/config.py`, `src/database.py`, `data/schema.sql`: configuration and raw SQLite persistence.
- `knowledge_base/`: six supplied Markdown policies; the grounding corpus.
- `eval/run_eval.py`: live-path runner with injected offline test boundary.

SQLite stores users with password hashes, tickets with full facts JSON, one saved decision per successful ticket, and policy index metadata/chunks with embeddings. Ticket ownership comes from the JWT, not request-supplied user IDs. Index metadata tracks embedding compatibility; reingest when policies or embedding model change. Database files and `.env` are local artifacts, excluded from Git.

## Known limitations

No production deployment, photo/evidence processing, order/stock integration, calibrated confidence or proof of semantic grounding. Source membership checks cannot prove a recommendation is correct. Dedicated functional-defect rules take precedence over broad returns routing; wrong-item identity gaps should be clarified; the refund-only branch uses the documented `OFFER_REFUND` extension. Shipping days 0–5 use the documented WAIT_AND_TRACK interpretation. Mixed baskets and contradictory evidence may require clarification. See DEVELOPMENT.md and parent `refrence.md` for ambiguity details.
