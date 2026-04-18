"""
Export SQLite data to JSON and generate the static bilingual dashboard.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data/articles.db")
DOCS_PATH = Path("docs")


def load_articles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT id, source, region, title, url, published_at, fetched_at,
               topics, actors, tone, framing, summary_en, title_hu, summary_hu, analyzed
        FROM articles
        ORDER BY published_at DESC
    """).fetchall()
    cols = ["id", "source", "region", "title", "url", "published_at", "fetched_at",
            "topics", "actors", "tone", "framing", "summary_en", "title_hu", "summary_hu", "analyzed"]
    articles = []
    for row in rows:
        a = dict(zip(cols, row))
        a["topics"] = json.loads(a["topics"] or "[]")
        a["actors"] = json.loads(a["actors"] or "[]")
        articles.append(a)
    return articles


def build_stats(articles: list[dict]) -> dict:
    analyzed = [a for a in articles if a["analyzed"]]

    tone_counts = Counter(a["tone"] for a in analyzed if a["tone"])
    framing_counts = Counter(a["framing"] for a in analyzed if a["framing"])
    region_counts = Counter(a["region"] for a in articles)
    source_counts = Counter(a["source"] for a in articles)

    topic_counts: Counter = Counter()
    for a in analyzed:
        topic_counts.update(a["topics"])

    daily: dict[str, int] = defaultdict(int)
    for a in articles:
        day = (a["published_at"] or "")[:10]
        if day:
            daily[day] += 1
    daily_sorted = dict(sorted(daily.items())[-30:])

    tone_by_source: dict[str, Counter] = defaultdict(Counter)
    for a in analyzed:
        if a["tone"]:
            tone_by_source[a["source"]][a["tone"]] += 1
    tone_by_source_out = {src: dict(counts) for src, counts in tone_by_source.items()}

    return {
        "total_articles": len(articles),
        "analyzed_articles": len(analyzed),
        "tone": dict(tone_counts),
        "framing": dict(framing_counts),
        "region": dict(region_counts),
        "source": dict(source_counts.most_common(25)),
        "topics": dict(topic_counts.most_common(20)),
        "daily": daily_sorted,
        "tone_by_source": tone_by_source_out,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def write_json(articles: list[dict], stats: dict) -> None:
    DOCS_PATH.mkdir(exist_ok=True)
    (DOCS_PATH / "articles.json").write_text(
        json.dumps(articles, ensure_ascii=False, indent=2)
    )
    (DOCS_PATH / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2)
    )


def generate_html(stats: dict, stats_json: str) -> str:
    last_updated = stats.get("last_updated", "")[:16].replace("T", " ") + " UTC"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hungary International Press Monitor</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a; color: #e2e8f0; margin: 0; padding: 0;
    }}
    header {{
      background: #1e293b; border-bottom: 1px solid #334155;
      padding: 1.25rem 2rem; display: flex; align-items: center; justify-content: space-between;
    }}
    header h1 {{ font-size: 1.25rem; margin: 0; color: #f8fafc; }}
    header .right {{ display: flex; align-items: center; gap: 1.25rem; }}
    header small {{ color: #94a3b8; font-size: 0.8rem; }}
    .lang-toggle {{
      display: flex; background: #0f172a; border-radius: 6px; overflow: hidden;
      border: 1px solid #334155;
    }}
    .lang-toggle button {{
      background: none; border: none; color: #94a3b8; padding: 0.3rem 0.75rem;
      font-size: 0.85rem; cursor: pointer; font-weight: 600;
    }}
    .lang-toggle button.active {{ background: #38bdf8; color: #0f172a; }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 2rem; }}
    .kpi-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 1rem; margin-bottom: 2rem;
    }}
    .kpi {{
      background: #1e293b; border: 1px solid #334155; border-radius: 8px;
      padding: 1.25rem; text-align: center;
    }}
    .kpi .value {{ font-size: 2rem; font-weight: 700; color: #38bdf8; }}
    .kpi .label {{ font-size: 0.8rem; color: #94a3b8; margin-top: 0.25rem; }}
    .charts-grid {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 1.5rem; margin-bottom: 2rem;
    }}
    .card {{
      background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 1.5rem;
    }}
    .card h2 {{
      font-size: 0.95rem; color: #94a3b8; margin: 0 0 1rem;
      text-transform: uppercase; letter-spacing: 0.05em;
    }}
    .chart-wrap {{ position: relative; height: 220px; }}
    .filters {{ display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1rem; }}
    .filters input, .filters select {{
      background: #1e293b; border: 1px solid #334155; color: #e2e8f0;
      border-radius: 6px; padding: 0.5rem 0.75rem; font-size: 0.875rem;
    }}
    .filters input {{ flex: 1; min-width: 200px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
    th {{ text-align: left; padding: 0.6rem 0.75rem; color: #94a3b8; border-bottom: 1px solid #334155; }}
    td {{ padding: 0.6rem 0.75rem; border-bottom: 1px solid #1e293b; vertical-align: top; }}
    tr:hover td {{ background: #1e3a5f22; }}
    a {{ color: #38bdf8; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{
      display: inline-block; padding: 0.15rem 0.5rem;
      border-radius: 999px; font-size: 0.72rem; font-weight: 600;
    }}
    .tone-positive {{ background: #14532d; color: #86efac; }}
    .tone-neutral   {{ background: #1e3a5f; color: #93c5fd; }}
    .tone-critical  {{ background: #450a0a; color: #fca5a5; }}
    .tone-mixed     {{ background: #3b1f00; color: #fcd34d; }}
    .tag {{
      display: inline-block; background: #334155; color: #cbd5e1;
      border-radius: 4px; padding: 0.1rem 0.4rem; font-size: 0.72rem; margin: 0.1rem;
    }}
    #pagination {{ display: flex; gap: 0.5rem; justify-content: center; margin-top: 1rem; flex-wrap: wrap; }}
    #pagination button {{
      background: #334155; border: none; color: #e2e8f0;
      padding: 0.4rem 0.8rem; border-radius: 4px; cursor: pointer;
    }}
    #pagination button.active {{ background: #38bdf8; color: #0f172a; }}
  </style>
</head>
<body>
<header>
  <h1 id="siteTitle">Hungary International Press Monitor</h1>
  <div class="right">
    <small>Updated: {last_updated}</small>
    <div class="lang-toggle">
      <button id="btnEN" class="active" onclick="setLang('en')">EN</button>
      <button id="btnHU" onclick="setLang('hu')">HU</button>
    </div>
  </div>
</header>
<div class="container">
  <div class="kpi-grid" id="kpis"></div>
  <div class="charts-grid">
    <div class="card">
      <h2 data-en="Articles per Day (last 30 days)" data-hu="Napi cikkszám (elmúlt 30 nap)">Articles per Day (last 30 days)</h2>
      <div class="chart-wrap"><canvas id="dailyChart"></canvas></div>
    </div>
    <div class="card">
      <h2 data-en="Tone Distribution" data-hu="Hangnem megoszlása">Tone Distribution</h2>
      <div class="chart-wrap"><canvas id="toneChart"></canvas></div>
    </div>
    <div class="card">
      <h2 data-en="Framing" data-hu="Keretezés">Framing</h2>
      <div class="chart-wrap"><canvas id="framingChart"></canvas></div>
    </div>
    <div class="card">
      <h2 data-en="Top Topics" data-hu="Leggyakoribb témák">Top Topics</h2>
      <div class="chart-wrap"><canvas id="topicsChart"></canvas></div>
    </div>
    <div class="card">
      <h2 data-en="Articles by Region" data-hu="Cikkek régiónként">Articles by Region</h2>
      <div class="chart-wrap"><canvas id="regionChart"></canvas></div>
    </div>
    <div class="card">
      <h2 data-en="Top Sources" data-hu="Legtöbbet publikáló lapok">Top Sources</h2>
      <div class="chart-wrap"><canvas id="sourcesChart"></canvas></div>
    </div>
  </div>

  <div class="card">
    <h2 id="articlesHeading" data-en="Articles" data-hu="Cikkek">Articles</h2>
    <div class="filters">
      <input type="text" id="searchInput" placeholder="Search...">
      <select id="toneFilter">
        <option value="" data-en="All tones" data-hu="Minden hangnem">All tones</option>
        <option value="positive" data-en="Positive" data-hu="Pozitív">Positive</option>
        <option value="neutral" data-en="Neutral" data-hu="Semleges">Neutral</option>
        <option value="critical" data-en="Critical" data-hu="Kritikus">Critical</option>
        <option value="mixed" data-en="Mixed" data-hu="Vegyes">Mixed</option>
      </select>
      <select id="regionFilter"><option value="">–</option></select>
      <select id="sourcFilter"><option value="">–</option></select>
    </div>
    <table>
      <thead>
        <tr>
          <th data-en="Date" data-hu="Dátum">Date</th>
          <th data-en="Source" data-hu="Forrás">Source</th>
          <th data-en="Title" data-hu="Cím">Title</th>
          <th data-en="Tone" data-hu="Hangnem">Tone</th>
          <th data-en="Topics" data-hu="Témák">Topics</th>
        </tr>
      </thead>
      <tbody id="articlesBody"></tbody>
    </table>
    <div id="pagination"></div>
  </div>
</div>

<script>
const STATS = __STATS_JSON__;
const PAGE_SIZE = 50;
let allArticles = [];
let filteredArticles = [];
let currentPage = 1;
let lang = 'en';

const TONE_COLORS = {{
  positive: '#4ade80', neutral: '#60a5fa', critical: '#f87171', mixed: '#fbbf24'
}};
const PALETTE = ['#38bdf8','#818cf8','#34d399','#fb923c','#a78bfa','#f472b6','#facc15','#2dd4bf'];

const I18N = {{
  siteTitle: {{ en: 'Hungary International Press Monitor', hu: 'Magyarország a Nemzetközi Sajtóban' }},
  searchPlaceholder: {{ en: 'Search title, source, summary…', hu: 'Keresés cím, forrás, összefoglaló…' }},
  kpi: {{
    total: {{ en: 'Total Articles', hu: 'Összes cikk' }},
    analyzed: {{ en: 'Analyzed', hu: 'Elemzett' }},
    sources: {{ en: 'Sources', hu: 'Forrás' }},
    critical: {{ en: 'Critical', hu: 'Kritikus' }},
    positive: {{ en: 'Positive', hu: 'Pozitív' }},
  }},
  tone: {{
    positive: {{ en: 'positive', hu: 'pozitív' }},
    neutral: {{ en: 'neutral', hu: 'semleges' }},
    critical: {{ en: 'critical', hu: 'kritikus' }},
    mixed: {{ en: 'mixed', hu: 'vegyes' }},
  }},
}};

function t(obj) {{ return obj[lang] || obj.en; }}

function setLang(l) {{
  lang = l;
  document.getElementById('btnEN').classList.toggle('active', l === 'en');
  document.getElementById('btnHU').classList.toggle('active', l === 'hu');
  document.getElementById('siteTitle').textContent = t(I18N.siteTitle);
  document.getElementById('searchInput').placeholder = t(I18N.searchPlaceholder);
  document.querySelectorAll('[data-en]').forEach(el => {{
    el.textContent = el.getAttribute('data-' + l) || el.getAttribute('data-en');
  }});
  renderKPIs();
  renderTable();
}}

function renderKPIs() {{
  const el = document.getElementById('kpis');
  const kpis = [
    [STATS.total_articles, t(I18N.kpi.total)],
    [STATS.analyzed_articles, t(I18N.kpi.analyzed)],
    [Object.keys(STATS.source).length, t(I18N.kpi.sources)],
    [STATS.tone.critical || 0, t(I18N.kpi.critical)],
    [STATS.tone.positive || 0, t(I18N.kpi.positive)],
  ];
  el.innerHTML = kpis.map(([v,l]) =>
    `<div class="kpi"><div class="value">${{v}}</div><div class="label">${{l}}</div></div>`
  ).join('');
}}

function makeChart(id, type, labels, data, colors) {{
  new Chart(document.getElementById(id), {{
    type,
    data: {{ labels, datasets: [{{ data, backgroundColor: colors || PALETTE, borderColor: '#0f172a', borderWidth: 1 }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ display: type === 'doughnut', labels: {{ color: '#94a3b8', boxWidth: 12 }} }} }},
      scales: type !== 'doughnut' ? {{
        x: {{ ticks: {{ color: '#64748b', maxRotation: 45 }}, grid: {{ color: '#1e293b' }} }},
        y: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#334155' }} }}
      }} : {{}},
    }}
  }});
}}

function renderCharts() {{
  makeChart('dailyChart', 'bar', Object.keys(STATS.daily), Object.values(STATS.daily));
  makeChart('toneChart', 'doughnut',
    Object.keys(STATS.tone), Object.values(STATS.tone),
    Object.keys(STATS.tone).map(t => TONE_COLORS[t] || '#94a3b8'));
  makeChart('framingChart', 'doughnut', Object.keys(STATS.framing), Object.values(STATS.framing));
  const topTopics = Object.entries(STATS.topics).slice(0, 10);
  makeChart('topicsChart', 'bar', topTopics.map(([k]) => k), topTopics.map(([,v]) => v));
  makeChart('regionChart', 'doughnut', Object.keys(STATS.region), Object.values(STATS.region));
  const sources = Object.entries(STATS.source).slice(0, 10);
  makeChart('sourcesChart', 'bar', sources.map(([k]) => k), sources.map(([,v]) => v));
}}

function populateFilters() {{
  const regions = [...new Set(allArticles.map(a => a.region).filter(Boolean))].sort();
  const sources = [...new Set(allArticles.map(a => a.source).filter(Boolean))].sort();
  document.getElementById('regionFilter').innerHTML =
    `<option value="">– region –</option>` +
    regions.map(r => `<option value="${{r}}">${{r}}</option>`).join('');
  document.getElementById('sourcFilter').innerHTML =
    `<option value="">– source –</option>` +
    sources.map(s => `<option value="${{s}}">${{s}}</option>`).join('');
}}

function applyFilters() {{
  const q = document.getElementById('searchInput').value.toLowerCase();
  const tone = document.getElementById('toneFilter').value;
  const region = document.getElementById('regionFilter').value;
  const source = document.getElementById('sourcFilter').value;

  filteredArticles = allArticles.filter(a => {{
    const titleField = lang === 'hu' && a.title_hu ? a.title_hu : a.title;
    const summaryField = lang === 'hu' && a.summary_hu ? a.summary_hu : a.summary_en;
    if (q && !`${{titleField}} ${{a.source}} ${{summaryField}}`.toLowerCase().includes(q)) return false;
    if (tone && a.tone !== tone) return false;
    if (region && a.region !== region) return false;
    if (source && a.source !== source) return false;
    return true;
  }});
  currentPage = 1;
  renderTable();
}}

function toneLabel(tone) {{
  return lang === 'hu' ? (I18N.tone[tone]?.hu || tone) : tone;
}}

function renderTable() {{
  const start = (currentPage - 1) * PAGE_SIZE;
  const page = filteredArticles.slice(start, start + PAGE_SIZE);
  const tbody = document.getElementById('articlesBody');
  tbody.innerHTML = page.map(a => {{
    const title = lang === 'hu' && a.title_hu ? a.title_hu : a.title;
    const summary = lang === 'hu' && a.summary_hu ? a.summary_hu : a.summary_en;
    return `
    <tr>
      <td style="white-space:nowrap;color:#64748b">${{(a.published_at||'').slice(0,10)}}</td>
      <td style="white-space:nowrap">${{a.source}}</td>
      <td><a href="${{a.url}}" target="_blank" rel="noopener">${{title}}</a>
        ${{summary ? `<div style="color:#64748b;font-size:0.8rem;margin-top:0.25rem">${{summary}}</div>` : ''}}
      </td>
      <td><span class="badge tone-${{a.tone}}">${{toneLabel(a.tone)||'—'}}</span></td>
      <td>${{(a.topics||[]).map(t => `<span class="tag">${{t}}</span>`).join('')}}</td>
    </tr>`;
  }}).join('');
  renderPagination();
}}

function renderPagination() {{
  const totalPages = Math.ceil(filteredArticles.length / PAGE_SIZE);
  const el = document.getElementById('pagination');
  if (totalPages <= 1) {{ el.innerHTML = ''; return; }}
  const pages = [];
  for (let i = 1; i <= totalPages; i++) {{
    pages.push(`<button class="${{i === currentPage ? 'active' : ''}}" onclick="gotoPage(${{i}})">${{i}}</button>`);
  }}
  el.innerHTML = pages.join('');
}}

function gotoPage(p) {{
  currentPage = p;
  renderTable();
  window.scrollTo({{top: 0, behavior: 'smooth'}});
}}

async function init() {{
  const resp = await fetch('articles.json');
  allArticles = await resp.json();
  filteredArticles = allArticles;
  renderKPIs();
  renderCharts();
  populateFilters();
  renderTable();
  document.getElementById('searchInput').addEventListener('input', applyFilters);
  document.getElementById('toneFilter').addEventListener('change', applyFilters);
  document.getElementById('regionFilter').addEventListener('change', applyFilters);
  document.getElementById('sourcFilter').addEventListener('change', applyFilters);
}}

init();
</script>
</body>
</html>"""


def main() -> None:
    if not DB_PATH.exists():
        print("No database found. Run fetch.py first.")
        return

    conn = sqlite3.connect(DB_PATH)

    # Add new columns if DB predates this version
    existing = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    for col in ["title_hu", "summary_hu"]:
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} TEXT")
    conn.commit()

    articles = load_articles(conn)
    conn.close()

    stats = build_stats(articles)
    write_json(articles, stats)

    stats_json = json.dumps(stats, ensure_ascii=False)
    html = generate_html(stats, stats_json).replace("__STATS_JSON__", stats_json)
    DOCS_PATH.mkdir(exist_ok=True)
    (DOCS_PATH / "index.html").write_text(html, encoding="utf-8")

    print(f"Dashboard built. Articles: {stats['total_articles']}, Analyzed: {stats['analyzed_articles']}")
    print(f"Output: {DOCS_PATH}/index.html")


if __name__ == "__main__":
    main()
