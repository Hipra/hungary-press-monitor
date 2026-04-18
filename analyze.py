"""
Analyze pending articles using Claude Haiku via the Batch API.
Extracts topics, actors, tone, framing, and English summary per article.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path("data/articles.db")
MODEL = "claude-haiku-4-5-20251001"
BATCH_POLL_INTERVAL = 60   # seconds between status checks
BATCH_TIMEOUT = 7200       # 2 hours max wait

SYSTEM_PROMPT = """You are a press analysis assistant. Analyze news articles about Hungary.
Return ONLY a valid JSON object with these exact fields:
- topics: array of 1-4 strings from: ["government transition", "EU relations", "economy", "democracy", "geopolitics", "elections", "foreign policy", "rule of law", "media", "society", "migration", "energy", "nato", "other"]
- actors: array of person/organization names mentioned (max 5)
- tone: one of "positive", "neutral", "critical", "mixed"
- framing: one of "democracy", "geopolitics", "economy", "eu_integration", "regional", "other"
- summary_en: 2-3 sentence English summary of the article

Do not include any explanation or text outside the JSON object."""

ANALYSIS_PROMPT_TEMPLATE = """Source: {source} ({region})
Title: {title}
URL: {url}
Published: {published_at}

Analyze this article about Hungary based on the title and source context."""


def get_pending_articles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, source, region, title, url, published_at FROM articles WHERE analyzed = 0"
    ).fetchall()
    return [
        {
            "id": r[0], "source": r[1], "region": r[2],
            "title": r[3], "url": r[4], "published_at": r[5],
        }
        for r in rows
    ]


def build_batch_requests(articles: list[dict]) -> list[dict]:
    requests_list = []
    for article in articles:
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(**article)
        requests_list.append({
            "custom_id": article["id"],
            "params": {
                "model": MODEL,
                "max_tokens": 512,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            },
        })
    return requests_list


def submit_batch(client: anthropic.Anthropic, requests_list: list[dict]) -> str:
    batch = client.messages.batches.create(requests=requests_list)
    log.info("Batch submitted: %s (%d requests)", batch.id, len(requests_list))
    return batch.id


def wait_for_batch(client: anthropic.Anthropic, batch_id: str) -> bool:
    """Poll until batch ends. Returns True on success."""
    deadline = time.time() + BATCH_TIMEOUT
    while time.time() < deadline:
        batch = client.messages.batches.retrieve(batch_id)
        status = batch.processing_status
        counts = batch.request_counts
        log.info(
            "Batch %s — status: %s | succeeded: %d, errored: %d, processing: %d",
            batch_id, status,
            counts.succeeded, counts.errored, counts.processing,
        )
        if status == "ended":
            return True
        time.sleep(BATCH_POLL_INTERVAL)
    log.error("Batch timed out after %d seconds", BATCH_TIMEOUT)
    return False


def parse_analysis(text: str) -> dict | None:
    """Extract JSON from model response."""
    try:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return None


def process_batch_results(client: anthropic.Anthropic, batch_id: str, conn: sqlite3.Connection) -> tuple[int, int]:
    """Stream results and update DB. Returns (saved, failed)."""
    saved = failed = 0
    for result in client.messages.batches.results(batch_id):
        article_id = result.custom_id
        if result.result.type != "succeeded":
            log.warning("Article %s failed: %s", article_id, result.result.type)
            failed += 1
            continue

        content_blocks = result.result.message.content
        text = content_blocks[0].text if content_blocks else ""
        analysis = parse_analysis(text)

        if not analysis:
            log.warning("Could not parse analysis for %s: %s", article_id, text[:200])
            failed += 1
            continue

        try:
            conn.execute(
                """
                UPDATE articles SET
                    topics = ?,
                    actors = ?,
                    tone = ?,
                    framing = ?,
                    summary_en = ?,
                    analyzed = 1
                WHERE id = ?
                """,
                (
                    json.dumps(analysis.get("topics", [])),
                    json.dumps(analysis.get("actors", [])),
                    analysis.get("tone", "neutral"),
                    analysis.get("framing", "other"),
                    analysis.get("summary_en", ""),
                    article_id,
                ),
            )
            saved += 1
        except sqlite3.Error as e:
            log.error("DB error saving analysis for %s: %s", article_id, e)
            failed += 1

    conn.commit()
    return saved, failed


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    articles = get_pending_articles(conn)

    if not articles:
        log.info("No pending articles to analyze.")
        conn.close()
        return

    log.info("Analyzing %d pending articles via Batch API...", len(articles))

    client = anthropic.Anthropic()

    requests_list = build_batch_requests(articles)
    batch_id = submit_batch(client, requests_list)

    success = wait_for_batch(client, batch_id)
    if not success:
        log.error("Batch did not complete in time. Results may be partial.")

    saved, failed = process_batch_results(client, batch_id, conn)
    conn.close()

    log.info("Analysis complete. Saved: %d, Failed: %d", saved, failed)


if __name__ == "__main__":
    main()
