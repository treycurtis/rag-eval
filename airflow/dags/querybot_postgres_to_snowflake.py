# ~/projects/rag-eval/airflow/dags/querybot_postgres_to_snowflake.py

from datetime import datetime, timedelta, UTC
from pathlib import Path

import psycopg2
import snowflake.connector

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator

import logging
logger = logging.getLogger(__name__)

DEFAULT_ARGS = {
    "owner": "trey",
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

SNOWFLAKE_CONN = dict(
    account="ZADUWZC-QRC41354",
    user="BUBCHAMPAGNE",
    private_key_file=str(Path.home() / ".snowflake" / "rsa_key.pem"),
    database="RAG_EVAL",
    schema="QUERYBOT",
    warehouse="DEV_WH",
    role="ACCOUNTADMIN",
)

POSTGRES_CONN = dict(
    host="172.28.42.77",
    port=5432,
    dbname="query_bot",
    user="tcurtis_turkle_reader",
)

TABLE_SCHEMAS = {
    "CONVERSATIONS": [
        "id", "user_id", "conversation_uuid", "sdk_session_id", "is_active",
        "message_count", "total_runs", "total_cost_usd", "last_message_at",
        "last_run_at", "learning_extraction_status", "summary", "created_at", "updated_at"
    ],
    "CONVERSATION_RUNS": [
        "id", "conversation_id", "user_message_id", "num_turns", "duration_ms",
        "cost_usd", "sdk_session_id", "started_at", "completed_at"
    ],
    "CONVERSATION_MESSAGES": [
        "id", "conversation_id", "message_type", "content", "message_metadata",
        "tool_use_id", "is_error", "created_at", "sequence_number"
    ],
}

def get_watermark() -> str:
    return Variable.get("querybot_watermark", default_var="2024-01-01T00:00:00+00:00")


def set_watermark(ts: str):
    Variable.set("querybot_watermark", ts)

def capture_watermark(**ctx):
    watermark = get_watermark()
    ctx["ti"].xcom_push(key="watermark", value=watermark)


# ── Helpers ─────────────────────────────────────────────────────────────────

def get_sf_connection():
    return snowflake.connector.connect(**SNOWFLAKE_CONN)


def get_pg_connection():
    return psycopg2.connect(**POSTGRES_CONN)


def ensure_schema(cs):
    cs.execute("CREATE SCHEMA IF NOT EXISTS RAG_EVAL.QUERYBOT")

def ensure_table(cs, table: str, cols: list):
    col_defs = ", ".join(f"{c.upper()} VARCHAR" for c in cols)
    cs.execute(f"""
        CREATE TABLE IF NOT EXISTS RAG_EVAL.QUERYBOT.{table} (
            {col_defs}
        )
    """)

def upsert_to_snowflake(cs, table: str, cols: list, rows: list):
    ensure_table(cs, table, cols) 
    if not rows:
        return

    col_names = ", ".join(c.upper() for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    update_set = ", ".join(
        f"{c.upper()} = src.{c.upper()}" for c in cols if c != "id"
    )

    merge_sql = f"""
        MERGE INTO RAG_EVAL.QUERYBOT.{table} tgt
        USING (
            SELECT {col_names}
            FROM VALUES ({placeholders}) AS v({col_names})
        ) src
        ON tgt.ID = src.ID
        WHEN MATCHED THEN UPDATE SET {update_set}
        WHEN NOT MATCHED THEN INSERT ({col_names})
            VALUES ({', '.join(f'src.{c.upper()}' for c in cols)})
    """

    chunk_size = 500
    for i in range(0, len(rows), chunk_size):
        chunk = rows[i:i + chunk_size]
        cs.executemany(
            merge_sql,
            [[str(v) if v is not None else None for v in row] for row in chunk]
        )


# ── Tasks ────────────────────────────────────────────────────────────────────

def extract_and_load_conversations(**ctx):
    watermark = ctx["ti"].xcom_pull(
        key="watermark",
        task_ids="capture_watermark"
    )
    new_watermark = None

    logger.info(f"Starting conversations extract with watermark: {watermark}")
    
    # ── Postgres fetch ───────────────────────────────────────────────────────
    pg = get_pg_connection()
    try:
        pg_cur = pg.cursor()
        
        t0 = datetime.now(UTC)
        pg_cur.execute("""
            SELECT
                id,
                user_id,
                conversation_uuid,
                sdk_session_id,
                is_active,
                message_count,
                total_runs,
                total_cost_usd,
                last_message_at,
                last_run_at,
                learning_extraction_status,
                summary,
                created_at,
                updated_at
            FROM conversations
            WHERE updated_at > %s
            ORDER BY updated_at
        """, (watermark,))
        cols = [d[0] for d in pg_cur.description]
        rows = pg_cur.fetchall()
        logger.info(f"Postgres fetch complete: {len(rows)} rows in {(datetime.now(UTC) - t0).total_seconds():.2f}s")

        if rows:
            updated_at_idx = cols.index("updated_at")
            new_watermark = str(max(r[updated_at_idx] for r in rows))

    finally:
        pg_cur.close()
        pg.close()

    # ── Snowflake load ───────────────────────────────────────────────────────
    sf = get_sf_connection()
    try:
        sf_cur = sf.cursor()
        ensure_schema(sf_cur)

        t1 = datetime.now(UTC)
        upsert_to_snowflake(sf_cur, "CONVERSATIONS", cols, [list(r) for r in rows])
        logger.info(f"Snowflake upsert complete in {(datetime.now(UTC) - t1).total_seconds():.2f}s")

        if new_watermark:
            set_watermark(new_watermark)
            logger.info(f"Watermark advanced to: {new_watermark}")

    finally:
        sf_cur.close()
        sf.close()


def extract_and_load_conversation_runs(**ctx):
    watermark = ctx["ti"].xcom_pull(
        key="watermark",
        task_ids="capture_watermark"
    )

    logger.info(f"Starting conversation_runs extract with watermark: {watermark}")

    # ── Postgres fetch ───────────────────────────────────────────────────────
    pg = get_pg_connection()
    try:
        pg_cur = pg.cursor()

        t0 = datetime.now(UTC)
        pg_cur.execute("""
            SELECT
                cr.id,
                cr.conversation_id,
                cr.user_message_id,
                cr.num_turns,
                cr.duration_ms,
                cr.cost_usd,
                cr.sdk_session_id,
                cr.started_at,
                cr.completed_at
            FROM conversation_runs cr
            JOIN conversations c ON cr.conversation_id = c.id
            WHERE c.updated_at > %s
        """, (watermark,))
        cols = [d[0] for d in pg_cur.description]
        rows = pg_cur.fetchall()
        logger.info(f"Postgres fetch complete: {len(rows)} rows in {(datetime.now(UTC) - t0).total_seconds():.2f}s")

    finally:
        pg_cur.close()
        pg.close()

    # ── Snowflake load ───────────────────────────────────────────────────────
    sf = get_sf_connection()
    try:
        sf_cur = sf.cursor()
        ensure_schema(sf_cur)

        t1 = datetime.now(UTC)
        upsert_to_snowflake(sf_cur, "CONVERSATION_RUNS", cols, [list(r) for r in rows])
        logger.info(f"Snowflake upsert complete in {(datetime.now(UTC) - t1).total_seconds():.2f}s")

    finally:
        sf_cur.close()
        sf.close()


def extract_and_load_conversation_messages(**ctx):
    watermark = ctx["ti"].xcom_pull(
        key="watermark",
        task_ids="capture_watermark"
    )

    logger.info(f"Starting conversation_messages extract with watermark: {watermark}")

    # ── Postgres fetch ───────────────────────────────────────────────────────
    pg = get_pg_connection()
    try:
        pg_cur = pg.cursor()

        t0 = datetime.now(UTC)
        pg_cur.execute("""
            SELECT
                cm.id,
                cm.conversation_id,
                cm.message_type,
                cm.content,
                cm.message_metadata,
                cm.tool_use_id,
                cm.is_error,
                cm.created_at,
                cm.sequence_number
            FROM conversation_messages cm
            JOIN conversations c ON cm.conversation_id = c.id
            WHERE c.updated_at > %s
              AND cm.message_type IN ('user','tool_call','tool_result','thinking')
            ORDER BY cm.conversation_id, cm.sequence_number
        """, (watermark,))
        cols = [d[0] for d in pg_cur.description]
        all_rows = [list(r) for r in pg_cur.fetchall()]
        logger.info(f"Postgres fetch complete: {len(all_rows)} rows in {(datetime.now(UTC) - t0).total_seconds():.2f}s")

    finally:
        pg_cur.close()
        pg.close()

    # ── Snowflake load ───────────────────────────────────────────────────────
    if cols and all_rows:
        sf = get_sf_connection()
        try:
            sf_cur = sf.cursor()
            ensure_schema(sf_cur)

            t1 = datetime.now(UTC)
            upsert_to_snowflake(sf_cur, "CONVERSATION_MESSAGES", cols, all_rows)
            logger.info(f"Snowflake upsert complete in {(datetime.now(UTC) - t1).total_seconds():.2f}s")

        finally:
            sf_cur.close()
            sf.close()
    else:
        logger.info("No messages to load — skipping Snowflake connection")

# ── DAG ──────────────────────────────────────────────────────────────────────

with DAG(
    dag_id="querybot_postgres_to_snowflake",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    schedule=None,  # ← change from "@hourly"
    catchup=False,
    tags=["querybot", "rag-eval"],
) as dag:

    t0 = PythonOperator(
        task_id="capture_watermark",
        python_callable=capture_watermark,
    )
    t1 = PythonOperator(
        task_id="extract_and_load_conversations",
        python_callable=extract_and_load_conversations,
    )
    t2 = PythonOperator(
        task_id="extract_and_load_conversation_runs",
        python_callable=extract_and_load_conversation_runs,
    )
    t3 = PythonOperator(
        task_id="extract_and_load_conversation_messages",
        python_callable=extract_and_load_conversation_messages,
    )

    t0 >> t1 >> [t2, t3]
