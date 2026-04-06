# Query Bot Research: Failure Mode Reconnaissance

**Goal:** Identify failure modes, retrieval gaps, and edge cases to inform RAG Eval pipeline design.
**Sources available:** Confluence, Jira, Bitbucket/GitHub, Alation (data catalog)
**Environment:** Dev only

---

## 1. Retrieval Quality — Single Source

*Does it find the right thing when the answer clearly exists in one place?*

- "What does the `[table you know well]` table contain and who owns it?"
- "What are the documented data quality issues for the revenue pipeline?"
- "Where is the onboarding documentation for new data team members?"
- "What's the definition of 'active user' in our data catalog?"
- "Are there any open Jira tickets related to the ETL pipeline?"

**What to watch:** Does it surface the right document? Does it cite its source? Does it confidently answer something it shouldn't know?

---

## 2. Multi-Source / Cross-System Queries

*Where RAG systems most commonly fall apart — requires joining context across retrieval sources.*

- "What tables are used in the customer churn pipeline and are there any open tickets related to them?"
- "Who owns the `[dataset]` table and have there been any recent PRs touching it?"
- "Is there documentation in Confluence that matches what's defined in Alation for `[domain]`?"
- "What's the history of changes to the revenue model — both code changes and ticket history?"
- "Are there any known discrepancies between what Confluence says about `[process]` and what's actually in the catalog?"

**What to watch:** Does it attempt to synthesize across sources or just answer from one? Does it acknowledge when it can only partially answer?

---

## 3. Faithfulness Probes

*You know the answer — does it stay grounded or make things up?*

- Ask about a table schema you know well and verify accuracy field by field
- Ask about a Jira ticket you worked on recently
- Ask about a pipeline you know the owner of
- Ask something that changed recently — does it have stale info?
- Ask about a table that definitely does NOT exist and see if it hallucinates one

**What to watch:** Does it fabricate plausible-sounding but wrong details? Does it caveat appropriately when uncertain?

---

## 4. Confidence Calibration

*Is its uncertainty signal trustworthy?*

- After any answer, ask: "How confident are you in that and why?"
- Ask the same question three different ways — are answers consistent?
- Ask something slightly outside its scope and see if it knows its limits
- Ask a question with a wrong premise baked in: "Since the orders table is updated weekly..." (if it's actually daily) — does it correct you or go with it?

**What to watch:** Uniform high confidence regardless of answer quality is a red flag. Good systems hedge when retrieval was weak.

---

## 5. Self-Reported Limitations

*Ask it directly — surprisingly useful.*

- "What kinds of questions are you least confident answering?"
- "What types of queries do you think you handle poorly?"
- "When should I not trust your answers?"
- "What information do you wish you had better access to?"
- "What contributes to those struggles — is it limited source access, documentation gaps, or something else?"

**What to watch:** Tag each answer by category — retrieval source limitation, corpus quality, query complexity, model calibration. These become your eval test case categories.

---

## 6. Scope and Boundary Testing

*Does it know what it doesn't know?*

- Ask something completely outside its scope (a general coding question, something personal)
- Ask about prod environment data (it only has dev access)
- Ask about a system it has no connector to
- Ask a question so vague it could mean ten different things — does it ask for clarification or just pick one interpretation?

**What to watch:** Graceful degradation vs confident hallucination. The latter is your most important failure mode to capture.

---

## 7. Consistency and Regression

*Same question, different sessions or phrasing.*

- Ask the same factual question in three different phrasings
- Ask a question, get an answer, then ask a follow-up that contradicts it — does it hold its ground or capitulate?
- Ask "what did you just retrieve to answer that?" if it supports source transparency

**What to watch:** High variance across phrasings suggests fragile retrieval. Capitulation to contradictions suggests poor grounding.

---

## Capture Template

For each interesting finding, note:

```
Question asked:
Category: [retrieval quality / multi-source / faithfulness / calibration / scope / consistency]
What happened:
Suspected cause: [source limitation / corpus gap / multi-hop failure / model behavior]
Implication for eval dataset:
Retrieval count: [how many chunks came back]
Were the right chunks in the retrieved set at all?
```

---

## Output

Produce a `struggles.md` summarizing:
- Top 3-5 confirmed failure modes with examples
- Suspected root causes per failure mode
- Query types the bot handles well (useful as positive eval cases)
- Recommended test case categories for the RAG Eval pipeline