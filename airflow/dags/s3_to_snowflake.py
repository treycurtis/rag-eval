
"""
DAG: s3_to_snowflake
Phase 2 — RAG Eval Pipeline
 
Ingests new JSONL files from S3 into rag_eval.raw.arxiv_papers in Snowflake.

Flow:
    list_new_s3_files -> download_parse_load -> log_run_metadata

State tracking:
    Uses Airflow Variable `rag_eval.last_s3_prefix_processed` to track the last
    loaded S3 key. On first run this Variable won't exist and all files are loaded.
 
Idempotency:
    MERGE on paper_id — reruns are safe.
 
Performance note (future):
    At scale, swap the Python INSERT loop for S3 stage → COPY INTO. The current
    approach is fine for hundreds of papers; rethink at ~100k+ rows.
"""

from __future__ import annotations
 
import json
import logging
from datetime import datetime, timezone
 
import boto3
from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
 
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
 
S3_BUCKET = "rag-eval-papers-raw"
S3_PREFIX = "raw/arxiv/"
SNOWFLAKE_CONN_ID = "snowflake_default"
SNOWFLAKE_TABLE = "rag_eval.raw.arxiv_papers"
LAST_KEY_VAR = "rag_eval.last_s3_key_processed"

# ── DAG ───────────────────────────────────────────────────────────────────────

@dag(
    dag_id="s3_to_snowflake",
    description="Ingest new arXiv JSONL files from S3 into Snowflake raw layer",
    schedule="@daily",
    start_date=datetime(2026, 4, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["rag-eval", "ingestion", "phase-2"],
    doc_md=__doc__,
)
def s3_to_snowflake():

    @task()
    def list_new_s3_files() -> list[str]:
        """
        List JSONL files in S3 that haven't been processed yet.
 
        Reads the last-processed key from an Airflow Variable. On first run,
        the Variable won't exist and all files in the prefix are returned.
 
        Returns a list of S3 keys (strings). Empty list = nothing to do.
        """
        last_key = Variable.get(LAST_KEY_VAR, default_var=None)
        log.info("Last processed S3 key: %s", last_key or "None (first run)")

        s3 = boto3.client("s3")
        paginator = s3.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX)

        all_keys: list[str] = []
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".jsonl"):
                    all_keys.append(key)

        all_keys.sort()  # lexicographic = chronological given our timestamp naming

        if last_key is None:
            new_keys = all_keys
        else:
            # Only keys strictly after the last processed one
            new_keys = [k for k in all_keys if k > last_key]

        log.info("Found %d new JSONL file(s) to process: %s", len(new_keys), new_keys)
        return new_keys
    
    @task()
    def download_parse_load(s3_keys: list[str]) -> int:
        """
        Download each JSONL file from S3, parse it line-by-line, and load into Snowflake.

        Returns the number of papers processed as an int.

        Expected JSONL fields per line:
        paper_id, arxiv_url, title, abstract, authors, categories, published_date, s3_key, ingested_at

        Then we use SnowflakeHook to insert these records into the target table. 
        The paper_id is extracted from the arXiv URL (JSONL "id" field) for easier querying. 
        We also log the number of papers parsed from each file and the total across all files.

        MERGE parsed paper rows into rag_eval.raw.arxiv_papers.
 
        Uses MERGE ON paper_id — reruns are idempotent.
        Updates all fields if the paper already exists (handles re-ingests
        of corrected source data).
 
        Returns row count loaded.

        """

        if not s3_keys:
            log.info("No new files - skipping download.")
            return 0
        
        s3 = boto3.client("s3")
        papers: list[dict] = []

        for key in s3_keys:
            log.info("Downloading s3://%s/%s", S3_BUCKET, key)
            response = s3.get_object(Bucket=S3_BUCKET, Key=key)
            body = response["Body"].read().decode("utf-8")

            file_papers = 0
            for line_num, line in enumerate(body.splitlines(), start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    papers.append(
                        {
                            "paper_id": record["id"].rstrip("/").split("/")[-1],  # extract arXiv ID from URL
                            "arxiv_url": record["id"],
                            "title": record["title"],
                            "abstract": record["summary"],
                            "authors": json.dumps(record["authors"]), 
                            "categories": json.dumps(record["categories"]),
                            "published_date": datetime.fromisoformat(record["published"]).replace(tzinfo=None),
                            "s3_key": key,
                            "ingested_at": datetime.now(timezone.utc),
                        }
                    )
                    file_papers += 1
                except (json.JSONDecodeError, KeyError) as e:
                    log.warning("Skipping malformed line %d in %s: %s", line_num, key, e)

            log.info("Parsed %d papers from %s", file_papers, key)

        log.info("Total papers parsed from all files: %d", len(papers))

        # MERGE parsed paper rows into rag_eval.raw.arxiv_papers.
        # Uses MERGE ON paper_id — reruns are idempotent.
        # Updates all fields if the paper already exists (handles re-ingests of corrected source data).
        # Returns row count loaded.

        if not papers:
            log.info("No valid papers parsed - skipping Snowflake load.")
            return 0
        
        hook = SnowflakeHook(snowflake_conn_id=SNOWFLAKE_CONN_ID)

        merge_sql = f"""
            MERGE INTO {SNOWFLAKE_TABLE} AS target
            USING (
                SELECT
                    %(paper_id)s AS paper_id,
                    %(arxiv_url)s AS arxiv_url,
                    %(title)s AS title,
                    %(abstract)s AS abstract,
                    PARSE_JSON(%(authors)s) AS authors,
                    PARSE_JSON(%(categories)s) AS categories,
                    %(published_date)s AS published_date,
                    %(s3_key)s AS s3_key,
                    %(ingested_at)s AS ingested_at
            ) AS source
            ON target.paper_id = source.paper_id
            WHEN MATCHED THEN UPDATE SET
                arxiv_url = source.arxiv_url,
                title = source.title,
                abstract = source.abstract,
                authors = source.authors,
                categories = source.categories,
                published_date = source.published_date,
                s3_key = source.s3_key,
                ingested_at = source.ingested_at
            WHEN NOT MATCHED THEN INSERT (
                paper_id, arxiv_url, title, abstract, authors, categories, published_date, s3_key, ingested_at
            ) VALUES (
                source.paper_id, source.arxiv_url, source.title, source.abstract, source.authors, 
                source.categories, source.published_date, source.s3_key, source.ingested_at
            ) 
        """

        conn = hook.get_conn()
        cursor = conn.cursor()
        rows_affected = 0

        try:
            # Snowflake Python connector handles parameterized batch well here;
            # executemany sends rows in batches internally for efficiency.
            cursor.executemany(merge_sql, papers)
            conn.commit()
            rows_affected = cursor.rowcount
            log.info("MERGE complete -- %d row(s) affected.", rows_affected)
        
            # Advance the state marker to the last key we processed
            if s3_keys:
                last_key = sorted(s3_keys)[-1]
                Variable.set(LAST_KEY_VAR, last_key)
                log.info("Updated last processed S3 key to: %s", last_key)   
        finally:
            cursor.close()
            conn.close()
        
        return rows_affected
    

    @task()
    def log_run_metadata(rows_loaded: int, s3_keys: list[str]) -> None:
        """
        Log a structured run summary. Extend this later to write to a
        rag_eval.raw.pipeline_runs table for observability.
        """

        summary = {
            "dag": "s3_to_snowflake",
            "run_at": datetime.now(timezone.utc).isoformat(),
            "files_processed": len(s3_keys),
            "rows_loaded": rows_loaded,
            "files": s3_keys,
        }

        log.info("Run summary: %s", json.dumps(summary, indent=2))

        # TODO (Phase 6 / Evidently integration): write this to
        # rag_eval.raw.pipeline_runs for drift + freshness tracking.
 
    # ── Wire tasks ────────────────────────────────────────────────────────────

    new_keys = list_new_s3_files()
    rows = download_parse_load(new_keys)
    log_run_metadata(rows, new_keys)

s3_to_snowflake()