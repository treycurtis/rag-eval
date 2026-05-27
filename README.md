# Querybot Quality Pipeline

Production quality signal layer for a Claude-powered SQL query assistant. Ingests raw conversation logs, classifies conversation outcomes using an LLM-as-judge approach, and builds a structured eval layer on top of existing operational telemetry.

The goal is to turn the assistant from a system with operational observability (it knows what ran) into one with quality observability (it knows what worked). The north star is a self-improving feedback loop: quality scores gate the learning extraction pipeline so only high-quality conversations generate documentation updates, and eventually the assistant queries its own eval data for prescriptive self-improvement.

**Note on naming:** The repo is called `rag-eval` because the original plan involved RAGAS metrics as the primary eval mechanism. That approach was dropped in favor of a conversation outcome classifier, which is a better fit for the assistant's multi-turn, tool-heavy conversation structure. RAGAS may be incorporated downstream as a retrieval quality signal, but it is not the core of what this pipeline does.

---

## Architecture

```
Source DB (production assistant logs)
    └── Airflow DAG: querybot_postgres_to_snowflake
            └── Snowflake: RAG_EVAL.STAGING (raw tables)
                    └── dbt: staging models → intermediate models
                            └── Classifiers (Python + Claude API)
                                    └── INT_CONVERSATION_OUTCOMES_RAW
                                            └── FCT_CONVERSATION_OUTCOMES
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
| `int_conversation_metrics` | Rolled-up signals per conversation: turns, cost, SQL writes, errors, user interrupts, corpus era |
| `int_conversation_type` | One row per conversation, type label with boolean signals. Types: generation / modification / diagnostic / consultation / ghost / anomalous / unknown |

### Classifier Output

| Table | Description |
|---|---|
| `INT_CONVERSATION_OUTCOMES_RAW` | Raw classifier output — one row per classification run, includes rubric scores, outcome label, reasoning, char count. Not unique on conversation_id — deduplicate on MAX(classified_at). |

### Marts (dbt)

| Model | Description |
|---|---|
| `FCT_CONVERSATION_OUTCOMES` | Final quality layer. Joins metrics + type + classifier output. One row per classifiable conversation. 429 rows, 407 classified. |

---

## Conversation Corpus

**735 total conversations** as of last DAG run.

| Bucket | Count | Notes |
|---|---|---|
| ghost | 133 | `total_turns = 0`, no messages |
| anomalous | 36 | `total_turns > 75`, excluded from classifier |
| unknown | 137 | pre-prefetch era, zero behavioral signals |
| **classifiable** | **429** | generation + modification + diagnostic + consultation |

### Classifiable breakdown

| Type | Count | Notes |
|---|---|---|
| modification | 236 | SQL written without schema prefetch. Includes mixed-write sessions. |
| consultation | 110 | No file writes. Verbal answers, schema/doc/logic lookups. |
| generation | 63 | Post-prefetch, SQL written. Absorbs former `complex` type. |
| diagnostic | 20 | Non-SQL file written. Routes to consultation classifier prompt. |

---

## Classifiers

Two classifier prompts covering the full classifiable corpus. Both use Claude as judge via the Anthropic API. Output written to `INT_CONVERSATION_OUTCOMES_RAW`.

### Prompt 1 — SQL Output Classifier ✅ Complete

**Script:** `classifiers/run_sql_output_classifier.py`  
**Covers:** generation + modification conversations (299 total)  
**Status:** Classified and written to Snowflake

**Outcome labels:** `success_clean`, `success_iterative`, `failure_wrong_direction`, `failure_environment`, `failure_schema_gap`, `failure_abandoned`, `inconclusive`

**Outcome distribution:**

| Outcome | Type | Count |
|---|---|---|
| success_iterative | modification | 145 |
| success_clean | modification | 58 |
| success_iterative | generation | 29 |
| success_clean | generation | 16 |
| failure_environment | generation | 15 |
| inconclusive | modification | 10 |
| failure_environment | modification | 11 |
| failure_wrong_direction | modification | 5 |
| failure_abandoned | modification | 5 |
| success_with_correction | modification | 2 |
| failure_abandoned | generation | 1 |

---

### Prompt 2 — Consultation Classifier ✅ Complete

**Script:** `classifiers/run_consultation_classifier.py`  
**Covers:** consultation + diagnostic conversations (130 total)  
**Status:** Classified and written to Snowflake

**Outcome labels:** `success_clean`, `success_with_correction`, `failure_knowledge_gap`, `failure_wrong_direction`, `failure_abandoned`, `inconclusive`

**Outcome distribution:**

| Outcome | Count |
|---|---|
| success_clean | 90 |
| failure_abandoned | 11 |
| success_with_correction | 3 |
| failure_wrong_direction | 3 |
| inconclusive | 3 |

---

## Classifier Harness

```bash
cd ~/projects/rag-eval
source .venv/bin/activate

# Full run
python classifiers/run_sql_output_classifier.py
python classifiers/run_consultation_classifier.py

# Patch run (specific IDs)
# Edit fetch_unclassified_ids() to return a hardcoded list, then:
python classifiers/run_sql_output_classifier.py
# Restore fetch_unclassified_ids() after
```

**Before running:** ensure all environment variables are set in `.env`. Authentication uses Snowflake keypair auth — key at `~/snowflake/rsa_key.pem`.
---

## v2 Backlog

| Item | Notes |
|---|---|
| Relevance pre-classifier | Score conversations 1-3 on relevance to querybot's core job before running full outcome rubric. See inline TODO in classifier harnesses. |
| Self-learning gate | Wire `FCT_CONVERSATION_OUTCOMES` scores as learning extraction gate. Revisit after MLOps build list. |
| OTel quality signal hookup | Connect quality scores to existing operational observability. |
| `execute_sql_count` double-count fix | Currently counts both `tool_call` and `tool_result` rows. |
| `execute_sql` success signal | `%preview%` match may produce false positives — needs tighter string. |
| `failure_schema_gap` investigation | Zero occurrences in current corpus — confirm classifier coverage or genuine absence. |

---

## arXiv Track

Separate pipeline. 258 papers ingested into `RAG_EVAL.ARXIV` schema via S3 (`rag-eval-papers-raw`, managed by Terraform). `stg_arxiv_papers` staging model with 7 passing tests. MLflow (EC2 Spot) and FastAPI classifier layer planned for later phases.