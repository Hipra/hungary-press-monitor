"""
Daily digest builder.
Reads today's analyzed+relevant articles and data/context.md,
uses Claude Sonnet to synthesize a short bilingual (EN/HU) daily digest.
Saves:
  - data/digests/YYYY-MM-DD.md (archive)
  - docs/digest.json (latest, consumed by the dashboard)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path("data/articles.db")
CONTEXT_PATH = Path("data/context.md")
DIGEST_DIR = Path("data/digests")
DIGEST_JSON = Path("docs/digest.json")
LOOKBACK_HOURS = 24
MAX_ARTICLES = 60

PROMPT_TEMPLATE = """You are a senior press analyst producing a daily briefing on international coverage of Hungary.

Today: {today}.

CURRENT CONTEXT (synthesized from the last 7 days):
---
{context}
---

TODAY'S ARTICLES ({n} articles from the last 24 hours, analyzed and Hungary-relevant):
---
{articles}
---

Write a concise BILINGUAL daily digest. Return ONLY a valid JSON object with these exact fields:

- top_story_en / top_story_hu: 1-2 sentence headline finding of the day (most important story or pattern)

- key_developments_en / key_developments_hu: array of 3-5 short bullet strings (each 1 sentence, under 200 chars). Focus on concrete events, decisions, statements — not opinion pieces.

- narrative_shifts_en / narrative_shifts_hu: array of 1-3 bullet strings describing how coverage tone or framing has shifted today vs. recent days. Empty array if no shift detected.

- quotes_en: array of 1-3 objects {{"quote": "...", "speaker": "..."}} — most notable direct quotes from officials/analysts in today's coverage. Empty array if none.
- quotes_hu: same structure but quotes translated to Hungarian.

- what_to_watch_en / what_to_watch_hu: array of 1-3 bullet strings on upcoming/unresolved issues worth tracking.

Total word count across all English fields: target 350-450 words. Hungarian sections mirror English but adapted idiomatically, not literal translation.

Do not include any explanation or text outside the JSON object."""


def load_today_articles(conn: sqlite3.Connection) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    rows = conn.execute(
        """SELECT source, region, title, summary_en, tone, framing, main_actor,
                  topics, actors, quotes, published_at
           FROM articles
           WHERE analyzed = 1 AND is_relevant = 1 AND published_at >= ?
           ORDER BY published_at DESC
           LIMIT ?""",
        (cutoff, MAX_ARTICLES),
    ).fetchall()
    return [
        {
            "source": r[0], "region": r[1], "title": r[2], "summary_en": r[3],
            "tone": r[4], "framing": r[5], "main_actor": r[6],
            "topics": r[7], "actors": r[8], "quotes": r[9],
            "published_at": r[10],
        }
        for r in rows
    ]


def format_articles(articles: list[dict]) -> str:
    lines = []
    for a in articles:
        quotes = ""
        try:
            qlist = json.loads(a["quotes"] or "[]")
            if qlist:
                quotes = " | quotes: " + " // ".join(f'"{q}"' for q in qlist[:2])
        except Exception:
            pass
        lines.append(
            f"[{a['published_at'][:16]}] {a['source']} ({a['region']}) "
            f"tone={a['tone']} framing={a['framing']} actor={a['main_actor']}\n"
            f"  Title: {a['title']}\n"
            f"  Summary: {a['summary_en'] or '(none)'}{quotes}\n"
        )
    return "\n".join(lines)


def call_sonnet(prompt: str) -> str | None:
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "claude-sonnet-4-6",
             "--dangerously-skip-permissions"],
            capture_output=True,
            text=True,
            timeout=180,
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


def parse_json(text: str) -> dict | None:
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return None


def render_markdown(digest: dict, date: str) -> str:
    """Human-readable archive format."""
    def bullets(items):
        return "\n".join(f"- {x}" for x in items) if items else "_(none)_"

    def quote_bullets(items):
        lines = []
        for q in items or []:
            if isinstance(q, dict):
                lines.append(f"> \"{q.get('quote','')}\" — {q.get('speaker','')}")
        return "\n".join(lines) if lines else "_(none)_"

    return f"""# Daily digest — {date}

## Top story
**EN:** {digest.get('top_story_en','')}
**HU:** {digest.get('top_story_hu','')}

## Key developments / Főbb fejlemények
### EN
{bullets(digest.get('key_developments_en', []))}

### HU
{bullets(digest.get('key_developments_hu', []))}

## Narrative shifts / Narratíva-váltások
### EN
{bullets(digest.get('narrative_shifts_en', []))}

### HU
{bullets(digest.get('narrative_shifts_hu', []))}

## Quotes of the day / Napi idézetek
### EN
{quote_bullets(digest.get('quotes_en', []))}

### HU
{quote_bullets(digest.get('quotes_hu', []))}

## What to watch / Figyelendő
### EN
{bullets(digest.get('what_to_watch_en', []))}

### HU
{bullets(digest.get('what_to_watch_hu', []))}
"""


def main() -> None:
    if not DB_PATH.exists():
        log.error("DB not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    articles = load_today_articles(conn)
    conn.close()

    if not articles:
        log.info("No analyzed articles in last %dh — skipping digest.", LOOKBACK_HOURS)
        return

    context = CONTEXT_PATH.read_text(encoding="utf-8") if CONTEXT_PATH.exists() else "(none)"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = PROMPT_TEMPLATE.format(
        today=today,
        context=context,
        articles=format_articles(articles),
        n=len(articles),
    )

    log.info("Generating digest from %d articles...", len(articles))
    result = call_sonnet(prompt)
    if not result:
        log.error("Digest generation failed.")
        return

    digest = parse_json(result)
    if not digest:
        log.error("Could not parse digest JSON: %s", result[:300])
        return

    digest["date"] = today
    digest["article_count"] = len(articles)
    digest["generated_at"] = datetime.now(timezone.utc).isoformat()

    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    (DIGEST_DIR / f"{today}.md").write_text(render_markdown(digest, today), encoding="utf-8")

    DIGEST_JSON.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_JSON.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("Digest saved: %s and %s", DIGEST_DIR / f"{today}.md", DIGEST_JSON)


if __name__ == "__main__":
    main()
