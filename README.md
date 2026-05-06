# RAG Eval Pipeline

An eval and observability platform for production AI systems. Two tracks, one mission: make AI systems that measure, explain, and improve their own output quality.

---

## The Problem

Production AI systems are operationally observable — you can track latency, error rates, token counts, cost. What they can't tell you is whether they're actually giving good answers. A querybot can have 99.9% uptime and sub-2s response times while consistently retrieving the wrong context and generating bad SQL. Nobody knows until a user complains.

This project builds the missing layer: **quality observability**. Not just "did the system respond?" but "was the response any good, and what do we do about it?"

---

## Two Tracks

### Track 1 — arXiv RAG Eval (Portfolio)

A full-stack RAG evaluation pipeline on arXiv ML papers. Demonstrates end-to-end ML infrastructure: ingestion, transformation, quality scoring, model routing, and observability.

**Pipeline:**
```
arXiv API → S3 → Airflow (ECS Fargate) → Snowflake → dbt
    → RAGAS eval → MLflow → quality classifier
    → FastAPI → Evidently AI → Streamlit dashboard
```

**Goal:** Cost-aware model routing — route queries to a cheap model (DeepSeek V3.2) when quality is sufficient, escalate to a strong model (Claude Sonnet) when it isn't. Model-agnostic by design — swap via config.

### Track 2 — Querybot Eval (Production)

RAGAS eval and observability layer on top of Alarm.com's production Claude-powered BI assistant (querybot). The querybot has operational observability (OTel/Prometheus) and a learning extraction pipeline — but both are quality-blind.

**Goal:** Add a quality signal that gates the self-improving feedback loop.

```
User question → schema_prefetch_tool (3-layer table discovery)
    → SQL generation → answer

                    ↓ eval

RAGAS scores → quality gate → learning pipeline
    → documentation augmentation → better retrieval
    → repeat
```

The learning extraction pipeline already exists — after every conversation, Claude Opus extracts methodology notes, business rules, and data caveats and writes them back to the documentation. The eval layer makes that loop quality-aware: only propagate learnings from conversations that scored well.

---

## Querybot Eval — Architecture

### The Eval Unit

Each eval unit maps to one `schema_prefetch` chain — one user question answered via table discovery + SQL generation:

| Field | Source | Description |
|-------|--------|-------------|
| `question` | `conversation_messages` | User message preceding the chain |
| `context` | `conversation_messages` | Last `schema_prefetch_tool` result before SQL write |
| `answer` | `conversation_messages` | Tool result containing generated SQL |

**81 chains** across 588 conversations. Multiple `schema_prefetch` calls can occur per user turn — use the last prefetch before SQL write as context, not the first.

### RAGAS Metrics

- **Faithfulness** — Does the SQL answer stay grounded in the retrieved schema context?
- **Context Relevance** — Did `schema_prefetch` retrieve tables actually relevant to the question?
- **Answer Relevance** — Does the SQL address what the user actually asked?

### Observability Metrics (Beyond RAGAS)

Per eval unit:

| Metric | Source |
|--------|--------|
| `chain_length` | Tool call count between user message and `schema_prefetch` |
| `prefetch_iteration_count` | How many `schema_prefetch` calls per user turn |
| `context_size_chars` | Length of `schema_prefetch` tool result |
| `cost_usd` | `conversation_runs.cost_usd` |
| `duration_ms` | `conversation_runs.duration_ms` |
| `num_turns` | `conversation_runs.num_turns` |

### The Feedback Loop

```
querybot generates SQL
    → RAGAS scores it
    → scores land in Snowflake
    → quality gate filters learning pipeline
    → only high-scoring conversations generate documentation updates
    → better table documentation → better retrieval → better SQL
    → repeat

Phase 2: querybot queries its own eval data
    → "which query patterns scored lowest last week?"
    → prescriptive recommendations, not just measurements
```

---

## Data Pipeline

### Sources
- **Postgres** (`172.28.42.77:5432`, db `query_bot`) — 672 conversations, ~39k messages
- **arXiv API** — 258 papers ingested, raw JSONL in S3

### Airflow DAGs

| DAG | Description | Schedule |
|-----|-------------|----------|
| `s3_to_snowflake` | arXiv papers → Snowflake | Manual (dev) |
| `querybot_postgres_to_snowflake` | Querybot Postgres → Snowflake | Manual (dev) |

Both DAGs use:
- Watermark-based incremental loads via Airflow Variables
- Late Snowflake connection (open after Postgres fetch completes)
- Batch INSERT with chunked VALUES (one round-trip per 500-row chunk)

### Snowflake

| Database | Schema | Tables |
|----------|--------|--------|
| `RAG_EVAL` | `ARXIV` | `RAW_PAPERS` |
| `RAG_EVAL` | `QUERYBOT` | `CONVERSATIONS`, `CONVERSATION_RUNS`, `CONVERSATION_MESSAGES` |

### dbt Project (`rag_eval/`)

```
models/
└── staging/
    ├── stg_arxiv_papers.sql        ✅ live, 7 passing tests
    ├── stg_conversations.sql       🔲 planned
    ├── stg_conversation_runs.sql   🔲 planned
    └── stg_conversation_messages.sql 🔲 planned

intermediate/                       🔲 planned
    └── int_schema_prefetch_chains.sql  — assembles eval unit per chain

marts/                              🔲 planned
    └── eval_units.sql              — final RAGAS input grain
```

---

## Progress Tracker

### Track 1 — arXiv Pipeline

| Phase | Description | Status |
|-------|-------------|--------|
| P1 | AWS foundation (IAM, S3, Terraform) | ✅ Complete |
| P2 | Airflow + Snowflake ingestion | ✅ Complete |
| P3 | RAG system + RAGAS eval dataset | 🔲 Planned |
| P4 | dbt feature models | 🔲 Planned |
| P5 | MLflow + quality classifier | 🔲 Planned |
| P6 | FastAPI + Evidently | 🔲 Planned |
| P7 | Streamlit dashboard + polish | 🔲 Planned |

### Track 2 — Querybot Eval

| Phase | Description | Status |
|-------|-------------|--------|
| P1 | Postgres → Snowflake pipeline (Airflow DAG) | ✅ Complete |
| P2 | dbt staging layer (typed, clean) | 🔲 In progress |
| P3 | dbt intermediate — eval unit assembly | 🔲 Planned |
| P4 | RAGAS scoring service | 🔲 Planned |
| P5 | Observability dashboard | 🔲 Planned |
| P6 | Quality gate on learning pipeline | 🔲 Planned |
| P7 | Meta layer — querybot queries its own eval data | 🔲 Planned |

---

## Repo Structure

```
.
├── airflow/
│   ├── dags/
│   │   ├── querybot_postgres_to_snowflake.py   # Track 2 ingestion
│   │   └── s3_to_snowflake.py                  # Track 1 ingestion
│   └── config/
├── config/
│   └── models.yaml                             # Model routing config
├── data/raw/                                   # arXiv raw JSONL
├── infra/terraform/                            # AWS IaC (S3, IAM, ECS)
├── ingestion/
│   └── arxiv_ingest.py                        # arXiv → S3
├── rag_eval/                                   # dbt project
│   └── models/staging/
│       └── stg_arxiv_papers.sql
└── research/
    ├── querybot_research.md
    └── struggles.md
```

---

## Environment

- **Dev**: WSL2 Ubuntu, VS Code, `uv` for package management
- **Airflow**: Docker Compose at `~/airflow/`, DAGs mounted from `~/projects/rag-eval/airflow/dags/`
- **Snowflake**: Account `ZADUWZC-QRC41354`, keypair auth at `~/.snowflake/rsa_key.pem`
- **AWS**: IAM user `trey-dev`, S3 bucket `rag-eval-papers-raw`, Terraform state in S3

---

## Key Design Decisions

**Why watermark over full refresh?** Querybot is a production system. Full table scans on every DAG run would stress the Postgres instance unnecessarily. Watermark on `conversations.updated_at` keeps incremental loads cheap.

**Why batch INSERT over MERGE?** The watermark guarantees no duplicate loads — MERGE upsert logic is unnecessary complexity. Plain INSERT with chunked VALUES batches is simpler and faster.

**Why all four message types?** Filtering to just `schema_prefetch` messages at extract time would force reingestion if the eval strategy changes. Extract everything (`user`, `tool_call`, `tool_result`, `thinking`), filter in dbt. Raw layer stays immutable.

**Why Snowflake over Grafana for eval data?** Operational metrics (OTel/Prometheus) belong in Grafana. Quality metrics belong somewhere queryable — both by humans writing SQL and eventually by querybot itself querying its own performance data.

---

*Repo: `git@github-personal:treycurtis/rag-eval.git`*