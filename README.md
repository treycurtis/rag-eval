# Querybot Quality Pipeline

Production quality signal layer for a Claude-powered enterprise BI assistant. Ingests raw conversation logs, classifies conversation outcomes using an LLM-as-judge approach, and builds a structured eval layer on top of existing operational telemetry.

The goal is to turn the assistant from a system with operational observability (it knows what ran) into one with quality observability (it knows what worked). The north star is a self-improving feedback loop: quality scores gate the learning extraction pipeline so only high-quality conversations generate documentation updates, and eventually the assistant queries its own eval data for prescriptive self-improvement.

**Note on naming:** The repo is called `rag-eval` because the original plan involved RAGAS metrics as the primary eval mechanism. That approach was dropped in favor of a conversation outcome classifier, which is a better fit for the assistant's multi-turn, tool-heavy conversation structure. RAGAS may be incorporated downstream as a retrieval quality signal, but it is not the core of what this pipeline does.

---

## Architecture

```
Source DB (production assistant logs)
    └── Airflow DAG: source_to_snowflake
            └── Snowflake: RAG_EVAL.STAGING (raw tables)
                    └── dbt: staging models → intermediate models
                            └── Classifiers (Python + Claude API)
                                    └── INT_CONVERSATION_OUTCOMES_RAW
                                            └── fct_conversation_outcomes (pending)
```

**Airflow:** `~/airflow/` — Docker Compose, Airflow 3.2.1, `schedule=None` (manual trigger only)  
**dbt project:** `~/projects/rag-eval/rag_eval/`  
**Classifiers:** `~/projects/rag-eval/classifiers/`  

---

## Connections

Configured via environment variables in `.env`. See `.env.example` for required keys:

| Variable | Description |
|---|---|
| `SOURCE_DB_HOST` | Production Postgres host |
| `SOURCE_DB_NAME` | Source database name |
| `SOURCE_DB_USER` | Read-only DB user |
| `SNOWFLAKE_ACCOUNT` | Snowflake account identifier |
| `SNOWFLAKE_USER` | Snowflake username |
| `SNOWFLAKE_PASSWORD` | Snowflake password |

Snowflake keypair auth: `~/.snowflake/rsa_key.pem`

---

## Data Model

### Staging (dbt)

| Model | Description |
|---|---|
| `stg_conversations` | One row per conversation, metadata |
| `stg_conversation_runs` | One row per run, cost and duration signals |
| `stg_conversation_messages` | One row per message, full content |
| `stg_arxiv_papers` | arXiv ingestion pipeline (separate track) |

### Intermediate (dbt)

| Model | Description |
|---|---|
| `int_conversation_metrics` | Rolled-up signals per conversation: turns, cost, SQL writes, errors, user interrupts |
| `int_conversation_type` | One row per conversation, type label (generation / modification / consultation / diagnostic / ghost / anomalous / unknown) |

### Classifier Output

| Table | Description |
|---|---|
| `INT_CONVERSATION_OUTCOMES_RAW` | Raw classifier output — one row per classified conversation, includes rubric scores, outcome label, reasoning, char count |

### Pending

| Model | Description |
|---|---|
| `fct_conversation_outcomes` | Joins metrics + type + classifier output into final quality layer |

---

## Conversation Corpus

**735 total conversations** as of last DAG run.

| Bucket | Count | Notes |
|---|---|---|
| ghost | 133 | `total_turns = 0`, no messages |
| anomalous | 36 | `total_turns > 75` |
| unknown | 137 | pre-prefetch, zero signals |
| **classifiable** | **429** | generation + modification + consultation + diagnostic |

### Classifiable breakdown

| Type | Count |
|---|---|
| modification | 206 |
| consultation | 143 |
| generation | 61 |
| diagnostic | 19 |

---

## Classifiers

### Prompt 2 — Consultation Classifier ✅ Complete

**Script:** `classifiers/run_consultation_classifier.py`  
**Output table:** `RAG_EVAL.STAGING.INT_CONVERSATION_OUTCOMES_RAW`  
**Conversations:** 143  
**Status:** All 143 classified and written to Snowflake

**Outcome distribution:**

| Outcome | Count |
|---|---|
| success_clean | 103 |
| failure_abandoned | 13 |
| success_with_correction | 8 |
| failure_wrong_direction | 6 |
| inconclusive | 4 |
| error / skipped | 1 |

**Validation harness:** `classifiers/validate_consultation_classifier.py`  
5 validation cases + 1 blind test

---

### Prompt 1 — SQL Output Classifier 🔲 In Progress

Covers generation + modification conversations (~267 total).  
Key label to add: `success_needs_validation` — for cases where the assistant produced SQL but environmental differences mean the output can't be fully confirmed.

---

## Running the Classifier

```bash
cd ~/projects/rag-eval
source .venv/bin/activate

# Full run
python classifiers/run_consultation_classifier.py

# Patch run (specific IDs)
# Edit fetch_consultation_ids() to return a hardcoded list, then:
python classifiers/run_consultation_classifier.py
# Remember to restore fetch_consultation_ids() after
```

**Before running:** ensure all environment variables are set in `.env` and have your MFA token ready.

---

## arXiv Track

Separate pipeline. 258 papers ingested into `RAG_EVAL.ARXIV` schema via S3 (`rag-eval-papers-raw`, managed by Terraform). `stg_arxiv_papers` staging model with 7 passing tests. MLflow (EC2 Spot) and FastAPI classifier layer planned for later phases.