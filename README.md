# Text-to-SQL AI Data Analyst Agent

Ask a plain-English question about the Olist Brazilian e-commerce data and get
back **validated SQL, a chart, and a plain-English answer** — with every step
logged and every guardrail enforced before anything runs.

Built with **LangGraph** (Python 3.10+), **FastAPI**, **Streamlit**, and
**SQLite by default** (Postgres optional). CPU-only — it runs on a laptop with
no GPU. The LLM is OpenAI-compatible and configured **only via environment
variables**; no key is ever stored in code.

> This is a portfolio project. It is real, working code — not a skeleton —
> but its eval numbers below are honest about what was measured with a mock
> LLM and what is still a placeholder for a real model.

## Quick start

```bash
git clone <your-repo> text-to-sql-agent   # or copy this folder
cd text-to-sql-agent
python -m venv .venv
.venv\Scripts\activate        # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then put your LLM_API_KEY in .env
python scripts/make_sample_db.py --out data/sample.db
python -m pytest -q           # 30 tests, no API key needed
python scripts/run_eval.py --llm mock --db data/sample.db
```

Run the UI and the API:

```bash
streamlit run app/streamlit_app.py            # http://localhost:8501
uvicorn app.main:app --reload --port 8000     # http://localhost:8000/docs
```

Full step-by-step for Windows is in **[BUILD-GUIDE.md](BUILD-GUIDE.md)**.

## How it works

```
Question
   |
   v
Schema Agent      -- reads the live schema (information_schema) + the
                    business glossary, so the model sees real tables/columns
   |
   v
SQL Agent         -- schema-aware prompt; answers in {"sql": "..."} or
                    {"ambiguous": true, "clarification": "..."}
   |                A deterministic pre-check refuses undefined business
   |                terms (profit, churn, retention...) BEFORE any LLM call.
   v
Validation Agent  -- SELECT-only allow-list. Blocks DELETE/DROP/UPDATE/
                    INSERT/ALTER/CREATE/TRUNCATE and multi-statement SQL,
                    rejects unknown tables/columns, runs EXPLAIN.
                    A failed check is NOT an error to the user: it is the
                    input to the self-heal loop.
   |
   v
Self-heal loop    -- the validator's exact error is re-prompted to the model.
                    Max 3 retries, then a clear failure (never a guess).
   |
   v
Execution         -- read-only run, 30s timeout, 100-row cap.
   |
   v
Chart Agent       -- deterministic rules, not an LLM call:
                    date x + number y -> line, category x + number y -> bar,
                    otherwise a table. Structured JSON, not an image.
   |
   v
Insight Agent     -- plain-English findings with the numbers, plus caveats
                    ("this is revenue = sum(price); there is no cost data,
                    so no profit figure is possible").
```

### Guardrails (the point of the project)

| Guardrail | Where | What it does |
| --- | --- | --- |
| Read-only SQL | `text_to_sql_agent/validator.py` | Single `SELECT`/`WITH` only; blocklist with string-literal stripping so a value like `'drop'` does not trip it |
| Schema grounding | `db.py`, `validator.py` | Every table/column checked against the live schema; hallucinated columns rejected |
| Ambiguity path | `agents.py: detect_ambiguity` | Undefined business terms -> clarification, zero LLM tokens spent |
| Self-heal | `pipeline.py` | <= 3 retries with the real error message; then fail cleanly |
| Cost ceiling | `config.py`, `pipeline.py` | Session token budget (default 100,000); the run stops instead of spending more |
| Step log | `logging.py` | Every decision and tool call appended as JSONL (`logs/steps.jsonl`) |
| Secrets | `config.py`, `.env` | LLM base URL / key / model from env only; `.env` is git-ignored |

### The business glossary (semantic layer)

Most text-to-SQL demos fail here: the model guesses what a word means.
`text_to_sql_agent/glossary.py` pins the definitions:

- **revenue** = `SUM(order_items.price)` (freight excluded; use `SUM(price + freight_value)` for "gross merchandise value")
- **late delivery** = `order_delivered_customer_date > order_estimated_delivery_date`
- **delivery time** = days between purchase and delivered date
- **average order value** = `SUM(price) / COUNT(DISTINCT order_id)`
- **repeat customer** = `customer_unique_id` with more than one order

And the terms the data **cannot** answer — profit, margin, churn, retention,
ROI, CAC, LTV — are listed as undefined, so the agent asks instead of
inventing a column.

## Dataset

**Olist Brazilian E-Commerce Public Dataset** (Kaggle
`olistbr/brazilian-ecommerce`, ~99,441 orders, 9 tables, CC BY-NC-SA 4.0).

- **Sample dataset (built in, zero setup):** `scripts/make_sample_db.py`
  creates a deterministic 40-order slice with the same table and column names
  (10 sellers, 15 products, 30 customers, 40 orders, 76 order items,
  48 payments, 32 reviews). The agent runs end-to-end on this.
- **Full dataset:** `scripts/load_olist.py` downloads the Kaggle files and
  loads them into SQLite or Postgres. Needs a Kaggle API token —
  see BUILD-GUIDE.md.

## Evaluation

The golden set is `eval/golden_set.json`: **32 hand-written questions**
(8 single-table, 8 join, 8 aggregation, 8 ambiguous), each with gold SQL and
expected rows computed by running the gold SQL against the sample DB.

```bash
python scripts/run_eval.py --llm mock --db data/sample.db        # no key
python scripts/run_eval.py --llm openai --db data/sample.db      # real model
python scripts/run_eval.py --llm openai --db data/olist.db --out eval/results-real.json
python scripts/run_eval.py --refresh-expected --db data/sample.db  # rebuild expected rows
```

Metrics are reported **per difficulty category, never as one blended
number**. Reported for each category: execution accuracy, valid-SQL rate,
self-heal success rate (% of first-attempt failures fixed within 3 retries),
hallucinated-column rate, median/p95 latency, cost per query.

### Measured baseline — mock LLM (a ceiling, not a real result)

The mock returns the gold SQL by design, so 100% here only proves the
plumbing (validation, execution, comparison, self-heal) works. It says
nothing about a real model's accuracy.

| Category | n | Execution accuracy | Valid SQL | Self-heal | Hallucinated columns | Median latency | Cost/query |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| single-table | 8 | 100% | 100% | — | 0% | 0.02s | $0.0002 |
| join | 8 | 100% | 100% | — | 0% | 0.02s | $0.0003 |
| aggregation | 8 | 100% | 100% | 100% (1/1) | 12.5%* | 0.02s | $0.0003 |
| ambiguous | 8 | 100%† | — | — | 0% | 0.01s | $0.0001 |

\* The aggregation hallucination is deliberate: question `a01` is scripted to
return `SUM(profit)` on the first attempt so the self-heal loop is exercised.
The validator rejected it and the retry produced the correct SQL.
† "Accuracy" for ambiguous questions means "correctly asked for
clarification instead of guessing".

### PLACEHOLDER — real LLM results (replace after a real run)

Run `python scripts/run_eval.py --llm openai --db data/sample.db` and paste
the per-category table here. Do **not** copy the mock numbers into a CV.

| Category | n | Execution accuracy | Valid SQL | Self-heal | Hallucinated columns | Median latency | Cost/query |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| single-table | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER | — | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER |
| join | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER | — | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER |
| aggregation | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER |
| ambiguous | PLACEHOLDER | PLACEHOLDER | — | — | PLACEHOLDER | PLACEHOLDER | PLACEHOLDER |

**Extending the golden set to 60–80 questions:** add entries to
`eval/golden_set.json` (same shape: `id`, `difficulty`, `question`,
`gold_sql`, `expected_rows: null`), then run with `--refresh-expected` to
compute expected rows from the gold SQL. Keep the difficulty split balanced
and keep ambiguous questions in the set — a model that guesses on those is
worse than one that asks. See BUILD-GUIDE.md §9.

## Cost and latency notes

- Default model is `gpt-4o-mini`. Prices in `.env.example` are the
  gpt-4o-mini list prices at the time of writing ($0.15 / $0.60 per 1M
  input/output tokens); update `LLM_PRICE_INPUT_PER_1K` /
  `LLM_PRICE_OUTPUT_PER_1K` if you change model or provider — the reported
  cost is only as accurate as those two numbers.
- One question costs roughly **2 LLM calls** (SQL + insight), ~1,400 tokens
  total with this schema, i.e. **~$0.0003 per question** at gpt-4o-mini
  prices. A self-heal retry adds one more call. The session budget
  (`LLM_SESSION_TOKEN_BUDGET`, default 100,000 tokens) caps a whole session
  at roughly **$0.03–$0.05** of model cost.
- The schema is sent on every SQL call (it is the grounding). On a much
  larger schema, consider a retrieval step that sends only the relevant
  tables — that is a known scaling limit, see below.
- Latency with the mock is ~0.02s per question (no network). With a real
  model, expect ~2–5s per question (two sequential calls), plus the
  30s query timeout as a hard cap on the database side.

## Where this breaks and why

Honest limits, not marketing:

1. **It is only as good as the model.** A mini-class model will still
   hallucinate columns on unfamiliar phrasing; the validator catches the
   bad SQL, but after 3 failed retries the answer is "I could not answer
   that", not a guess. That is the design, but it means real-model accuracy
   on the golden set is the number that matters — and it is not measured yet.
2. **The schema is sent in full on every call.** Fine for 8 Olist tables;
   on a 200-table warehouse the prompt gets expensive and the model gets
   worse. A table-retrieval step would be needed before that scales.
3. **CTEs are a validator blind spot.** `WITH ...` queries are allowed
   through the first-keyword check but the CTE name is not tracked as a
   table, so a valid CTE query can be rejected as "unknown table". The
   golden set avoids CTEs for that reason.
4. **Output aliases are not meaning-checked.** `SELECT SUM(price) -
   SUM(freight_value) AS profit` passes validation because `profit` is an
   output label, not a source column. The glossary + ambiguity pre-check
   are the defence; a model that invents labels unprompted can still
   mislead. This is documented in the tests.
5. **The sample DB is tiny and clean.** Real Olist data has missing values,
   cancelled orders mixed into revenue, and date formats that need care.
   The golden-set numbers on the sample DB will be higher than on the full
   dataset.
6. **Charts are rule-based, not intelligent.** A two-column result with a
   date x-axis becomes a line chart whether or not a line is the right
   choice. Multi-series and drill-downs are not supported.
7. **No authentication, no multi-user isolation.** The FastAPI backend is a
   demo service. Do not expose it publicly without adding auth and a
   per-user budget.
8. **SQLite is single-writer.** Concurrent writes are not a concern here
   (read-only queries), but the Postgres option exists for a reason if this
   ever serves more than one person.

## Project structure

```
text-to-sql-agent/
├── text_to_sql_agent/      # the package
│   ├── config.py           # env-only settings (no secrets in code)
│   ├── llm.py              # OpenAI-compatible client + scripted MockLLM
│   ├── db.py               # schema introspection, execution, timeouts
│   ├── glossary.py         # business definitions / semantic layer
│   ├── validator.py        # SELECT-only allow-list + grounding checks
│   ├── agents.py           # Schema / SQL / Chart / Insight agents
│   ├── pipeline.py         # LangGraph StateGraph + sequential fallback
│   ├── logging.py          # JSONL step log
│   └── eval_utils.py       # golden-set loading + per-category metrics
├── app/
│   ├── main.py             # FastAPI backend  (POST /ask, GET /schema, /health)
│   └── streamlit_app.py    # Streamlit UI
├── scripts/
│   ├── make_sample_db.py   # built-in 40-order sample dataset
│   ├── load_olist.py       # Kaggle download + full load (SQLite/Postgres)
│   └── run_eval.py         # golden-set eval runner
├── eval/
│   ├── golden_set.json     # 32 questions (8 per difficulty)
│   └── results.json        # latest eval output (mock baseline)
├── tests/                  # 30 tests: validator, grounding, self-heal, smoke
├── Dockerfile
├── docker-compose.yml
├── .github/workflows/ci.yml   # tests + mock eval on every push
├── .env.example
├── README.md
└── BUILD-GUIDE.md
```

## API

- `GET /health` — status, model, whether a real LLM is configured
- `GET /schema` — tables and columns the agent can query
- `POST /ask` — `{"question": "..."}` → full result: `status` (`ok` /
  `clarification` / `failed`), `sql`, `columns`, `rows`, `chart`, `insight`,
  `attempts`, `latency_seconds`, `tokens_used`, `cost_usd`, `step_log`
- `GET /metrics` — session totals

## Tests

```bash
python -m pytest -q
```

30 tests, no API key, no network: validator allow-list, schema grounding,
self-heal loop (including "clarify without spending an LLM call"), and a
full-pipeline smoke test with the mock LLM.

## License

MIT.
