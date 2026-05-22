# IMPORTANT — Read Before Touching This Project

Critical facts, known quirks, and decisions that have been made. Read this before starting a new session or building anything new.

---

## Assistant Architecture — DO NOT SECOND GUESS

**The final `thinking` message IS the user-facing response.**

The assistant's WebSocket handler persists all TextBlock content as `thinking`. There is no separate assistant message type. When evaluating a conversation, the last `thinking` message is what the user actually received. Earlier `thinking` messages may be internal reasoning steps.

This means:
- Do not treat the thinking trace as internal-only
- Do not look for a separate "assistant" message after the last thinking block — it does not exist
- A conversation that ends on a `thinking` message is complete, not truncated
- A conversation that ends on a `user` message means the assistant never responded (connection drop)

---

## Classifier Architecture Decisions

### Eval unit
One user → schema_prefetch chain per conversation. Inputs are:
- **Question:** user message
- **Context:** last schema_prefetch `tool_result` markdown blob
- **Answer:** SQL file content echoed in subsequent `tool_result`

### Conversation types
Priority order for type assignment: `ghost → anomalous → unknown → generation → modification → diagnostic → consultation`

`lookup` type was retired — folded into consultation.  
`complex` type was retired — only 3 conversations, folded into modification. `has_non_sql_write` boolean carries the distinction.

### Ghost conversations
133 conversations have `total_turns = 0` and no messages. These are excluded from the classifiable corpus. Correct classifiable count is 429.

### Pre-prefetch sql_write conversations
Labeled `modification` but may be generation attempts from before tooling existed. Spot-check before classifier training.

---

## Known Data Quirks

### execute_sql double-counting
CTEs count both `tool_call` and `tool_result` rows. 16 `tool_result` rows affected. Not material for v1 — fix in v2.

### Three distinct execute_sql error patterns
- `is_error = TRUE` → MCP layer failure
- `is_error = FALSE` + `OperationalError` → DB error (captured as `permission_error_count`)
- `is_error = FALSE` + `execution_status: error` → Python executor errors (NOT captured — v2 backlog)

### User-rejected tool calls
Surface via `%tool use was rejected%` in `tool_result` content where `is_error = FALSE`. Captured as `user_rejected_tool_count` in `int_conversation_metrics`.

### Dev/prod value disclaimer
Evidence in `thinking` content of the assistant reasoning about dev/prod environment differences. Cannot confirm whether it surfaces in final responses. Warrants `success_needs_validation` label in Prompt 1.

### Connection drops
Some conversations end on a `user` message — the assistant connection dropped before responding. These are NOT the same as `failure_abandoned` (where the assistant responded but couldn't complete). A future observability metric, not a classifier priority for v1.

### Duplicate user messages
Several conversations have duplicate `user` messages (same content, multiple rows). Usually a UI glitch or double-send, not meaningful. Only matters if the duplicate is the final message — indicates a connection drop.

### Corpus grew mid-project
714 → 735 conversations from a manual DAG run. Pipeline is healthy. `schedule=None` confirmed.

---

## Harness Configuration

### Rate limits
- API limit: **30,000 input tokens/minute** (Tier 1)
- Safe worker count: **3 workers** (3 × ~8k tokens = ~24k/minute)
- Do not exceed 5 workers without upgrading to Tier 2 ($40 cumulative spend)
- Retry logic handles occasional spikes — 60s wait on first retry

### Truncation strategy
Per-message limits in `fetch_conversation`:
```python
TRUNCATION_LIMITS = {
    "tool_result": 2000,
    "thinking": None,   # never truncate — final response lives here
    "tool_call": 500,
    "user": 2000,
}
```

Whole-conversation cap in `truncate_conversation`: **30,000 chars**
- Keep first 20,000 chars (question + initial approach)
- Keep last 10,000 chars (final response)
- Drop middle (investigative tool call loops)

**For Prompt 1:** consider bumping `user` limit to 4000 — SQL output conversations frequently have large SQL pastes in user messages.

### Token budget
- `max_tokens=2000` for classifier responses (bumped from 1000 to prevent JSON truncation)
- Always validate `outcome` field exists after parse — raise `ValueError` to trigger retry if missing

---

## Snowflake Schema Naming

- `RAG_EVAL.STAGING` — all staging and intermediate dbt models
- `RAG_EVAL.ARXIV` — arXiv pipeline (separate track)

Always fully qualify table names in scripts: `RAG_EVAL.STAGING.TABLE_NAME`

---

## Patch Run Protocol

When rerunning specific conversations:
1. Delete the bad rows from Snowflake first: `DELETE FROM RAG_EVAL.STAGING.INT_CONVERSATION_OUTCOMES_RAW WHERE conversation_id IN (...)`
2. Hardcode `fetch_consultation_ids()` to return the target IDs
3. Change `backup_path` to a patch filename (e.g. `consultation_outcomes_patch.json`) to avoid overwriting the main backup
4. Run the classifier
5. Restore `fetch_consultation_ids()` to the real query
6. Restore `backup_path` to default
7. Commit — never commit the hardcoded version

---

## Consultation Classifier Validation Cases

| Conversation | Expected Label | Notes |
|---|---|---|
| 225 | success_with_correction | Initial misclassification corrected cleanly |
| 530 | success_clean | Definitive dead end — no universal flag exists |
| 691 | success_clean | Short but complete |
| 701 | failure_abandoned | Permission error blocked completion |
| 643 | inconclusive | Test message |
| 724 | success_clean | Blind test — data lineage question, fully answered |