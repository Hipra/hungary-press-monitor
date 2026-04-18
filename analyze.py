"""
Analyze pending articles using the local Claude CLI.
Calls `claude -p` per article, extracts structured JSON including
Hungarian translation of title and summary.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path("data/articles.db")
DELAY_BETWEEN_CALLS = 0.5
MAX_PER_RUN = 50

SYSTEM_PROMPT = """You are a press analyst specializing in international coverage of Hungary.

Context: In April 2026, Hungary underwent a historic government transition.
Péter Magyar and the Tisza Party won a two-thirds parliamentary majority,
ending Viktor Orbán's 15-year rule. The international press is now covering
Hungary's democratic transition, EU reintegration, and geopolitical realignment.

Analyze the article and return ONLY a valid JSON object with these exact fields:

- is_relevant: true if Hungary is a main subject; false if mentioned only briefly or incidentally

- tone: overall tone toward Hungary or its new direction
  "positive" = hopeful, supportive, praising
  "neutral" = factual, balanced
  "critical" = skeptical, warning, negative
  "mixed" = both positive and critical elements

- framing: the dominant narrative frame used
  "democracy_restoration" = Hungary returning to rule of law, democratic norms
  "geopolitics" = NATO, Russia, Ukraine, US relations angle
  "economy" = markets, EU funds, investment, fiscal policy
  "eu_integration" = Hungary rejoining EU mainstream, Brussels relations
  "regional" = V4, CEE, Balkans context
  "other"

- main_actor: the primary subject of the article
  "magyar_peter" = Péter Magyar or his government
  "orban_viktor" = Viktor Orbán (as ex-PM, opposition, legacy)
  "hungary_country" = Hungary as a country/institution
  "eu_institutions" = European Commission, Parliament, Council
  "other"

- comparison_countries: array of country names explicitly compared to Hungary (max 3, empty array if none)

- topics: array of 1-4 strings from:
  ["government transition", "EU relations", "economy", "democracy", "geopolitics",
   "elections", "foreign policy", "rule of law", "media", "society", "migration",
   "energy", "nato", "other"]

- actors: array of key person/organization names mentioned (max 5)

- summary_en: 2-3 sentence English summary focused on Hungary angle

- title_hu: Hungarian translation of the article title

- summary_hu: 2-3 sentence Hungarian summary (translate and adapt summary_en)

Do not include any explanation or text outside the JSON object."""

PROMPT_TEMPLATE = """{system}

Source: {source} ({region})
Title: {title}
URL: {url}
Published: {published_at}

Analyze this article."""


def migrate_db(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    new_cols = [
        ("title_hu", "TEXT"),
        ("summary_hu", "TEXT"),
        ("is_relevant", "INTEGER DEFAULT 1"),
        ("main_actor", "TEXT"),
        ("comparison_countries", "TEXT"),
    ]
    for col, typedef in new_cols:
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typedef}")
            log.info("Added column: %s", col)
    conn.commit()


def get_pending_articles(conn: sqlite3.Connection) -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT id, source, region, title, url, published_at FROM articles
           WHERE analyzed = 0 AND published_at >= ?
           ORDER BY published_at DESC LIMIT ?""",
        (today, MAX_PER_RUN)
    ).fetchall()
    return [
        {"id": r[0], "source": r[1], "region": r[2],
         "title": r[3], "url": r[4], "published_at": r[5]}
        for r in rows
    ]


def call_claude(prompt: str) -> str | None:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-haiku-4-5-20251001",
             "--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            log.warning("claude CLI error (rc=%d) stderr: %s | stdout: %s",
                        result.returncode, result.stderr[:300], result.stdout[:300])
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
            topics = ?, actors = ?, tone = ?, framing = ?,
            summary_en = ?, title_hu = ?, summary_hu = ?,
            is_relevant = ?, main_actor = ?, comparison_countries = ?,
            analyzed = 1
        WHERE id = ?
        """,
        (
            json.dumps(analysis.get("topics", [])),
            json.dumps(analysis.get("actors", [])),
            analysis.get("tone", "neutral"),
            analysis.get("framing", "other"),
            analysis.get("summary_en", ""),
            analysis.get("title_hu", ""),
            analysis.get("summary_hu", ""),
            1 if analysis.get("is_relevant", True) else 0,
            analysis.get("main_actor", "other"),
            json.dumps(analysis.get("comparison_countries", [])),
            article_id,
        ),
    )
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    migrate_db(conn)
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
