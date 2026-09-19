# AI Support Ticket Assistant

Python + Applied GEN AI Engineer shortlist assignment: a minimal AI decision API with a
Streamlit frontend, a FastAPI backend, JWT authentication, SQLite persistence and RAG over
local policy documents using the Gemini API.

**Application source and full documentation: [`intern-project/`](intern-project/README.md)**

| Path | Contents |
| --- | --- |
| [`intern-project/`](intern-project/README.md) | The application: FastAPI backend, Streamlit UI, ingestion/retrieval, SQLite schema, tests and evaluation runner. Start with its [README](intern-project/README.md) and [DEVELOPMENT](intern-project/DEVELOPMENT.md). |
| `candidate_pack/` | Supplied synthetic inputs: six policy documents, 214 historical tickets (`data/tickets.csv`), five visible sample cases and `DATA_NOTES.md`. |

## Quick start

```bash
cd intern-project
python -m venv .venv
.venv/Scripts/activate                 # Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env                   # then set your own GEMINI_API_KEY and JWT_SECRET
python -m src.retrieval ingest
python -m uvicorn src.api:app --reload    # terminal 1
python -m streamlit run streamlit_app.py  # terminal 2
```

Setup details, the six REST endpoints, the evaluation runner, the schema and the known
limitations are documented in [`intern-project/README.md`](intern-project/README.md).

## Notes

- No credentials are committed: `.env` is gitignored and `.env.example` is the template.
- The supplied policy documents ground every recommendation; the historical CSV labels are
  used only for optional diagnostics and never as runtime answers.
- Internal planning and handover notes are excluded from this repository by `.gitignore`.