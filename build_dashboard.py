"""
Export SQLite data to JSON and generate the static GitHub Pages dashboard.
"""

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
               topics, actors, tone, framing, summary_en, analyzed
        FROM articles
        ORDER BY published_at DESC
    """).fetchall()
    cols = ["id", "source", "region", "title", "url", "published_at", "fetched_at",
            "topics", "actors", "tone", "framing", "summary_en", "analyzed"]
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

    # Articles per day (last 30 days)
    daily: dict[str, int] = defaultdict(int)
    for a in articles:
        day = (a["published_at"] or "")[:10]
        if day:
            daily[day] += 1
    daily_sorted = dict(sorted(daily.items())[-30:])

    # Tone per source
    tone_by_source: dict[str, Counter] = defaultdict(Counter)
    for a in analyzed:
        if a["tone"]:
            tone_by_source[a["source"]][a["tone"]] += 1
    tone_by_source_out = {
        src: dict(counts) for src, counts in tone_by_source.items()
    }

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


def generate_html(stats: dict) -> str:
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
      background: #0f172a;
      color: #e2e8f0;
      margin: 0;
      padding: 0;
    }}
    header {{
      background: #1e293b;
      border-bottom: 1px solid #334155;
      padding: 1.25rem 2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    header h1 {{ font-size: 1.25rem; margin: 0; color: #f8fafc; }}
    header small {{ color: #94a3b8; font-size: 0.8rem; }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 2rem; }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }}
    .kpi {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 1.25rem;
      text-align: center;
    }}
    .kpi .value {{ font-size: 2rem; font-weight: 700; color: #38bdf8; }}
    .kpi .label {{ font-size: 0.8rem; color: #94a3b8; margin-top: 0.25rem; }}
    .charts-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 1.5rem;
      margin-bottom: 2rem;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 1.5rem;
    }}
    .card h2 {{ font-size: 0.95rem; color: #94a3b8; margin: 0 0 1rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .chart-wrap {{ position: relative; height: 220px; }}
    .filters {{
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
      margin-bottom: 1rem;
    }}
    .filters input, .filters select {{
      background: #1e293b;
      border: 1px solid #334155;
      color: #e2e8f0;
      border-radius: 6px;
      padding: 0.5rem 0.75rem;
      font-size: 0.875rem;
    }}
    .filters input {{ flex: 1; min-width: 200px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
    th {{ text-align: left; padding: 0.6rem 0.75rem; color: #94a3b8; border-bottom: 1px solid #334155; }}
    td {{ padding: 0.6rem 0.75rem; border-bottom: 1px solid #1e293b; vertical-align: top; }}
    tr:hover td {{ background: #1e3a5f22; }}
    a {{ color: #38bdf8; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 600;
    }}
    .tone-positive {{ background: #14532d; color: #86efac; }}
    .tone-neutral   {{ background: #1e3a5f; color: #93c5fd; }}
    .tone-critical  {{ background: #450a0a; color: #fca5a5; }}
    .tone-mixed     {{ background: #3b1f00; color: #fcd34d; }}
    .tag {{
      display: inline-block;
      background: #334155;
      color: #cbd5e1;
      border-radius: 4px;
      padding: 0.1rem 0.4rem;
      font-size: 0.72rem;
      margin: 0.1rem;
    }}
    #pagination {{ display: flex; gap: 0.5rem; justify-content: center; margin-top: 1rem; }}
    #pagination button {{
      background: #334155;
      border: none;
      color: #e2e8f0;
      padding: 0.4rem 0.8rem;
      border-radius: 4px;
      cursor: pointer;
    }}
    #pagination button.active {{ background: #38bdf8; color: #0f172a; }}
  </style>
</head>
<body>
<header>
  <h1>Hungary International Press Monitor</h1>
  <small>Updated: {last_updated}</small>
</header>
<div class="container">

  <div class="kpi-grid" id="kpis"></div>

  <div class="charts-grid">
    <div class="card">
      <h2>Articles per Day (last 30 days)</h2>
      <div class="chart-wrap"><canvas id="dailyChart"></canvas></div>
    </div>
    <div class="card">
      <h2>Tone Distribution</h2>
      <div class="chart-wrap"><canvas id="toneChart"></canvas></div>
    </div>
    <div class="card">
      <h2>Framing</h2>
      <div class="chart-wrap"><canvas id="framingChart"></canvas></div>
    </div>
    <div class="card">
      <h2>Top Topics</h2>
      <div class="chart-wrap"><canvas id="topicsChart"></canvas></div>
    </div>
    <div class="card">
      <h2>Articles by Region</h2>
      <div class="chart-wrap"><canvas id="regionChart"></canvas></div>
    </div>
    <div class="card">
      <h2>Top Sources</h2>
      <div class="chart-wrap"><canvas id="sourcesChart"></canvas></div>
    </div>
  </div>

  <div class="card">
    <h2>Articles</h2>
    <div class="filters">
      <input type="text" id="searchInput" placeholder="Search title, source, summary...">
      <select id="toneFilter">
        <option value="">All tones</option>
        <option value="positive">Positive</option>
        <option value="neutral">Neutral</option>
        <option value="critical">Critical</option>
        <option value="mixed">Mixed</option>
      </select>
      <select id="regionFilter"><option value="">All regions</option></select>
      <select id="sourcFilter"><option value="">All sources</option></select>
    </div>
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Source</th>
          <th>Title</th>
          <th>Tone</th>
          <th>Topics</th>
        </tr>
      </thead>
      <tbody id="articlesBody"></tbody>
    </table>
    <div id="pagination"></div>
  </div>
</div>

<script>
const STATS = {stats_json};
const PAGE_SIZE = 50;
let allArticles = [];
let filteredArticles = [];
let currentPage = 1;

const TONE_COLORS = {{
  positive: '#4ade80', neutral: '#60a5fa', critical: '#f87171', mixed: '#fbbf24'
}};
const PALETTE = ['#38bdf8','#818cf8','#34d399','#fb923c','#a78bfa','#f472b6','#facc15','#2dd4bf'];

function renderKPIs() {{
  const el = document.getElementById('kpis');
  const kpis = [
    [STATS.total_articles, 'Total Articles'],
    [STATS.analyzed_articles, 'Analyzed'],
    [Object.keys(STATS.source).length, 'Sources'],
    [STATS.tone.critical || 0, 'Critical'],
    [STATS.tone.positive || 0, 'Positive'],
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
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: type === 'doughnut', labels: {{ color: '#94a3b8', boxWidth: 12 }} }} }},
      scales: type !== 'doughnut' ? {{
        x: {{ ticks: {{ color: '#64748b', maxRotation: 45 }}, grid: {{ color: '#1e293b' }} }},
        y: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#334155' }} }}
      }} : {{}},
    }}
  }});
}}

function renderCharts() {{
  const daily = STATS.daily;
  makeChart('dailyChart', 'bar', Object.keys(daily), Object.values(daily));

  const tone = STATS.tone;
  makeChart('toneChart', 'doughnut',
    Object.keys(tone), Object.values(tone),
    Object.keys(tone).map(t => TONE_COLORS[t] || '#94a3b8'));

  const framing = STATS.framing;
  makeChart('framingChart', 'doughnut', Object.keys(framing), Object.values(framing));

  const topics = STATS.topics;
  const topTopics = Object.entries(topics).slice(0, 10);
  makeChart('topicsChart', 'bar',
    topTopics.map(([k]) => k), topTopics.map(([,v]) => v));

  const region = STATS.region;
  makeChart('regionChart', 'doughnut', Object.keys(region), Object.values(region));

  const sources = Object.entries(STATS.source).slice(0, 10);
  makeChart('sourcesChart', 'bar',
    sources.map(([k]) => k), sources.map(([,v]) => v));
}}

function populateFilters() {{
  const regions = [...new Set(allArticles.map(a => a.region).filter(Boolean))].sort();
  const sources = [...new Set(allArticles.map(a => a.source).filter(Boolean))].sort();
  document.getElementById('regionFilter').innerHTML +=
    regions.map(r => `<option value="${{r}}">${{r}}</option>`).join('');
  document.getElementById('sourcFilter').innerHTML +=
    sources.map(s => `<option value="${{s}}">${{s}}</option>`).join('');
}}

function applyFilters() {{
  const q = document.getElementById('searchInput').value.toLowerCase();
  const tone = document.getElementById('toneFilter').value;
  const region = document.getElementById('regionFilter').value;
  const source = document.getElementById('sourcFilter').value;

  filteredArticles = allArticles.filter(a => {{
    if (q && !`${{a.title}} ${{a.source}} ${{a.summary_en}}`.toLowerCase().includes(q)) return false;
    if (tone && a.tone !== tone) return false;
    if (region && a.region !== region) return false;
    if (source && a.source !== source) return false;
    return true;
  }});
  currentPage = 1;
  renderTable();
}}

function renderTable() {{
  const start = (currentPage - 1) * PAGE_SIZE;
  const page = filteredArticles.slice(start, start + PAGE_SIZE);
  const tbody = document.getElementById('articlesBody');
  tbody.innerHTML = page.map(a => `
    <tr>
      <td style="white-space:nowrap;color:#64748b">${{(a.published_at||'').slice(0,10)}}</td>
      <td style="white-space:nowrap">${{a.source}}</td>
      <td><a href="${{a.url}}" target="_blank" rel="noopener">${{a.title}}</a>
        ${{a.summary_en ? `<div style="color:#64748b;font-size:0.8rem;margin-top:0.25rem">${{a.summary_en}}</div>` : ''}}
      </td>
      <td><span class="badge tone-${{a.tone}}">${{a.tone||'—'}}</span></td>
      <td>${{(a.topics||[]).map(t => `<span class="tag">${{t}}</span>`).join('')}}</td>
    </tr>
  `).join('');

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
    articles = load_articles(conn)
    conn.close()

    stats = build_stats(articles)
    write_json(articles, stats)

    stats_json = json.dumps(stats, ensure_ascii=False)
    html = generate_html(stats).replace("{stats_json}", stats_json)
    DOCS_PATH.mkdir(exist_ok=True)
    (DOCS_PATH / "index.html").write_text(html, encoding="utf-8")

    print(f"Dashboard built. Articles: {stats['total_articles']}, Analyzed: {stats['analyzed_articles']}")
    print(f"Output: {DOCS_PATH}/index.html")


if __name__ == "__main__":
    main()
