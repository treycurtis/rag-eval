# RAG Eval Pipeline

An evaluation and observability system for RAG applications that enables cost-aware model routing. The pipeline continuously scores RAG outputs using RAGAS (faithfulness, answer relevance, context relevance), tracks quality over time in Snowflake, and feeds a lightweight classifier that routes queries between a cheap tier (DeepSeek V3.2) and a strong tier (Claude Sonnet) based on predicted complexity. The routing model is config-driven and model-agnostic by design. A diagnostic dashboard surfaces cost-per-query alongside quality scores, drift alerts, and drill-down to failing samples — making the cost/quality tradeoff visible and actionable.

**Mission:** Maximize the benefits of AI systems while minimizing their energy and cost footprint — not by sacrificing quality, but by measuring it well enough to know when you don't need the expensive model.

---

## Architecture

```
arXiv ML Papers
      │
      ▼
   AWS S3
(raw paper store)
      │
      ▼
 Airflow on ECS Fargate
(orchestration + ingestion DAGs)
      │
      ▼
   Snowflake
(raw + staged data warehouse)
      │
      ▼
     dbt
(feature engineering + transforms)
      │
      ▼
  RAG System
(retrieval + generation layer)
      │
      ▼
  RAGAS Eval
(faithfulness / answer relevance / context relevance)
      │
      ▼
 MLflow on EC2 Spot
(experiment tracking + model registry)
      │
      ▼
Quality Classifier
(predicts query complexity → routing signal)
      │
      ▼
   Router
(DeepSeek V3.2 ← simple │ complex → Claude Sonnet)
      │
      ▼
 FastAPI on ECS Fargate
(serving layer)
      │
      ▼
  Evidently AI
(drift monitoring)
      │
      ▼
Streamlit Dashboard
(cost-per-query, quality scores, drift alerts, failing sample explorer)
```

---

## Stack

| Layer | Technology |
|---|---|
| Cloud | AWS (S3, ECS Fargate, EC2 Spot) |
| Orchestration | Apache Airflow on ECS Fargate |
| Data Warehouse | Snowflake |
| Transforms | dbt |
| Eval Framework | RAGAS |
| Experiment Tracking | MLflow |
| Serving | FastAPI |
| Drift Monitoring | Evidently AI |
| Dashboard | Streamlit Cloud |
| Cheap Model Tier | DeepSeek V3.2 (config-swappable) |
| Strong Model Tier | Claude Sonnet (config-swappable) |
| IaC | Terraform |

---

## Project Phases

### Phase 1 — AWS Foundation + arXiv Ingestion (Weeks 1–2)
- AWS account, IAM roles, S3 bucket structure
- arXiv ingestion script (Python, ML papers via API)
- Papers landing in S3 as raw JSON
- Project repo scaffolded, Terraform introduced week 2

### Phase 2 — Orchestration + Snowflake (Weeks 3–4)
- Airflow on ECS Fargate
- First DAG: S3 → Snowflake raw load
- Snowflake schema design for papers + eval results

### Phase 3 — RAG System + RAGAS Eval Dataset (Weeks 5–6)
- RAG system wired up against arXiv corpus
- RAGAS scoring pipeline (faithfulness, answer relevance, context relevance)
- LLM-as-judge via Claude generates labeled eval dataset

### Phase 4 — dbt Feature Engineering (Weeks 7–8)
- dbt models on top of raw eval results in Snowflake
- Feature transforms: query complexity signals, score distributions, retrieval stats
- dbt tests + documentation

### Phase 5 — MLflow + Quality Classifier (Weeks 9–10)
- MLflow experiment tracking on EC2 Spot
- Train lightweight classifier on RAGAS-labeled data
- Classifier predicts query complexity → routing signal
- Model registry: champion/challenger pattern

### Phase 6 — FastAPI + Evidently Drift Monitoring (Weeks 11–12)
- FastAPI serving layer on ECS Fargate
- Router logic: classifier score → DeepSeek V3.2 or Claude Sonnet
- Evidently AI drift monitoring wired into Airflow
- Model and data drift alerts

### Phase 7 — Streamlit Dashboard + Polish (Weeks 13–14)
- Public Streamlit dashboard
- Cost-per-query alongside quality scores
- Score distributions with drill-down to failing samples
- A/B comparison between prompt/model versions
- Retrieval coverage heatmap
- Drift alerts + sample explorer
- README polish, architecture diagram, cost breakdown doc

---

## Routing Design

The router is intentionally model-agnostic. Model selection is driven by config, not hardcoded logic:

```yaml
# config/models.yaml
router:
  cheap_tier:
    provider: deepseek
    model: deepseek-chat
    max_cost_per_1m_tokens: 0.28
  strong_tier:
    provider: anthropic
    model: claude-sonnet-4-5
    max_cost_per_1m_tokens: 15.00
  threshold: 0.65  # classifier confidence below this → cheap tier
```

Swapping models requires a config change, not a code change.

---

## Dashboard Design

The dashboard is diagnostic, not decorative. Every view exists to answer a question:

| View | Question it answers |
|---|---|
| Cost-per-query over time | Is routing saving money? |
| Score distributions | Where is quality concentrated and where does it fall off? |
| Failing sample explorer | What does a bad RAG answer actually look like? |
| A/B comparison | Does prompt version B actually outperform A? |
| Retrieval coverage heatmap | Which parts of the corpus are being used vs ignored? |
| Drift alerts | Has something broken or degraded since last week? |

---

## Repository Structure

```
rag-eval/
├── ingestion/          # arXiv ingestion scripts
├── airflow/            # DAG definitions
├── dbt/                # dbt project (transforms + features)
├── api/                # FastAPI serving layer
├── dashboard/          # Streamlit app
├── infra/
│   └── terraform/      # IaC for all AWS resources
├── notebooks/          # Exploration + eval analysis
├── research/           # Failure mode recon, querybot notes
├── config/             # Model routing config, env templates
├── .env                # Never committed
└── pyproject.toml
```

---

## Local Development

```bash
# Clone and install
git clone https://github.com/yourusername/rag-eval.git
cd rag-eval
uv sync

# Configure environment
cp .env.example .env
# Add API keys and Snowflake credentials

# Run ingestion
uv run python ingestion/arxiv_ingest.py
```

---

## Status

| Phase | Status |
|---|---|
| P1: AWS Foundation + Ingestion | 🔄 In progress |
| P2: Airflow + Snowflake | ⬜ Not started |
| P3: RAG + RAGAS Eval | ⬜ Not started |
| P4: dbt Features | ⬜ Not started |
| P5: MLflow + Classifier | ⬜ Not started |
| P6: FastAPI + Evidently | ⬜ Not started |
| P7: Dashboard + Polish | ⬜ Not started |

---

*Target: Portfolio-ready late July / early August 2026*