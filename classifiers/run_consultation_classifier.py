
import anthropic
import json
import os
import re
import snowflake.connector
from pathlib import Path
from datetime import datetime, timezone
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

from dotenv import load_dotenv
load_dotenv()

# ── Snowflake connection ──────────────────────────────────────────────────────
def get_snowflake_connection():
    return snowflake.connector.connect(
        account="ZADUWZC-QRC41354",
        user="BUBCHAMPAGNE",
        password=os.environ.get("SNOWFLAKE_PASSWORD"),
        passcode=input("Enter Snowflake MFA code: "),
        warehouse="DEV_WH",
        database="RAG_EVAL",
        schema="STAGING"
    )

# ── Fetch consultation conversation IDs ──────────────────────────────────────
def fetch_consultation_ids(conn) -> list[int]:
    query = """
        SELECT t.conversation_id
        FROM INT_CONVERSATION_TYPE t
        WHERE t.conversation_type = 'consultation'
        ORDER BY t.conversation_id
    """
    cursor = conn.cursor()
    cursor.execute(query)
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
    "user": 2000,       # full user message usually matters
}

    lines = []
    for _, msg_type, content in rows:
        limit = TRUNCATION_LIMITS.get(msg_type)
        if limit and content and len(content) > limit:
            content = content[:limit] + "... [truncated]"
        lines.append(f"[{msg_type}] {content or ''}")

    return "\n\n".join(lines)

# ── Prompt 2 ──────────────────────────────────────────────────────────────────
PROMPT_2 = """
You are evaluating conversations between a user and an AI assistant called querybot (also referred to as Turkle or Turkle Bot in some conversations and internal documentation). Querybot is a SQL assistant with access to a proprietary data warehouse schema, a memory system of past learnings, Confluence documentation, and Jira. It operates in a dev environment with no access to production data.

Your job is to evaluate CONSULTATION conversations — conversations where querybot did not write a SQL file. The user's goal in these conversations is to get a correct, actionable answer to a question about the schema, data, business logic, or querybot's own capabilities. Success is not measured by whether SQL was produced — it is measured by whether the user got what they needed to move forward.

---

## EVALUATION DIMENSIONS

Score each dimension 1-3:

**1. Question Understanding**
Did querybot correctly interpret what the user was asking?
1 = Misunderstood the question or went in a clearly wrong direction
2 = Partially understood, required significant redirection
3 = Correctly understood the question from the start, or self-corrected quickly when redirected

**2. Resource Exhaustion**
Did querybot use the available tools appropriately before concluding?
1 = Gave up too early, missed obvious sources to check
2 = Used some tools but missed meaningful avenues
3 = Thoroughly searched schema, memory, Confluence, and/or executed validation queries before concluding

**3. Answer Grounding**
Was the conclusion supported by evidence from actual query results, schema documentation, or Confluence?
1 = Conclusion was vague, speculative, or unsupported
2 = Partially grounded, some evidence but gaps or contradictions
3 = Conclusion was clearly grounded in evidence — cited actual table names, field values, query results, or documentation

**4. Actionability**
Could the user act on querybot's response?
1 = User left with nothing — no answer, no direction, no next step
2 = User got partial information but would need significant follow-up to move forward
3 = User got a complete, actionable response — either a correct answer, a confirmed dead end with explanation, or a clear next step

---

## OUTCOME CLASSIFICATION

Based on the four dimensions, assign one of the following outcome labels:

**success_clean** — querybot correctly understood the question, used appropriate tools, grounded its answer in evidence, and gave the user something actionable. Scores mostly 3s. Includes cases where the honest answer is "this data doesn't exist" — a well-researched definitive dead end is a success.

**success_with_correction** — querybot initially went in a wrong direction but self-corrected when redirected by the user, and ultimately delivered an actionable response. At least one dimension scored 2 but the final answer was solid.

**failure_knowledge_gap** — querybot searched thoroughly and in good faith but the schema, documentation, and memory system did not contain enough information to answer the question. Querybot correctly identified the gap. Not querybot's fault — the knowledge simply wasn't available.

**failure_wrong_direction** — querybot misunderstood the question or pursued a clearly wrong approach without self-correcting, and the user did not get what they needed.

**failure_abandoned** — the conversation ended before querybot could complete its work — either the user stopped responding, querybot hit infrastructure failures it couldn't recover from, or the session ended prematurely. Distinct from knowledge gap — the failure was process not content.

**inconclusive** — the conversation is too short, too ambiguous, or too clearly a test/diagnostic session to evaluate meaningfully. Use for conversations like "test test", "can you give me a quick test of your functionality", or single-message exchanges with no substantive response.

---

## FEW-SHOT EXAMPLES

### Example 1 — success_with_correction
User asked for a comprehensive analysis of Smart Arming's impact on customer engagement and retention, including survival analysis, pre/post engagement comparison, and customer profiling. Querybot built a full research and analysis plan, searched schema and Confluence, identified the correct tables, wrote multiple SQL files and Python scripts. When the user pointed out that Smart Arming is an automation not a notification, querybot immediately searched Confluence, confirmed the correction, and updated all deliverables accordingly.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
Outcome: success_with_correction (initial misclassification corrected cleanly)

### Example 2 — success_clean (definitive dead end)
User asked to audit which customers were created manually vs via API/webservices. Querybot searched every plausible field across lu_customer, S_CUSTOMER_SOURCE, lu_system_access_source, lu_dealer_customer_source, and Confluence. Confluence confirmed there is no single universal flag for manual vs API creation. Querybot explained this clearly and described what the candidate fields actually represent. The user got a definitive, well-researched answer — the data doesn't exist in this form — which is exactly what they needed before building on a false assumption.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
Outcome: success_clean

### Example 3 — inconclusive
User sent "test test" with no follow-up. No substantive exchange occurred.

Scores: N/A
Outcome: inconclusive

### Example 4 — failure_abandoned
User asked querybot to find tables related to Mobile Surveillance Trailers. Querybot searched schema, memory, and documentation but hit a SQL role permission error that blocked live queries. No matches were found in documentation. Querybot correctly reported the blocker but the user had no path forward — querybot couldn't execute the queries needed to complete the search and the session ended.

Scores: Question Understanding 3, Resource Exhaustion 2, Answer Grounding 2, Actionability 1
Outcome: failure_abandoned

### Example 5 — success_clean (short conversation)
User pasted a complex multi-CTE query and asked what three specific sections were doing. Querybot gave a thorough, accurate breakdown of each section with clear explanations of the currency normalization logic, filtering, and territory renaming. Two turns, definitively answered.

Scores: Question Understanding 3, Resource Exhaustion 3, Answer Grounding 3, Actionability 3
Outcome: success_clean

---

## IMPORTANT NOTES

- The final user-facing response is the last `thinking` message in the conversation. Earlier `thinking` messages may be internal reasoning. Evaluate the full arc of the conversation but weight the final response heavily for actionability scoring.
- Querybot operates in a dev environment. Sparse or missing data in query results is expected and does not indicate a failure — querybot should note this and proceed accordingly.
- A short conversation is not automatically inconclusive. If the question was answered correctly and completely in 2 turns, score it as success_clean.
- Infrastructure failures (SQL role not selected, Python executor unreachable, Confluence credentials not configured) that block querybot from completing its work should be scored as failure_abandoned, not failure_knowledge_gap.

---

## OUTPUT FORMAT

Respond only with valid JSON. No preamble, no explanation outside the JSON.

{
  "question_understanding": <1|2|3>,
  "resource_exhaustion": <1|2|3>,
  "answer_grounding": <1|2|3>,
  "actionability": <1|2|3>,
  "outcome": "<success_clean|success_with_correction|failure_knowledge_gap|failure_wrong_direction|failure_abandoned|inconclusive>",
  "reasoning": "<2-3 sentences explaining the outcome label>"
}

---

## CONVERSATION TO EVALUATE

{conversation_content}
"""

# Truncate long conversations while preserving key context
def truncate_conversation(content: str, max_chars: int = 30000) -> str:
    if len(content) <= max_chars:
        return content
    keep_start = 20000
    keep_end = 10000
    middle_msg = f"\n\n... [{len(content) - keep_start - keep_end} chars truncated] ...\n\n"
    return content[:keep_start] + middle_msg + content[-keep_end:]

# Wrapper to handle rate limits with exponential backoff
def classify_with_retry(conversation_content: str, max_retries: int = 3) -> dict:
    for attempt in range(max_retries):
        try:
            return classify_conversation(conversation_content)
        except anthropic.RateLimitError:
            wait = 60 * (attempt + 1)
            print(f"  Rate limited — waiting {wait}s before retry...")
            time.sleep(wait)
    raise Exception(f"Failed after {max_retries} retries")

# ── Claude API call ───────────────────────────────────────────────────────────
def classify_conversation(conversation_content: str) -> dict:
    client = anthropic.Anthropic()
    prompt = PROMPT_2.replace("{conversation_content}", conversation_content)
    
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS INT_CONVERSATION_OUTCOMES_RAW (
            conversation_id         INTEGER,
            conversation_type       VARCHAR,
            outcome                 VARCHAR,
            question_understanding  INTEGER,
            resource_exhaustion     INTEGER,
            answer_grounding        INTEGER,
            actionability           INTEGER,
            reasoning               VARCHAR,
            char_count              INTEGER,
            error                   VARCHAR,
            classified_at           TIMESTAMP_TZ
        )
    """)

    insert_sql = """
        INSERT INTO INT_CONVERSATION_OUTCOMES_RAW (
            conversation_id, conversation_type, outcome,
            question_understanding, resource_exhaustion,
            answer_grounding, actionability, reasoning,
            char_count, error, classified_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    rows = []
    for r in results:
        rows.append((
            r["conversation_id"],
            "consultation",
            r.get("outcome"),
            r.get("question_understanding"),
            r.get("resource_exhaustion"),
            r.get("answer_grounding"),
            r.get("actionability"),
            r.get("reasoning"),
            r.get("char_count"),
            r.get("error"),
            datetime.now(timezone.utc)
        ))

    cursor.executemany(insert_sql, rows)
    conn.commit()
    print(f"Wrote {len(rows)} rows to INT_CONVERSATION_OUTCOMES_RAW")

# ── Full run ──────────────────────────────────────────────────────────────────
def run_full_classification(backup_path: str = "consultation_outcomes.json"):
    conn = get_snowflake_connection()
    results = []

    conversation_ids = fetch_consultation_ids(conn)
    total = len(conversation_ids)
    print(f"Found {total} consultation conversations to classify\n")

    # fetch all content upfront, single connection, no thread safety issues
    print("Fetching conversation content...")
    conversations = {}
    for conv_id in conversation_ids:
        conversations[conv_id] = fetch_conversation(conn, conv_id)

    # parallelize only the Claude API calls
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                classify_with_retry,
                truncate_conversation(conversations[conv_id])
            ): conv_id
            for conv_id in conversation_ids
        }
        for future in as_completed(futures):
            conv_id = futures[future]
            try:
                classification = future.result()
                char_count = len(conversations[conv_id])
                result = {"conversation_id": conv_id, "char_count": char_count, **classification}
                print(f"Done: {conv_id} → {result['outcome']}")
                results.append(result)
            except Exception as e:
                print(f"Error: {conv_id} → {e}")
                results.append({
                    "conversation_id": conv_id,
                    "char_count": len(conversations[conv_id]),
                    "outcome": None,
                    "error": str(e)
                })

    Path(backup_path).write_text(json.dumps(results, indent=2))
    print(f"\nLocal backup written to {backup_path}")

    write_results(conn, results)
    conn.close()

    outcome_counts = Counter(r.get("outcome") for r in results)
    errors = sum(1 for r in results if r.get("error"))
    print(f"\n── Results ──────────────────────────────")
    for outcome, count in outcome_counts.most_common():
        print(f"  {outcome or 'error':<30} {count}")
    print(f"  {'errors':<30} {errors}")

    return results

if __name__ == "__main__":
    run_full_classification()