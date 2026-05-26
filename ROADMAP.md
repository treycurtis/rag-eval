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

- Prompt 1 — SQL Output Classifier ✅
  - Covers generation + modification (271 conversations)
  - Results in `INT_CONVERSATION_OUTCOMES_RAW`
  - Same harness pattern as Prompt 2
  - Grounded in real corpus examples, built and validated clean
  - Blind test (conv 702) validated as `failure_environment`
  - Internal branding scrubbed from all classifier files
  - Data dictionary updated and committed


---

## 🔲 Next Up

### `fct_conversation_outcomes`
- One row per classifiable conversation with complete quality signal
- Joins:
  - `INT_CONVERSATION_METRICS` — behavioral signals (turns, cost, permission errors, code review trajectory)
  - `INT_CONVERSATION_TYPE` — conversation type label
  - `INT_CONVERSATION_OUTCOMES_RAW` — classifier outcome + rubric scores (deduplicated on MAX(classified_at))
- This model completes the quality signal layer — the north star from the original project brief
- Once complete: RAGAS scores gate the learning extraction pipeline and the self-improving loop closes


---

## 🗓 Upcoming (Ordered)

1. `fct_conversation_outcomes` dbt model
2. Learning extraction pipeline gate (outcome scores → only high-quality conversations generate doc updates)
3. PyTorch fine-tuning on classifier labels + MLflow logging
4. GitHub Actions CI pipeline
5. pgvector on Postgres for arXiv semantic search
6. FastAPI model router on ECS Fargate
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