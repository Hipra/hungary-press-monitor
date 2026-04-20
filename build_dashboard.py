"""
Export SQLite data to JSON and generate the static bilingual dashboard.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("data/articles.db")
DOCS_PATH = Path("docs")


def load_articles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT id, source, region, title, url, published_at, fetched_at,
               topics, actors, tone, framing, summary_en, title_hu, summary_hu, analyzed,
               is_relevant,
               COALESCE(main_actor, 'other') as main_actor,
               COALESCE(comparison_countries, '[]') as comparison_countries,
               COALESCE(quotes, '[]') as quotes
        FROM articles
        WHERE analyzed = 1 AND is_relevant = 1
        ORDER BY published_at DESC
    """).fetchall()
    cols = ["id", "source", "region", "title", "url", "published_at", "fetched_at",
            "topics", "actors", "tone", "framing", "summary_en", "title_hu", "summary_hu",
            "analyzed", "is_relevant", "main_actor", "comparison_countries", "quotes"]
    articles = []
    for row in rows:
        a = dict(zip(cols, row))
        a["topics"] = json.loads(a["topics"] or "[]")
        a["actors"] = json.loads(a["actors"] or "[]")
        a["comparison_countries"] = json.loads(a["comparison_countries"] or "[]")
        a["quotes"] = json.loads(a["quotes"] or "[]")
        articles.append(a)
    return articles


def build_stats(articles: list[dict]) -> dict:
    # All articles here are already analyzed=1 AND is_relevant=1 (filtered in load_articles)
    relevant = articles
    analyzed = articles

    tone_counts = Counter(a["tone"] for a in analyzed if a["tone"])
    framing_counts = Counter(a["framing"] for a in analyzed if a["framing"])
    region_counts = Counter(a["region"] for a in relevant)
    source_counts = Counter(a["source"] for a in relevant)

    topic_counts: Counter = Counter()
    actor_counts: Counter = Counter()
    main_actor_counts: Counter = Counter()
    comparison_counts: Counter = Counter()
    for a in analyzed:
        topic_counts.update(a["topics"])
        actor_counts.update(a["actors"])
        if a.get("main_actor"):
            main_actor_counts[a["main_actor"]] += 1
        comparison_counts.update(a.get("comparison_countries", []))

    daily: dict[str, int] = defaultdict(int)
    for a in relevant:
        day = (a["published_at"] or "")[:10]
        if day:
            daily[day] += 1
    daily_sorted = dict(sorted(daily.items())[-30:])

    tone_by_source: dict[str, Counter] = defaultdict(Counter)
    for a in analyzed:
        if a["tone"]:
            tone_by_source[a["source"]][a["tone"]] += 1
    tone_by_source_out = {src: dict(counts) for src, counts in tone_by_source.items()}

    now = datetime.now(timezone.utc)

    # Source bias: dominant tone per source (for stacked bar)
    source_bias: dict[str, dict] = {}
    for src, counts in tone_by_source.items():
        total = sum(counts.values())
        if total >= 3:
            source_bias[src] = {t: round(c / total * 100) for t, c in counts.items()}

    # Coverage gaps: topics seen in articles older than 14 days but not in last 14 days
    cutoff_gap = (now - timedelta(days=14)).isoformat()
    recent14 = [a for a in analyzed if (a["published_at"] or "") >= cutoff_gap]
    older = [a for a in analyzed if (a["published_at"] or "") < cutoff_gap]
    recent_topics: set[str] = set()
    for a in recent14:
        recent_topics.update(a["topics"])
    gap_topics = []
    old_topic_counts: Counter = Counter()
    for a in older:
        old_topic_counts.update(a["topics"])
    for topic, cnt in old_topic_counts.most_common():
        if topic not in recent_topics and cnt >= 3:
            gap_topics.append({"topic": topic, "last_seen_count": cnt})

    return {
        "total_articles": len(relevant),
        "analyzed_articles": len(analyzed),
        "tone": dict(tone_counts),
        "framing": dict(framing_counts),
        "region": dict(region_counts),
        "source": dict(source_counts.most_common(25)),
        "topics": dict(topic_counts.most_common(20)),
        "actors": dict(actor_counts.most_common(20)),
        "main_actor": dict(main_actor_counts),
        "comparison_countries": dict(comparison_counts.most_common(15)),
        "daily": daily_sorted,
        "tone_by_source": tone_by_source_out,
        "source_bias": source_bias,
        "coverage_gaps": gap_topics[:10],
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
  <title>Hungary Press Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: #0f172a; color: #e2e8f0; margin: 0; padding: 0; }}
    a {{ color: #38bdf8; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}

    /* Layout */
    .layout {{ display: flex; min-height: 100vh; }}
    .sidebar {{
      width: 260px; flex-shrink: 0;
      background: #1e293b; border-right: 1px solid #334155;
      position: sticky; top: 0; height: 100vh; overflow-y: auto;
      display: flex; flex-direction: column; padding: 1.25rem 1rem; gap: 1.25rem;
    }}
    .main {{ flex: 1; min-width: 0; padding: 1.75rem 2rem; overflow: hidden; }}
    .main-inner {{ max-width: 900px; margin: 0 auto; }}

    /* Sidebar elements */
    .sidebar-brand {{ font-size: 0.95rem; font-weight: 700; color: #f8fafc; line-height: 1.3; }}
    .sidebar-brand small {{ display: block; font-size: 0.72rem; font-weight: 400; color: #64748b; margin-top: 0.2rem; }}
    .lang-toggle {{ display: flex; background: #0f172a; border-radius: 6px; overflow: hidden; border: 1px solid #334155; align-self: flex-start; }}
    .lang-toggle button {{ background: none; border: none; color: #94a3b8; padding: 0.3rem 0.8rem; font-size: 0.82rem; cursor: pointer; font-weight: 600; }}
    .lang-toggle button.active {{ background: #38bdf8; color: #0f172a; }}
    .sidebar-label {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.07em; color: #475569; font-weight: 600; }}
    .sidebar-filter {{ display: flex; flex-direction: column; gap: 0.5rem; }}
    .sidebar-filter input,
    .sidebar-filter select {{
      background: #0f172a; border: 1px solid #334155; color: #e2e8f0;
      border-radius: 6px; padding: 0.45rem 0.65rem; font-size: 0.82rem; width: 100%;
    }}
    .kpi-list {{ display: flex; flex-direction: column; gap: 0.4rem; }}
    .kpi-row {{ display: flex; justify-content: space-between; align-items: center;
      font-size: 0.82rem; padding: 0.35rem 0; border-bottom: 1px solid #1e293b; }}
    .kpi-row .kv {{ font-weight: 700; color: #38bdf8; font-size: 1rem; font-family: 'JetBrains Mono', monospace; }}

    /* Cards */
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 1.25rem; margin-bottom: 1.25rem; }}
    .card-title {{ font-size: 0.78rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; margin: 0 0 0.9rem; font-family: 'JetBrains Mono', monospace; }}

    /* Digest */
    .digest-top {{ font-size: 1rem; font-weight: 600; color: #f8fafc; margin-bottom: 0.9rem; line-height: 1.45; }}
    .digest-section {{ font-size: 0.78rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; margin: 0.8rem 0 0.3rem; font-family: 'JetBrains Mono', monospace; }}
    .digest-bullets {{ margin: 0 0 0.25rem 1.1rem; padding: 0; }}
    .digest-bullets li {{ margin: 0.2rem 0; font-size: 0.875rem; line-height: 1.5; }}
    .digest-quote {{ color: #a78bfa; font-style: italic; border-left: 2px solid #334155;
      padding-left: 0.6rem; margin: 0.3rem 0; font-size: 0.85rem; }}
    .digest-meta {{ font-size: 0.75rem; color: #475569; margin-bottom: 0.75rem; }}

    /* Nav */
    .nav {{ display: flex; flex-direction: column; gap: 0.15rem; }}
    .nav-item {{
      display: flex; align-items: center; gap: 0.6rem;
      padding: 0.55rem 0.75rem; border-radius: 6px; cursor: pointer;
      font-size: 0.875rem; color: #94a3b8; border: none; background: none;
      text-align: left; width: 100%; transition: background 0.1s;
    }}
    .nav-item:hover {{ background: #0f172a; color: #e2e8f0; }}
    .nav-item.active {{ background: #0f172a; color: #38bdf8; font-weight: 600; }}
    .nav-icon {{ font-size: 0.9rem; width: 1rem; text-align: center; }}

    /* Sections */
    .section {{ display: none; }}
    .section.active {{ display: block; }}

    /* Charts */
    .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1.25rem; margin-bottom: 1.25rem; }}
    .chart-wrap {{ position: relative; height: 200px; }}

    /* Day groups */
    .day-group {{ margin-bottom: 2rem; }}
    .day-header {{
      font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em;
      color: #475569; padding: 0.4rem 0; border-bottom: 1px solid #1e3a5f;
      margin-bottom: 0.75rem; display: flex; align-items: center; gap: 0.6rem;
      font-family: 'JetBrains Mono', monospace;
    }}
    .day-header .day-count {{ background: #1e3a5f; color: #60a5fa; border-radius: 999px;
      padding: 0.1rem 0.55rem; font-size: 0.7rem; font-weight: 600; }}

    /* Article cards */
    .article-item {{ display: flex; gap: 0.75rem; padding: 0.75rem 0; border-bottom: 1px solid #1e293b; }}
    .article-item:last-child {{ border-bottom: none; }}
    .article-time {{ font-size: 0.75rem; color: #475569; white-space: nowrap; padding-top: 0.15rem; min-width: 38px; font-family: 'JetBrains Mono', monospace; }}
    .article-body {{ flex: 1; min-width: 0; }}
    .article-source {{ font-size: 0.72rem; color: #64748b; margin-bottom: 0.2rem; font-family: 'JetBrains Mono', monospace; }}
    .article-title {{ font-size: 0.9rem; font-weight: 500; color: #e2e8f0; line-height: 1.4; margin-bottom: 0.3rem; font-family: 'JetBrains Mono', monospace; }}
    .article-title a {{ color: inherit; }}
    .article-title a:hover {{ color: #38bdf8; }}
    .article-summary {{ font-size: 0.8rem; color: #64748b; line-height: 1.5; margin-bottom: 0.35rem; }}
    .article-meta {{ display: flex; flex-wrap: wrap; gap: 0.3rem; align-items: center; }}
    .badge {{ display: inline-block; padding: 0.12rem 0.45rem; border-radius: 999px; font-size: 0.68rem; font-weight: 600; font-family: 'JetBrains Mono', monospace; }}
    .tone-positive {{ background: #14532d; color: #86efac; }}
    .tone-neutral   {{ background: #1e3a5f; color: #93c5fd; }}
    .tone-critical  {{ background: #450a0a; color: #fca5a5; }}
    .tone-mixed     {{ background: #3b1f00; color: #fcd34d; }}
    .tag {{ display: inline-block; background: #1e293b; border: 1px solid #334155; color: #94a3b8;
      border-radius: 4px; padding: 0.1rem 0.4rem; font-size: 0.68rem; font-family: 'JetBrains Mono', monospace; }}
    .tag-actor {{ background: #0f2744; border-color: #1e3a5f; color: #7dd3fc; }}

    #pagination button, #pagination .pg-active {{
      background: #1e293b; border: 1px solid #334155; color: #e2e8f0;
      padding: 0.35rem 0.7rem; border-radius: 5px; cursor: pointer; font-size: 0.82rem;
    }}
    #pagination button:disabled {{ opacity: 0.3; cursor: default; }}
    #pagination .pg-active {{ background: #38bdf8; color: #0f172a; border-color: #38bdf8; font-weight: 700; }}

    @media (max-width: 768px) {{
      .layout {{ flex-direction: column; }}
      .sidebar {{ width: 100%; height: auto; position: static; flex-direction: row; flex-wrap: wrap; }}
      .main {{ padding: 1rem; }}
    }}
  </style>
</head>
<body>
<div class="layout">

  <!-- SIDEBAR -->
  <aside class="sidebar">
    <div>
      <div class="sidebar-brand" id="siteTitle">Hungary Press Monitor
        <small>Updated: {last_updated}</small>
      </div>
    </div>
    <div class="lang-toggle">
      <button id="btnEN" class="active" onclick="setLang('en')">EN</button>
      <button id="btnHU" onclick="setLang('hu')">HU</button>
    </div>
    <nav class="nav">
      <button class="nav-item active" id="nav-digest" onclick="showSection('digest')">
        <span class="nav-icon">📋</span>
        <span data-en="Daily Digest" data-hu="Napi digest">Daily Digest</span>
      </button>
      <button class="nav-item" id="nav-stats" onclick="showSection('stats')">
        <span class="nav-icon">📊</span>
        <span data-en="Statistics" data-hu="Statisztikák">Statistics</span>
      </button>
      <button class="nav-item" id="nav-articles" onclick="showSection('articles')">
        <span class="nav-icon">📰</span>
        <span data-en="Articles" data-hu="Cikkek">Articles</span>
      </button>
    </nav>
    <div>
      <div class="sidebar-label" data-en="Overview" data-hu="Áttekintés">Overview</div>
      <div class="kpi-list" id="kpis"></div>
    </div>
  </aside>

  <!-- MAIN -->
  <main class="main">

    <!-- SECTION: Digest -->
    <div class="section active" id="section-digest">
      <div id="digestCard" style="display:none">
        <div id="digestBody"></div>
      </div>
      <div id="digestEmpty" style="color:#475569;padding:2rem 0;display:none">
        <span data-en="No digest available yet. Run the pipeline to generate one." data-hu="Még nincs digest. Futtasd a pipeline-t a generáláshoz.">No digest available yet.</span>
      </div>
    </div>
    </div>

    <!-- SECTION: Statistics (full width) -->
    <div class="section" id="section-stats">
      <div class="charts-grid">
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Articles per Day" data-hu="Napi cikkszám">Articles per Day</div>
          <div class="chart-wrap"><canvas id="dailyChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Tone Distribution" data-hu="Hangnem megoszlása">Tone Distribution</div>
          <div class="chart-wrap"><canvas id="toneChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Framing" data-hu="Keretezés">Framing</div>
          <div class="chart-wrap"><canvas id="framingChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Top Topics" data-hu="Leggyakoribb témák">Top Topics</div>
          <div class="chart-wrap"><canvas id="topicsChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Articles by Region" data-hu="Régiónként">Articles by Region</div>
          <div class="chart-wrap"><canvas id="regionChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Top Sources" data-hu="Legtöbbet publikáló lapok">Top Sources</div>
          <div class="chart-wrap"><canvas id="sourcesChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Top Actors" data-hu="Legtöbbet említett szereplők">Top Actors</div>
          <div class="chart-wrap"><canvas id="actorsChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Main Actor Focus" data-hu="Főszereplő megoszlása">Main Actor Focus</div>
          <div class="chart-wrap"><canvas id="mainActorChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Countries Compared to Hungary" data-hu="Összehasonlított országok">Countries Compared to Hungary</div>
          <div class="chart-wrap"><canvas id="comparisonChart"></canvas></div>
        </div>
        <div class="card" style="margin:0">
          <div class="card-title" data-en="Source Bias (tone %)" data-hu="Forrás hangnem-profil (%)">Source Bias (tone %)</div>
          <div class="chart-wrap" style="height:260px"><canvas id="sourceBiasChart"></canvas></div>
        </div>
      </div>
    </div>

    <!-- SECTION: Articles -->
    <div class="main-inner">
    <div class="section" id="section-articles">
      <div style="display:flex;flex-direction:column;gap:0.5rem;margin-bottom:1.25rem">
        <input type="text" id="searchInput" placeholder="Search…"
          style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:0.5rem 0.75rem;font-size:0.875rem">
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
          <select id="toneFilter" style="flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:0.45rem 0.65rem;font-size:0.82rem">
            <option value="" data-en="All tones" data-hu="Minden hangnem">All tones</option>
            <option value="positive" data-en="Positive" data-hu="Pozitív">Positive</option>
            <option value="neutral" data-en="Neutral" data-hu="Semleges">Neutral</option>
            <option value="critical" data-en="Critical" data-hu="Kritikus">Critical</option>
            <option value="mixed" data-en="Mixed" data-hu="Vegyes">Mixed</option>
          </select>
          <select id="regionFilter" style="flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:0.45rem 0.65rem;font-size:0.82rem"><option value="">– region –</option></select>
          <select id="sourceFilter" style="flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;border-radius:6px;padding:0.45rem 0.65rem;font-size:0.82rem"><option value="">– source –</option></select>
        </div>
      </div>
      <div id="articlesContainer"></div>
      <div id="pagination"></div>
    </div>
    </div>

  </main>
</div>

<script>
const STATS = __STATS_JSON__;
let allArticles = [];
let filteredArticles = [];
let lang = 'en';
let currentPage = 1;
let pageSize = 20;

const TONE_COLORS = {{ positive:'#4ade80', neutral:'#60a5fa', critical:'#f87171', mixed:'#fbbf24' }};
const PALETTE = ['#38bdf8','#818cf8','#34d399','#fb923c','#a78bfa','#f472b6','#facc15','#2dd4bf'];

const I18N = {{
  siteTitle: {{ en:'Hungary Press Monitor', hu:'Magyarország a Nemzetközi Sajtóban' }},
  searchPlaceholder: {{ en:'Search…', hu:'Keresés…' }},
  kpi: {{
    total: {{ en:'Articles', hu:'Cikk' }},
    sources: {{ en:'Sources', hu:'Forrás' }},
    critical: {{ en:'Critical', hu:'Kritikus' }},
    positive: {{ en:'Positive', hu:'Pozitív' }},
    neutral: {{ en:'Neutral', hu:'Semleges' }},
  }},
  tone: {{
    positive: {{ en:'positive', hu:'pozitív' }},
    neutral: {{ en:'neutral', hu:'semleges' }},
    critical: {{ en:'critical', hu:'kritikus' }},
    mixed: {{ en:'mixed', hu:'vegyes' }},
  }},
}};

function L(en, hu) {{ return lang === 'hu' ? hu : en; }}

function showSection(name) {{
  ['digest','stats','articles'].forEach(s => {{
    document.getElementById('section-' + s).classList.toggle('active', s === name);
    document.getElementById('nav-' + s).classList.toggle('active', s === name);
  }});
  localStorage.setItem('section', name);
}}
function toneLabel(t) {{ return lang === 'hu' ? (I18N.tone[t]?.hu || t) : t; }}

function setLang(l) {{
  lang = l;
  localStorage.setItem('lang', l);
  document.getElementById('btnEN').classList.toggle('active', l === 'en');
  document.getElementById('btnHU').classList.toggle('active', l === 'hu');
  document.getElementById('siteTitle').childNodes[0].textContent = L(I18N.siteTitle.en, I18N.siteTitle.hu);
  document.getElementById('searchInput').placeholder = L(I18N.searchPlaceholder.en, I18N.searchPlaceholder.hu);
  document.querySelectorAll('[data-en]').forEach(el => {{
    el.textContent = el.getAttribute('data-' + l) || el.getAttribute('data-en');
  }});
  renderKPIs();
  renderDigest();
  renderArticles();
}}

function renderKPIs() {{
  document.getElementById('kpis').innerHTML = [
    [STATS.total_articles, L(I18N.kpi.total.en, I18N.kpi.total.hu)],
    [Object.keys(STATS.source).length, L(I18N.kpi.sources.en, I18N.kpi.sources.hu)],
    [STATS.tone.positive || 0, L(I18N.kpi.positive.en, I18N.kpi.positive.hu)],
    [STATS.tone.neutral || 0, L(I18N.kpi.neutral.en, I18N.kpi.neutral.hu)],
    [STATS.tone.critical || 0, L(I18N.kpi.critical.en, I18N.kpi.critical.hu)],
  ].map(([v,lbl]) => `<div class="kpi-row"><span>${{lbl}}</span><span class="kv">${{v}}</span></div>`).join('');
}}

function makeChart(id, type, labels, data, colors) {{
  new Chart(document.getElementById(id), {{
    type,
    data: {{ labels, datasets: [{{ data, backgroundColor: colors || PALETTE, borderColor:'#0f172a', borderWidth:1 }}] }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      plugins: {{ legend: {{ display: type==='doughnut', labels: {{ color:'#94a3b8', boxWidth:10, font:{{size:11}} }} }} }},
      scales: type !== 'doughnut' ? {{
        x: {{ ticks:{{ color:'#64748b', maxRotation:45, font:{{size:10}} }}, grid:{{ color:'#1e293b' }} }},
        y: {{ ticks:{{ color:'#64748b', font:{{size:10}} }}, grid:{{ color:'#334155' }} }}
      }} : {{}},
    }}
  }});
}}

function renderCharts() {{
  makeChart('dailyChart','bar', Object.keys(STATS.daily), Object.values(STATS.daily));
  makeChart('toneChart','doughnut', Object.keys(STATS.tone), Object.values(STATS.tone),
    Object.keys(STATS.tone).map(t => TONE_COLORS[t]||'#94a3b8'));
  makeChart('framingChart','doughnut', Object.keys(STATS.framing), Object.values(STATS.framing));
  const topics = Object.entries(STATS.topics).slice(0,10);
  makeChart('topicsChart','bar', topics.map(([k])=>k), topics.map(([,v])=>v));
  makeChart('regionChart','doughnut', Object.keys(STATS.region), Object.values(STATS.region));
  const srcs = Object.entries(STATS.source).slice(0,10);
  makeChart('sourcesChart','bar', srcs.map(([k])=>k), srcs.map(([,v])=>v));
  const actors = Object.entries(STATS.actors||{{}}).slice(0,15);
  makeChart('actorsChart','bar', actors.map(([k])=>k), actors.map(([,v])=>v));
  const AL = {{ magyar_peter:'Magyar Péter', orban_viktor:'Orbán Viktor', fidesz:'Fidesz',
    hungary_country:'Magyarország', eu_institutions:'EU intézmények', other:'egyéb' }};
  const ma = STATS.main_actor||{{}};
  makeChart('mainActorChart','doughnut', Object.keys(ma).map(k=>AL[k]||k), Object.values(ma));
  const cc = Object.entries(STATS.comparison_countries||{{}}).slice(0,12);
  makeChart('comparisonChart','bar', cc.map(([k])=>k), cc.map(([,v])=>v), ['#34d399']);
  const bias = STATS.source_bias||{{}};
  const bLabels = Object.keys(bias).slice(0,15);
  if (bLabels.length) {{
    const tones = ['positive','neutral','mixed','critical'];
    const tc = {{ positive:'#4ade80', neutral:'#60a5fa', mixed:'#fbbf24', critical:'#f87171' }};
    new Chart(document.getElementById('sourceBiasChart'), {{
      type:'bar',
      data:{{ labels:bLabels, datasets:tones.map(t=>({{ label:t, data:bLabels.map(s=>bias[s][t]||0), backgroundColor:tc[t] }})) }},
      options:{{ responsive:true, maintainAspectRatio:false,
        scales:{{
          x:{{ stacked:true, ticks:{{ color:'#64748b', maxRotation:45, font:{{size:10}} }}, grid:{{ color:'#1e293b' }} }},
          y:{{ stacked:true, ticks:{{ color:'#64748b' }}, grid:{{ color:'#334155' }}, max:100 }}
        }},
        plugins:{{ legend:{{ labels:{{ color:'#94a3b8', boxWidth:10, font:{{size:11}} }} }} }}
      }}
    }});
  }}
}}

function localTime(iso) {{
  return new Date(iso).toLocaleTimeString([], {{ hour:'2-digit', minute:'2-digit' }});
}}

function localDayKey(iso) {{
  const d = new Date(iso);
  return d.getFullYear() + '-' +
    String(d.getMonth()+1).padStart(2,'0') + '-' +
    String(d.getDate()).padStart(2,'0');
}}

function formatDayHeader(dayKey) {{
  const d = new Date(dayKey + 'T12:00:00');
  return d.toLocaleDateString(lang === 'hu' ? 'hu-HU' : 'en-GB', {{
    weekday:'long', year:'numeric', month:'long', day:'numeric'
  }});
}}

function groupByDay(articles) {{
  const groups = {{}};
  for (const a of articles) {{
    const key = localDayKey(a.published_at || '');
    if (!groups[key]) groups[key] = [];
    groups[key].push(a);
  }}
  return Object.entries(groups)
    .sort(([a],[b]) => b.localeCompare(a))
    .map(([day, arts]) => ({{ day, articles: arts.sort((a,b)=>b.published_at.localeCompare(a.published_at)) }}));
}}

function renderPagination() {{
  const total = filteredArticles.length;
  const totalPages = Math.ceil(total / pageSize);
  const el = document.getElementById('pagination');
  if (totalPages <= 1) {{ el.innerHTML = ''; return; }}
  const from = (currentPage-1)*pageSize+1, to = Math.min(currentPage*pageSize, total);
  const pages = [];
  for (let i = 1; i <= totalPages; i++) {{
    if (i === 1 || i === totalPages || Math.abs(i - currentPage) <= 2)
      pages.push(`<button class="${{i===currentPage?'pg-active':''}}" onclick="gotoPage(${{i}})">${{i}}</button>`);
    else if (Math.abs(i - currentPage) === 3)
      pages.push(`<span style="color:#475569;padding:0 0.25rem">…</span>`);
  }}
  el.innerHTML = `<div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;justify-content:center;margin-top:1.5rem">
    <button onclick="gotoPage(${{currentPage-1}})" ${{currentPage===1?'disabled':''}}>‹</button>
    ${{pages.join('')}}
    <button onclick="gotoPage(${{currentPage+1}})" ${{currentPage===totalPages?'disabled':''}}>›</button>
    <span style="color:#475569;font-size:0.78rem;margin-left:0.75rem">${{from}}–${{to}} / ${{total}}</span>
    <select id="pageSizeSelect" style="background:#1e293b;border:1px solid #334155;color:#e2e8f0;border-radius:5px;padding:0.3rem 0.5rem;font-size:0.78rem;margin-left:0.5rem">
      <option value="10" ${{pageSize===10?'selected':''}}>10</option>
      <option value="20" ${{pageSize===20?'selected':''}}>20</option>
      <option value="50" ${{pageSize===50?'selected':''}}>50</option>
    </select>
  </div>`;
  document.getElementById('pageSizeSelect').addEventListener('change', e => {{
    pageSize = parseInt(e.target.value);
    localStorage.setItem('pageSize', pageSize);
    currentPage = 1;
    renderArticles();
  }});
}}

function gotoPage(p) {{
  const totalPages = Math.ceil(filteredArticles.length / pageSize);
  currentPage = Math.max(1, Math.min(p, totalPages));
  renderArticles();
  document.getElementById('articlesContainer').scrollIntoView({{ behavior:'smooth', block:'start' }});
}}

function renderArticles() {{
  const paged = filteredArticles.slice((currentPage-1)*pageSize, currentPage*pageSize);
  const groups = groupByDay(paged);
  const container = document.getElementById('articlesContainer');
  if (!groups.length) {{
    container.innerHTML = `<div style="color:#475569;padding:2rem 0;text-align:center">${{L('No articles found.','Nincs találat.')}}</div>`;
    return;
  }}
  container.innerHTML = groups.map(g => `
    <div class="day-group">
      <div class="day-header">
        ${{formatDayHeader(g.day)}}
        <span class="day-count">${{g.articles.length}}</span>
      </div>
      ${{g.articles.map(a => {{
        const title = lang === 'hu' && a.title_hu ? a.title_hu : a.title;
        const summary = lang === 'hu' && a.summary_hu ? a.summary_hu : a.summary_en;
        const quotes = (a.quotes||[]).filter(q => typeof q === 'string' && q.length > 0);
        return `<div class="article-item">
          <div class="article-time">${{a.published_at ? localTime(a.published_at) : ''}}</div>
          <div class="article-body">
            <div class="article-source">${{a.source}} <span style="color:#334155">·</span> ${{a.region}}</div>
            <div class="article-title"><a href="${{a.url}}" target="_blank" rel="noopener">${{title}}</a></div>
            ${{summary ? `<div class="article-summary">${{summary}}</div>` : ''}}
            ${{quotes.length ? quotes.map(q=>`<div class="digest-quote" style="margin:0.25rem 0">"${{q}}"</div>`).join('') : ''}}
            <div class="article-meta">
              <span class="badge tone-${{a.tone}}">${{toneLabel(a.tone)||''}}</span>
              ${{(a.topics||[]).map(t=>`<span class="tag">${{t}}</span>`).join('')}}
              ${{(a.actors||[]).map(ac=>`<span class="tag tag-actor">${{ac}}</span>`).join('')}}
            </div>
          </div>
        </div>`;
      }}).join('')}}
    </div>
  `).join('');
  renderPagination();
}}

function populateFilters() {{
  const regions = [...new Set(allArticles.map(a=>a.region).filter(Boolean))].sort();
  const sources = [...new Set(allArticles.map(a=>a.source).filter(Boolean))].sort();
  document.getElementById('regionFilter').innerHTML =
    `<option value="">– region –</option>` + regions.map(r=>`<option value="${{r}}">${{r}}</option>`).join('');
  document.getElementById('sourceFilter').innerHTML =
    `<option value="">– source –</option>` + sources.map(s=>`<option value="${{s}}">${{s}}</option>`).join('');
}}

function applyFilters() {{
  const q = document.getElementById('searchInput').value.toLowerCase();
  const tone = document.getElementById('toneFilter').value;
  const region = document.getElementById('regionFilter').value;
  const source = document.getElementById('sourceFilter').value;
  filteredArticles = allArticles.filter(a => {{
    const tf = lang==='hu' && a.title_hu ? a.title_hu : a.title;
    const sf = lang==='hu' && a.summary_hu ? a.summary_hu : a.summary_en;
    if (q && !`${{tf}} ${{a.source}} ${{sf||''}}`.toLowerCase().includes(q)) return false;
    if (tone && a.tone !== tone) return false;
    if (region && a.region !== region) return false;
    if (source && a.source !== source) return false;
    return true;
  }});
  currentPage = 1;
  renderArticles();
}}

let DIGEST = null;

function renderDigest() {{
  const el = document.getElementById('digestCard');
  const empty = document.getElementById('digestEmpty');
  if (!DIGEST) {{
    el.style.display='none';
    if (empty) empty.style.display='';
    return;
  }}
  el.style.display='';
  if (empty) empty.style.display='none';
  const suf = lang==='hu' ? '_hu' : '_en';
  const top = DIGEST['top_story'+suf]||'';
  const devs = DIGEST['key_developments'+suf]||[];
  const shifts = DIGEST['narrative_shifts'+suf]||[];
  const quotes = DIGEST['quotes'+suf]||[];
  const watch = DIGEST['what_to_watch'+suf]||[];
  const bullets = arr => arr.length
    ? `<ul class="digest-bullets">${{arr.map(x=>`<li>${{x}}</li>`).join('')}}</ul>` : '';
  const quoteBlock = quotes.map(q=>
    `<div class="digest-quote">"${{q.quote||''}}" <span style="color:#64748b;font-style:normal;font-size:0.78rem">— ${{q.speaker||''}}</span></div>`
  ).join('');
  document.getElementById('digestBody').innerHTML = `
    <div class="digest-meta">${{DIGEST.date||''}} · ${{DIGEST.article_count||0}} ${{L('articles','cikk')}}</div>
    <div class="digest-top">${{top}}</div>
    <div class="digest-section">${{L('Key developments','Főbb fejlemények')}}</div>
    ${{bullets(devs)}}
    ${{shifts.length ? `<div class="digest-section">${{L('Narrative shifts','Narratíva-váltások')}}</div>${{bullets(shifts)}}` : ''}}
    ${{quotes.length ? `<div class="digest-section">${{L('Quotes of the day','Napi idézetek')}}</div>${{quoteBlock}}` : ''}}
    <div class="digest-section">${{L('What to watch','Figyelendő')}}</div>
    ${{bullets(watch)}}
  `;
}}

async function init() {{
  const savedLang = localStorage.getItem('lang');
  if (savedLang) lang = savedLang;
  const savedPageSize = parseInt(localStorage.getItem('pageSize'));
  if (savedPageSize) pageSize = savedPageSize;
  showSection(localStorage.getItem('section') || 'digest');
  const resp = await fetch('articles.json');
  allArticles = await resp.json();
  filteredArticles = allArticles;
  try {{
    const dr = await fetch('digest.json');
    if (dr.ok) DIGEST = await dr.json();
  }} catch(e) {{ DIGEST = null; }}
  renderCharts();
  populateFilters();
  setLang(lang);
  document.getElementById('searchInput').addEventListener('input', applyFilters);
  document.getElementById('toneFilter').addEventListener('change', applyFilters);
  document.getElementById('regionFilter').addEventListener('change', applyFilters);
  document.getElementById('sourceFilter').addEventListener('change', applyFilters);
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
    for col, typedef in [
        ("title_hu", "TEXT"), ("summary_hu", "TEXT"),
        ("is_relevant", "INTEGER DEFAULT 1"),
        ("main_actor", "TEXT"), ("comparison_countries", "TEXT"),
        ("quotes", "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typedef}")
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
