"""
Analyze pending articles using the local Claude CLI.
Calls `claude -p` per article, extracts structured JSON.
"""

import json
import logging
import sqlite3
import subprocess
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path("data/articles.db")
DELAY_BETWEEN_CALLS = 0.5  # seconds

SYSTEM_PROMPT = """You are a press analysis assistant. Analyze news articles about Hungary.
Return ONLY a valid JSON object with these exact fields:
- topics: array of 1-4 strings from: ["government transition", "EU relations", "economy", "democracy", "geopolitics", "elections", "foreign policy", "rule of law", "media", "society", "migration", "energy", "nato", "other"]
- actors: array of person/organization names mentioned (max 5)
- tone: one of "positive", "neutral", "critical", "mixed"
- framing: one of "democracy", "geopolitics", "economy", "eu_integration", "regional", "other"
- summary_en: 2-3 sentence English summary of the article

Do not include any explanation or text outside the JSON object."""

PROMPT_TEMPLATE = """{system}

Source: {source} ({region})
Title: {title}
URL: {url}
Published: {published_at}

Analyze this article about Hungary based on the title and source context."""


def get_pending_articles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, source, region, title, url, published_at FROM articles WHERE analyzed = 0"
    ).fetchall()
    return [
        {"id": r[0], "source": r[1], "region": r[2],
         "title": r[3], "url": r[4], "published_at": r[5]}
        for r in rows
    ]


def call_claude(prompt: str) -> str | None:
    """Call `claude -p` and return stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.warning("claude CLI error: %s", result.stderr[:200])
            return None
        return result.stdout.strip()
    except FileNotFoundError:
        log.error("claude CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        raise
    except subprocess.TimeoutExpired:
        log.warning("claude CLI timed out")
        return None


def parse_json(text: str) -> dict | None:
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return None


def save_analysis(conn: sqlite3.Connection, article_id: str, analysis: dict) -> None:
    conn.execute(
        """
        UPDATE articles SET
            topics = ?, actors = ?, tone = ?, framing = ?, summary_en = ?, analyzed = 1
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
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    articles = get_pending_articles(conn)

    if not articles:
        log.info("No pending articles to analyze.")
        conn.close()
        return

    log.info("Analyzing %d articles...", len(articles))
    saved = failed = 0

    for i, article in enumerate(articles, 1):
        log.info("[%d/%d] %s — %s", i, len(articles), article["source"], article["title"][:60])

        prompt = PROMPT_TEMPLATE.format(system=SYSTEM_PROMPT, **article)
        response = call_claude(prompt)

        if not response:
            failed += 1
            continue

        analysis = parse_json(response)
        if not analysis:
            log.warning("Could not parse JSON for %s: %s", article["id"], response[:200])
            failed += 1
            continue

        save_analysis(conn, article["id"], analysis)
        saved += 1
        time.sleep(DELAY_BETWEEN_CALLS)

    conn.close()
    log.info("Done. Saved: %d, Failed: %d", saved, failed)


if __name__ == "__main__":
    main()
