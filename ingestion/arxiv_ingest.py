
import json
import logging
import time
from datetime import datetime
from pathlib import Path

import boto3
import requests
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ARXIV_API_URL = "http://export.arxiv.org/api/query"
S3_BUCKET = os.getenv("S3_BUCKET_NAME")
LOCAL_STAGING = Path("data/raw")


def fetch_papers(query: str = "cat:cs.LG", max_results: int = 100) -> str:
    """Hit the arXiv API and return raw XML response."""
    params = {
        "search_query": query,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending"
    }
    logger.info(f"Fetching {max_results} papers from arXiv...")
    response = requests.get(ARXIV_API_URL, params=params)
    response.raise_for_status()
    return response.text


def parse_papers(xml_text: str) -> list[dict]:
    """Parse Atom XML into a list of paper dicts."""
    import xml.etree.ElementTree as ET

    ns = {"atom": "http://www.w3.org/2005/Atom",
          "arxiv": "http://arxiv.org/schemas/atom"}
    
    root = ET.fromstring(xml_text)
    papers = []

    for entry in root.findall("atom:entry", ns):
        paper = {
            "id": entry.findtext("atom:id", namespaces=ns),
            "title": entry.findtext("atom:title", namespaces=ns, default="").strip(),
            "summary": entry.findtext("atom:summary", namespaces=ns, default="").strip(),
            "published": entry.findtext("atom:published", namespaces=ns),
            "authors": [
                a.findtext("atom:name", namespaces=ns)
                for a in entry.findall("atom:author", ns)
            ],
            "categories": [
                c.get("term")
                for c in entry.findall("atom:category", ns)
            ],
        }
        papers.append(paper)

    logger.info(f"Parsed {len(papers)} papers.")
    return papers


def save_locally(papers: list[dict]) -> Path:
    """Save papers as newline-delimited JSON to local staging."""
    LOCAL_STAGING.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    filepath = LOCAL_STAGING / f"arxiv_{timestamp}.jsonl"


    with open(filepath, "w") as f:
        for paper in papers:
            f.write(json.dumps(paper) + "\n")

    logger.info(f"Saved {len(papers)} papers to {filepath}")
    return filepath


def upload_to_s3(filepath: Path) -> None:
    """Upload localJSON file to S3."""
    s3 = boto3.client("s3")
    s3_key = f"raw/arxiv/{filepath.name}"
    s3.upload_file(str(filepath), S3_BUCKET, s3_key)
    logger.info("Upload complete")


def run():
    xml = fetch_papers(max_results=100)
    papers = parse_papers(xml)
    filepath = save_locally(papers)
    upload_to_s3(filepath)


if __name__ == "__main__":
    run()
