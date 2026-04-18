# Hungary International Press Monitor — Project Briefing

> Ez a fájl Claude Code számára készült. Automatikusan olvasd be fejlesztés elején.

---

## Projekt célja

Magyarország megjelenésének monitorozása a nemzetközi sajtóban. A 2026 áprilisi kormányváltás (Tisza Párt / Magyar Péter kétharmados győzelme) után az eredeti magyar belpolitikai narratíva-monitor helyett a projekt új irányt vesz: mit ír a világ Magyarországról.

---

## Architektúra

```
GitHub Actions (cron: 2 óránként)
  → fetch.py        — 25 Google News RSS feed lekérése
  → deduplikáció    — URL alapján, SQLite-ban tárolt history
  → analyze.py      — Claude Haiku 4.5 elemzi az új cikkeket
  → SQLite          — eredmények tárolása (a repóban)
  → dashboard       — GitHub Pages statikus frontend
```

**Nincs n8n.** Az előző rendszer GitHub Actions + Python alapú volt, ezt tartjuk meg.  
**Cloudflare** csak DNS-t kezel, a hosting marad GitHub-on.

---

## Forrásstratégia

**Kizárólag Google News RSS** — minden forráshoz azonos URL-struktúra:
```
https://news.google.com/rss/search?q=site:{domain}+hungary&hl=en-US&gl=US&ceid=US:en
```

Előnyök:
- Egységes architektúra, nincs vegyes RSS kezelés
- Automatikusan szűr Hungary-témájú cikkekre
- 100 cikk/feed, jellemzően 5–44 órán belüli frissesség
- Ingyenes, rate limit nincs

---

## 25 forrás

| # | Forrás | Régió | Domain |
|---|--------|-------|--------|
| 1 | Reuters | wire | reuters.com |
| 2 | AP News | wire | apnews.com |
| 3 | BBC | uk | bbc.com |
| 4 | The Guardian | uk | theguardian.com |
| 5 | The Economist | uk | economist.com |
| 6 | Financial Times | uk | ft.com |
| 7 | The Times | uk | thetimes.com |
| 8 | New York Times | us | nytimes.com |
| 9 | Washington Post | us | washingtonpost.com |
| 10 | Foreign Policy | us | foreignpolicy.com |
| 11 | Wall Street Journal | us | wsj.com |
| 12 | Politico US | us | politico.com |
| 13 | The Atlantic | us | theatlantic.com |
| 14 | Politico Europe | eu | politico.eu |
| 15 | Euractiv | eu | euractiv.com |
| 16 | EUobserver | eu | euobserver.com |
| 17 | The Parliament Magazine | eu | theparliamentmagazine.eu |
| 18 | Euronews | eu | euronews.com |
| 19 | Le Monde (EN) | fr | lemonde.fr |
| 20 | DW English | de | dw.com |
| 21 | Süddeutsche | de | sueddeutsche.de |
| 22 | Visegrad Insight | cee | visegradinsight.eu |
| 23 | Balkan Insight | cee | balkaninsight.com |
| 24 | Notes from Poland | cee | notesfrompoland.com |
| 25 | ECFR | think | ecfr.eu |

---

## sources.json struktúra (config fájl)

```json
[
  {
    "name": "Reuters",
    "region": "wire",
    "domain": "reuters.com",
    "feed_url": "https://news.google.com/rss/search?q=site:reuters.com+hungary&hl=en-US&gl=US&ceid=US:en"
  }
]
```

---

## Modellek

| Feladat | Model | Indok |
|---------|-------|-------|
| Per-article elemzés | `claude-haiku-4-5-20251001` | Strukturált extrakció, olcsó ($1/$5 per MTok) |
| Napi összefoglaló (opcionális) | `claude-sonnet-4-6` | Szintetizáló szöveghez jobb |

**Batch API** használata javasolt az elemzésnél — 50% árengedmény, és az aszinkron feldolgozás belefér a 2 órás cron ablakba.

---

## Elemzési dimenziók (per article)

Minden cikknél a következőket kell kinyerni strukturált JSON-ban:

```json
{
  "article_id": "...",
  "source": "Reuters",
  "region": "wire",
  "published_at": "2026-04-18T10:00:00Z",
  "title": "...",
  "url": "...",
  "topics": ["government transition", "EU relations", "economy"],
  "actors": ["Magyar Péter", "Orbán Viktor", "von der Leyen"],
  "tone": "neutral",
  "framing": "democracy restoration",
  "summary_en": "2-3 mondatos összefoglaló angolul"
}
```

**tone** lehetséges értékek: `positive`, `neutral`, `critical`, `mixed`  
**framing** lehetséges értékek: `democracy`, `geopolitics`, `economy`, `eu_integration`, `regional`, `other`

---

## SQLite séma (javasolt)

```sql
CREATE TABLE articles (
  id TEXT PRIMARY KEY,          -- URL hash
  source TEXT,
  region TEXT,
  title TEXT,
  url TEXT UNIQUE,
  published_at TEXT,
  fetched_at TEXT,
  topics TEXT,                  -- JSON array
  actors TEXT,                  -- JSON array
  tone TEXT,
  framing TEXT,
  summary_en TEXT,
  analyzed INTEGER DEFAULT 0    -- 0=pending, 1=done
);

CREATE TABLE fetch_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT,
  fetched_at TEXT,
  new_articles INTEGER,
  status TEXT
);
```

---

## GitHub Actions

```yaml
name: Fetch and analyze
on:
  schedule:
    - cron: '0 */2 * * *'   # 2 óránként
  workflow_dispatch:          # manuális futtatás is

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python fetch.py
      - run: python analyze.py
      - run: python build_dashboard.py
      - name: Commit updated DB and dashboard
        run: |
          git config user.name "github-actions"
          git config user.email "actions@github.com"
          git add data/articles.db docs/
          git diff --staged --quiet || git commit -m "chore: update [$(date -u +%H:%M)]"
          git push
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

---

## Dashboard (GitHub Pages)

- `docs/` mappa → GitHub Pages
- Statikus HTML/JS, SQLite adatbázist JSON exportból olvassa
- `build_dashboard.py` generálja a JSON-t és a HTML-t minden futásnál

Javasolt nézetek:
- **Idővonal** — mikor mennyi cikk jelent meg Magyarországról
- **Forrás szerint** — melyik lap mennyit ír
- **Téma szerint** — topic tag-ek megoszlása
- **Tónus** — pozitív/semleges/kritikus arány forrásonként
- **Framing** — milyen keretben tárgyalják (demokrácia, geopolitika, gazdaság...)
- **Cikkek listája** — szűrhető, kereshető

---

## Repo struktúra (javasolt)

```
hungary-press-monitor/
├── CLAUDE.md               ← ez a fájl (vagy hivatkozás rá)
├── sources.json            ← 25 forrás config
├── fetch.py                ← RSS lekérés, deduplikáció, DB mentés
├── analyze.py              ← Claude Haiku elemzés
├── build_dashboard.py      ← JSON export + HTML generálás
├── requirements.txt
├── .github/
│   └── workflows/
│       └── monitor.yml
├── data/
│   └── articles.db         ← SQLite (gitignore-olható, vagy LFS)
└── docs/                   ← GitHub Pages
    ├── index.html
    ├── articles.json
    └── ...
```

---

## Fontos megjegyzések

- A Google News RSS **nem garantált forrás** — a Google bármikor változtathat a struktúrán vagy blokkolhatja a kéréseket. Érdemes figyelni a fetch hibákat és alertet küldeni ha egy forrás tartósan 0 találatot ad.
- A `site:` operator **nem szűr forrásonként** az API szintjén — a cikkek `source` mező alapján utólag kell validálni hogy tényleg az adott laptól jött-e.
- **Süddeutsche** gyenge lefedettséggel szerepel (9 találat a tesztben) — ha tartósan keveset hoz, érdemes cserélni.
- A **Batch API** 24 órás feldolgozási időt ígér, de jellemzően 1-2 órán belül végez — 2 órás cron mellé elfogadható.
- Az SQLite fájl gitben tárolva egyszerű, de 6+ hónap után érdemes archiválni vagy LFS-re váltani.
