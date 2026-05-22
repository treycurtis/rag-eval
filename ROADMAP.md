# Roadmap

---

## ✅ Done

### Infrastructure
- Airflow DAG pulling from source Postgres → Snowflake
- Snowflake keypair auth
- dbt project setup with 4 staging models, all passing tests
- S3 bucket (`rag-eval-papers-raw`) managed via Terraform
- 258 arXiv papers ingested

### dbt Models
- `stg_conversations` ✅
- `stg_conversation_runs` ✅
- `stg_conversation_messages` ✅
- `stg_arxiv_papers` ✅ (7 passing tests)
- `int_conversation_metrics` ✅ (cost signals, user interrupt signals)
- `int_conversation_type` ✅ (type labels for all 735 conversations)

### Classifiers
- Prompt 2 (consultation outcome classifier) ✅
  - 143 conversations classified
  - Results in `INT_CONVERSATION_OUTCOMES_RAW`
  - Validation harness with 5 cases + blind test
  - Harness hardened: retry logic, parallelization, truncation, token budget

---

## 🔲 Next Up

### Prompt 1 — SQL Output Classifier
- Covers generation + modification (~267 conversations)
- Read 3-4 examples first: clean first-try generation, iterative modification, failed generation
- Key label: `success_needs_validation` for dev/prod value uncertainty
- Bump `user` truncation limit to 4000 for large SQL pastes
- Same harness pattern as Prompt 2

### Wire Output to Snowflake
- `INT_CONVERSATION_OUTCOMES_RAW` already exists and has consultation results
- Add generation + modification rows from Prompt 1 run
- `conversation_type` column already in schema to distinguish

### `fct_conversation_outcomes`
- Joins `int_conversation_metrics` + `int_conversation_type` + `INT_CONVERSATION_OUTCOMES_RAW`
- Final quality layer — one row per classifiable conversation with full signal set

### Commit `int_conversation_type`
- Iterated in place, needs a clean commit

---

## 🗓 Upcoming (Ordered)

1. Prompt 1 prompt design + few-shot examples
2. Prompt 1 harness (copy Prompt 2 pattern, update fetch query and prompt)
3. Prompt 1 validation harness
4. Prompt 1 full run
5. `fct_conversation_outcomes` dbt model
6. Learning extraction pipeline gate (outcome scores → only high-quality conversations generate doc updates)
7. Streamlit diagnostic dashboard (score distributions, A/B prompt/model comparison, retrieval coverage heatmap, drift alerts, sample explorer)
8. RAGAS retrieval metrics (optional downstream addition — context precision/recall for schema retrieval quality)

---

## 🗃 Backlog (V2)

### Classifier improvements
- Add `failure_connection_dropped` outcome label to distinguish connection drops from `failure_abandoned`
- Connection drop rate monitoring (infra observability signal)
- Handle conversations where truncation drops a critical user correction from the middle

### `int_conversation_metrics` v2
- Fix `execute_sql_count` double-counting (tool_call + tool_result both counted)
- Add Python executor error signals (`return_code = -1`, `ImportError`, `FileNotFoundError`)
- Distinguish infrastructure noise from genuine SQL failures
- Full `stg_conversation_runs` intermediate model

### arXiv track
- MLflow (EC2 Spot) for experiment tracking
- Quality classifier → FastAPI (ECS Fargate)
- Evidently for drift monitoring

### Infrastructure
- Move Airflow from Docker Compose to ECS Fargate
- Scheduled DAG runs (currently `schedule=None`, manual only)

---

## 🚫 Decisions Made — Don't Revisit

- **No RAGAS-first approach** — dropped in favor of a custom LLM-as-judge conversation outcome classifier. RAGAS is a retrieval eval framework and doesn't fit a multi-turn, tool-heavy conversation structure well. May be incorporated downstream as a retrieval quality signal, but is not the core eval mechanism.
- **`lookup` type retired** — `execute_sql` is research/validation in dev, not data delivery. Folded into consultation.
- **`complex` type retired** — only 3 conversations. Folded into modification. `has_non_sql_write` boolean carries the distinction.
- **No intermediate model for `stg_conversation_runs` in v1** — cost rolled up directly in `int_conversation_metrics`.
- **Classifier eval unit = one user→schema_prefetch chain** — not full conversation, not individual turns.