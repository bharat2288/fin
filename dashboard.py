"""Generate an HTML spending dashboard from fin.db.

Queries the database, builds charts (Chart.js), and writes a standalone
HTML file that can be opened directly in a browser.

Usage:
    py dashboard.py              # Generate and open dashboard
    py dashboard.py --no-open    # Generate without opening
"""

import argparse
import json
import os
import webbrowser
from collections import defaultdict
from pathlib import Path

from db import get_connection, init_db

DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

# Category colors — warm palette that works on dark backgrounds.
# Ordered by typical spend magnitude so the biggest categories get
# the most distinguishable colors.
CATEGORY_COLORS = {
    "Groceries": "#7ab07a",       # soft green
    "Dining": "#d4a574",          # camel (accent)
    "Transport": "#6ba3be",       # slate blue
    "Shopping": "#cd8264",        # terracotta
    "Health & Beauty": "#b88bc4", # soft purple
    "Medical": "#cd6464",         # error red
    "Entertainment": "#e8c170",   # warm gold
    "Utilities": "#8faabe",       # muted blue
    "Subscriptions": "#a8c078",   # lime
    "Insurance": "#7a8fa6",       # steel
    "Loan/EMI": "#c97878",       # dusty rose
    "Travel": "#5db8a3",         # teal
    "Pet": "#d4a07a",            # warm tan
    "Home": "#9b8ec4",           # lavender
    "Personal": "#a0a0a0",       # grey
    "Education": "#6bc4b8",      # aqua
    "Fitness": "#c4a84e",        # olive gold
    "Kids": "#e8a0b0",          # pink
    "Gifts & Donations": "#b8a070", # khaki
    "Moom": "#787878",           # muted (business)
    "Other": "#585858",          # dark grey
}


def query_monthly_data(conn, personal_only: bool = False) -> dict:
    """Get monthly spending by category."""
    where = ["t.is_payment = 0", "t.is_transfer = 0", "t.amount_sgd > 0"]
    if personal_only:
        where.append("c.is_personal = 1")

    rows = conn.execute(f"""
        SELECT
            substr(t.date, 1, 7) as month,
            COALESCE(c.name, 'Other') as category,
            c.is_personal,
            SUM(t.amount_sgd) as total,
            COUNT(*) as txn_count,
            SUM(CASE WHEN t.is_anomaly = 1 THEN t.amount_sgd ELSE 0 END) as anomaly_total
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE {' AND '.join(where)}
        GROUP BY month, category
        ORDER BY month, total DESC
    """).fetchall()

    return [dict(r) for r in rows]


def query_subscriptions(conn) -> list[dict]:
    """Get active subscriptions with monthly equivalent."""
    rows = conn.execute("""
        SELECT s.service, s.amount_sgd, s.frequency, s.periods,
               s.card, s.status, c.name as category, c.is_personal
        FROM subscriptions s
        LEFT JOIN categories c ON s.category_id = c.id
        WHERE s.status = 'active'
        ORDER BY c.name, s.service
    """).fetchall()

    subs = []
    for r in rows:
        r = dict(r)
        # Calculate monthly equivalent
        if r["frequency"] == "monthly":
            r["monthly"] = r["amount_sgd"] / (r["periods"] or 1)
        elif r["frequency"] == "yearly":
            r["monthly"] = r["amount_sgd"] / (12 * (r["periods"] or 1))
        elif r["frequency"] == "quarterly":
            r["monthly"] = r["amount_sgd"] / (3 * (r["periods"] or 1))
        else:
            r["monthly"] = r["amount_sgd"]
        subs.append(r)

    return subs


def query_top_merchants(conn, limit: int = 15) -> list[dict]:
    """Get top merchants by total spend."""
    rows = conn.execute("""
        SELECT
            t.description,
            COALESCE(c.name, 'Other') as category,
            SUM(t.amount_sgd) as total,
            COUNT(*) as txn_count,
            ROUND(AVG(t.amount_sgd), 2) as avg_amount
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        WHERE t.is_payment = 0 AND t.is_transfer = 0 AND t.amount_sgd > 0
        GROUP BY t.description
        ORDER BY total DESC
        LIMIT ?
    """, (limit,)).fetchall()

    return [dict(r) for r in rows]


def build_chart_data(monthly_rows: list[dict]) -> dict:
    """Structure monthly data for Chart.js stacked bar chart."""
    # Collect all months and categories
    months = sorted(set(r["month"] for r in monthly_rows))
    categories = sorted(set(r["category"] for r in monthly_rows))

    # Build matrix: {category: {month: total}}
    matrix = defaultdict(lambda: defaultdict(float))
    for r in monthly_rows:
        matrix[r["category"]][r["month"]] = round(r["total"], 2)

    # Sort categories by total spend (descending) for better chart stacking
    cat_totals = {cat: sum(matrix[cat].values()) for cat in categories}
    categories = sorted(categories, key=lambda c: cat_totals[c], reverse=True)

    datasets = []
    for cat in categories:
        color = CATEGORY_COLORS.get(cat, "#666666")
        datasets.append({
            "label": cat,
            "data": [matrix[cat].get(m, 0) for m in months],
            "backgroundColor": color,
            "borderColor": color,
            "borderWidth": 1,
        })

    # Monthly totals for the trend line
    month_totals = []
    for m in months:
        total = sum(matrix[cat].get(m, 0) for cat in categories)
        month_totals.append(round(total, 2))

    return {
        "labels": months,
        "datasets": datasets,
        "totals": month_totals,
        "categories": categories,
        "cat_totals": {c: round(cat_totals[c], 2) for c in categories},
    }


def build_subscription_chart_data(subs: list[dict]) -> dict:
    """Structure subscription data for Chart.js donut chart."""
    # Group by category
    cat_totals = defaultdict(float)
    for s in subs:
        cat_totals[s["category"] or "Other"] += s["monthly"]

    # Sort by monthly amount descending
    sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)

    return {
        "labels": [c[0] for c in sorted_cats],
        "data": [round(c[1], 2) for c in sorted_cats],
        "colors": [CATEGORY_COLORS.get(c[0], "#666") for c in sorted_cats],
        "total": round(sum(c[1] for c in sorted_cats), 2),
    }


def generate_html(chart_data: dict, sub_chart: dict, subs: list[dict],
                   top_merchants: list[dict]) -> str:
    """Build the complete HTML dashboard."""

    # Summary stats
    months = chart_data["labels"]
    totals = chart_data["totals"]
    n_months = len(months)
    grand_total = sum(totals)
    avg_monthly = grand_total / n_months if n_months else 0
    latest_month = months[-1] if months else "N/A"
    latest_total = totals[-1] if totals else 0

    personal_sub_burn = sum(s["monthly"] for s in subs if s["is_personal"])
    moom_sub_burn = sum(s["monthly"] for s in subs if not s["is_personal"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>fin — spending dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,700&family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="chart.min.js"></script>
<style>
:root {{
  --bg-base: #0c0f0d;
  --bg-surface: #1a1d1b;
  --bg-raised: #252a27;
  --bg-elevated: #323832;
  --border-subtle: #2a2f2c;
  --border-default: #3a3f3c;
  --text-primary: #fafafa;
  --text-secondary: #a8a8a8;
  --text-tertiary: #787878;
  --text-muted: #585858;
  --accent-camel: #d4a574;
  --accent-camel-hover: #c9956c;
  --accent-terra: #cd8264;
  --semantic-success: #7ab07a;
  --semantic-error: #cd6464;
  --font-base: 'Geist', 'Plus Jakarta Sans', -apple-system, sans-serif;
  --font-display: 'Fraunces', Georgia, serif;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.3), 0 1px 3px rgba(0,0,0,0.2);
  --shadow-md: 0 4px 8px rgba(0,0,0,0.35), 0 2px 4px rgba(0,0,0,0.25);
  --shadow-lg: 0 12px 28px rgba(0,0,0,0.45), 0 8px 12px rgba(0,0,0,0.35);
}}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: var(--font-base);
  font-size: 14px;
  line-height: 1.5;
  padding: 40px;
  max-width: 1200px;
  margin: 0 auto;
}}

/* Character moment — page title */
h1 {{
  font-family: var(--font-display);
  font-size: 48px;
  font-weight: 700;
  line-height: 1.0;
  margin-bottom: 8px;
}}

.subtitle {{
  color: var(--text-tertiary);
  font-size: 14px;
  margin-bottom: 40px;
}}

/* Section labels — 12px tracked caps */
.label {{
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--accent-camel);
  margin-bottom: 16px;
}}

/* Cards */
.card {{
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: 12px;
  padding: 24px;
  box-shadow: var(--shadow-lg);
  position: relative;
}}

.card::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.06), transparent);
  border-radius: 12px 12px 0 0;
}}

/* Stats row */
.stats-grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 32px;
}}

.stat-value {{
  font-size: 28px;
  font-weight: 700;
  font-family: var(--font-display);
  line-height: 1.2;
}}

.stat-value.accent {{ color: var(--accent-camel); }}

.stat-label {{
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  margin-top: 4px;
}}

/* Charts grid */
.charts-grid {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-bottom: 32px;
}}

.chart-full {{ grid-column: 1 / -1; }}

.chart-container {{
  position: relative;
  width: 100%;
}}

/* Toggle controls */
.toggle-row {{
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}}

.toggle-btn {{
  padding: 6px 14px;
  background: var(--bg-base);
  border: 1px solid var(--border-subtle);
  border-radius: 6px;
  color: var(--text-secondary);
  font-family: var(--font-base);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: all 0.15s ease;
}}

.toggle-btn:hover {{
  background: var(--bg-raised);
  color: var(--text-primary);
}}

.toggle-btn.active {{
  background: linear-gradient(135deg, var(--accent-camel), var(--accent-camel-hover));
  color: var(--bg-base);
  border-color: transparent;
  box-shadow: var(--shadow-sm), 0 0 12px rgba(212,165,116,0.15);
}}

/* Data table */
.data-table {{
  width: 100%;
  border-collapse: collapse;
}}

.data-table th {{
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-default);
}}

.data-table td {{
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  font-size: 13px;
}}

.data-table td.amount {{
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  font-weight: 500;
}}

.data-table tr:hover td {{
  background: rgba(255,255,255,0.02);
}}

.color-dot {{
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
}}

/* Subscription list */
.sub-row {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-subtle);
}}

.sub-row:last-child {{ border-bottom: none; }}

.sub-name {{
  color: var(--text-secondary);
  font-size: 13px;
}}

.sub-amount {{
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}}

.sub-cat {{
  font-size: 11px;
  color: var(--text-muted);
  padding: 2px 8px;
  background: var(--bg-raised);
  border-radius: 4px;
}}
</style>
</head>
<body>

<h1>fin</h1>
<p class="subtitle">Personal finance dashboard &middot; {months[0]} to {months[-1]} &middot; {n_months} months</p>

<!-- Stats -->
<div class="stats-grid">
  <div class="card">
    <div class="stat-value accent">${latest_total:,.0f}</div>
    <div class="stat-label">{latest_month} spend</div>
  </div>
  <div class="card">
    <div class="stat-value">${avg_monthly:,.0f}</div>
    <div class="stat-label">Monthly average</div>
  </div>
  <div class="card">
    <div class="stat-value">${personal_sub_burn:,.0f}</div>
    <div class="stat-label">Subscription burn</div>
  </div>
  <div class="card">
    <div class="stat-value">${grand_total:,.0f}</div>
    <div class="stat-label">Total ({n_months} months)</div>
  </div>
</div>

<!-- Main chart: stacked bar -->
<div class="card chart-full" style="margin-bottom: 24px;">
  <div class="label">/ monthly spending by category</div>
  <div class="toggle-row">
    <button class="toggle-btn active" onclick="toggleAnomalies(this, true)">All spending</button>
    <button class="toggle-btn" onclick="toggleAnomalies(this, false)">Exclude anomalies</button>
    <button class="toggle-btn" onclick="togglePersonal(this)">Personal only</button>
  </div>
  <div class="chart-container" style="height: 400px;">
    <canvas id="monthlyChart"></canvas>
  </div>
</div>

<!-- Second row: trend + category breakdown -->
<div class="charts-grid">
  <div class="card">
    <div class="label">/ spending trend</div>
    <div class="chart-container" style="height: 280px;">
      <canvas id="trendChart"></canvas>
    </div>
  </div>
  <div class="card">
    <div class="label">/ category totals</div>
    <div class="chart-container" style="height: 280px;">
      <canvas id="categoryChart"></canvas>
    </div>
  </div>
</div>

<!-- Third row: subscriptions + top merchants -->
<div class="charts-grid">
  <div class="card">
    <div class="label">/ subscription burn &middot; ${sub_chart['total']:,.0f}/mo</div>
    <div style="display: flex; gap: 24px; align-items: flex-start;">
      <div class="chart-container" style="width: 180px; height: 180px; flex-shrink: 0;">
        <canvas id="subChart"></canvas>
      </div>
      <div style="flex: 1; max-height: 300px; overflow-y: auto;">
        {"".join(f'''<div class="sub-row">
          <div><span class="color-dot" style="background:{CATEGORY_COLORS.get(s['category'] or 'Other', '#666')}"></span><span class="sub-name">{s['service']}</span></div>
          <div><span class="sub-amount">${s['monthly']:,.0f}</span> <span class="sub-cat">{s['category'] or 'Other'}</span></div>
        </div>''' for s in sorted(subs, key=lambda x: x['monthly'], reverse=True) if s['status'] == 'active')}
      </div>
    </div>
  </div>
  <div class="card">
    <div class="label">/ top merchants</div>
    <table class="data-table">
      <thead>
        <tr><th>Merchant</th><th>Category</th><th style="text-align:right">Total</th><th style="text-align:right">Txns</th></tr>
      </thead>
      <tbody>
        {"".join(f'''<tr>
          <td><span class="color-dot" style="background:{CATEGORY_COLORS.get(m['category'], '#666')}"></span>{m['description'][:35]}</td>
          <td>{m['category']}</td>
          <td class="amount">${m['total']:,.0f}</td>
          <td class="amount">{m['txn_count']}</td>
        </tr>''' for m in top_merchants)}
      </tbody>
    </table>
  </div>
</div>

<script>
// Chart.js global defaults for our dark theme
Chart.defaults.color = '#a8a8a8';
Chart.defaults.borderColor = '#2a2f2c';
Chart.defaults.font.family = "'Geist', sans-serif";
Chart.defaults.font.size = 12;

const chartData = {json.dumps(chart_data)};
const subData = {json.dumps(sub_chart)};

// Stacked bar chart — monthly spending by category
const monthlyCtx = document.getElementById('monthlyChart').getContext('2d');
const monthlyChart = new Chart(monthlyCtx, {{
  type: 'bar',
  data: {{
    labels: chartData.labels,
    datasets: chartData.datasets
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{
        position: 'bottom',
        labels: {{
          usePointStyle: true,
          pointStyle: 'circle',
          padding: 12,
          font: {{ size: 11 }}
        }}
      }},
      tooltip: {{
        mode: 'index',
        intersect: false,
        backgroundColor: '#1a1d1b',
        borderColor: '#3a3f3c',
        borderWidth: 1,
        titleColor: '#fafafa',
        bodyColor: '#a8a8a8',
        padding: 12,
        callbacks: {{
          label: function(ctx) {{
            if (ctx.raw === 0) return null;
            return ctx.dataset.label + ': $' + ctx.raw.toLocaleString(undefined, {{maximumFractionDigits: 0}});
          }},
          footer: function(items) {{
            const total = items.reduce((sum, item) => sum + item.raw, 0);
            return 'Total: $' + total.toLocaleString(undefined, {{maximumFractionDigits: 0}});
          }}
        }}
      }}
    }},
    scales: {{
      x: {{
        stacked: true,
        grid: {{ display: false }},
        ticks: {{ font: {{ size: 11 }} }}
      }},
      y: {{
        stacked: true,
        grid: {{ color: '#1a1d1b' }},
        ticks: {{
          callback: v => '$' + (v/1000).toFixed(0) + 'k',
          font: {{ size: 11 }}
        }}
      }}
    }}
  }}
}});

// Trend line chart
const trendCtx = document.getElementById('trendChart').getContext('2d');
new Chart(trendCtx, {{
  type: 'line',
  data: {{
    labels: chartData.labels,
    datasets: [{{
      label: 'Monthly Total',
      data: chartData.totals,
      borderColor: '#d4a574',
      backgroundColor: 'rgba(212, 165, 116, 0.1)',
      borderWidth: 2,
      fill: true,
      tension: 0.3,
      pointBackgroundColor: '#d4a574',
      pointBorderColor: '#0c0f0d',
      pointBorderWidth: 2,
      pointRadius: 4,
      pointHoverRadius: 6
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        backgroundColor: '#1a1d1b',
        borderColor: '#3a3f3c',
        borderWidth: 1,
        callbacks: {{
          label: ctx => '$' + ctx.raw.toLocaleString(undefined, {{maximumFractionDigits: 0}})
        }}
      }}
    }},
    scales: {{
      x: {{ grid: {{ display: false }} }},
      y: {{
        grid: {{ color: '#1a1d1b' }},
        ticks: {{ callback: v => '$' + (v/1000).toFixed(0) + 'k' }}
      }}
    }}
  }}
}});

// Category donut
const catCtx = document.getElementById('categoryChart').getContext('2d');
const catLabels = Object.keys(chartData.cat_totals);
const catValues = Object.values(chartData.cat_totals);
const catColors = catLabels.map(c => {json.dumps(CATEGORY_COLORS)}[c] || '#666');

new Chart(catCtx, {{
  type: 'doughnut',
  data: {{
    labels: catLabels,
    datasets: [{{
      data: catValues,
      backgroundColor: catColors,
      borderColor: '#0c0f0d',
      borderWidth: 2
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    cutout: '55%',
    plugins: {{
      legend: {{
        position: 'right',
        labels: {{
          usePointStyle: true,
          pointStyle: 'circle',
          padding: 8,
          font: {{ size: 10 }},
          generateLabels: function(chart) {{
            const data = chart.data;
            const total = data.datasets[0].data.reduce((a, b) => a + b, 0);
            return data.labels.map((label, i) => ({{
              text: label + ' $' + data.datasets[0].data[i].toLocaleString(undefined, {{maximumFractionDigits: 0}}),
              fillStyle: data.datasets[0].backgroundColor[i],
              strokeStyle: 'transparent',
              pointStyle: 'circle',
              hidden: false,
              index: i
            }}));
          }}
        }}
      }},
      tooltip: {{
        backgroundColor: '#1a1d1b',
        borderColor: '#3a3f3c',
        borderWidth: 1,
        callbacks: {{
          label: function(ctx) {{
            const pct = (ctx.raw / catValues.reduce((a,b) => a+b, 0) * 100).toFixed(1);
            return ctx.label + ': $' + ctx.raw.toLocaleString(undefined, {{maximumFractionDigits: 0}}) + ' (' + pct + '%)';
          }}
        }}
      }}
    }}
  }}
}});

// Subscription donut
const subCtx = document.getElementById('subChart').getContext('2d');
new Chart(subCtx, {{
  type: 'doughnut',
  data: {{
    labels: subData.labels,
    datasets: [{{
      data: subData.data,
      backgroundColor: subData.colors,
      borderColor: '#0c0f0d',
      borderWidth: 2
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    cutout: '60%',
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        backgroundColor: '#1a1d1b',
        borderColor: '#3a3f3c',
        borderWidth: 1,
        callbacks: {{
          label: ctx => ctx.label + ': $' + ctx.raw.toLocaleString(undefined, {{maximumFractionDigits: 0}}) + '/mo'
        }}
      }}
    }}
  }}
}});

// Toggle handlers
function toggleAnomalies(btn, showAll) {{
  // Simple toggle — in a fuller version this would re-query data
  document.querySelectorAll('.toggle-row .toggle-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}}

function togglePersonal(btn) {{
  document.querySelectorAll('.toggle-row .toggle-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}}
</script>

</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate fin spending dashboard")
    parser.add_argument("--no-open", action="store_true", help="Don't open in browser")
    args = parser.parse_args()

    init_db()
    conn = get_connection()

    # Query all data
    monthly_rows = query_monthly_data(conn)
    subs = query_subscriptions(conn)
    top_merchants = query_top_merchants(conn)

    # Build chart structures
    chart_data = build_chart_data(monthly_rows)
    sub_chart = build_subscription_chart_data(subs)

    # Generate HTML
    html = generate_html(chart_data, sub_chart, subs, top_merchants)
    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard written to: {DASHBOARD_PATH}")

    if not args.no_open:
        webbrowser.open(str(DASHBOARD_PATH))
        print("Opened in browser.")

    conn.close()


if __name__ == "__main__":
    main()
