# Data Dictionary — RAG_EVAL

All dbt models live in `RAG_EVAL.STAGING` unless otherwise noted.

---

## Staging Models (dbt)

### `STG_CONVERSATIONS`
One row per conversation. Source: production assistant Postgres.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key. Unique identifier for each conversation session. |
| `user_id` | INTEGER | Identifier for the user who had the conversation. |
| `conversation_uuid` | VARCHAR | UUID for the conversation. |
| `sdk_session_id` | VARCHAR | SDK session ID for the conversation. |
| `title` | VARCHAR | Title of the conversation. |
| `is_active` | BOOLEAN | Whether the conversation is currently active. |
| `message_count` | INTEGER | Number of messages in the conversation. |
| `total_runs` | INTEGER | Total number of runs in the conversation. |
| `total_turns` | INTEGER | Total number of turns across all runs. |
| `total_cost_usd` | NUMERIC(10,4) | Total API cost in USD. |
| `total_duration_ms` | INTEGER | Total duration of all runs in milliseconds. |
| `last_message_at` | TIMESTAMP_NTZ | Timestamp of the last message. |
| `last_run_at` | TIMESTAMP_NTZ | Timestamp of the last run. |
| `learning_extraction_status` | VARCHAR | Status of the learning extraction pipeline for this conversation. |
| `learning_extracted_at` | TIMESTAMP_NTZ | When learning extraction completed. NULL if not yet run. |
| `summary` | VARCHAR | Summary of the conversation. |
| `created_at` | TIMESTAMP_NTZ | When the conversation was initiated. |
| `updated_at` | TIMESTAMP_NTZ | Last activity timestamp. |

---

### `STG_CONVERSATION_RUNS`
One row per run within a conversation. A conversation can have multiple runs (e.g. user sends a message, assistant responds — that's one run). Source: production assistant Postgres.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key. Unique identifier for the run. |
| `conversation_id` | INTEGER | Foreign key to `STG_CONVERSATIONS`. |
| `user_message_id` | INTEGER | Identifier for the user message that triggered the run. |
| `num_turns` | INTEGER | Number of turns in this run. |
| `duration_ms` | INTEGER | Wall clock time for the run in milliseconds. |
| `cost_usd` | NUMERIC(10,4) | API cost for this run in USD. |
| `sdk_session_id` | VARCHAR | SDK session ID for the run. |
| `started_at` | TIMESTAMP_NTZ | When the run started. |
| `completed_at` | TIMESTAMP_NTZ | When the run completed. |

---

### `STG_CONVERSATION_MESSAGES`
One row per message within a conversation. This is the most granular table — all content lives here. Source: production assistant Postgres.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Primary key. Internal row ID. |
| `conversation_id` | INTEGER | Foreign key to `STG_CONVERSATIONS`. |
| `message_type` | VARCHAR | Type of message. See message types below. |
| `content` | VARCHAR | Full message content. May be NULL for some tool calls. |
| `message_metadata` | VARIANT | JSON blob with tool call input/output metadata. |
| `tool_use_id` | VARCHAR | Links `tool_call` and `tool_result` rows for the same tool invocation. |
| `is_error` | BOOLEAN | Whether the message represents an error state. |
| `sequence_number` | INTEGER | Ordering within the conversation. Ascending. |
| `created_at` | TIMESTAMP_NTZ | When the message was persisted. |

**Message types:**

| Type | Description |
|---|---|
| `user` | Message from the human user. |
| `thinking` | Assistant internal reasoning AND user-facing response. The final `thinking` message is what the user received. Earlier `thinking` messages may be intermediate reasoning. |
| `tool_call` | The assistant invoking a tool (schema search, SQL execution, memory search, etc.). |
| `tool_result` | The result returned from a tool invocation. Paired with `tool_call` via `tool_use_id`. |

---

### `STG_ARXIV_PAPERS`
One row per arXiv paper ingested. Separate pipeline from the conversation eval track. Schema: `RAG_EVAL.ARXIV`.

| Column | Type | Description |
|---|---|---|
| `paper_id` | VARCHAR | Primary key. arXiv paper identifier (e.g. `2401.00001`). |
| `arxiv_url` | VARCHAR | URL to the paper on arXiv. |
| `title` | VARCHAR | Paper title. |
| `abstract` | VARCHAR | Full abstract text. |
| `authors` | VARCHAR | Author list. |
| `categories` | VARCHAR | arXiv category tags. |
| `published_date` | TIMESTAMP_NTZ | Original publication date. |
| `ingested_date` | TIMESTAMP_NTZ | When the paper was ingested into S3 and Snowflake. |

---

## Intermediate Models (dbt)

### `INT_CONVERSATION_METRICS`
One row per conversation. Rolled-up behavioral signals for use in classification and analysis.

| Column | Type | Description |
|---|---|---|
| `conversation_id` | INTEGER | Primary key. Foreign key to `STG_CONVERSATIONS`. |
| `total_turns` | INTEGER | Total number of message exchanges in the conversation. |
| `total_duration_ms` | INTEGER | Sum of all run durations in milliseconds. |
| `total_user_messages` | INTEGER | Count of `user` message type rows. |
| `prefetch_call_count` | INTEGER | Number of schema_prefetch tool calls. |
| `first_prefetch_sequence` | INTEGER | Sequence number of the first schema_prefetch call. |
| `sql_write_count` | INTEGER | Number of SQL files written to disk. |
| `non_sql_write_count` | INTEGER | Number of non-SQL files written (Python, markdown, etc.). |
| `first_write_sequence` | INTEGER | Sequence number of the first file write. |
| `prefetch_to_write_gap` | INTEGER | Sequence gap between first prefetch and first write. |
| `execute_sql_count` | INTEGER | Number of `execute_sql` tool calls. Note: double-counts `tool_call` + `tool_result` rows — v2 fix backlog. |
| `execute_sql_success_count` | INTEGER | Number of successful SQL executions. |
| `permission_error_count` | INTEGER | Number of SQL executions that returned `OperationalError` (DB permission failures). |
| `code_review_count` | INTEGER | Number of code review tool calls. |
| `code_review_score_first` | INTEGER | Score from the first code review in the conversation. |
| `code_review_score_last` | INTEGER | Score from the last code review in the conversation. |
| `code_review_score_delta` | INTEGER | Difference between last and first code review score (positive = improvement). |
| `user_correction_count` | INTEGER | Number of turns where user corrected querybot's direction. |
| `stale_doc_warning_count` | INTEGER | Number of stale documentation warnings surfaced by the schema tool. |
| `tool_use_error_count` | INTEGER | Number of tool calls where `is_error = TRUE` (MCP layer failures). |
| `codebase_error_count` | INTEGER | Number of codebase-related errors encountered. |
| `conversation_created_at` | TIMESTAMP_NTZ | When the conversation was initiated. Pass-through from `STG_CONVERSATIONS.created_at`. Used as the time axis for trend analysis. |
| `corpus_era` | VARCHAR | `pre_prefetch` or `post_prefetch` — indicates whether schema_prefetch tooling was available when the conversation occurred. |
| `user_rejected_tool_count` | INTEGER | Number of tool calls rejected by the user (surfaces via `%tool use was rejected%` in `tool_result` content). |
| `total_cost_usd` | FLOAT | Sum of `cost_usd` from `STG_CONVERSATION_RUNS`. |
| `run_count` | INTEGER | Number of runs in the conversation. |
| `avg_run_duration_ms` | FLOAT | Average run duration in milliseconds. |
| `learning_extracted` | BOOLEAN | Whether the learning extraction pipeline has processed this conversation. Derived from `learning_extracted_at IS NOT NULL` on `STG_CONVERSATIONS`. |

---

### `INT_CONVERSATION_TYPE`
One row per conversation. Assigns a conversation type label based on behavioral signals from `INT_CONVERSATION_METRICS`.

| Column | Type | Description |
|---|---|---|
| `conversation_id` | INTEGER | Primary key. |
| `corpus_era` | VARCHAR | Pass-through from `INT_CONVERSATION_METRICS`. `pre_prefetch` or `post_prefetch`. |
| `conversation_type` | VARCHAR | Type label. See types below. |
| `is_ghost` | BOOLEAN | `total_turns = 0` — no messages at all. |
| `is_anomalous` | BOOLEAN | `total_turns > 75` — excluded from classifiable corpus. |
| `is_unknown` | BOOLEAN | Pre-prefetch era, zero signals, not ghost. |
| `has_generation_signal` | BOOLEAN | Post-prefetch AND `prefetch_call_count > 0`. |
| `has_sql_write` | BOOLEAN | `sql_write_count > 0`. |
| `has_non_sql_write` | BOOLEAN | `non_sql_write_count > 0`. |
| `has_execute_sql` | BOOLEAN | `execute_sql_count > 0`. |
| `has_user_interrupt` | BOOLEAN | `user_rejected_tool_count > 0`. |

**Type labels (priority order):**

| Type | Count | Description |
|---|---|---|
| `ghost` | 133 | `total_turns = 0`, no messages. Excluded from classifiable corpus. |
| `anomalous` | 36 | `total_turns > 75`. Excluded from classifiable corpus. |
| `unknown` | 137 | Pre-prefetch era, insufficient signals to classify. Excluded. |
| `generation` | 63 | Post-prefetch, prefetch fired, SQL written. Absorbs former `complex` type (sessions with both SQL and non-SQL writes). |
| `modification` | 236 | SQL written without schema prefetch. Includes mixed-write sessions (SQL + non-SQL). |
| `diagnostic` | 20 | Non-SQL file written, no SQL write. Routes to consultation classifier prompt; use `has_non_sql_deliverable` on `FCT_CONVERSATION_OUTCOMES` to distinguish within results. |
| `consultation` | 110 | No file writes. Querybot answered questions verbally. Catch-all for schema/doc/logic lookups. |

**Classifiable corpus: 429 conversations** (generation + modification + diagnostic + consultation)

**Type logic notes:**
- `complex` type retired — post-prefetch sessions with both SQL and non-SQL writes fold into `generation`
- `lookup` type retired — execute-only sessions fold into `consultation` or `modification` based on write signals
- Priority order: `ghost` → `anomalous` → `unknown` → `generation` → `modification` → `diagnostic` → `consultation`

---

## Classifier Output

### `INT_CONVERSATION_OUTCOMES_RAW`
One row per classification run. Written by the Python classifier scripts, not dbt. Not unique on `conversation_id` — patch runs can create duplicates. Deduplicate on `MAX(classified_at)` before joining.

| Column | Type | Description |
|---|---|---|
| `conversation_id` | INTEGER | Foreign key to `STG_CONVERSATIONS`. |
| `conversation_type` | VARCHAR | Type label at time of classification (`consultation`, `generation`, `modification`). |
| `outcome` | VARCHAR | Classifier outcome label. See outcome labels below — labels differ by conversation type. |
| `question_understanding` | INTEGER | Rubric score 1-3. Did querybot correctly interpret the question? |
| `resource_exhaustion` | INTEGER | Rubric score 1-3. Did querybot use available tools appropriately before writing SQL? |
| `answer_grounding` | INTEGER | Rubric score 1-3. Was the conclusion supported by evidence? |
| `actionability` | INTEGER | Rubric score 1-3. Could the user act on the response? |
| `flag_dev_acknowledged` | BOOLEAN | SQL output only. Whether querybot proactively noted dev environment limitations. NULL for consultation rows. |
| `reasoning` | VARCHAR | 2-3 sentence explanation of the outcome label from the classifier. |
| `char_count` | INTEGER | Character count of the assembled conversation content fed to the classifier (before truncation). |
| `error` | VARCHAR | Error message if classification failed. NULL on success. |
| `classified_at` | TIMESTAMP_TZ | When the classification was written. |

**Outcome labels — consultation (`run_consultation_classifier.py`):**

| Outcome | Description |
|---|---|
| `success_clean` | Querybot understood the question, used tools appropriately, grounded its answer in evidence, and gave the user something actionable. |
| `success_with_correction` | Querybot initially went wrong but self-corrected when redirected, and ultimately delivered a solid response. |
| `failure_knowledge_gap` | Querybot searched thoroughly but the information simply wasn't available. Not querybot's fault. |
| `failure_wrong_direction` | Querybot misunderstood the question or pursued a wrong approach without self-correcting. |
| `failure_abandoned` | Conversation ended prematurely — infrastructure failure, user stopped responding, or connection dropped. |
| `inconclusive` | Too short, too ambiguous, or clearly a test/diagnostic session with no substantive exchange. |

**Outcome labels — SQL output (`run_sql_output_classifier.py`):**

| Outcome | Description |
|---|---|
| `success_clean` | Querybot understood the request, used appropriate tools, produced correct SQL, and the user got a working result. |
| `success_iterative` | Querybot ultimately delivered working SQL but required meaningful back-and-forth — user corrections, misunderstandings resolved, multiple revision cycles. |
| `failure_wrong_direction` | Querybot misunderstood the requirement and didn't self-correct. User did not get working SQL. |
| `failure_environment` | Querybot built logically correct SQL but couldn't execute or validate it due to environment blockers outside its control (role not selected, IF block restriction, no DB access). |
| `failure_schema_gap` | Querybot searched thoroughly but the schema, memory, and documentation did not contain what was needed. Not querybot's fault. |
| `failure_abandoned` | Conversation ended before querybot could complete the SQL — session termination, not environment blocker. |
| `inconclusive` | Too short, too ambiguous, or a test/diagnostic session. Also used when querybot was asked to review SQL but no file was written. |

**Current coverage:** 407 classified rows across 429 classifiable conversations. 
22 unclassified remaining. Diagnostic conversations (20) included, routed to consultation classifier prompt
---

## Marts Models (dbt)

### `FCT_CONVERSATION_OUTCOMES`
Final quality layer. One row per classifiable conversation (generation, modification, diagnostic, consultation). Ghost, anomalous, and unknown conversations are excluded. Conversations not yet classified have NULL outcome fields with `is_classified = FALSE`. Rubric scores, reasoning, and `flag_dev_acknowledged` are additionally NULL for `inconclusive` outcomes.

| Column | Type | Description |
|---|---|---|
| `conversation_id` | INTEGER | Primary key. |
| `conversation_type` | VARCHAR | Type label from `INT_CONVERSATION_TYPE`. One of: `generation`, `modification`, `diagnostic`, `consultation`. |
| `is_classified` | BOOLEAN | TRUE if a classifier result exists for this conversation. |
| `outcome` | VARCHAR | Classifier outcome label. NULL if not yet classified. |
| `classified_as_type` | VARCHAR | `conversation_type` at time of classification. May differ from current label if type logic was updated. |
| `classified_at` | TIMESTAMP_TZ | Timestamp of the most recent classification run. |
| `question_understanding` | INTEGER | Rubric score 1-3. NULL if not classified or outcome is `inconclusive`. |
| `resource_exhaustion` | INTEGER | Rubric score 1-3. NULL if not classified or outcome is `inconclusive`. |
| `answer_grounding` | INTEGER | Rubric score 1-3. NULL if not classified or outcome is `inconclusive`. |
| `actionability` | INTEGER | Rubric score 1-3. NULL if not classified or outcome is `inconclusive`. |
| `flag_dev_acknowledged` | BOOLEAN | SQL output only. NULL for consultation, diagnostic, unclassified, and `inconclusive` rows. |
| `reasoning` | VARCHAR | Classifier explanation. NULL if not classified or `inconclusive`. |
| `char_count` | INTEGER | Character count of conversation content fed to the classifier. Valid for all classified rows including `inconclusive`. |
| `total_turns` | INTEGER | Total message turns. |
| `total_user_messages` | INTEGER | Count of `user` message type rows. |
| `run_count` | INTEGER | Number of runs in the conversation. |
| `total_cost_usd` | FLOAT | Sum of API costs across all runs. |
| `total_duration_ms` | INTEGER | Sum of all run durations in milliseconds. |
| `avg_run_duration_ms` | FLOAT | Average run duration in milliseconds. |
| `corpus_era` | VARCHAR | `pre_prefetch` or `post_prefetch`. |
| `conversation_created_at` | TIMESTAMP_NTZ | When the conversation was initiated. Pass-through from `STG_CONVERSATIONS.created_at`. Used as the time axis for trend analysis. |
| `prefetch_call_count` | INTEGER | Number of schema_prefetch tool calls. |
| `first_prefetch_sequence` | INTEGER | Sequence number of first schema_prefetch call. |
| `sql_write_count` | INTEGER | Number of SQL files written. |
| `non_sql_write_count` | INTEGER | Number of non-SQL files written. |
| `first_write_sequence` | INTEGER | Sequence number of first file write. |
| `prefetch_to_write_gap` | INTEGER | Sequence gap between first prefetch and first write. |
| `execute_sql_count` | INTEGER | Number of `execute_sql` tool calls. |
| `execute_sql_success_count` | INTEGER | Number of successful SQL executions. |
| `permission_error_count` | INTEGER | Number of `OperationalError` (DB permission) failures. |
| `code_review_count` | INTEGER | Number of code review tool calls. |
| `code_review_score_first` | INTEGER | Score from the first code review. |
| `code_review_score_last` | INTEGER | Score from the last code review. |
| `code_review_score_delta` | INTEGER | Last minus first code review score. Positive = improvement. |
| `user_correction_count` | INTEGER | Proxy count of user correction turns. |
| `user_rejected_tool_count` | INTEGER | Number of tool calls rejected by the user. |
| `stale_doc_warning_count` | INTEGER | Number of stale documentation warnings. |
| `tool_use_error_count` | INTEGER | Number of MCP layer failures (`is_error = TRUE`). |
| `codebase_error_count` | INTEGER | Number of `ImportError` / `ModuleNotFoundError` occurrences. |
| `has_generation_signal` | BOOLEAN | Post-prefetch AND `prefetch_call_count > 0`. |
| `has_sql_write` | BOOLEAN | `sql_write_count > 0`. |
| `has_non_sql_write` | BOOLEAN | `non_sql_write_count > 0`. |
| `has_non_sql_deliverable` | BOOLEAN | Alias of `has_non_sql_write`. Use to identify diagnostic conversations within consultation classifier results. |
| `has_execute_sql` | BOOLEAN | `execute_sql_count > 0`. |
| `has_user_interrupt` | BOOLEAN | `user_rejected_tool_count > 0`. |
| `learning_extracted` | BOOLEAN | Whether the learning extraction pipeline has processed this conversation. |

---

## Schema: RAG_EVAL.ARXIV

### `STG_ARXIV_PAPERS`
See entry above under Staging Models. 258 papers ingested as of last run. 7 passing dbt tests.
