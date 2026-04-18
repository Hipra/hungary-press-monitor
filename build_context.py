"""
Self-learning context engine.
Reads analyzed+relevant articles from the last 7 days and uses Claude Sonnet
to synthesize and continuously refine data/context.md — a structured background
document that analyze.py prepends to its system prompt so Haiku gets current context.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path("data/articles.db")
CONTEXT_PATH = Path("data/context.md")
LOOKBACK_DAYS = 7
MAX_ARTICLES = 120

SYNTHESIS_PROMPT_TEMPLATE = """You are an expert analyst tracking international press coverage of Hungary.

Today is {today}.

Your task: produce a structured, concise CONTEXT DOCUMENT (max 800 words) summarizing what the international press currently says about Hungary. This document will be used as background context for AI press analysis.

EXISTING CONTEXT (from previous synthesis — refine, update, or replace outdated entries):
---
{existing_context}
---

RECENT ARTICLES (last 7 days, analyzed and Hungary-relevant):
---
{articles_text}
---

Write a context document with EXACTLY these sections. Be specific, name actors and events. Update older entries if the situation has changed. Remove outdated claims. Keep each section under 150 words.

## Political situation
Current government composition, key decisions, parliamentary dynamics, Tisza Party / Péter Magyar developments.

## Geopolitics
NATO stance, EU-Hungary relations, Russia/Ukraine policy, US relations, regional diplomacy (V4, Serbia, Romania).

## Economy
EU funds status, fiscal policy, investment climate, key economic decisions or crises.

## Rule of law & human rights
Judicial reform progress, media freedom, civil society, corruption cases, human rights concerns flagged internationally.

## Key actors
Who is currently prominent in international coverage: names, roles, current positions/actions (max 8 actors).

## Emerging narratives
New story lines appearing in the last 7 days that weren't in previous coverage. First-time topics.

## Coverage gaps
Topics that appear to have dropped off international radar recently.

Return ONLY the markdown document, no preamble or explanation."""


def load_recent_articles(conn: sqlite3.Connection) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT source, region, title, summary_en, tone, framing, main_actor,
                  topics, actors, published_at
           FROM articles
           WHERE analyzed = 1 AND is_relevant = 1 AND published_at >= ?
           ORDER BY published_at DESC
           LIMIT ?""",
        (cutoff, MAX_ARTICLES),
    ).fetchall()
    return [
        {
            "source": r[0], "region": r[1], "title": r[2],
            "summary_en": r[3], "tone": r[4], "framing": r[5],
            "main_actor": r[6], "topics": r[7], "actors": r[8],
            "published_at": r[9],
        }
        for r in rows
    ]


def format_articles(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        lines.append(
            f"[{a['published_at'][:10]}] {a['source']} ({a['region']}) | "
            f"tone={a['tone']} framing={a['framing']} actor={a['main_actor']}\n"
            f"Title: {a['title']}\n"
            f"Summary: {a['summary_en'] or '(no summary)'}\n"
        )
    return "\n".join(lines)


def call_sonnet(prompt: str) -> str | None:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-sonnet-4-6",
             "--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            log.warning("claude CLI error (rc=%d): %s", result.returncode, result.stderr[:300])
            return None
        return result.stdout.strip()
    except FileNotFoundError:
        log.error("claude CLI not found")
        raise
    except subprocess.TimeoutExpired:
        log.warning("claude CLI timed out")
        return None


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    articles = load_recent_articles(conn)
    conn.close()

    if not articles:
        log.info("No analyzed articles found for context synthesis.")
        return

    log.info("Synthesizing context from %d articles...", len(articles))

    existing_context = ""
    if CONTEXT_PATH.exists():
        existing_context = CONTEXT_PATH.read_text(encoding="utf-8")

    articles_text = format_articles(articles)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = SYNTHESIS_PROMPT_TEMPLATE.format(
        today=today,
        existing_context=existing_context or "(none — first run)",
        articles_text=articles_text,
    )

    result = call_sonnet(prompt)
    if not result:
        log.error("Failed to synthesize context.")
        return

    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_PATH.write_text(result, encoding="utf-8")
    log.info("Context saved to %s (%d chars)", CONTEXT_PATH, len(result))


if __name__ == "__main__":
    main()
