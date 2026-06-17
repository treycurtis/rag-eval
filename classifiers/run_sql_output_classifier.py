
# TODO v2: Relevance pre-classifier
# Before running the full outcome rubric, score each conversation 1-3 on relevance
# to querybot's core job (schema lookup, SQL generation, data analysis support).
# Score 1 = capability test, environment debug, or off-task (skip outcome classification).
# Score 2-3 = proceed to full classifier.
# Gate logic: learning extraction requires relevance >= 2 AND outcome in success tier.
# Benefit: separates "not scoreable" (inconclusive) from "not relevant" (relevance=1),
# cleans outcome distributions, and makes learning gate decisions auditable.
# Also enables relevance scoring of unknown/pre-prefetch corpus without new type logic.
# Excluded conversation IDs (hardcoded): [49] — capability tests confirmed non-relevant.


import anthropic
import hashlib
import json
import os
import re
import snowflake.connector
from pathlib import Path
from datetime import datetime, timezone
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from metrics.metrics import CLASSIFIER_RETRIES_TOTAL
from metrics.instrumented import instrumented_classify
from metrics.server import start_metrics_server

from dotenv import load_dotenv
load_dotenv()

# ── Snowflake connection ──────────────────────────────────────────────────────
def get_snowflake_connection():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    with open(os.path.expanduser("~/.snowflake/rsa_key.pem"), "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(), password=None, backend=default_backend()
        )
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )

    return snowflake.connector.connect(
        account="ZADUWZC-QRC41354",
        user="BUBCHAMPAGNE",
        private_key=private_key_bytes,
        warehouse="DEV_WH",
        database="RAG_EVAL",
        schema="STAGING"
    )

# ── Classifier identity (model + prompt) ──────────────────────────────────
# MODEL is the judge. PROMPT_VERSION is derived (below, after PROMPT_1) from a
# hash of (MODEL + PROMPT_1), so ANY change to the model or the prompt
# automatically rotates the version and triggers a full reclassification. Pure
# plumbing changes leave it untouched, so reruns stay incremental. Never
# hand-edit the version.
MODEL = "claude-sonnet-4-6"

# ── Ensure outcomes table / version columns exist ───────────────────────────
def ensure_outcomes_table(conn):
    cursor = conn.cursor()
    cursor.execute("""
        ALTER TABLE INT_CONVERSATION_OUTCOMES_RAW
        ADD COLUMN IF NOT EXISTS prompt_version VARCHAR
    """)
    cursor.execute("""
        ALTER TABLE INT_CONVERSATION_OUTCOMES_RAW
        ADD COLUMN IF NOT EXISTS model VARCHAR
    """)
    cursor.execute("""
        ALTER TABLE INT_CONVERSATION_OUTCOMES_RAW
        ADD COLUMN IF NOT EXISTS prompt_hash VARCHAR
    """)
    conn.commit()

# ── Fetch SQL output conversation IDs ─────────────────────────────────────────

# Fetch converations that hit retry limit
# def fetch_sql_output_ids(conn) -> list[int]:
#     return [19, 210, 609, 633]

def fetch_sql_output_ids(conn, prompt_version: str | None = None) -> list[int]:
    prompt_version = prompt_version or PROMPT_VERSION
    query = """
        SELECT t.conversation_id
        FROM INT_CONVERSATION_TYPE t
        WHERE t.conversation_type IN ('generation', 'modification')
          AND t.conversation_id NOT IN (
              SELECT conversation_id FROM INT_CONVERSATION_OUTCOMES_RAW
              WHERE prompt_version = %s
                AND error IS NULL
          )
        ORDER BY t.conversation_id
    """
    cursor = conn.cursor()
    cursor.execute(query, (prompt_version,))
    rows = cursor.fetchall()
    return [row[0] for row in rows]

# ── Fetch conversation thread from Snowflake ──────────────────────────────────
def fetch_conversation(conn, conversation_id: int) -> str:
    query = """
        SELECT sequence_number, message_type, content
        FROM STG_CONVERSATION_MESSAGES
        WHERE conversation_id = %s
        ORDER BY sequence_number
    """
    cursor = conn.cursor()
    cursor.execute(query, (conversation_id,))
    rows = cursor.fetchall()

    TRUNCATION_LIMITS = {
        "tool_result": 2000,
        "thinking": None,   # never truncate — final response lives here
        "tool_call": 500,   # just need to know what tool was called
        "user": 4000,       # bumped from 2000 — SQL pastes in user messages are large
    }

    lines = []
    for _, msg_type, content in rows:
        limit = TRUNCATION_LIMITS.get(msg_type)
        if limit and content and len(content) > limit:
            content = content[:limit] + "... [truncated]"
        lines.append(f"[{msg_type}] {content or ''}")

    return "\n\n".join(lines)

# ── Prompt 1 ──────────────────────────────────────────────────────────────────
PROMPT_1 = """
You are evaluating conversations between a user and an AI assistant called querybot (also referred to by various names in conversation content — treat all references to the assistant as querybot regardless of what name the user uses). Querybot is a SQL assistant with access to a proprietary data warehouse schema, a memory system of past learnings, Confluence documentation, and Jira. It operates in a dev environment with no access to production data.

Your job is to evaluate SQL OUTPUT conversations — conversations where querybot wrote one or more SQL files. These are either generation conversations (writing SQL from scratch based on a user request) or modification conversations (editing or extending existing SQL the user provided). Success is measured by whether the SQL querybot produced was correct, complete, and usable.

---

## EVALUATION DIMENSIONS

Score each dimension 1-3:

**1. Question Understanding**
Did querybot correctly interpret what the user was asking for?
1 = Fundamental misunderstanding — wrote SQL for the wrong thing entirely, or pursued a clearly wrong approach without self-correcting
2 = Partially understood, made wrong assumptions that required significant user correction, or initially went in the wrong direction before recovering
3 = Correctly understood the request from the start, or self-corrected quickly when the user redirected

**2. Resource Exhaustion**
Did querybot use available tools appropriately before writing and finalizing SQL?
1 = Jumped to writing SQL without researching schema, memory, or documentation; missed obvious avenues
2 = Used some tools but skipped meaningful sources, or researched after the fact rather than before writing
3 = Read the SQL skill, searched memory and schema, looked up relevant tables, and used code review before delivering the final result

**3. Answer Grounding**
Is the SQL logically correct based on what querybot learned from schema, memory, and documentation?
1 = SQL references wrong tables, wrong join keys, wrong column names, or has obvious logical errors that querybot didn't catch
2 = SQL is mostly plausible but has gaps — edge cases not handled, join risks not addressed, or code review issues dismissed without good reason
3 = SQL is logically sound, uses verified table names and columns, handles join fanout and filter correctness, and addresses code review findings appropriately

**4. Actionability**
Did the conversation end with something the user could actually use?
1 = No usable SQL delivered — conversation ended without a working file, or the final SQL was clearly broken
2 = SQL was delivered but the user expressed doubt, found issues that weren't resolved, or the conversation ended before the user confirmed it worked
3 = SQL was delivered and accepted by the user, or the conversation ended with clear evidence the query ran correctly and the user got what they needed

---

## BOOLEAN FLAG

**flag_dev_acknowledged** — did querybot explicitly note that results may differ in production, or that dev data is sparse and the output should be validated before relying on it? Set to true only when querybot proactively calls this out, not when the user raises it.

---

## OUTCOME CLASSIFICATION

Based on the four dimensions, assign one of the following outcome labels:

**success_clean** — querybot understood the request, used appropriate tools, produced correct SQL, and the user got a working result. flag_dev_acknowledged may be TRUE on a success_clean outcome — proactively noting dev limitations is good behavior, not a failure signal. Only use failure_environment if the SQL could not be executed or validated due to an environment blocker. Code review iteration that fixes real issues is still success_clean as long as the arc was efficient. Scores mostly 3s.

**success_iterative** — querybot ultimately delivered working SQL, but required meaningful back-and-forth to get there. This includes: user corrections that changed the approach, misunderstandings that were resolved, multiple revision cycles driven by user feedback, or conversations where querybot initially misread the ask but recovered. The end state is a success but the path was inefficient. Scores mix of 2s and 3s.

**failure_wrong_direction** — querybot misunderstood the requirement and didn't self-correct, or went down a fundamentally wrong path that the user couldn't redirect. The user did not get working SQL.

**failure_environment** — querybot built SQL that appears logically correct, but couldn't execute or validate it due to blockers outside its control: role not selected, IF block executor restriction, no DB access configured, cross-database permission errors. The SQL artifact itself may be sound but the outcome is unvalidated. Distinct from failure_wrong_direction — the SQL is plausible; the environment blocked it. flag_dev_acknowledged is independent of this outcome — it may be TRUE or FALSE regardless of whether an environment blocker occurred.

**failure_schema_gap** — querybot searched thoroughly and in good faith but the schema, memory, and documentation did not contain enough information to answer the question. Querybot correctly identified the gap. Not querybot's fault — the data simply doesn't exist in the warehouse.

**failure_abandoned** — the conversation ended before querybot could complete the SQL — user stopped responding, connection dropped, or session ended prematurely. Distinct from failure_environment — the cause is session termination, not an environment permission blocker.

**inconclusive** — the conversation is too short, too ambiguous, or clearly a test/diagnostic session to evaluate meaningfully. Also use for conversations where querybot was asked to review or explain SQL (not generate or modify it) and no SQL file was written.

---

## FEW-SHOT EXAMPLES

### Example 1 — success_clean (conv 553)
User asked how much video cameras increase account survival. Querybot read the DERL skill and SQL best practices, searched memory for camera device type IDs, prefetched schema, identified the correct device type (ID_COMBINED_DEVICE_TYPE_CURR = 11) and camera table join path, and wrote a temp-table survival dataset query. Code review flagged a many-to-many fan-out on lu_customer — querybot correctly fixed it by deduplicating before the join. Code review also flagged a non-sargable DATEDIFF predicate — querybot noted this was unavoidable for tenure math and proceeded with a clear explanation. The Python DERL model executed successfully and returned quantified survival results with appropriate caveats about dev data sparsity.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
flag_dev_acknowledged: false
Outcome: success_clean

### Example 2 — success_iterative (conv 725)
User provided a Jira ticket requesting a tariff invoice charges report. Querybot identified 5 definitional ambiguities (invoice date meaning, holding OB join path, dealer key, MAC format, currency) and asked the user to clarify before writing. After the user answered, querybot wrote a multi-step temp table query. Code review caught a real many-to-many risk in the modem install join — querybot restructured with ROW_NUMBER() deduplication. Additional review passes led to replacing DISTINCT with ROW_NUMBER() on invoice headers and adding missing covering indexes. When the user clarified that the operating business filter should be a display column (not a WHERE predicate), querybot self-corrected immediately and updated the join accordingly.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
flag_dev_acknowledged: false
Outcome: success_iterative

### Example 3 — success_clean with flag_dev_acknowledged (conv 628)
User asked for average access control sensors per building for applicable accounts. Querybot asked one clarifying question to scope "applicable" correctly, then read the SQL skill, searched memory (finding device type 73 = Card Reader and 131 = Innovation Credential Reader), prefetched schema, and wrote a two-step temp table query. Code review flagged a redundant index — querybot removed it. During inline execution testing, querybot caught that FLAG_ACTIVE_CUSTOMER is a 'Y'/'N' char column (not 1/0) and self-corrected before the user hit it. The query ran and returned 7,300 qualifying buildings at avg 110.8 sensors. Querybot explicitly noted that the dev environment has sparse data and the user should validate against production before relying on the distribution numbers.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
flag_dev_acknowledged: true
Outcome: success_clean

### Example 4 — failure_environment (conv 692)
User requested a generation-type SQL query. Querybot researched schema and memory appropriately, wrote the SQL file, and attempted execution. Every execute_sql call returned a role permission error ("No role selected. Please select a role from the menu. Available: SQL security group"). Querybot attempted multiple approaches — inline queries, file execution, different query scopes — but the role error blocked all execution paths. After 17 consecutive permission failures across 73 turns, querybot handed off the unvalidated SQL file and explained the environment blocker. The SQL itself appears logically constructed but could not be confirmed to run correctly.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 2, Actionability 1
flag_dev_acknowledged: false
Outcome: failure_environment

### Example 5 — success_iterative (conv 618)
User pasted an existing SGT appointment query and asked for a review and corrected version. Querybot identified multiple real bugs: DT_CREATED is an ETL audit timestamp (not a booking timestamp), the C_ADDRESS_CURR join was missing the ID_CUST composite key condition causing fan-out, and verification tables weren't being deduplicated. Querybot rewrote the query and iterated through three code review passes addressing the filter-early pattern and EXISTS vs IN rewrites. When the user ran it and hit a scalar variable scoping error (@now not visible across batches), querybot diagnosed the batch-scoping issue correctly and inlined SYSUTCDATETIME() directly. The user then provided a second stored procedure to rewrite entirely — querybot executed a full DW rewrite with schema research and delivered a clean temp-table version.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
flag_dev_acknowledged: false
Outcome: success_iterative

### Example 6 — success_clean (conv 708)
User had an existing modem query and asked what to join to get shipping address. Querybot looked up REL_MODEM_ORDER_TO_INSTALL documentation, confirmed DT_SHIPPED already existed on the joined table, then identified lu_dealer_order_raw as the shipping address source via the order_id key. Querybot looked up dealer order type IDs, identified type 10 = "Direct Customer Fulfillment" as the customer address case to exclude, and correctly placed the exclusion in the JOIN condition (not WHERE) to avoid accidentally filtering rows with no order. User then provided a different base query and asked for the same additions — querybot applied them cleanly.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
flag_dev_acknowledged: false
Outcome: success_clean

---

## IMPORTANT NOTES

- The final user-facing response is the last `thinking` message in the conversation. Earlier `thinking` messages may be internal reasoning. Evaluate the full arc but weight the final response heavily for actionability scoring.
- Querybot operates in a dev environment. Sparse query results, 0-row returns, and empty tables are expected and do not automatically indicate a SQL error. Only score Answer Grounding low if there is evidence of a logical problem — wrong tables, wrong join keys, wrong filter logic — not just low row counts.
- Code review scores of 4/10 "MAJOR_ISSUES" are the baseline in this system. The reviewer fires on infrastructure concerns (no SHOWPLAN, potential base-table scans) that querybot cannot control. Evaluate whether querybot correctly addressed actionable findings and correctly dismissed noise, not whether the score went up.
- A modification conversation that successfully applies a user's requested change is success_clean even if the original SQL had problems — evaluate the final state of the work, not the starting state.
- failure_environment is for environment blockers (role not selected, IF block restriction, no DB access). Don't use it for conversations where the SQL itself was wrong.
- flag_dev_acknowledged = true only when querybot proactively notes prod/dev differences before the user asks. Don't set it true because querybot mentioned dev environment in passing.

---

## OUTPUT FORMAT

Respond only with valid JSON. No preamble, no explanation outside the JSON.

{
  "question_understanding": <1|2|3>,
  "resource_exhaustion": <1|2|3>,
  "answer_grounding": <1|2|3>,
  "actionability": <1|2|3>,
  "flag_dev_acknowledged": <true|false>,
  "outcome": "<success_clean|success_iterative|failure_wrong_direction|failure_environment|failure_schema_gap|failure_abandoned|inconclusive>",
  "reasoning": "<2-3 sentences explaining the outcome label>"
}

---

## CONVERSATION TO EVALUATE

{conversation_content}
"""

# Static rubric prefix (everything before the variable conversation content).
# Identical on every call, so it is sent as a cacheable prompt block.
PROMPT_1_PREFIX = PROMPT_1.split("{conversation_content}")[0]

# Scoring fingerprint: any change to MODEL or PROMPT_1 rotates the version.
PROMPT_HASH = hashlib.sha256(f"{MODEL}\n{PROMPT_1}".encode()).hexdigest()[:8]
PROMPT_VERSION = f"sql_output_{PROMPT_HASH}"

# ── Truncate long conversations while preserving key context ──────────────────
def truncate_conversation(content: str, max_chars: int = 30000) -> str:
    if len(content) <= max_chars:
        return content
    keep_start = 20000
    keep_end = 10000
    middle_msg = f"\n\n... [{len(content) - keep_start - keep_end} chars truncated] ...\n\n"
    return content[:keep_start] + middle_msg + content[-keep_end:]

# # PATCH VERSION
# def truncate_conversation(content: str, max_chars: int = 20000) -> str:
#     if len(content) <= max_chars:
#         return content
#     keep_start = int(max_chars * 0.67)
#     keep_end = max_chars - keep_start
#     middle_msg = f"\n\n... [{len(content) - keep_start - keep_end} chars truncated] ...\n\n"
#     return content[:keep_start] + middle_msg + content[-keep_end:]


# ── Wrapper to handle rate limits with exponential backoff ────────────────────
def classify_with_retry(conversation_content: str, conversation_type: str, max_retries: int = 3) -> dict:
    for attempt in range(max_retries):
        try:
            return classify_conversation(conversation_content)
        except anthropic.RateLimitError:
            wait = 60 * (attempt + 1)
            CLASSIFIER_RETRIES_TOTAL.labels(conversation_type=conversation_type).inc()
            print(f"  Rate limited — waiting {wait}s before retry...")
            time.sleep(wait)
    raise Exception(f"Failed after {max_retries} retries")

# ── Claude API call ───────────────────────────────────────────────────────────
def classify_conversation(conversation_content: str) -> dict:
    client = anthropic.Anthropic()

    # Split into a cached static block (rubric + few-shot examples) and the
    # variable conversation block. The cached prefix bills at ~10% of normal
    # input tokens after the first hit.
    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": PROMPT_1_PREFIX,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": conversation_content},
            ],
        }],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"```json|```", "", raw).strip()
    result = json.loads(raw)

    if not result.get("outcome"):
        raise ValueError(f"Missing outcome field in response: {raw}")

    return result

# ── Write results to Snowflake ────────────────────────────────────────────────
def write_results(conn, results: list[dict]):
    cursor = conn.cursor()

    # Table already exists from consultation run.
    # SQL output classifier uses same four rubric columns as consultation:
    # question_understanding, resource_exhaustion, answer_grounding, actionability
    # Plus flag_dev_acknowledged (boolean, SQL output only — NULL for consultation rows).
    cursor.execute("""
        ALTER TABLE INT_CONVERSATION_OUTCOMES_RAW
        ADD COLUMN IF NOT EXISTS flag_dev_acknowledged BOOLEAN
    """)
    cursor.execute("""
        ALTER TABLE INT_CONVERSATION_OUTCOMES_RAW
        ADD COLUMN IF NOT EXISTS prompt_version VARCHAR
    """)
    cursor.execute("""
        ALTER TABLE INT_CONVERSATION_OUTCOMES_RAW
        ADD COLUMN IF NOT EXISTS model VARCHAR
    """)
    cursor.execute("""
        ALTER TABLE INT_CONVERSATION_OUTCOMES_RAW
        ADD COLUMN IF NOT EXISTS prompt_hash VARCHAR
    """)

    insert_sql = """
        INSERT INTO INT_CONVERSATION_OUTCOMES_RAW (
            conversation_id, conversation_type, outcome,
            question_understanding, resource_exhaustion,
            answer_grounding, actionability,
            flag_dev_acknowledged,
            reasoning, char_count, error,
            prompt_version, model, prompt_hash, classified_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    rows = []
    for r in results:
        rows.append((
            r["conversation_id"],
            r.get("conversation_type", "unknown"),
            r.get("outcome"),
            r.get("question_understanding"),
            r.get("resource_exhaustion"),
            r.get("answer_grounding"),
            r.get("actionability"),
            r.get("flag_dev_acknowledged"),
            r.get("reasoning"),
            r.get("char_count"),
            r.get("error"),
            PROMPT_VERSION,
            MODEL,
            PROMPT_HASH,
            datetime.now(timezone.utc)
        ))

    cursor.executemany(insert_sql, rows)
    conn.commit()
    print(f"Wrote {len(rows)} rows to INT_CONVERSATION_OUTCOMES_RAW")

# ── Full run ──────────────────────────────────────────────────────────────────

def run_full_classification(backup_path: str = "sql_output_outcomes.json"):
    start_metrics_server()
    conn = get_snowflake_connection()
    results = []

    # # PATCH RUN — hardcoded retry list
    # id_type_pairs = [(19, 'modification'), (210, 'modification'),
    #                  (609, 'generation'), (633, 'modification')]

    # Normal run — fetch from Snowflake
    ensure_outcomes_table(conn)
    query = """
        SELECT t.conversation_id, t.conversation_type
        FROM INT_CONVERSATION_TYPE t
        WHERE t.conversation_type IN ('generation', 'modification')
          AND t.conversation_id NOT IN (
              SELECT conversation_id FROM INT_CONVERSATION_OUTCOMES_RAW
              WHERE prompt_version = %s
                AND error IS NULL
          )
        ORDER BY t.conversation_id
    """
    cursor = conn.cursor()
    cursor.execute(query, (PROMPT_VERSION,))
    id_type_pairs = cursor.fetchall()

    total = len(id_type_pairs)
    print(f"Found {total} SQL output conversations to classify "
          f"(prompt_version={PROMPT_VERSION})\n")

    if total == 0:
        print("Nothing new to classify — all SQL output conversations are up to date.")
        conn.close()
        return results

    print("Fetching conversation content...")
    conversations = {}
    conv_types = {}
    for conv_id, conv_type in id_type_pairs:
        conversations[conv_id] = fetch_conversation(conn, conv_id)
        conv_types[conv_id] = conv_type

    # Parallelize only the Claude API calls
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                instrumented_classify,
                truncate_conversation(conversations[conv_id]),
                conv_types[conv_id],
                classify_with_retry
            ): conv_id
            for conv_id, _ in id_type_pairs
        }
        for future in as_completed(futures):
            conv_id = futures[future]
            try:
                classification = future.result()
                char_count = len(conversations[conv_id])
                result = {
                    "conversation_id": conv_id,
                    "conversation_type": conv_types[conv_id],
                    "char_count": char_count,
                    **classification
                }                
                print(f"Done: {conv_id} ({conv_types[conv_id]}) → {result['outcome']}")
                results.append(result)
            except Exception as e:
                print(f"Error: {conv_id} → {e}")
                results.append({
                    "conversation_id": conv_id,
                    "conversation_type": conv_types[conv_id],
                    "char_count": len(conversations[conv_id]),
                    "outcome": None,
                    "error": str(e)
                })

    Path(backup_path).write_text(json.dumps(results, indent=2))
    print(f"\nLocal backup written to {backup_path}")

    write_results(conn, results)
    conn.close()

    # Summary breakdown by type + outcome
    print(f"\n── Results ──────────────────────────────")
    for conv_type in ("generation", "modification"):
        type_results = [r for r in results if r.get("conversation_type") == conv_type]
        outcome_counts = Counter(r.get("outcome") for r in type_results)
        errors = sum(1 for r in type_results if r.get("error"))
        print(f"\n  {conv_type.upper()} ({len(type_results)} conversations)")
        for outcome, count in outcome_counts.most_common():
            print(f"    {outcome or 'error':<35} {count}")
        if errors:
            print(f"    {'errors':<35} {errors}")

    return results

if __name__ == "__main__":
    run_full_classification()

# # PATCH JSON output
# if __name__ == "__main__":
#     run_full_classification(backup_path="sql_output_outcomes_patch.json")