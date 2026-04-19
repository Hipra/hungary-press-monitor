"""
Fetch Hungary-related articles from 25 Google News RSS feeds.
Deduplicates by URL, stores new articles in SQLite with analyzed=0.
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser

NON_ARTICLE_PATTERNS = [
    r"^video[:\.\s\-]",
    r"^podcast[:\.\s\-]",
    r"^audio[:\.\s\-]",
    r"^live[:\.\s]",
    r"latest news bulletin",
    r"news bulletin\s*\|",
    r"\bcartoon\b",
    r"^opinion\s*[-:–]",
    r"^world news\b",
    r"^analysis\s*[-:–]",
    r"^newsletter\s*[-:–]",
    r"^briefing\s*[-:–]",
    r"^weekly wrap",
    r"^today in",
    r"^morning brief",
    r"^evening brief",
    r"^tag:",
    r"^category:",
    r"^topic:",
]
NON_ARTICLE_RE = re.compile("|".join(NON_ARTICLE_PATTERNS), re.IGNORECASE)


def is_article_title(title: str) -> bool:
    """Heuristic filter: reject category/tag/video/cartoon pages based on title."""
    if not title:
        return False
    t = title.strip()
    if len(t) < 25:
        return False
    if NON_ARTICLE_RE.search(t):
        return False
    # Google News titles end with " - {source}". The main headline before that suffix
    # on a real article is usually 25+ chars; on section pages it's short (e.g. "Hungary", "World News").
    if " - " in t:
        main_part = t.rsplit(" - ", 1)[0].strip()
        if len(main_part) < 25:
            return False
    return True

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path("data/articles.db")
SOURCES_PATH = Path("sources.json")
DELAY_BETWEEN_FEEDS = 1.0  # seconds, polite crawling


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            source TEXT,
            region TEXT,
            title TEXT,
            url TEXT UNIQUE,
            published_at TEXT,
            fetched_at TEXT,
            topics TEXT,
            actors TEXT,
            tone TEXT,
            framing TEXT,
            summary_en TEXT,
            analyzed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            fetched_at TEXT,
            new_articles INTEGER,
            status TEXT
        );

        -- Permanent ledger of every URL we've ever seen. Survives article deletions
        -- so purged/irrelevant articles never get re-fetched and re-analyzed.
        CREATE TABLE IF NOT EXISTS seen_urls (
            url TEXT PRIMARY KEY,
            first_seen TEXT
        );
    """)
    conn.commit()


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def parse_published(entry) -> str:
    """Return ISO8601 UTC string from feedparser entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    return datetime.now(timezone.utc).isoformat()


def load_seen_urls(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT url FROM seen_urls")}


def fetch_feed(source: dict, seen: set[str]) -> list[dict]:
    """Fetch one RSS feed, return list of raw article dicts."""
    try:
        feed = feedparser.parse(
            source["feed_url"],
            request_headers={"User-Agent": "Mozilla/5.0 (compatible; HungaryMonitor/1.0)"},
        )
    except Exception as e:
        log.error("Failed to parse feed %s: %s", source["name"], e)
        return []

    log.info("  feed status=%s bozo=%s entries=%d", getattr(feed, "status", "?"), feed.bozo, len(feed.entries))
    if feed.bozo:
        log.warning("  bozo_exception: %s", feed.bozo_exception)

    articles = []
    skipped = 0
    already_seen = 0
    for entry in feed.entries:
        url = getattr(entry, "link", "")
        if not url:
            continue
        if url in seen:
            already_seen += 1
            continue
        title = getattr(entry, "title", "")
        if not is_article_title(title):
            skipped += 1
            continue

        articles.append({
            "id": article_id(url),
            "source": source["name"],
            "region": source["region"],
            "title": title,
            "url": url,
            "published_at": parse_published(entry),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    log.info("  filtered: %d already-seen, %d non-article, %d new candidates",
             already_seen, skipped, len(articles))
    return articles


def save_articles(conn: sqlite3.Connection, articles: list[dict], seen: set[str]) -> int:
    """Insert new articles, skip existing. Also record URL in seen_urls ledger."""
    new_count = 0
    for a in articles:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO articles
                    (id, source, region, title, url, published_at, fetched_at, analyzed)
                VALUES
                    (:id, :source, :region, :title, :url, :published_at, :fetched_at, 0)
                """,
                a,
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                new_count += 1
            conn.execute(
                "INSERT OR IGNORE INTO seen_urls (url, first_seen) VALUES (?, ?)",
                (a["url"], a["fetched_at"]),
            )
            seen.add(a["url"])
        except sqlite3.Error as e:
            log.error("DB error for %s: %s", a.get("url"), e)
    conn.commit()
    return new_count


def log_fetch(conn: sqlite3.Connection, source_name: str, new_count: int, status: str) -> None:
    conn.execute(
        "INSERT INTO fetch_log (source, fetched_at, new_articles, status) VALUES (?, ?, ?, ?)",
        (source_name, datetime.now(timezone.utc).isoformat(), new_count, status),
    )
    conn.commit()


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    sources = json.loads(SOURCES_PATH.read_text())

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    seen = load_seen_urls(conn)
    log.info("Loaded %d previously-seen URLs from ledger.", len(seen))

    total_new = 0
    for source in sources:
        log.info("Fetching %s ...", source["name"])
        try:
            articles = fetch_feed(source, seen)
            new_count = save_articles(conn, articles, seen)
            log_fetch(conn, source["name"], new_count, "ok")
            log.info("  %s: %d new / %d fetched", source["name"], new_count, len(articles))
            total_new += new_count
        except Exception as e:
            log.error("  %s: FAILED — %s", source["name"], e)
            log_fetch(conn, source["name"], 0, f"error: {e}")

        time.sleep(DELAY_BETWEEN_FEEDS)

    conn.close()
    log.info("Done. Total new articles: %d", total_new)


if __name__ == "__main__":
    main()
