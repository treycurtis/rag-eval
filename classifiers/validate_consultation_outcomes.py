
import anthropic
import json
import os
import re
import snowflake.connector
from pathlib import Path


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
You are evaluating conversations between a user and an AI assistant called querybot (also referred to by various names in conversation content — treat all references to the assistant as querybot regardless of what name the user uses). Querybot is a SQL assistant with access to a proprietary data warehouse schema, a memory system of past learnings, Confluence documentation, and Jira. It operates in a dev environment with no access to production data.

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

# ── Validation cases ──────────────────────────────────────────────────────────
VALIDATION_CASES = [
    {"conversation_id": 643, "expected_outcome": "inconclusive"},
    {"conversation_id": 691, "expected_outcome": "success_clean"},
    {"conversation_id": 701, "expected_outcome": "failure_abandoned"},
    {"conversation_id": 530, "expected_outcome": "success_clean"},
    {"conversation_id": 225, "expected_outcome": "success_with_correction"},
]

BLIND_TEST_CASES = [
    {"conversation_id": 724, "expected_outcome": "success_clean"},
]

# ── Validation runner ─────────────────────────────────────────────────────────
def run_validation(output_path: str = "validation_results.json"):
    conn = get_snowflake_connection()
    results = []
    all_cases = VALIDATION_CASES + BLIND_TEST_CASES

    for case in all_cases:
        conv_id = case["conversation_id"]
        expected = case["expected_outcome"]
        is_blind = case in BLIND_TEST_CASES

        print(f"Classifying conversation {conv_id} {'[BLIND]' if is_blind else ''}...")

        try:
            conversation_content = fetch_conversation(conn, conv_id)
            print(f"  Conv {conv_id}: {len(conversation_content):,} chars")

            classification = classify_conversation(conversation_content)

            actual = classification.get("outcome")
            passed = actual == expected

            result = {
                "conversation_id": conv_id,
                "expected_outcome": expected,
                "actual_outcome": actual,
                "passed": passed,
                "is_blind_test": is_blind,
                **classification
            }

            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"  {status} — expected: {expected}, got: {actual}")
            print(f"  Reasoning: {classification.get('reasoning')}")
            print()

        except Exception as e:
            print(f"  ⚠️  Error: {e}")
            result = {
                "conversation_id": conv_id,
                "expected_outcome": expected,
                "actual_outcome": None,
                "passed": False,
                "is_blind_test": is_blind,
                "error": str(e)
            }

        results.append(result)

    conn.close()

    output = Path(output_path)
    output.write_text(json.dumps(results, indent=2))
    print(f"Results written to {output_path}")

    passed_count = sum(1 for r in results if r["passed"] and not r["is_blind_test"])
    total = len(VALIDATION_CASES)
    print(f"\nValidation: {passed_count}/{total} passed")

    for b in [r for r in results if r["is_blind_test"]]:
        status = "✅" if b["passed"] else "❌"
        print(f"Blind test {b['conversation_id']}: {status} ({b['actual_outcome']})")

    return results

if __name__ == "__main__":
    run_validation()