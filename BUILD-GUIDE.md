# BUILD-GUIDE — Text-to-SQL AI Data Analyst Agent

A step-by-step guide for building, running, evaluating and deploying this
project. Written for a **Windows laptop with Python 3.12** (the author's
machine is a Tecno MegaBook T1, no GPU — nothing here needs one). macOS/Linux
notes are given where a command differs.

> **Do not put any API key in the code.** Keys live only in the `.env` file
> (which is git-ignored) or in the environment. If you ever paste a key into
> a chat, a file, or a screenshot, rotate it.

---

## 1. Prerequisites

| What | How to check | Install if missing |
| --- | --- | --- |
| Python 3.10+ (3.12 used here) | `python --version` in PowerShell | [python.org](https://www.python.org/downloads/) — tick **"Add Python to PATH"** during install |
| pip | `pip --version` | comes with Python |
| Git | `git --version` | [git-scm.com](https://git-scm.com/download/win) |
| A code editor | — | VS Code is fine |

You do **not** need Docker, a GPU, or a database server to run this locally.
SQLite is built into Python.

## 2. Get the project

```powershell
# If you have the project as a folder already, just cd into it:
cd C:\Users\anasr\path\to\text-to-sql-agent

# Otherwise clone your repo:
git clone <your-repo-url> text-to-sql-agent
cd text-to-sql-agent
```

## 3. Create a virtual environment and install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`.

Verify the install:

```powershell
python -c "import fastapi, streamlit, langgraph, sqlalchemy; print('imports OK')"
python -m pytest -q
```

Expected: **30 passed**. The tests use the built-in mock LLM — no API key,
no network.

## 4. Configure the LLM (environment variables only)

```powershell
copy .env.example .env
notepad .env
```

Fill in at least:

```ini
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...            # your key -- never commit this file
LLM_MODEL=gpt-4o-mini
```

**How to get a key:**

- **OpenAI:** [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
  → Create new secret key → paste it into `.env`. Set a usage limit on your
  OpenAI account (Billing → Usage limits) — $5 is plenty for this project.
- **NVIDIA NIM (verified working 2026-11-12, free tier):** get a free key at
  [build.nvidia.com](https://build.nvidia.com) (no credit card), then use:
  ```ini
  LLM_BASE_URL=https://integrate.api.nvidia.com/v1
  LLM_API_KEY=nvapi-...          # your key -- never commit this file
  LLM_MODEL=openai/gpt-oss-20b
  LLM_MAX_TOKENS=4096
  ```
  `gpt-oss-20b` was tested live against this project (SQL prompt, 2026-11-12)
  and returned clean, correct SQL. Note: the price lines in `.env` are set for
  gpt-4o-mini, so cost reporting will be wrong with NVIDIA — but the free tier
  costs nothing anyway. Warning: `nvidia/nemotron-3-ultra-550b-a55b` was tested
  the same day and returned empty/garbled answers, and several other Nemotron
  models (70B, 51B, Super, 340B, Ultra 253B) returned 404 despite being listed
  — do not use them for this project until re-tested.
- **Any OpenAI-compatible gateway** (Azure OpenAI, a local server, another
  provider): change `LLM_BASE_URL` and `LLM_MODEL` to that provider's chat
  completions endpoint and model name. The client only speaks the
  OpenAI chat-completions format, so the provider must too.
- **No key yet?** Everything except the real-model answers still works: the
  tests, the mock eval, and the UI/API all fall back to the built-in mock LLM
  and say so on screen. Do not let "no key" block you from running the code.

**Cost check before you run anything real:** one question costs ~2 LLM calls
(SQL + insight), ~1,400 tokens total, about **$0.0003** at gpt-4o-mini prices.
The session budget `LLM_SESSION_TOKEN_BUDGET` (default 100,000 tokens) stops
the run instead of spending more. A full 32-question eval is roughly
**$0.01**.

## 5. Build the sample database

```powershell
python scripts\make_sample_db.py --out data\sample.db
```

This creates a deterministic 40-order slice of the Olist schema (same table
and column names as the real dataset): 10 sellers, 15 products, 30 customers,
40 orders, 76 order items, 48 payments, 32 reviews. The default
`DB_URL=sqlite:///./data/sample.db` in `.env` already points at it.

## 6. Run the tests and the eval

```powershell
python -m pytest -q
python scripts\run_eval.py --llm mock --db data\sample.db
```

The eval prints a per-category table. With the mock LLM it is 100% in every
category — that is expected (the mock returns the gold SQL) and only proves
the plumbing works.

Run it for real once you have a key:

```powershell
python scripts\run_eval.py --llm openai --db data\sample.db --out eval\results-real.json
```

Then replace the PLACEHOLDER table in `README.md` with the numbers it prints.
Report per-category numbers; never quote one blended accuracy.

## 7. Run the app

### Streamlit UI (easiest)

```powershell
streamlit run app\streamlit_app.py
```

Open http://localhost:8501. Type a question, or click one of the samples in
the sidebar. The page shows the answer, the data table, the chart, the SQL
that was used, attempts/latency/tokens/cost, and the full step log.

### FastAPI backend

```powershell
uvicorn app.main:app --reload --port 8000
```

- Interactive docs: http://localhost:8000/docs
- `GET /health`, `GET /schema`, `GET /metrics`
- `POST /ask` with body `{"question": "What is our total revenue?"}`

Example with curl (Git Bash) or PowerShell:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/ask -Method Post `
  -ContentType "application/json" `
  -Body '{"question": "How many orders are there in total?"}'
```

## 8. Load the full Olist dataset (optional, recommended)

The sample DB is enough to demo the agent, but the real dataset is what
makes the project credible on a CV. Steps:

### 8a. Get a Kaggle API token

1. Create a Kaggle account at [kaggle.com](https://www.kaggle.com) (free).
2. Go to your profile → **Settings** → **API** → **Create New Token**.
   This downloads `kaggle.json` (it contains your username and key).
3. Put it where the Kaggle CLI expects it:
   - **Windows:** move it to `C:\Users\anasr\.kaggle\kaggle.json`
   - macOS/Linux: `~/.kaggle/kaggle.json`
4. The Kaggle CLI is already in `requirements.txt` (`kaggle` package).

> Treat `kaggle.json` like a password. Do not commit it, email it, or paste
> it anywhere.

### 8b. Download and load

```powershell
python scripts\load_olist.py --out data\olist.db
```

This downloads `olistbr/brazilian-ecommerce` (~100 MB zipped, ~99,441
orders, 9 tables) and loads it into SQLite. Then point the app at it:

```ini
# in .env
DB_URL=sqlite:///./data/olist.db
```

And re-run the eval against the full data:

```powershell
python scripts\run_eval.py --llm openai --db data\olist.db --out eval\results-olist.json
```

Note: a few golden-set expected rows were computed on the sample DB. On the
full dataset, re-run with `--refresh-expected` so the expected rows are
recomputed from the gold SQL against the full data:

```powershell
python scripts\run_eval.py --refresh-expected --llm openai --db data\olist.db --out eval\results-olist.json
```

### 8c. Postgres option

If you want Postgres instead of SQLite (for example to deploy with a real
database server):

```powershell
# 1. Install Postgres and create a database named olist.
# 2. Load the data:
python scripts\load_olist.py --db-url "postgresql+psycopg2://user:password@localhost:5432/olist"
# 3. In .env:
# DB_URL=postgresql+psycopg2://user:password@localhost:5432/olist
```

`psycopg2-binary` is already in `requirements.txt`. The schema introspection,
validator and execution code are database-agnostic (SQLAlchemy +
`information_schema`).

## 9. Extend the golden set (30 → 60–80 questions)

1. Open `eval/golden_set.json`. Each entry looks like this:

```json
{
  "id": "s09",
  "difficulty": "single-table",
  "question": "How many products are there?",
  "gold_sql": "SELECT COUNT(*) FROM products",
  "expected_rows": null
}
```

2. Add your questions, keeping the four difficulties balanced
   (`single-table`, `join`, `aggregation`, `ambiguous`). Good additions:
   questions that use the glossary terms ("late delivery", "average order
   value"), questions with a date filter, and ambiguous ones the agent
   should refuse ("What is our profit margin?", "Who are our best
   customers?").
3. Recompute expected rows (this runs the gold SQL against the DB — it does
   not call the LLM):

```powershell
python scripts\run_eval.py --refresh-expected --db data\sample.db
```

4. Re-run the eval and check the per-category table. If a new question fails
   with a real model, look at `logs/steps.jsonl` — the step log shows exactly
   what SQL was generated and why the validator rejected it.

## 10. Run with Docker

```powershell
docker compose up --build
```

- API: http://localhost:8000
- UI: http://localhost:8501

The image builds the sample DB at build time. Put your key in `.env` first —
`docker-compose.yml` reads it via `env_file`. To run the API only:

```powershell
docker build -t text-to-sql-agent .
docker run -p 8000:8000 --env-file .env text-to-sql-agent
```

## 11. Deploy (free tiers)

Nothing here needs a paid host. Two straightforward options:

### Option A — Streamlit Cloud (UI only, simplest)

1. Push the project to a **private** GitHub repo (do not push `.env` —
   it is git-ignored; double-check with `git status` before committing).
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app →
   pick the repo, branch `main`, file `app/streamlit_app.py`.
3. In the app's **Settings → Secrets**, paste:

```toml
LLM_API_KEY = "sk-..."
LLM_BASE_URL = "https://api.openai.com/v1"
LLM_MODEL = "gpt-4o-mini"
```

   Note: Streamlit secrets are TOML, not `.env` format, and they become
   environment variables at runtime — the app reads them the same way.
4. Deploy. You get a public URL to put on your CV.

### Option B — Render (API + UI)

1. Push to GitHub (same warning about `.env`).
2. On [render.com](https://render.com) create a **Web Service** from the
   repo, using the `Dockerfile` (Docker runtime). Set the environment
   variables `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `DB_URL` in the
   Render dashboard — never in the repo.
3. The free tier sleeps after inactivity; the first request after a sleep
   takes ~30–60s to wake up. That is normal for free tiers.

### What to put on the CV

Link the **live demo** and the **repo**, and quote the per-category eval
numbers from the real-model run (not the mock). One honest line beats a
blended headline:

> "Text-to-SQL agent (LangGraph + FastAPI + Streamlit) over the 99k-order
> Olist dataset. Golden-set eval (n=32, per-category): single-table X%,
> join Y%, aggregation Z%, ambiguous-clarification W%; $0.0003/query at
> gpt-4o-mini prices."

## 12. CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every push and pull request:

1. Install dependencies (Python 3.12)
2. Build the sample DB
3. `pytest -q`
4. `run_eval.py --llm mock` and upload `eval/results.json` as an artifact

No secrets are needed in CI — the eval runs against the mock LLM.

## 13. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError` | venv not activated, or installed in the wrong Python | `deactivate`, then `.\.venv\Scripts\activate` and re-run `pip install -r requirements.txt` |
| `LLM_API_KEY` empty / mock warning in the UI | `.env` missing or not loaded | `copy .env.example .env`, fill in the key, restart the app |
| `401` from the LLM | wrong or revoked key | regenerate the key at platform.openai.com/api-keys |
| `429` from the LLM | rate limit / no credits | wait, or add credits / lower the session budget |
| `kaggle` command not found | Kaggle CLI not installed or `kaggle.json` missing | `pip install kaggle`, then place `kaggle.json` in `%USERPROFILE%\.kaggle\` |
| `sqlite3.OperationalError: no such table` | sample DB not built, or `DB_URL` points elsewhere | run step 5; check `DB_URL` in `.env` |
| Eval shows 100% everywhere | you ran `--llm mock` | expected — run `--llm openai` for real numbers |
| Streamlit shows "no module named text_to_sql_agent" | launched from the wrong folder | `cd` to the project root first |
| `address already in use` | an old server is still running | `Ctrl+C` in that terminal, or use another port (`--port 8001`) |
| Docker build is slow | first build downloads the Python image | normal; later builds are cached |

## 14. File map

```
text_to_sql_agent/   config.py  llm.py  db.py  glossary.py
                     validator.py  agents.py  pipeline.py
                     logging.py  eval_utils.py
app/                 main.py (FastAPI)   streamlit_app.py (UI)
scripts/             make_sample_db.py  load_olist.py  run_eval.py
eval/                golden_set.json  results.json
tests/               test_validator.py  test_schema_grounding.py
                     test_self_heal.py  test_pipeline_mock.py  conftest.py
```

## 15. What is NOT in this project (on purpose)

- No API key, token or secret anywhere in the code or files.
- No GitHub repo created, nothing published — that is your step, when you
  are ready.
- No real LLM calls were made while building it (no key was available).
  The first real-model eval is yours to run — section 6.
- No authentication on the API. Add it before exposing the backend publicly.
