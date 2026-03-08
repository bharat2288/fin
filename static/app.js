/* fin — app.js
   Frontend logic for Dashboard, Import, Subscriptions, Services, and Masters tabs */

// ============================================================
// STATE
// ============================================================

let categories = [];          // [{id, name, parent_id, parent_name, display_name, is_personal}, ...]
let accounts = [];            // [{id, name, ...}, ...]
let currentImportId = null;   // Active import preview
let currentImportData = null; // Preview data from upload
let monthlyChart = null;      // Chart.js instance
let categoryChart = null;     // Chart.js instance
let txCurrentPage = 1;
let spendFilter = 'personal';  // 'all', 'personal', 'moom' — default to personal
let selectedFiles = [];
let txSortCol = 'date';       // Current sort column
let txSortDir = 'desc';       // Current sort direction
let showSubcategories = false; // Charts: roll up to parent by default
let chartMode = 'bar';         // 'bar' or 'trend' for monthly chart
let lastMonthlyData = null;    // Cache for chart mode toggle
let lastGranularity = null;    // Cache for chart mode toggle
let subsFilter = 'active';     // 'active', 'all', 'deactivated'
let subsSpend = 'personal';    // 'personal', 'all', 'moom'
let allSubs = [];              // Cached subscription data
let subsSortCol = 'service';   // Current sort column
let subsSortAsc = true;        // Sort direction
let subsFxRate = 1.35;         // USD→SGD rate from subscriptions API
let noteModalTxId = null;      // Transaction ID being edited in note modal
let noteModalIconEl = null;    // Icon element to update after save
let ruleModalCatId = null;     // Category ID for rule creation modal

// Chart-table linking state
let chartFilter = { categories: [], period: null, source: null };
let monthlyPeriods = [];       // Raw period strings for chart click lookup
let catFilterSelections = [];  // Multi-select category filter selections

// Category color palette (warm neutrals matching design system)
const CAT_COLORS = [
    '#d4a574', '#cd8264', '#7ab07a', '#6b9bd2', '#c97a7a',
    '#9b8ec4', '#d4c074', '#74b8b8', '#c48fb0', '#8bc474',
    '#b89474', '#7a8fc9', '#c9b67a', '#74a0c4', '#c47a9b',
    '#787878', '#a8a8a8', '#585858', '#4a4f4c', '#323832',
    '#d4a574', '#cd8264',
];

const MONTH_NAMES = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// ============================================================
// INIT
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {
    initTabs();
    await loadReferenceData();

    // Restore tab from URL hash, or default to dashboard
    const hashTab = location.hash.replace('#', '');
    const startTab = hashTab || 'dashboard';
    history.replaceState({ tab: startTab }, '', '#' + startTab);
    if (startTab !== 'dashboard') {
        switchTab(startTab, { pushHistory: false });
    } else {
        await loadDashboard();
    }
});

async function loadReferenceData() {
    const [catRes, acctRes] = await Promise.all([
        fetch('/api/categories').then(r => r.json()),
        fetch('/api/accounts').then(r => r.json()),
    ]);
    categories = catRes;
    accounts = acctRes;

    populateCategoryDropdowns();
    populateAccountFilter();
    populateYearDropdown();
}

function populateCategoryDropdowns() {
    // Populate rule-category select (standard dropdown)
    const ruleSel = document.getElementById('rule-category');
    if (ruleSel) {
        const current = ruleSel.value;
        ruleSel.innerHTML = '';
        const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
        parents.forEach(p => {
            ruleSel.innerHTML += `<option value="${p.id}">${p.name}</option>`;
            const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
            children.forEach(c => {
                ruleSel.innerHTML += `<option value="${c.id}">&nbsp;&nbsp;${p.name} > ${c.name}</option>`;
            });
        });
        if (current) ruleSel.value = current;
    }

    // Populate multi-select category filter
    populateCatMultiSelect();
}

function populateAccountFilter() {
    // Dashboard account filter
    ['filter-account'].forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">All Accounts</option>';
        accounts.forEach(a => {
            sel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
        });
        if (current) sel.value = current;
    });

    // Subscription card dropdowns (add form + edit modal) — use account_id as value
    ['sub-card', 'edit-sub-card'].forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">—</option>';
        accounts.forEach(a => {
            sel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
        });
        if (current) sel.value = current;
    });
}

function populateYearDropdown() {
    // Fetch date range from transactions to populate year dropdown
    fetch('/api/transactions?per_page=1&sort=date&sort_dir=asc').then(r => r.json()).then(oldest => {
        fetch('/api/transactions?per_page=1&sort=date&sort_dir=desc').then(r => r.json()).then(newest => {
            const sel = document.getElementById('filter-year');
            sel.innerHTML = '<option value="">All Years</option>';

            if (oldest.transactions.length && newest.transactions.length) {
                const startYear = parseInt(oldest.transactions[0].date.substring(0, 4));
                const endYear = parseInt(newest.transactions[0].date.substring(0, 4));
                for (let y = endYear; y >= startYear; y--) {
                    sel.innerHTML += `<option value="${y}">${y}</option>`;
                }
            }
        });
    });
}

// ============================================================
// TABS
// ============================================================

function initTabs() {
    // Main tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // Import icon button
    document.querySelector('.tab-icon-btn[data-tab="import"]')?.addEventListener('click', () => {
        switchTab('import');
    });

    // Masters dropdown toggle
    document.getElementById('masters-toggle').addEventListener('click', (e) => {
        e.stopPropagation();
        document.getElementById('masters-menu').classList.toggle('open');
    });

    // Close masters dropdown when clicking outside
    document.addEventListener('click', () => {
        document.getElementById('masters-menu').classList.remove('open');
    });

    // Masters menu items switch tabs
    document.querySelectorAll('.masters-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.stopPropagation();
            const tab = item.dataset.tab;
            switchTab(tab);
            document.getElementById('masters-menu').classList.remove('open');
        });
    });
}

// Centralized tab switching — called by main tabs, icon buttons, and masters items
function switchTab(tabName, {pushHistory = true} = {}) {
    // Deactivate all main tab buttons
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    // Deactivate all icon buttons
    document.querySelectorAll('.tab-icon-btn').forEach(b => b.classList.remove('active'));
    // Deactivate all masters items
    document.querySelectorAll('.masters-item').forEach(b => b.classList.remove('active'));
    // Hide all tab content
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    // Show the target tab content
    const tabEl = document.getElementById('tab-' + tabName);
    if (tabEl) tabEl.classList.add('active');

    // Highlight the correct nav element
    const mainBtn = document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
    if (mainBtn) {
        // It's a main tab
        mainBtn.classList.add('active');
    } else if (tabName === 'import') {
        // Import icon button
        document.querySelector('.tab-icon-btn[data-tab="import"]')?.classList.add('active');
    } else {
        // Masters sub-tab — highlight the gear icon and the menu item
        document.getElementById('masters-toggle')?.classList.add('active');
        const mastersItem = document.querySelector(`.masters-item[data-tab="${tabName}"]`);
        if (mastersItem) mastersItem.classList.add('active');
    }

    // Push browser history so back button works
    if (pushHistory) {
        history.pushState({ tab: tabName }, '', '#' + tabName);
    }

    // Load tab data on switch
    if (tabName === 'dashboard') loadDashboard();
    if (tabName === 'import') loadHistory();
    if (tabName === 'subs') loadSubscriptions();
    if (tabName === 'services') renderServicesTab();
    if (tabName === 'accounts') renderAccountsTab();
    if (tabName === 'rules') loadRules();
    if (tabName === 'categories') renderCategoriesMaster();
    if (tabName === 'services-master') renderServicesMaster();
}

// Handle browser back/forward
window.addEventListener('popstate', (e) => {
    const tab = e.state?.tab || 'dashboard';
    switchTab(tab, { pushHistory: false });
});

// ============================================================
// DASHBOARD
// ============================================================

async function loadDashboard() {
    // Clear chart selection when dashboard filters change
    if (chartFilter.categories.length) {
        chartFilter = { categories: [], period: null, source: null };
        renderFilterChip();
    }

    const params = buildFilterParams();           // full filter (includes month narrowing)
    const chartFilterParams = buildChartParams();  // year-level only (no month narrowing)
    const granularity = getGranularity();
    const groupParent = showSubcategories ? 'false' : 'true';
    const chartParams = chartFilterParams + (chartFilterParams ? '&' : '') + 'granularity=' + granularity + '&group_parent=' + groupParent;
    const catChartParams = params + (params ? '&' : '') + 'group_parent=' + groupParent;

    // Stat cards: pass ref_month if a specific month is selected
    const year = document.getElementById('filter-year').value;
    const month = document.getElementById('filter-month').value;
    let statParams = '';
    if (year && month) {
        statParams = `ref_month=${year}-${month}`;
    }
    if (spendFilter === 'personal') statParams += (statParams ? '&' : '') + 'personal_only=true';
    if (spendFilter === 'moom') statParams += (statParams ? '&' : '') + 'moom_only=true';
    if (document.getElementById('filter-anomaly').checked) statParams += (statParams ? '&' : '') + 'exclude_anomaly=true';
    if (document.getElementById('filter-one-off').checked) statParams += (statParams ? '&' : '') + 'exclude_one_off=true';
    const accountId = document.getElementById('filter-account')?.value;
    if (accountId) statParams += (statParams ? '&' : '') + 'account_id=' + accountId;

    const [statCards, monthly, catData, txData] = await Promise.all([
        fetch('/api/dashboard/stat-cards?' + statParams).then(r => r.json()),
        fetch('/api/dashboard/monthly?' + chartParams).then(r => r.json()),
        fetch('/api/dashboard/categories?' + catChartParams).then(r => r.json()),
        fetch('/api/transactions?' + params + '&per_page=50&page=1&sort=' + txSortCol + '&sort_dir=' + txSortDir).then(r => r.json()),
    ]);

    renderStatCards(statCards);
    renderMonthlyChart(monthly, granularity);
    renderCategoryChart(catData);
    renderTransactions(txData);
}

function buildFilterParams() {
    const p = new URLSearchParams();

    // Year/month preset → date range
    const year = document.getElementById('filter-year').value;
    const month = document.getElementById('filter-month').value;

    if (year && month) {
        // Specific month: start = first day, end = last day
        const y = parseInt(year);
        const m = parseInt(month);
        const lastDay = new Date(y, m, 0).getDate(); // last day of month
        p.set('start', `${year}-${month}-01`);
        p.set('end', `${year}-${month}-${String(lastDay).padStart(2, '0')}`);
    } else if (year) {
        p.set('start', `${year}-01-01`);
        p.set('end', `${year}-12-31`);
    }

    const accountId = document.getElementById('filter-account')?.value;
    if (accountId) p.set('account_id', accountId);

    if (spendFilter === 'personal') p.set('personal_only', 'true');
    if (spendFilter === 'moom') p.set('moom_only', 'true');
    if (document.getElementById('filter-anomaly').checked) p.set('exclude_anomaly', 'true');
    if (document.getElementById('filter-one-off').checked) p.set('exclude_one_off', 'true');
    return p.toString();
}

function buildChartParams() {
    // Like buildFilterParams but always year-level (no month narrowing)
    // so charts show full year context with month highlighted
    const p = new URLSearchParams();
    const year = document.getElementById('filter-year').value;
    if (year) {
        p.set('start', `${year}-01-01`);
        p.set('end', `${year}-12-31`);
    }
    const accountId = document.getElementById('filter-account')?.value;
    if (accountId) p.set('account_id', accountId);
    if (spendFilter === 'personal') p.set('personal_only', 'true');
    if (spendFilter === 'moom') p.set('moom_only', 'true');
    if (document.getElementById('filter-anomaly').checked) p.set('exclude_anomaly', 'true');
    if (document.getElementById('filter-one-off').checked) p.set('exclude_one_off', 'true');
    return p.toString();
}

function getGranularity() {
    return 'monthly';
}

function applyDashboardFilters() { loadDashboard(); }

function toggleSubcategories() {
    showSubcategories = !showSubcategories;
    const btn = document.getElementById('subcategory-toggle');
    if (btn) btn.classList.toggle('active', showSubcategories);
    loadDashboard();
}

function setSpendFilter(filter) {
    spendFilter = filter;
    document.querySelectorAll('[data-filter]').forEach(b => {
        b.classList.toggle('active', b.dataset.filter === filter);
    });
    // Auto-toggle subcategories: on for Moom, off for Personal/All
    if (filter === 'moom' && !showSubcategories) {
        showSubcategories = true;
        const btn = document.getElementById('subcategory-toggle');
        if (btn) btn.classList.add('active');
    } else if (filter !== 'moom' && showSubcategories) {
        showSubcategories = false;
        const btn = document.getElementById('subcategory-toggle');
        if (btn) btn.classList.remove('active');
    }
    loadDashboard();
}

function renderStatCards(d) {
    function delta(val, avg) {
        if (!avg || avg === 0) return '';
        const pct = ((val - avg) / avg) * 100;
        const sign = pct >= 0 ? '+' : '';
        const cls = pct > 5 ? 'delta-up' : pct < -5 ? 'delta-down' : 'delta-flat';
        return `<span class="stat-delta ${cls}">${sign}${pct.toFixed(0)}% vs 3mo avg</span>`;
    }

    document.getElementById('stats-row').innerHTML = `
        <div class="stat-card">
            <div class="stat-label">${d.ref_label} Spend</div>
            <div class="stat-value">S$${formatAmount(d.spend)}</div>
            <div class="stat-sub">${d.tx_count} transactions</div>
            ${delta(d.spend, d.avg_spend)}
        </div>
        <div class="stat-card">
            <div class="stat-label">Personal</div>
            <div class="stat-value accent">S$${formatAmount(d.personal)}</div>
            ${delta(d.personal, d.avg_personal)}
        </div>
        <div class="stat-card">
            <div class="stat-label">Moom (Business)</div>
            <div class="stat-value moom">S$${formatAmount(d.moom)}</div>
            ${delta(d.moom, d.avg_moom)}
        </div>
        <div class="stat-card">
            <div class="stat-label">Uncategorized</div>
            <div class="stat-value ${d.uncategorized > 0 ? 'text-warning' : ''}">${d.uncategorized}</div>
        </div>
    `;
}

function setChartMode(mode) {
    chartMode = mode;
    document.querySelectorAll('[data-chart-mode]').forEach(b => {
        b.classList.toggle('active', b.dataset.chartMode === mode);
    });
    if (lastMonthlyData) renderMonthlyChart(lastMonthlyData, lastGranularity);
}

function renderMonthlyChart(data, granularity) {
    // Cache for mode toggle re-render
    lastMonthlyData = data;
    lastGranularity = granularity;

    const periods = Object.keys(data).sort();
    if (!periods.length) return;

    // Update chart title based on granularity + mode
    const titleEl = document.getElementById('chart-trend-title');
    const prefix = chartMode === 'trend' ? 'Category Trends' : (
        granularity === 'weekly' ? 'Weekly Spending' :
        granularity === 'quarterly' ? 'Quarterly Spending' : 'Monthly Spending'
    );
    titleEl.textContent = prefix;

    // Collect all categories across periods
    const allCats = new Set();
    periods.forEach(p => Object.keys(data[p]).forEach(c => allCats.add(c)));

    // Backend already filters by personal_only / moom_only — just sort
    let catList = [...allCats].sort();

    // Format labels: "2025-09" → "Sep-25"
    const labels = periods.map(p => {
        const parts = p.split('-');
        if (parts.length === 2 && parts[1].length === 2) {
            const m = parseInt(parts[1]);
            return MONTH_NAMES[m] + '-' + parts[0].slice(2);
        }
        return p;
    });

    // Store periods for chart click lookup
    monthlyPeriods = periods;

    if (chartMode === 'trend') {
        renderTrendChart(catList, periods, labels, data);
    } else {
        renderBarChart(catList, periods, labels, data);
    }
}

function renderBarChart(catList, periods, labels, data) {
    // Determine if a specific month is highlighted
    const filterYear = document.getElementById('filter-year').value;
    const filterMonth = document.getElementById('filter-month').value;
    const highlightPeriod = (filterYear && filterMonth) ? `${filterYear}-${filterMonth}` : null;

    const datasets = catList.map((cat, i) => {
        const color = CAT_COLORS[i % CAT_COLORS.length];
        return {
            label: cat,
            data: periods.map(p => data[p][cat] || 0),
            backgroundColor: highlightPeriod
                ? periods.map(p => p === highlightPeriod ? color : hexToRgba(color, 0.25))
                : color,
            borderColor: highlightPeriod
                ? periods.map(p => p === highlightPeriod ? color : 'transparent')
                : 'transparent',
            borderWidth: highlightPeriod ? 1 : 0,
            borderRadius: 3,
        };
    });

    const ctx = document.getElementById('chart-monthly');
    if (monthlyChart) monthlyChart.destroy();

    monthlyChart = new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            onClick: (event, elements) => {
                if (!elements.length) return;
                const el = elements[0];
                const category = monthlyChart.data.datasets[el.datasetIndex].label;
                const period = monthlyPeriods[el.index];
                toggleChartFilter(category, period, 'bar');
            },
            onHover: (event, elements) => {
                event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: { color: '#a8a8a8', font: { size: 11 }, boxWidth: 12, padding: 12 },
                },
                tooltip: {
                    backgroundColor: '#1a1d1b',
                    borderColor: '#323832',
                    borderWidth: 1,
                    titleColor: '#fafafa',
                    bodyColor: '#a8a8a8',
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: S$${formatAmount(ctx.raw)}`,
                    },
                },
            },
            interaction: {
                mode: 'nearest',
                intersect: true,
            },
            scales: {
                x: {
                    stacked: true,
                    ticks: { color: '#787878', font: { size: 11 } },
                    grid: { display: false },
                },
                y: {
                    stacked: true,
                    ticks: {
                        color: '#787878',
                        font: { size: 11 },
                        callback: v => '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'K' : v),
                    },
                    grid: { color: '#2a2f2c', drawBorder: false },
                },
            },
        },
    });
}

function renderTrendChart(catList, periods, labels, data) {
    // Rank categories by total spend, take top 8, aggregate rest as "Other"
    const catTotals = catList.map(cat => ({
        cat,
        total: periods.reduce((s, p) => s + (data[p][cat] || 0), 0),
    }));
    catTotals.sort((a, b) => b.total - a.total);

    const topCats = catTotals.slice(0, 8).map(c => c.cat);
    const restCats = catTotals.slice(8).map(c => c.cat);

    const datasets = topCats.map((cat, i) => ({
        label: cat,
        data: periods.map(p => data[p][cat] || 0),
        borderColor: CAT_COLORS[i % CAT_COLORS.length],
        backgroundColor: 'transparent',
        borderWidth: 2,
        tension: 0.3,
        pointRadius: 3,
        pointHoverRadius: 6,
        pointBackgroundColor: CAT_COLORS[i % CAT_COLORS.length],
    }));

    // Aggregate remaining categories (use "Rest" to avoid collision with "Other" category)
    if (restCats.length) {
        datasets.push({
            label: 'Rest',
            data: periods.map(p => restCats.reduce((s, c) => s + (data[p][c] || 0), 0)),
            borderColor: '#585858',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            borderDash: [4, 3],
            tension: 0.3,
            pointRadius: 2,
            pointHoverRadius: 5,
            pointBackgroundColor: '#585858',
        });
    }

    const ctx = document.getElementById('chart-monthly');
    if (monthlyChart) monthlyChart.destroy();

    monthlyChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            onClick: (event, elements) => {
                if (!elements.length) return;
                const el = elements[0];
                const category = monthlyChart.data.datasets[el.datasetIndex].label;
                if (category === 'Other') return; // can't filter on aggregate
                const period = monthlyPeriods[el.index];
                toggleChartFilter(category, period, 'bar');
            },
            onHover: (event, elements) => {
                event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'bottom',
                    labels: {
                        color: '#a8a8a8',
                        font: { size: 11 },
                        boxWidth: 12,
                        padding: 12,
                        usePointStyle: true,
                        pointStyle: 'line',
                    },
                },
                tooltip: {
                    backgroundColor: '#1a1d1b',
                    borderColor: '#323832',
                    borderWidth: 1,
                    titleColor: '#fafafa',
                    bodyColor: '#a8a8a8',
                    mode: 'index',
                    intersect: false,
                    itemSort: (a, b) => b.raw - a.raw,
                    filter: item => item.raw > 0,
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: S$${formatAmount(ctx.raw)}`,
                    },
                },
            },
            interaction: {
                mode: 'index',
                intersect: false,
            },
            scales: {
                x: {
                    ticks: { color: '#787878', font: { size: 11 } },
                    grid: { display: false },
                },
                y: {
                    ticks: {
                        color: '#787878',
                        font: { size: 11 },
                        callback: v => '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'K' : v),
                    },
                    grid: { color: '#2a2f2c', drawBorder: false },
                },
            },
        },
    });
}

function renderCategoryChart(data) {
    let filtered = data;
    if (spendFilter === 'personal') filtered = data.filter(d => d.is_personal === 1);
    if (spendFilter === 'moom') filtered = data.filter(d => d.is_personal === 0);

    // Top 10 + "Other"
    const top = filtered.slice(0, 10);
    const rest = filtered.slice(10);
    if (rest.length) {
        top.push({
            category: 'Other',
            total: rest.reduce((s, d) => s + d.total, 0),
            count: rest.reduce((s, d) => s + d.count, 0),
        });
    }

    const ctx = document.getElementById('chart-categories');
    if (categoryChart) categoryChart.destroy();

    categoryChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: top.map(d => d.category),
            datasets: [{
                data: top.map(d => d.total),
                backgroundColor: top.map((_, i) => CAT_COLORS[i % CAT_COLORS.length]),
                borderWidth: 0,
                offset: 0,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '55%',
            onClick: (event, elements) => {
                if (!elements.length) return;
                const el = elements[0];
                const category = categoryChart.data.labels[el.index];
                toggleChartFilter(category, null, 'doughnut');
            },
            onHover: (event, elements) => {
                event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
            },
            plugins: {
                legend: {
                    position: 'right',
                    labels: { color: '#a8a8a8', font: { size: 11 }, boxWidth: 10, padding: 8 },
                },
                tooltip: {
                    backgroundColor: '#1a1d1b',
                    borderColor: '#323832',
                    borderWidth: 1,
                    titleColor: '#fafafa',
                    bodyColor: '#a8a8a8',
                    callbacks: {
                        label: ctx => `S$${formatAmount(ctx.raw)} (${ctx.dataset.data.length ? ((ctx.raw / ctx.dataset.data.reduce((a, b) => a + b, 0)) * 100).toFixed(1) : 0}%)`,
                    },
                },
            },
        },
    });
}

// ============================================================
// TRANSACTIONS TABLE
// ============================================================

function renderCategoryBadges(tx) {
    // Wrap in a clickable container to allow reassignment
    const badges = (() => {
        if (!tx.category) return '<span class="badge badge-warning">Uncategorized</span>';
        if (tx.parent_category) {
            return `<span class="badge badge-parent">${escapeHtml(tx.parent_category)}</span><span class="badge badge-sub">${escapeHtml(tx.category)}</span>`;
        }
        return `<span class="badge badge-success">${escapeHtml(tx.category)}</span>`;
    })();
    return `<span class="tx-cat-editable" onclick="showCategoryPicker(${tx.id}, this)" title="Click to change category">${badges}</span>`;
}

function renderTransactions(data) {
    const tbody = document.getElementById('tx-body');
    tbody.innerHTML = '';

    data.transactions.forEach(tx => {
        if (tx.is_payment || tx.is_transfer) return;
        const tr = document.createElement('tr');
        tr.dataset.description = tx.description || '';
        // Note indicator: small icon after description, clickable to edit
        const noteIcon = tx.notes
            ? `<span class="tx-note-icon has-note" title="${escapeHtml(tx.notes)}" onclick="editNote(${tx.id}, this)">&#9998;</span>`
            : `<span class="tx-note-icon" title="Add note" onclick="editNote(${tx.id}, this)">&#9998;</span>`;
        tr.innerHTML = `
            <td class="col-date">${formatDate(tx.date)}</td>
            <td class="col-desc">${escapeHtml(tx.description)}${noteIcon}</td>
            <td style="font-size:12px;">${tx.service_id ? `<a href="#" class="svc-link" onclick="navigateToService(${tx.service_id});return false;">${escapeHtml(tx.service_name)}</a>` : ''}</td>
            <td>${renderCategoryBadges(tx)}</td>
            <td class="text-secondary" style="font-size:12px;">${tx.account_name || ''}</td>
            <td class="col-amount ${tx.amount_sgd < 0 ? 'text-success' : ''}">${tx.amount_sgd < 0 ? '-' : ''}S$${formatAmount(Math.abs(tx.amount_sgd))}</td>
        `;
        tbody.appendChild(tr);
    });

    txCurrentPage = data.page;
    document.getElementById('tx-page-info').textContent =
        `Page ${data.page} of ${data.pages} (${data.total} transactions)`;
    document.getElementById('tx-prev').disabled = data.page <= 1;
    document.getElementById('tx-next').disabled = data.page >= data.pages;

    // Update sort indicators
    document.querySelectorAll('#tx-table th.sortable').forEach(th => {
        const col = th.dataset.sort;
        const arrow = th.querySelector('.sort-arrow');
        if (col === txSortCol) {
            th.classList.add('active');
            arrow.textContent = txSortDir === 'asc' ? '▲' : '▼';
        } else {
            th.classList.remove('active');
            arrow.textContent = '';
        }
    });
}

function toggleSort(col) {
    if (txSortCol === col) {
        txSortDir = txSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        txSortCol = col;
        txSortDir = col === 'date' ? 'desc' : 'asc'; // date defaults desc, others asc
    }
    txCurrentPage = 1;
    txPage(0);
}

function txPage(delta) {
    txCurrentPage += delta;
    if (txCurrentPage < 1) txCurrentPage = 1;
    const params = buildFilterParams();
    const search = document.getElementById('tx-search').value;

    let url = `/api/transactions?${params}&per_page=50&page=${txCurrentPage}&sort=${txSortCol}&sort_dir=${txSortDir}`;
    if (search) url += '&search=' + encodeURIComponent(search);

    // Category filter: chart filter takes precedence over multi-select
    const activeCats = chartFilter.categories.length > 0
        ? chartFilter.categories
        : catFilterSelections;
    if (activeCats.length) {
        url += '&categories=' + encodeURIComponent(activeCats.join(','));
    }

    // Chart-driven date narrowing
    if (chartFilter.period) {
        const range = periodToDateRange(chartFilter.period);
        if (range.start) url += '&chart_start=' + range.start;
        if (range.end) url += '&chart_end=' + range.end;
    }

    fetch(url).then(r => r.json()).then(renderTransactions);
}

function editNote(txId, iconEl) {
    noteModalTxId = txId;
    noteModalIconEl = iconEl;

    // Get current note text and transaction description
    const current = iconEl.classList.contains('has-note') ? iconEl.title : '';
    const row = iconEl.closest('tr');
    const desc = row?.dataset.description || '';

    document.getElementById('note-modal-desc').textContent = desc;
    document.getElementById('note-text').value = current;
    document.getElementById('note-modal').style.display = 'flex';
    document.getElementById('note-text').focus();
}

function closeNoteModal() {
    document.getElementById('note-modal').style.display = 'none';
    noteModalTxId = null;
    noteModalIconEl = null;
}

async function saveNote() {
    if (!noteModalTxId) return;
    const note = document.getElementById('note-text').value.trim();
    const btn = document.querySelector('#note-modal .btn-primary');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        await fetch(`/api/transactions/${noteModalTxId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes: note || null }),
        });

        // Update icon state inline
        if (noteModalIconEl) {
            if (note) {
                noteModalIconEl.classList.add('has-note');
                noteModalIconEl.title = note;
            } else {
                noteModalIconEl.classList.remove('has-note');
                noteModalIconEl.title = 'Add note';
            }
        }
        closeNoteModal();
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save';
    }
}

async function clearNote() {
    if (!noteModalTxId) return;
    const btn = document.querySelector('#note-modal .modal-footer .btn:first-child');
    btn.disabled = true;

    try {
        await fetch(`/api/transactions/${noteModalTxId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notes: null }),
        });

        if (noteModalIconEl) {
            noteModalIconEl.classList.remove('has-note');
            noteModalIconEl.title = 'Add note';
        }
        closeNoteModal();
    } finally {
        btn.disabled = false;
    }
}

function showCategoryPicker(txId, containerEl) {
    // Don't open a second picker if one is already showing
    if (containerEl.querySelector('select')) return;

    // Build hierarchical dropdown
    let options = '<option value="">-- Select --</option>';
    const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
    parents.forEach(p => {
        options += `<option value="${p.id}">${p.name}</option>`;
        const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
        children.forEach(c => {
            options += `<option value="${c.id}">&nbsp;&nbsp;${p.name} > ${c.name}</option>`;
        });
    });

    const select = document.createElement('select');
    select.className = 'cat-picker-inline';
    select.innerHTML = options;

    // Replace badges with dropdown
    containerEl.innerHTML = '';
    containerEl.appendChild(select);
    select.focus();

    // On selection, save and offer rule creation
    select.addEventListener('change', async () => {
        const catId = parseInt(select.value);
        if (!catId) return;

        const row = containerEl.closest('tr');
        const desc = row?.dataset.description || '';

        await fetch(`/api/transactions/${txId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category_id: catId }),
        });

        txPage(0); // reload current page to show updated category

        // Offer to create a merchant rule
        const cat = categories.find(c => c.id === catId);
        if (desc) {
            openRuleModal(desc, catId, cat ? cat.display_name : '');
        }
    });

    // On blur (click away), cancel
    select.addEventListener('blur', () => {
        txPage(0); // reload to restore badges
    });
}

// Wire up search/filter inputs
document.addEventListener('DOMContentLoaded', () => {
    ['tx-search'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => { txCurrentPage = 1; txPage(0); });
    });
    const searchEl = document.getElementById('tx-search');
    if (searchEl) {
        let debounce;
        searchEl.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => { txCurrentPage = 1; txPage(0); }, 300);
        });
    }

    // Services search debounce
    const svcSearchEl = document.getElementById('services-search');
    if (svcSearchEl) {
        svcSearchEl.addEventListener('input', debounce(renderServicesTab, 300));
    }

    // Services master search debounce
    const svcMasterSearchEl = document.getElementById('svc-master-search');
    if (svcMasterSearchEl) {
        svcMasterSearchEl.addEventListener('input', debounce(renderServicesMaster, 300));
    }

    // Services category-view search debounce
    const svcCatSearchEl = document.getElementById('services-cat-search');
    if (svcCatSearchEl) {
        svcCatSearchEl.addEventListener('input', debounce(renderServicesCategoryView, 300));
    }

    // Close modals on Escape
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            if (document.getElementById('note-modal').style.display !== 'none') closeNoteModal();
            if (document.getElementById('rule-modal').style.display !== 'none') closeRuleModal();
            if (document.getElementById('edit-rule-modal').style.display !== 'none') closeEditRuleModal();
            if (document.getElementById('add-service-modal').style.display !== 'none') closeAddServiceModal();
            if (document.getElementById('edit-service-modal').style.display !== 'none') closeEditServiceModal();
        }
    });

    // Close modals on overlay click
    ['note-modal', 'rule-modal', 'edit-rule-modal', 'add-service-modal', 'edit-service-modal'].forEach(id => {
        document.getElementById(id)?.addEventListener('click', e => {
            if (e.target.classList.contains('modal-overlay')) {
                if (id === 'note-modal') closeNoteModal();
                if (id === 'rule-modal') closeRuleModal();
                if (id === 'edit-rule-modal') closeEditRuleModal();
                if (id === 'add-service-modal') closeAddServiceModal();
                if (id === 'edit-service-modal') closeEditServiceModal();
            }
        });
    });
});

// ============================================================
// RULE CREATION MODAL (from category picker)
// ============================================================

function suggestPattern(description) {
    // Extract a useful merchant pattern from the description
    // Strip trailing reference numbers and clean up
    let pattern = description.toUpperCase();
    // Remove common suffixes: country codes, dates, reference numbers
    pattern = pattern.replace(/\s+(SG|SGP|SIN|US|USA|GB|GBR|AU|AUS)\s*$/i, '');
    pattern = pattern.replace(/\s+\d{2}\/\d{2}$/, '');  // trailing dates
    // Take first meaningful segment (before reference numbers)
    const parts = pattern.split(/\s+/);
    // Find where "noise" starts (long alphanumeric strings, pure numbers)
    let cutoff = parts.length;
    for (let i = 1; i < parts.length; i++) {
        if (/^[A-Z0-9]{8,}$/.test(parts[i]) || /^\d+$/.test(parts[i])) {
            cutoff = i;
            break;
        }
    }
    return parts.slice(0, Math.max(cutoff, 2)).join(' ');
}

function openRuleModal(description, categoryId, categoryName) {
    ruleModalCatId = categoryId;
    document.getElementById('rule-modal-desc').textContent =
        `Create a rule to auto-categorize similar transactions as "${categoryName}"`;
    document.getElementById('rule-modal-pattern').value = suggestPattern(description);
    document.getElementById('rule-modal-match').value = 'contains';
    document.getElementById('rule-modal').style.display = 'flex';
    document.getElementById('rule-modal-pattern').focus();
    document.getElementById('rule-modal-pattern').select();
}

function closeRuleModal() {
    document.getElementById('rule-modal').style.display = 'none';
    ruleModalCatId = null;
}

async function saveRuleFromModal() {
    const pattern = document.getElementById('rule-modal-pattern').value.trim();
    if (!pattern || !ruleModalCatId) return;

    const matchType = document.getElementById('rule-modal-match').value;
    const btn = document.querySelector('#rule-modal .btn-primary');
    btn.disabled = true;
    btn.textContent = 'Creating...';

    try {
        const res = await fetch('/api/rules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pattern, category_id: ruleModalCatId, match_type: matchType }),
        });
        const data = await res.json();
        if (data.error) {
            alert('Error: ' + data.error);
            return;
        }
        closeRuleModal();
    } finally {
        btn.disabled = false;
        btn.textContent = 'Create Rule';
    }
}

// ============================================================
// IMPORT
// ============================================================

// Drag & drop
document.addEventListener('DOMContentLoaded', () => {
    const zone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');

    zone.addEventListener('click', () => fileInput.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', e => {
        e.preventDefault();
        zone.classList.remove('dragover');
        handleFiles(e.dataTransfer.files);
    });
    fileInput.addEventListener('change', () => handleFiles(fileInput.files));
});

function handleFiles(fileList) {
    selectedFiles = [...fileList];
    const listEl = document.getElementById('file-list');
    listEl.innerHTML = selectedFiles.map(f =>
        `<div style="padding:4px 0;font-size:13px;color:var(--text-secondary);">📄 ${escapeHtml(f.name)} <span class="text-muted">(${(f.size / 1024).toFixed(0)} KB)</span></div>`
    ).join('');
    document.getElementById('upload-btn').style.display = selectedFiles.length ? 'block' : 'none';
}

async function uploadFiles() {
    if (!selectedFiles.length) return;

    const uploadBtn = document.getElementById('upload-btn');
    uploadBtn.textContent = 'Parsing...';
    uploadBtn.disabled = true;

    const formData = new FormData();
    selectedFiles.forEach(f => formData.append('files', f));

    try {
        const res = await fetch('/api/import/upload', { method: 'POST', body: formData });
        const data = await res.json();

        if (data.error) {
            alert('Upload error: ' + data.error);
            return;
        }

        currentImportId = data.import_id;
        currentImportData = data;
        renderImportPreview(data);
    } catch (err) {
        alert('Upload failed: ' + err.message);
    } finally {
        uploadBtn.textContent = 'Upload & Parse';
        uploadBtn.disabled = false;
    }
}

function renderImportPreview(data) {
    document.getElementById('import-upload-card').classList.add('hidden');
    document.getElementById('import-preview').classList.remove('hidden');

    // Stats
    document.getElementById('preview-stats').innerHTML = `
        <div class="preview-stat"><strong>${data.stats.total}</strong> Total</div>
        <div class="preview-stat"><strong class="text-success">${data.stats.categorized}</strong> Categorized</div>
        <div class="preview-stat"><strong class="text-warning">${data.stats.uncategorized}</strong> Uncategorized</div>
        <div class="preview-stat"><strong class="text-muted">${data.stats.skipped}</strong> Skipped</div>
        ${data.errors.length ? `<div class="preview-stat text-error">${data.errors.length} errors</div>` : ''}
    `;

    // Groups
    const groupsEl = document.getElementById('preview-groups');
    groupsEl.innerHTML = '';

    data.groups.forEach((group, gi) => {
        const div = document.createElement('div');
        div.className = 'account-group';
        div.innerHTML = `
            <div class="account-group-header">
                <span class="account-group-name">${escapeHtml(group.account)}</span>
                <div class="account-group-stats">
                    <span>${group.total} transactions</span>
                    <span class="text-success">${group.categorized} categorized</span>
                    <span class="text-warning">${group.uncategorized} uncategorized</span>
                </div>
            </div>
            <div class="preview-table-wrap">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th style="width:40px;"><input type="checkbox" checked onchange="toggleGroupSkip(${gi}, this.checked)"></th>
                            <th>Date</th>
                            <th>Description</th>
                            <th style="text-align:right">Amount</th>
                            <th>Category</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="preview-group-${gi}"></tbody>
                </table>
            </div>
        `;
        groupsEl.appendChild(div);

        const tbody = document.getElementById(`preview-group-${gi}`);
        group.transactions.forEach((tx, ti) => {
            const tr = document.createElement('tr');
            if (tx._skip) tr.style.opacity = '0.4';
            tr.innerHTML = `
                <td><input type="checkbox" ${tx._skip ? '' : 'checked'} data-gi="${gi}" data-ti="${ti}" onchange="toggleTxSkip(${gi}, ${ti}, this.checked)"></td>
                <td class="col-date">${tx.date}</td>
                <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(tx.description)}">${escapeHtml(tx.description)}</td>
                <td class="col-amount">${tx.amount_sgd < 0 ? '-' : ''}S$${formatAmount(Math.abs(tx.amount_sgd))}</td>
                <td>
                    <select class="cat-select ${tx.status === 'uncategorized' ? 'unresolved' : ''}" data-gi="${gi}" data-ti="${ti}" onchange="setCategoryOverride(${gi}, ${ti}, this.value)">
                        <option value="">-- Select --</option>
                        ${categories.map(c => `<option value="${c.id}" ${tx.category_id === c.id ? 'selected' : ''}>${c.display_name}</option>`).join('')}
                    </select>
                </td>
                <td><span class="badge badge-${tx.status === 'categorized' ? 'success' : tx.status === 'transfer' ? 'muted' : 'warning'}">${tx.status}</span></td>
            `;
            tbody.appendChild(tr);
        });
    });

    updateConfirmBar();
}

function toggleGroupSkip(gi, checked) {
    currentImportData.groups[gi].transactions.forEach((tx, ti) => {
        tx._skip = !checked;
        const cb = document.querySelector(`[data-gi="${gi}"][data-ti="${ti}"]`);
        if (cb && cb.type === 'checkbox') cb.checked = checked;
    });
    updateConfirmBar();
}

function toggleTxSkip(gi, ti, checked) {
    currentImportData.groups[gi].transactions[ti]._skip = !checked;
    const row = document.querySelector(`[data-gi="${gi}"][data-ti="${ti}"]`).closest('tr');
    if (row) row.style.opacity = checked ? '1' : '0.4';
    updateConfirmBar();
}

function setCategoryOverride(gi, ti, catId) {
    const tx = currentImportData.groups[gi].transactions[ti];
    tx.category_id = catId ? parseInt(catId) : null;
    tx.category_name = catId ? categories.find(c => c.id === parseInt(catId))?.name : null;
    tx.status = catId ? 'categorized' : 'uncategorized';

    // Update badge
    const row = document.querySelector(`[data-gi="${gi}"][data-ti="${ti}"]`).closest('tr');
    const badge = row.querySelector('.badge');
    if (badge) {
        badge.className = `badge badge-${catId ? 'success' : 'warning'}`;
        badge.textContent = catId ? 'categorized' : 'uncategorized';
    }
    const select = row.querySelector('.cat-select');
    if (select) select.classList.toggle('unresolved', !catId);

    updateConfirmBar();
}

function updateConfirmBar() {
    if (!currentImportData) return;
    let active = 0, total = 0;
    currentImportData.groups.forEach(g => {
        g.transactions.forEach(tx => {
            total++;
            if (!tx._skip) active++;
        });
    });
    document.getElementById('confirm-info').innerHTML =
        `<strong>${active}</strong> of ${total} transactions will be committed`;
}

function discardImport() {
    currentImportId = null;
    currentImportData = null;
    selectedFiles = [];
    document.getElementById('import-upload-card').classList.remove('hidden');
    document.getElementById('import-preview').classList.add('hidden');
    document.getElementById('file-list').innerHTML = '';
    document.getElementById('upload-btn').style.display = 'none';
    document.getElementById('file-input').value = '';
}

async function confirmImport() {
    if (!currentImportData) return;

    const body = {
        import_id: currentImportId,
        groups: currentImportData.groups.map(g => ({
            account: g.account,
            transactions: g.transactions,
        })),
        new_rules: [],
    };

    const confirmBtn = document.querySelector('.confirm-bar .btn-primary');
    confirmBtn.textContent = 'Committing...';
    confirmBtn.disabled = true;

    try {
        const res = await fetch('/api/import/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const result = await res.json();

        if (result.error) {
            alert('Commit error: ' + result.error);
            return;
        }

        const dupMsg = result.duplicates_skipped ? ` (${result.duplicates_skipped} duplicates skipped)` : '';
        alert(`Committed ${result.transactions_saved} transactions to ${result.accounts.length} accounts.${dupMsg}`);
        discardImport();
        await loadReferenceData();
    } catch (err) {
        alert('Commit failed: ' + err.message);
    } finally {
        confirmBtn.textContent = 'Confirm & Commit';
        confirmBtn.disabled = false;
    }
}

// ============================================================
// HISTORY
// ============================================================

async function loadHistory() {
    const res = await fetch('/api/import/history');
    const data = await res.json();

    const tbody = document.getElementById('history-body');
    const emptyEl = document.getElementById('history-empty');

    if (!data.length) {
        tbody.innerHTML = '';
        emptyEl.classList.remove('hidden');
        return;
    }
    emptyEl.classList.add('hidden');

    tbody.innerHTML = data.map(imp => `
        <tr>
            <td class="text-mono">${imp.id}</td>
            <td>${imp.filenames.map(f => escapeHtml(f)).join(', ')}</td>
            <td style="font-size:12px;">${imp.accounts.map(a => escapeHtml(a)).join(', ')}</td>
            <td>${imp.categorized_lines}/${imp.total_lines}</td>
            <td><span class="badge badge-${imp.status}">${imp.status}</span></td>
            <td class="col-date">${imp.created_at || ''}</td>
        </tr>
        ${imp.result ? `<tr><td colspan="6"><div class="history-detail">${JSON.stringify(imp.result)}</div></td></tr>` : ''}
    `).join('');
}

// ============================================================
// MERCHANT RULES
// ============================================================

let allRules = [];

async function loadRules() {
    const res = await fetch('/api/rules');
    allRules = await res.json();
    renderRules(allRules);
}

function filterRules() {
    const q = document.getElementById('rules-search').value.toLowerCase();
    if (!q) {
        renderRules(allRules);
        return;
    }
    const filtered = allRules.filter(r =>
        r.pattern.toLowerCase().includes(q) ||
        r.category_name.toLowerCase().includes(q)
    );
    renderRules(filtered);
}

function renderRules(rules) {
    document.getElementById('rules-count').textContent = `${rules.length} rules`;

    // Group by display_category (Parent > Sub or just Parent)
    const groups = {};
    rules.forEach(r => {
        const key = r.display_category || r.category_name;
        if (!groups[key]) groups[key] = { parentName: r.parent_name, categoryName: r.category_name, rules: [] };
        groups[key].rules.push(r);
    });

    const container = document.getElementById('rules-accordion');
    container.innerHTML = '';

    Object.keys(groups).sort().forEach(key => {
        const g = groups[key];
        const groupEl = document.createElement('div');
        groupEl.className = 'rules-acc-group';

        // Title: show "Parent > Sub" with styling
        let titleHtml;
        if (g.parentName) {
            titleHtml = `${escapeHtml(g.parentName)}<span class="acc-sub"> > ${escapeHtml(g.categoryName)}</span>`;
        } else {
            titleHtml = escapeHtml(g.categoryName);
        }

        groupEl.innerHTML = `
            <div class="rules-acc-header" onclick="toggleAccordion(this)">
                <div style="display:flex;align-items:center;">
                    <span class="acc-arrow">&#9654;</span>
                    <span class="rules-acc-title">${titleHtml}</span>
                </div>
                <span class="rules-acc-count">${g.rules.length} rules</span>
            </div>
            <div class="rules-acc-body">
                <table class="data-table rules-table">
                    <thead>
                        <tr>
                            <th style="width:32%;">Pattern</th>
                            <th style="width:12%;">Match</th>
                            <th style="width:6%;">Pri</th>
                            <th style="width:18%;">Amount</th>
                            <th style="width:10%;">Conf</th>
                            <th style="width:22%;text-align:right;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${g.rules.map(r => {
                            let amtStr = '';
                            if (r.min_amount != null && r.max_amount != null) {
                                amtStr = '$' + r.min_amount.toLocaleString() + '–$' + r.max_amount.toLocaleString();
                            } else if (r.min_amount != null) {
                                amtStr = '\u2265 $' + r.min_amount.toLocaleString();
                            } else if (r.max_amount != null) {
                                amtStr = '\u2264 $' + r.max_amount.toLocaleString();
                            }
                            return `
                            <tr>
                                <td class="text-mono" style="font-size:12px;">${escapeHtml(r.pattern)}</td>
                                <td style="font-size:12px;color:var(--text-tertiary);">${r.match_type}</td>
                                <td style="font-size:12px;color:${r.priority > 0 ? 'var(--camel)' : 'var(--text-tertiary)'};">${r.priority || ''}</td>
                                <td style="font-size:12px;color:${amtStr ? 'var(--camel)' : 'var(--text-tertiary)'};">${amtStr || '\u2014'}</td>
                                <td style="font-size:12px;color:var(--text-tertiary);">${r.confidence}</td>
                                <td style="text-align:right;white-space:nowrap;">
                                    <button class="btn btn-sm" onclick="editRule(${r.id})" style="margin-right:4px;">Edit</button>
                                    <button class="btn btn-sm btn-danger" onclick="deleteRule(${r.id})">Del</button>
                                </td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
        container.appendChild(groupEl);
    });
}

function toggleAccordion(header) {
    header.classList.toggle('open');
    const body = header.nextElementSibling;
    body.classList.toggle('open');
}

async function addRule() {
    const pattern = document.getElementById('rule-pattern').value.trim();
    const categoryId = document.getElementById('rule-category').value;
    const matchType = document.getElementById('rule-match-type').value;
    const priority = parseInt(document.getElementById('rule-priority').value) || 0;
    const minAmountVal = document.getElementById('rule-min-amount').value;
    const maxAmountVal = document.getElementById('rule-max-amount').value;

    if (!pattern || !categoryId) {
        alert('Pattern and category are required');
        return;
    }

    const body = {
        pattern,
        category_id: parseInt(categoryId),
        match_type: matchType,
        priority,
    };
    if (minAmountVal) body.min_amount = parseFloat(minAmountVal);
    if (maxAmountVal) body.max_amount = parseFloat(maxAmountVal);

    const res = await fetch('/api/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();

    if (data.error) {
        alert('Error: ' + data.error);
        return;
    }

    document.getElementById('rule-pattern').value = '';
    document.getElementById('rule-priority').value = '0';
    document.getElementById('rule-min-amount').value = '';
    document.getElementById('rule-max-amount').value = '';
    await loadRules();
}

function editRule(ruleId) {
    const rule = allRules.find(r => r.id === ruleId);
    if (!rule) return;

    document.getElementById('edit-rule-id').value = ruleId;
    document.getElementById('edit-rule-pattern').value = rule.pattern;
    document.getElementById('edit-rule-match').value = rule.match_type || 'contains';
    document.getElementById('edit-rule-priority').value = rule.priority || 0;
    document.getElementById('edit-rule-min').value = rule.min_amount || '';
    document.getElementById('edit-rule-max').value = rule.max_amount || '';

    // Populate category dropdown
    const sel = document.getElementById('edit-rule-category');
    sel.innerHTML = '';
    categories
        .sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name))
        .forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = c.display_name || c.name;
            if (c.id === rule.category_id) opt.selected = true;
            sel.appendChild(opt);
        });

    document.getElementById('edit-rule-modal').style.display = 'flex';
    document.getElementById('edit-rule-pattern').focus();
}

function closeEditRuleModal() {
    document.getElementById('edit-rule-modal').style.display = 'none';
}

async function saveEditRule() {
    const ruleId = document.getElementById('edit-rule-id').value;
    const payload = {
        pattern: document.getElementById('edit-rule-pattern').value.trim(),
        category_id: parseInt(document.getElementById('edit-rule-category').value),
        match_type: document.getElementById('edit-rule-match').value,
        priority: parseInt(document.getElementById('edit-rule-priority').value) || 0,
        min_amount: parseFloat(document.getElementById('edit-rule-min').value) || null,
        max_amount: parseFloat(document.getElementById('edit-rule-max').value) || null,
    };

    await fetch(`/api/rules/${ruleId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    closeEditRuleModal();
    await loadRules();
}

async function deleteRule(ruleId) {
    if (!confirm('Delete this rule?')) return;
    await fetch(`/api/rules/${ruleId}`, { method: 'DELETE' });
    await loadRules();
}

async function recategorizeAll() {
    if (!confirm('Re-run all merchant rules against existing transactions? This will update categories based on current rules.')) return;
    const btn = document.getElementById('recategorize-btn');
    btn.disabled = true;
    btn.textContent = 'Running...';
    try {
        const res = await fetch('/api/rules/recategorize', { method: 'POST' });
        const data = await res.json();
        alert(`Done: ${data.updated} transactions updated, ${data.unchanged} unchanged.`);
        loadDashboard();
    } finally {
        btn.disabled = false;
        btn.textContent = 'Re-categorize All';
    }
}

// ============================================================
// CATEGORY MASTER
// ============================================================

function toggleCatForm() {
    const form = document.getElementById('cat-form');
    form.classList.toggle('hidden');
    if (!form.classList.contains('hidden')) {
        populateCatParentDropdown();
    }
}

function populateCatParentDropdown() {
    const sel = document.getElementById('cat-new-parent');
    sel.innerHTML = '<option value="">-- Top-level --</option>';
    categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name)).forEach(c => {
        sel.innerHTML += `<option value="${c.id}">${c.name}</option>`;
    });
}

function renderCategoryTree() {
    const tree = document.getElementById('cat-tree');
    const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));

    document.getElementById('cat-count').textContent = `${categories.length} categories (${parents.length} top-level)`;

    // Separate parents with/without subcategories for cleaner layout
    const withChildren = parents.filter(p => categories.some(c => c.parent_id === p.id));
    const withoutChildren = parents.filter(p => !categories.some(c => c.parent_id === p.id));

    let html = '';

    // Categories with subcategories
    if (withChildren.length) {
        html += '<div class="cat-tree-grid">';
        withChildren.forEach(p => {
            const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
            const typeLabel = (!p.is_personal && p.name !== 'Moom') ? ' <span class="badge badge-muted">Moom</span>' : '';
            const collapsed = children.length > 5;
            html += `<div class="cat-group${collapsed ? ' collapsed' : ''}">
                <div class="cat-group-name" onclick="this.parentElement.classList.toggle('collapsed')">
                    <span class="cat-expand-arrow">${collapsed ? '&#9654;' : '&#9660;'}</span>
                    ${escapeHtml(p.name)}${typeLabel}
                    <span class="cat-sub-count">(${children.length})</span>
                </div>
                <div class="cat-sub-list">
                    ${children.map(c => `<div class="cat-sub-item">${escapeHtml(c.name)}</div>`).join('')}
                </div>
            </div>`;
        });
        html += '</div>';
    }

    // Simple categories (no subcategories) — compact inline chips
    if (withoutChildren.length) {
        html += '<div class="cat-simple-row">';
        withoutChildren.forEach(p => {
            const typeLabel = (!p.is_personal && p.name !== 'Moom') ? ' <span class="badge badge-muted">Moom</span>' : '';
            html += `<span class="cat-chip">${escapeHtml(p.name)}${typeLabel}</span>`;
        });
        html += '</div>';
    }

    tree.innerHTML = html;
}

async function addCategory() {
    const name = document.getElementById('cat-new-name').value.trim();
    const parentId = document.getElementById('cat-new-parent').value;
    const isPersonal = document.getElementById('cat-new-personal').value;

    if (!name) {
        alert('Category name is required');
        return;
    }

    const body = { name, is_personal: parseInt(isPersonal) };
    if (parentId) body.parent_id = parseInt(parentId);

    const res = await fetch('/api/categories', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();

    if (data.error) {
        alert('Error: ' + data.error);
        return;
    }

    document.getElementById('cat-new-name').value = '';
    await loadReferenceData();
    renderCategoryTree();
}

// ============================================================
// CHART-TABLE LINKING
// ============================================================

function toggleChartFilter(category, period, source) {
    // If switching source type (bar ↔ doughnut), reset
    if (chartFilter.source && chartFilter.source !== source) {
        chartFilter.categories = [];
        chartFilter.period = null;
    }

    // Bar chart: if clicking a different period, reset categories
    if (source === 'bar' && chartFilter.period && chartFilter.period !== period) {
        chartFilter.categories = [];
    }

    chartFilter.source = source;
    chartFilter.period = period;

    // Toggle category (multi-select)
    const idx = chartFilter.categories.indexOf(category);
    if (idx >= 0) {
        chartFilter.categories.splice(idx, 1);
    } else {
        chartFilter.categories.push(category);
    }

    // If nothing selected, clear entirely
    if (chartFilter.categories.length === 0) {
        clearChartFilter();
        return;
    }

    updateChartHighlights();
    renderFilterChip();
    updateCatFilterDisplay();
    txCurrentPage = 1;
    txPage(0);
    scrollToTable();
}

function clearChartFilter() {
    chartFilter = { categories: [], period: null, source: null };
    renderFilterChip();
    updateChartHighlights();
    updateCatFilterDisplay();
    txCurrentPage = 1;
    txPage(0);
}

function updateChartHighlights() {
    const hasFilter = chartFilter.categories.length > 0;

    // Bar chart highlights
    if (monthlyChart) {
        monthlyChart.data.datasets.forEach((ds, i) => {
            const baseColor = CAT_COLORS[i % CAT_COLORS.length];
            if (!hasFilter) {
                ds.backgroundColor = baseColor;
                ds.borderColor = 'transparent';
                ds.borderWidth = 0;
            } else if (chartFilter.categories.includes(ds.label)) {
                ds.backgroundColor = baseColor;
                ds.borderColor = '#fafafa';
                ds.borderWidth = 1.5;
            } else {
                ds.backgroundColor = hexToRgba(baseColor, 0.15);
                ds.borderColor = 'transparent';
                ds.borderWidth = 0;
            }
        });
        monthlyChart.update('none');
    }

    // Doughnut highlights
    if (categoryChart) {
        const ds = categoryChart.data.datasets[0];
        const labels = categoryChart.data.labels;
        if (!hasFilter) {
            ds.backgroundColor = labels.map((_, i) => CAT_COLORS[i % CAT_COLORS.length]);
            ds.offset = 0;
        } else {
            ds.backgroundColor = labels.map((label, i) => {
                const base = CAT_COLORS[i % CAT_COLORS.length];
                return chartFilter.categories.includes(label) ? base : hexToRgba(base, 0.15);
            });
            ds.offset = labels.map(label =>
                chartFilter.categories.includes(label) ? 8 : 0
            );
        }
        categoryChart.update('none');
    }
}

function renderFilterChip() {
    const el = document.getElementById('chart-filter-chip');
    if (!el) return;

    if (!chartFilter.categories.length) {
        el.classList.add('hidden');
        return;
    }

    const catsHtml = chartFilter.categories.map(c =>
        `<span class="chip-cat" onclick="removeSingleChartCat('${escapeHtml(c)}')">${escapeHtml(c)}<span class="chip-x">&times;</span></span>`
    ).join('');

    const periodHtml = chartFilter.period
        ? `<span class="chip-period">&middot; ${escapeHtml(formatPeriodLabel(chartFilter.period))}</span>`
        : '';

    el.innerHTML = `
        <span class="chip-label">Showing</span>
        <span class="chip-cats">${catsHtml}</span>
        ${periodHtml}
        <button class="chip-clear" onclick="clearChartFilter()" title="Clear filter">&times;</button>
    `;
    el.classList.remove('hidden');
}

function removeSingleChartCat(category) {
    const idx = chartFilter.categories.indexOf(category);
    if (idx >= 0) chartFilter.categories.splice(idx, 1);

    if (chartFilter.categories.length === 0) {
        clearChartFilter();
    } else {
        updateChartHighlights();
        renderFilterChip();
        updateCatFilterDisplay();
        txCurrentPage = 1;
        txPage(0);
    }
}

function scrollToTable() {
    const chip = document.getElementById('chart-filter-chip');
    const target = chip && !chip.classList.contains('hidden') ? chip : document.getElementById('tx-table');
    if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

function periodToDateRange(period) {
    // Monthly: "2025-02"
    const monthMatch = period.match(/^(\d{4})-(\d{2})$/);
    if (monthMatch) {
        const y = parseInt(monthMatch[1]), m = parseInt(monthMatch[2]);
        const lastDay = new Date(y, m, 0).getDate();
        return {
            start: `${monthMatch[1]}-${monthMatch[2]}-01`,
            end: `${monthMatch[1]}-${monthMatch[2]}-${String(lastDay).padStart(2, '0')}`
        };
    }

    // Weekly: "2025-W08" (day-of-year / 7 based, matching backend calculation)
    const weekMatch = period.match(/^(\d{4})-W(\d+)$/);
    if (weekMatch) {
        const year = parseInt(weekMatch[1]);
        const week = parseInt(weekMatch[2]);
        const startDay = (week - 1) * 7 + 1;
        const endDay = week * 7;
        const startDate = new Date(year, 0, startDay);
        const endDate = new Date(year, 0, endDay);
        const yearEnd = new Date(year, 11, 31);
        const actualEnd = endDate > yearEnd ? yearEnd : endDate;
        const fmt = d => d.toISOString().split('T')[0];
        return { start: fmt(startDate), end: fmt(actualEnd) };
    }

    // Quarterly: "2025-Q1"
    const qMatch = period.match(/^(\d{4})-Q(\d)$/);
    if (qMatch) {
        const year = qMatch[1];
        const q = parseInt(qMatch[2]);
        const sm = String((q - 1) * 3 + 1).padStart(2, '0');
        const em = String(q * 3).padStart(2, '0');
        const lastDay = new Date(parseInt(year), q * 3, 0).getDate();
        return {
            start: `${year}-${sm}-01`,
            end: `${year}-${em}-${String(lastDay).padStart(2, '0')}`
        };
    }

    return {};
}

function formatPeriodLabel(period) {
    const monthMatch = period.match(/^(\d{4})-(\d{2})$/);
    if (monthMatch) return `${MONTH_NAMES[parseInt(monthMatch[2])]} ${monthMatch[1]}`;

    const weekMatch = period.match(/^(\d{4})-W(\d+)$/);
    if (weekMatch) return `Week ${parseInt(weekMatch[2])}, ${weekMatch[1]}`;

    const qMatch = period.match(/^(\d{4})-Q(\d)$/);
    if (qMatch) return `Q${qMatch[2]} ${qMatch[1]}`;

    return period;
}

// ============================================================
// SEARCHABLE MULTI-SELECT (Category Filter)
// ============================================================

function populateCatMultiSelect() {
    const container = document.getElementById('cat-filter-options');
    if (!container) return;

    let html = '';
    // Uncategorized option
    const uncatChecked = catFilterSelections.includes('__uncategorized__');
    html += `<label class="ms-option ms-separator" data-name="uncategorized">
        <input type="checkbox" value="__uncategorized__" ${uncatChecked ? 'checked' : ''}> Uncategorized
    </label>`;

    // Categories grouped by parent
    const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
    parents.forEach(p => {
        const checked = catFilterSelections.includes(p.name);
        html += `<label class="ms-option ${checked ? 'ms-checked' : ''}" data-name="${escapeHtml(p.name.toLowerCase())}">
            <input type="checkbox" value="${escapeHtml(p.name)}" ${checked ? 'checked' : ''}> ${escapeHtml(p.name)}
        </label>`;
        // Subcategories indented
        const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
        children.forEach(c => {
            const cChecked = catFilterSelections.includes(c.name);
            html += `<label class="ms-option ms-indent ${cChecked ? 'ms-checked' : ''}" data-name="${escapeHtml(c.name.toLowerCase())}">
                <input type="checkbox" value="${escapeHtml(c.name)}" ${cChecked ? 'checked' : ''}> ${escapeHtml(c.name)}
            </label>`;
        });
    });

    container.innerHTML = html;
}

function toggleCatFilterPanel() {
    // Don't open if chart filter is overriding
    if (chartFilter.categories.length > 0) return;

    const panel = document.getElementById('cat-filter-panel');
    const trigger = document.getElementById('cat-filter-trigger');
    const isOpen = !panel.classList.contains('hidden');

    if (isOpen) {
        panel.classList.add('hidden');
        trigger.classList.remove('ms-active');
    } else {
        panel.classList.remove('hidden');
        trigger.classList.add('ms-active');
        document.getElementById('cat-filter-search').value = '';
        filterCatOptions(); // reset search
        document.getElementById('cat-filter-search').focus();
    }
}

function filterCatOptions() {
    const query = document.getElementById('cat-filter-search').value.toLowerCase();
    const options = document.querySelectorAll('#cat-filter-options .ms-option');
    options.forEach(opt => {
        const name = opt.dataset.name || '';
        opt.style.display = !query || name.includes(query) ? '' : 'none';
    });
}

function applyCatFilter() {
    // Read checked values
    catFilterSelections = [];
    document.querySelectorAll('#cat-filter-options input[type="checkbox"]:checked').forEach(cb => {
        catFilterSelections.push(cb.value);
    });

    // Close panel
    document.getElementById('cat-filter-panel').classList.add('hidden');
    document.getElementById('cat-filter-trigger').classList.remove('ms-active');

    updateCatFilterDisplay();
    txCurrentPage = 1;
    txPage(0);
}

function clearCatFilter() {
    catFilterSelections = [];
    document.querySelectorAll('#cat-filter-options input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });

    document.getElementById('cat-filter-panel').classList.add('hidden');
    document.getElementById('cat-filter-trigger').classList.remove('ms-active');

    updateCatFilterDisplay();
    txCurrentPage = 1;
    txPage(0);
}

function updateCatFilterDisplay() {
    const display = document.getElementById('cat-filter-display');
    const trigger = document.getElementById('cat-filter-trigger');
    if (!display || !trigger) return;

    if (chartFilter.categories.length > 0) {
        display.textContent = `Chart: ${chartFilter.categories.join(', ')}`;
        trigger.classList.add('ms-has-selection');
        trigger.classList.add('ms-disabled');
    } else if (catFilterSelections.length === 0) {
        display.textContent = 'All Categories';
        trigger.classList.remove('ms-has-selection');
        trigger.classList.remove('ms-disabled');
    } else if (catFilterSelections.length === 1) {
        display.textContent = catFilterSelections[0] === '__uncategorized__' ? 'Uncategorized' : catFilterSelections[0];
        trigger.classList.add('ms-has-selection');
        trigger.classList.remove('ms-disabled');
    } else {
        display.textContent = `${catFilterSelections.length} categories`;
        trigger.classList.add('ms-has-selection');
        trigger.classList.remove('ms-disabled');
    }
}

// Close multi-select when clicking outside
document.addEventListener('click', e => {
    const wrap = document.getElementById('cat-filter-wrap');
    const panel = document.getElementById('cat-filter-panel');
    if (wrap && panel && !wrap.contains(e.target) && !panel.classList.contains('hidden')) {
        panel.classList.add('hidden');
        document.getElementById('cat-filter-trigger')?.classList.remove('ms-active');
    }
});

// ============================================================
// SERVICES TAB (accordion view)
// ============================================================

let allServicesList = []; // cached for re-sorting without re-fetch

async function renderServicesTab(skipFetch) {
    // Populate category filter dropdown
    const catFilter = document.getElementById('services-category-filter');
    if (catFilter && catFilter.options.length <= 1) {
        const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
        parents.forEach(p => {
            catFilter.innerHTML += `<option value="${p.id}">${p.name}</option>`;
            const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
            children.forEach(c => {
                catFilter.innerHTML += `<option value="${c.id}">&nbsp;&nbsp;${p.name} > ${c.name}</option>`;
            });
        });
    }

    if (!skipFetch) {
        const res = await fetch('/api/services');
        allServicesList = await res.json();
    }

    const container = document.getElementById('services-accordion');
    const search = (document.getElementById('services-search')?.value || '').toLowerCase();
    const catFilterVal = document.getElementById('services-category-filter')?.value;
    const sortBy = document.getElementById('services-sort')?.value || 'name';

    let filtered = allServicesList;
    if (search) filtered = filtered.filter(s => s.name.toLowerCase().includes(search));
    if (catFilterVal) filtered = filtered.filter(s => String(s.category_id) === catFilterVal);

    // Sort
    filtered = [...filtered].sort((a, b) => {
        let va, vb;
        switch (sortBy) {
            case 'category':  va = (a.display_category || '').toLowerCase(); vb = (b.display_category || '').toLowerCase(); break;
            case 'txn_count': return (b.txn_count || 0) - (a.txn_count || 0); // descending
            default:          va = a.name.toLowerCase(); vb = b.name.toLowerCase();
        }
        return va < vb ? -1 : va > vb ? 1 : 0;
    });

    if (!filtered.length) {
        container.innerHTML = '<div style="padding:var(--space-4);color:var(--text-tertiary);">No services found</div>';
        return;
    }

    container.innerHTML = filtered.map(s => `
        <div class="svc-accordion-item" data-svc-id="${s.id}">
            <div class="svc-accordion-header" onclick="toggleServiceAccordion(${s.id})">
                <span class="svc-accordion-arrow">&#9654;</span>
                <span class="svc-accordion-name">${escapeHtml(s.name)}${s.is_one_off ? ' <span class="svc-one-off-badge" title="One-off">1x</span>' : ''}</span>
                <span class="svc-accordion-meta">
                    <span>${escapeHtml(s.display_category || '')}</span>
                    <span>${s.txn_count || 0} txns</span>
                </span>
            </div>
            <div class="svc-accordion-body" id="svc-body-${s.id}"></div>
        </div>
    `).join('');
}

// Cache loaded transactions per service for re-sorting without re-fetch
const svcTxCache = {};

async function toggleServiceAccordion(svcId) {
    const item = document.querySelector(`.svc-accordion-item[data-svc-id="${svcId}"]`);
    const body = document.getElementById('svc-body-' + svcId);

    if (item.classList.contains('open')) {
        item.classList.remove('open');
        return;
    }

    // Close others
    document.querySelectorAll('.svc-accordion-item.open').forEach(el => el.classList.remove('open'));

    // Load transactions
    body.innerHTML = '<div style="padding:var(--space-3);color:var(--text-tertiary);">Loading...</div>';
    item.classList.add('open');

    const res = await fetch('/api/services/' + svcId + '/transactions');
    svcTxCache[svcId] = await res.json();

    renderSvcTxTable(svcId, 'date', false);
}

function renderSvcTxTable(svcId, sortCol, sortAsc) {
    const body = document.getElementById('svc-body-' + svcId);
    const txns = svcTxCache[svcId] || [];

    if (!txns.length) {
        body.innerHTML = '<div style="padding:var(--space-3);color:var(--text-tertiary);">No transactions found</div>';
        return;
    }

    // Sort
    const sorted = [...txns].sort((a, b) => {
        let va, vb;
        switch (sortCol) {
            case 'description': va = a.description.toLowerCase(); vb = b.description.toLowerCase(); break;
            case 'amount':      va = Math.abs(a.amount_sgd); vb = Math.abs(b.amount_sgd); break;
            default:            va = a.date; vb = b.date;
        }
        if (va < vb) return sortAsc ? -1 : 1;
        if (va > vb) return sortAsc ? 1 : -1;
        return 0;
    });

    const arrow = (col) => {
        if (col !== sortCol) return '';
        return sortAsc ? ' ▲' : ' ▼';
    };

    body.innerHTML = `
        <table class="svc-tx-table">
            <thead><tr>
                <th class="sortable" style="cursor:pointer;" onclick="sortSvcTx(${svcId},'date',${sortCol === 'date' ? !sortAsc : true})">Date${arrow('date')}</th>
                <th class="sortable" style="cursor:pointer;" onclick="sortSvcTx(${svcId},'description',${sortCol === 'description' ? !sortAsc : true})">Description${arrow('description')}</th>
                <th class="sortable" style="cursor:pointer;text-align:right;" onclick="sortSvcTx(${svcId},'amount',${sortCol === 'amount' ? !sortAsc : false})">Amount${arrow('amount')}</th>
            </tr></thead>
            <tbody>
                ${sorted.map(t => `
                    <tr>
                        <td style="white-space:nowrap;">${formatDate(t.date)}</td>
                        <td>${escapeHtml(t.description)}</td>
                        <td style="text-align:right;">S$${formatAmount(Math.abs(t.amount_sgd))}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
        <div style="margin-top:var(--space-3);font-size:12px;color:var(--text-tertiary);">
            ${sorted.length} transaction${sorted.length !== 1 ? 's' : ''} &middot;
            Total: S$${formatAmount(sorted.reduce((s, t) => s + Math.abs(t.amount_sgd), 0))}
        </div>
    `;
}

function sortSvcTx(svcId, col, asc) {
    renderSvcTxTable(svcId, col, asc);
}

// ============================================================
// SERVICES TAB — VIEW TOGGLE + DEEP LINKING
// ============================================================

let currentServiceView = 'service'; // 'service' or 'category'

function switchServiceView(view) {
    currentServiceView = view;
    document.querySelectorAll('.svc-view-btn').forEach(b => b.classList.remove('active'));
    document.querySelector(`.svc-view-btn[data-view="${view}"]`)?.classList.add('active');

    document.getElementById('svc-view-service').style.display = view === 'service' ? '' : 'none';
    document.getElementById('svc-view-category').style.display = view === 'category' ? '' : 'none';

    if (view === 'service') renderServicesTab();
    if (view === 'category') renderServicesCategoryView();
}

// Deep link: navigate to Services tab, open a specific service accordion, scroll to it
async function navigateToService(svcId) {
    // Clear filters so the target service is visible
    const searchEl = document.getElementById('services-search');
    if (searchEl) searchEl.value = '';
    const catEl = document.getElementById('services-category-filter');
    if (catEl) catEl.value = '';

    switchTab('services');
    // Set view without triggering a render (avoid double-render race)
    currentServiceView = 'service';
    document.querySelectorAll('.svc-view-btn').forEach(b => b.classList.remove('active'));
    document.querySelector('.svc-view-btn[data-view="service"]')?.classList.add('active');
    document.getElementById('svc-view-service').style.display = '';
    document.getElementById('svc-view-category').style.display = 'none';

    // Ensure data is loaded, then render once
    if (!allServicesList || !allServicesList.length) {
        const res = await fetch('/api/services');
        allServicesList = await res.json();
    }
    renderServicesTab(true);

    // Small delay for DOM render, then open and scroll
    await new Promise(r => setTimeout(r, 150));
    await toggleServiceAccordion(svcId);
    const item = document.querySelector(`.svc-accordion-item[data-svc-id="${svcId}"]`);
    if (item) item.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// Deep link: navigate to Services tab in Category view, expand a category (and optionally subcategory)
async function navigateToCategory(parentName, subcatName) {
    // Clear filters so target is visible
    const searchEl = document.getElementById('services-cat-search');
    if (searchEl) searchEl.value = '';
    const catFilter = document.getElementById('services-cat-category-filter');
    if (catFilter) catFilter.value = '';
    const spendFilter = document.getElementById('services-cat-spend-filter');
    if (spendFilter) spendFilter.value = '';

    switchTab('services');
    switchServiceView('category');

    // Ensure data is loaded
    if (!allServicesList || !allServicesList.length) {
        const res = await fetch('/api/services');
        allServicesList = await res.json();
    }
    renderServicesCategoryView();

    // Small delay for DOM render, then open and scroll
    await new Promise(r => setTimeout(r, 150));

    // Open parent category accordion
    let scrollTarget = null;
    const items = document.querySelectorAll('.cat-accordion-item');
    for (const el of items) {
        if (el.dataset.catName === parentName) {
            el.classList.add('open');
            scrollTarget = el;

            // If subcategory specified, open it too
            if (subcatName) {
                await new Promise(r => setTimeout(r, 50));
                const subcats = el.querySelectorAll('.subcat-accordion-item');
                for (const sub of subcats) {
                    // Match by the subcategory name text
                    const nameEl = sub.querySelector('.subcat-name');
                    if (nameEl && nameEl.textContent.trim() === subcatName) {
                        sub.classList.add('open');
                        scrollTarget = sub;
                        break;
                    }
                }
            }
            break;
        }
    }
    if (scrollTarget) scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ============================================================
// SERVICES TAB — CATEGORY VIEW (nested: category → services)
// ============================================================

let catViewFilterPopulated = false;

async function renderServicesCategoryView() {
    if (!allServicesList || !allServicesList.length) {
        const res = await fetch('/api/services');
        allServicesList = await res.json();
    }

    // Populate category filter dropdown (once)
    if (!catViewFilterPopulated) {
        const catFilter = document.getElementById('services-cat-category-filter');
        if (catFilter) {
            const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
            parents.forEach(p => {
                catFilter.innerHTML += `<option value="${p.name}">${p.name}</option>`;
            });
            catViewFilterPopulated = true;
        }
    }

    const container = document.getElementById('services-cat-accordion');
    const search = (document.getElementById('services-cat-search')?.value || '').toLowerCase();
    const catFilterVal = document.getElementById('services-cat-category-filter')?.value || '';
    const spendFilter = document.getElementById('services-cat-spend-filter')?.value || '';

    let services = allServicesList;

    // Apply filters
    if (search) {
        services = services.filter(s =>
            s.name.toLowerCase().includes(search) ||
            (s.display_category || '').toLowerCase().includes(search)
        );
    }
    if (catFilterVal) {
        // Match parent category name or category name (for top-level cats)
        services = services.filter(s =>
            (s.parent_name || s.category_name) === catFilterVal
        );
    }
    if (spendFilter === 'personal') {
        services = services.filter(s => s.is_personal !== 0);
    } else if (spendFilter === 'moom') {
        services = services.filter(s => s.is_personal === 0);
    }

    // Build 3-level hierarchy: Parent Category → Subcategory → Services
    // Structure: { parentCat: { subcats: { subcatName: [services] }, directServices: [services] } }
    const hierarchy = {};
    services.forEach(s => {
        const parentCat = s.parent_name || s.category_name || 'Uncategorized';
        if (!hierarchy[parentCat]) hierarchy[parentCat] = { subcats: {}, direct: [] };

        if (s.parent_name && s.category_name !== s.parent_name) {
            // Has a subcategory
            const subcat = s.category_name;
            if (!hierarchy[parentCat].subcats[subcat]) hierarchy[parentCat].subcats[subcat] = [];
            hierarchy[parentCat].subcats[subcat].push(s);
        } else {
            // Top-level category, no subcategory
            hierarchy[parentCat].direct.push(s);
        }
    });

    const sortedParents = Object.keys(hierarchy).sort((a, b) => a.localeCompare(b));

    if (!sortedParents.length) {
        container.innerHTML = '<div style="padding:var(--space-4);color:var(--text-tertiary);">No categories found</div>';
        return;
    }

    container.innerHTML = sortedParents.map(parentName => {
        const group = hierarchy[parentName];
        const subcatNames = Object.keys(group.subcats).sort((a, b) => a.localeCompare(b));
        const allSvcs = [...group.direct, ...subcatNames.flatMap(sc => group.subcats[sc])];
        const totalTxns = allSvcs.reduce((sum, s) => sum + (s.txn_count || 0), 0);
        const totalSvcs = allSvcs.length;

        // Render subcategory accordions
        const subcatHtml = subcatNames.map(subcatName => {
            const svcs = group.subcats[subcatName].sort((a, b) => a.name.localeCompare(b.name));
            const subTxns = svcs.reduce((sum, s) => sum + (s.txn_count || 0), 0);
            return `
            <div class="subcat-accordion-item">
                <div class="subcat-accordion-header" onclick="this.parentElement.classList.toggle('open')">
                    <span class="svc-accordion-arrow">&#9654;</span>
                    <span class="subcat-name">${escapeHtml(subcatName)}</span>
                    <span class="svc-accordion-meta">
                        <span>${svcs.length} svc${svcs.length !== 1 ? 's' : ''}</span>
                        <span>${subTxns} txns</span>
                    </span>
                </div>
                <div class="subcat-accordion-body">
                    ${renderCatServiceItems(svcs)}
                </div>
            </div>`;
        }).join('');

        // Render direct services (no subcategory)
        const directHtml = group.direct.length ? renderCatServiceItems(
            group.direct.sort((a, b) => a.name.localeCompare(b.name))
        ) : '';

        return `
        <div class="cat-accordion-item" data-cat-name="${escapeHtml(parentName)}">
            <div class="cat-accordion-header" onclick="toggleCatAccordion(this.parentElement)">
                <span class="svc-accordion-arrow">&#9654;</span>
                <span class="svc-accordion-name">${escapeHtml(parentName)}</span>
                <span class="svc-accordion-meta">
                    <span>${totalSvcs} service${totalSvcs !== 1 ? 's' : ''}</span>
                    <span>${totalTxns} txns</span>
                </span>
            </div>
            <div class="cat-accordion-body">
                ${subcatHtml}${directHtml}
            </div>
        </div>`;
    }).join('');
}

// Shared helper: render service accordion items for category view
function renderCatServiceItems(svcs) {
    return svcs.map(s => `
        <div class="svc-accordion-item" data-svc-id="${s.id}">
            <div class="svc-accordion-header" onclick="toggleCatServiceAccordion(${s.id}, this.parentElement)">
                <span class="svc-accordion-arrow">&#9654;</span>
                <span class="svc-accordion-name">${escapeHtml(s.name)}</span>
                <span class="svc-accordion-meta">
                    <span>${s.txn_count || 0} txns</span>
                </span>
            </div>
            <div class="svc-accordion-body" id="cat-svc-body-${s.id}"></div>
        </div>
    `).join('');
}

function toggleCatAccordion(el) {
    el.classList.toggle('open');
}

async function toggleCatServiceAccordion(svcId, item) {
    const body = document.getElementById('cat-svc-body-' + svcId);

    if (item.classList.contains('open')) {
        item.classList.remove('open');
        return;
    }

    // Close sibling service accordions in same category
    item.parentElement.querySelectorAll('.svc-accordion-item.open').forEach(el => el.classList.remove('open'));

    body.innerHTML = '<div style="padding:var(--space-3);color:var(--text-tertiary);">Loading...</div>';
    item.classList.add('open');

    if (!svcTxCache[svcId]) {
        const res = await fetch('/api/services/' + svcId + '/transactions');
        svcTxCache[svcId] = await res.json();
    }

    renderCatSvcTxTable(svcId, 'date', false);
}

function renderCatSvcTxTable(svcId, sortCol, sortAsc) {
    const body = document.getElementById('cat-svc-body-' + svcId);
    const txns = svcTxCache[svcId] || [];

    if (!txns.length) {
        body.innerHTML = '<div style="padding:var(--space-3);color:var(--text-tertiary);">No transactions found</div>';
        return;
    }

    const sorted = [...txns].sort((a, b) => {
        let va, vb;
        switch (sortCol) {
            case 'description': va = a.description.toLowerCase(); vb = b.description.toLowerCase(); break;
            case 'amount':      va = Math.abs(a.amount_sgd); vb = Math.abs(b.amount_sgd); break;
            default:            va = a.date; vb = b.date;
        }
        if (va < vb) return sortAsc ? -1 : 1;
        if (va > vb) return sortAsc ? 1 : -1;
        return 0;
    });

    const arrow = (col) => col !== sortCol ? '' : (sortAsc ? ' ▲' : ' ▼');

    body.innerHTML = `
        <table class="svc-tx-table">
            <thead><tr>
                <th style="cursor:pointer;" onclick="sortCatSvcTx(${svcId},'date',${sortCol === 'date' ? !sortAsc : true})">Date${arrow('date')}</th>
                <th style="cursor:pointer;" onclick="sortCatSvcTx(${svcId},'description',${sortCol === 'description' ? !sortAsc : true})">Description${arrow('description')}</th>
                <th style="cursor:pointer;text-align:right;" onclick="sortCatSvcTx(${svcId},'amount',${sortCol === 'amount' ? !sortAsc : false})">Amount${arrow('amount')}</th>
            </tr></thead>
            <tbody>
                ${sorted.map(t => `
                    <tr>
                        <td style="white-space:nowrap;">${formatDate(t.date)}</td>
                        <td>${escapeHtml(t.description)}</td>
                        <td style="text-align:right;">S$${formatAmount(Math.abs(t.amount_sgd))}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
        <div style="margin-top:var(--space-3);font-size:12px;color:var(--text-tertiary);">
            ${sorted.length} txn${sorted.length !== 1 ? 's' : ''} &middot;
            Total: S$${formatAmount(sorted.reduce((s, t) => s + Math.abs(t.amount_sgd), 0))}
        </div>
    `;
}

function sortCatSvcTx(svcId, col, asc) {
    renderCatSvcTxTable(svcId, col, asc);
}

// ============================================================
// CATEGORIES MASTER TAB
// ============================================================

function renderCategoriesMaster() {
    renderCategoryTree();
}

// ============================================================
// SERVICES MASTER TAB (CRUD)
// ============================================================

let allServicesCache = [];
let svcMasterSortCol = 'name';
let svcMasterSortAsc = true;

async function renderServicesMaster(skipFetch) {
    if (!skipFetch) {
        const res = await fetch('/api/services');
        allServicesCache = await res.json();
    }
    const search = (document.getElementById('svc-master-search')?.value || '').toLowerCase();

    let filtered = allServicesCache;
    if (search) filtered = filtered.filter(s => s.name.toLowerCase().includes(search));

    // Sort
    filtered = [...filtered].sort((a, b) => {
        let va, vb;
        switch (svcMasterSortCol) {
            case 'category': va = (a.display_category || '').toLowerCase(); vb = (b.display_category || '').toLowerCase(); break;
            case 'notes':    va = (a.notes || '').toLowerCase(); vb = (b.notes || '').toLowerCase(); break;
            default:         va = a.name.toLowerCase(); vb = b.name.toLowerCase();
        }
        if (va < vb) return svcMasterSortAsc ? -1 : 1;
        if (va > vb) return svcMasterSortAsc ? 1 : -1;
        return 0;
    });

    // Update sort indicators
    document.querySelectorAll('#services-master-table th.sortable').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sort === svcMasterSortCol) {
            th.classList.add(svcMasterSortAsc ? 'sort-asc' : 'sort-desc');
        }
    });

    const body = document.getElementById('services-master-body');
    if (!filtered.length) {
        body.innerHTML = '<tr><td colspan="4" style="color:var(--text-tertiary);text-align:center;padding:var(--space-6);">No services</td></tr>';
        return;
    }

    body.innerHTML = filtered.map(s => `
        <tr>
            <td style="font-weight:500;">${escapeHtml(s.name)}</td>
            <td style="font-size:12px;color:var(--text-tertiary);">${escapeHtml(s.display_category || '')}</td>
            <td style="font-size:12px;color:var(--text-tertiary);">${escapeHtml(s.notes || '')}</td>
            <td style="text-align:right;white-space:nowrap;">
                <button class="btn btn-sm" onclick="openEditServiceModal(${s.id})">Edit</button>
            </td>
        </tr>
    `).join('');

    // Bind sortable headers
    document.querySelectorAll('#services-master-table th.sortable').forEach(th => {
        th.onclick = () => sortServicesMaster(th.dataset.sort);
    });
}

function sortServicesMaster(col) {
    if (svcMasterSortCol === col) {
        svcMasterSortAsc = !svcMasterSortAsc;
    } else {
        svcMasterSortCol = col;
        svcMasterSortAsc = true;
    }
    renderServicesMaster(true);
}

// ============================================================
// SERVICES MASTER — CLEANUP VIEW
// ============================================================

function switchMasterView(view) {
    document.querySelectorAll('#tab-services-master .svc-view-btn').forEach(b => b.classList.remove('active'));
    document.querySelector(`#tab-services-master .svc-view-btn[data-view="${view}"]`)?.classList.add('active');
    document.getElementById('svc-master-table-view').style.display = view === 'table' ? '' : 'none';
    document.getElementById('svc-master-cleanup-view').style.display = view === 'cleanup' ? '' : 'none';
    if (view === 'cleanup') renderCleanupView();
}

// Heuristic: service name likely needs rename if it looks like a raw pattern
function looksLikePattern(name) {
    if (!name) return false;
    // Contains * (card pattern artifact)
    if (name.includes('*')) return true;
    // Contains long numbers (account/reference numbers)
    if (/\d{4,}/.test(name)) return true;
    // All uppercase with spaces and >4 chars (raw description)
    if (name === name.toUpperCase() && name.length > 4 && /\s/.test(name)) return true;
    // All uppercase letters/digits/punctuation, >6 chars (e.g. "NET*SUBWAY")
    if (/^[A-Z][A-Z0-9 .*\-/]+$/.test(name) && name.length > 6) return true;
    return false;
}

let cleanupEdits = {}; // { svcId: newName }

function renderCleanupView() {
    const container = document.getElementById('svc-cleanup-list');
    const search = (document.getElementById('svc-cleanup-search')?.value || '').toLowerCase();
    const filter = document.getElementById('svc-cleanup-filter')?.value || 'all';

    let services = allServicesCache;
    if (search) {
        services = services.filter(s =>
            s.name.toLowerCase().includes(search) ||
            (s.rules || []).some(r => r.pattern.toLowerCase().includes(search))
        );
    }

    switch (filter) {
        case 'needs-rename':
            services = services.filter(s => looksLikePattern(s.name));
            break;
        case 'no-txns':
            services = services.filter(s => !s.txn_count);
            break;
        case 'no-rules':
            services = services.filter(s => !s.rule_count);
            break;
    }

    services = [...services].sort((a, b) => a.name.localeCompare(b.name));

    document.getElementById('svc-cleanup-count').textContent = `${services.length} service${services.length !== 1 ? 's' : ''}`;

    if (!services.length) {
        container.innerHTML = '<div style="padding:var(--space-4);color:var(--text-tertiary);">No services match this filter</div>';
        return;
    }

    container.innerHTML = services.map(s => {
        const patterns = (s.rules || []).map(r =>
            `<span class="cleanup-pattern-tag" title="${r.match_type}">${escapeHtml(r.pattern)}</span>`
        ).join('');
        const editedName = cleanupEdits[s.id] !== undefined ? cleanupEdits[s.id] : s.name;
        const isModified = cleanupEdits[s.id] !== undefined && cleanupEdits[s.id] !== s.name;

        return `
        <div class="cleanup-row" data-svc-id="${s.id}">
            <div class="cleanup-current">
                <div class="cleanup-current-name">${escapeHtml(s.name)}</div>
                <div class="cleanup-patterns">${patterns || '<span style="font-size:10px;color:var(--text-muted);">no rules</span>'}</div>
            </div>
            <div class="cleanup-arrow">→</div>
            <div class="cleanup-input-area">
                <input type="text" class="cleanup-input ${isModified ? 'modified' : ''}"
                       value="${escapeHtml(editedName)}"
                       data-svc-id="${s.id}" data-orig="${escapeHtml(s.name)}"
                       oninput="onCleanupEdit(this)">
                <div class="cleanup-meta">
                    <span>${s.txn_count || 0} txns</span>
                    <span>${s.rule_count || 0} rules</span>
                    <span>${escapeHtml(s.display_category || '—')}</span>
                </div>
            </div>
            <div class="cleanup-actions">
                <button class="btn btn-sm" onclick="openEditServiceModal(${s.id})" title="Edit / Merge">Edit</button>
            </div>
        </div>`;
    }).join('');
}

function onCleanupEdit(input) {
    const svcId = parseInt(input.dataset.svcId);
    const orig = input.dataset.orig;
    const newName = input.value.trim();

    if (newName !== orig && newName) {
        cleanupEdits[svcId] = newName;
        input.classList.add('modified');
    } else {
        delete cleanupEdits[svcId];
        input.classList.remove('modified');
    }

    // Enable/disable save button
    const count = Object.keys(cleanupEdits).length;
    const saveBtn = document.getElementById('svc-cleanup-save');
    saveBtn.disabled = count === 0;
    saveBtn.textContent = count ? `Save ${count} Rename${count > 1 ? 's' : ''}` : 'Save All Renames';
}

async function saveCleanupRenames() {
    const renames = Object.entries(cleanupEdits).map(([id, name]) => ({ id: parseInt(id), name }));
    if (!renames.length) return;

    if (!confirm(`Rename ${renames.length} service${renames.length > 1 ? 's' : ''}?`)) return;

    const res = await fetch('/api/services/bulk-rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ renames }),
    });
    const data = await res.json();

    if (data.errors && data.errors.length) {
        alert(`${data.updated} renamed, ${data.errors.length} errors:\n${data.errors.join('\n')}`);
    } else {
        alert(`${data.updated} service${data.updated !== 1 ? 's' : ''} renamed.`);
    }

    // Reset state and refresh
    cleanupEdits = {};
    const saveBtn = document.getElementById('svc-cleanup-save');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Save All Renames';

    // Refresh data
    const r = await fetch('/api/services');
    allServicesCache = await r.json();
    allServicesList = null; // clear services tab cache too
    renderCleanupView();
}

// Wire cleanup search debounce in initTabs
(function() {
    const el = document.getElementById('svc-cleanup-search');
    if (el) el.addEventListener('input', debounce(renderCleanupView, 300));
})();

function openAddServiceModal() {
    document.getElementById('add-svc-name').value = '';
    document.getElementById('add-svc-notes').value = '';

    // Populate category dropdown
    const sel = document.getElementById('add-svc-category');
    sel.innerHTML = '<option value="">—</option>';
    const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
    parents.forEach(p => {
        sel.innerHTML += `<option value="${p.id}">${p.name}</option>`;
        const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
        children.forEach(c => {
            sel.innerHTML += `<option value="${c.id}">&nbsp;&nbsp;${p.name} > ${c.name}</option>`;
        });
    });

    const modal = document.getElementById('add-service-modal');
    modal.style.display = 'flex';
    modal.onclick = (e) => { if (e.target === modal) closeAddServiceModal(); };
    document.getElementById('add-svc-name').focus();
}

function closeAddServiceModal() {
    document.getElementById('add-service-modal').style.display = 'none';
}

async function saveNewService() {
    const name = document.getElementById('add-svc-name').value.trim();
    if (!name) { alert('Service name is required'); return; }

    const body = {
        name,
        category_id: document.getElementById('add-svc-category').value ? parseInt(document.getElementById('add-svc-category').value) : null,
        notes: document.getElementById('add-svc-notes').value.trim() || null,
    };

    const res = await fetch('/api/services', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    closeAddServiceModal();
    await renderServicesMaster();
}

function openEditServiceModal(svcId) {
    const svc = allServicesCache.find(s => s.id === svcId);
    if (!svc) return;

    document.getElementById('edit-svc-id').value = svc.id;
    document.getElementById('edit-svc-name').value = svc.name || '';
    document.getElementById('edit-svc-notes').value = svc.notes || '';
    document.getElementById('edit-svc-one-off').checked = !!svc.is_one_off;

    // Populate category dropdown
    const sel = document.getElementById('edit-svc-category');
    sel.innerHTML = '<option value="">—</option>';
    const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
    parents.forEach(p => {
        sel.innerHTML += `<option value="${p.id}">${p.name}</option>`;
        const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
        children.forEach(c => {
            sel.innerHTML += `<option value="${c.id}">&nbsp;&nbsp;${p.name} > ${c.name}</option>`;
        });
    });
    if (svc.category_id) sel.value = svc.category_id;

    // Populate merge target dropdown (all services except this one, sorted by name)
    const mergeSel = document.getElementById('edit-svc-merge-target');
    mergeSel.innerHTML = '<option value="">— Select target —</option>';
    const mergeBtn = document.getElementById('edit-svc-merge-btn');
    const mergeInfo = document.getElementById('edit-svc-merge-info');
    mergeBtn.disabled = true;
    mergeInfo.textContent = '';

    allServicesCache
        .filter(s => s.id !== svcId)
        .sort((a, b) => a.name.localeCompare(b.name))
        .forEach(s => {
            mergeSel.innerHTML += `<option value="${s.id}">${s.name} (${s.txn_count || 0} txns)</option>`;
        });

    mergeSel.onchange = () => {
        const targetId = mergeSel.value;
        mergeBtn.disabled = !targetId;
        if (targetId) {
            const target = allServicesCache.find(s => s.id === parseInt(targetId));
            mergeInfo.textContent = target
                ? `All ${svc.txn_count || 0} txns, ${svc.rule_count || 0} rules will move to "${target.name}". This service will be deleted.`
                : '';
        } else {
            mergeInfo.textContent = '';
        }
    };

    const modal = document.getElementById('edit-service-modal');
    modal.style.display = 'flex';
    modal.onclick = (e) => { if (e.target === modal) closeEditServiceModal(); };
}

function closeEditServiceModal() {
    document.getElementById('edit-service-modal').style.display = 'none';
}

async function saveEditService() {
    const svcId = parseInt(document.getElementById('edit-svc-id').value);
    const body = {
        name: document.getElementById('edit-svc-name').value.trim(),
        category_id: document.getElementById('edit-svc-category').value ? parseInt(document.getElementById('edit-svc-category').value) : null,
        notes: document.getElementById('edit-svc-notes').value.trim() || null,
        is_one_off: document.getElementById('edit-svc-one-off').checked ? 1 : 0,
    };

    if (!body.name) { alert('Service name is required'); return; }

    const res = await fetch(`/api/services/${svcId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert('Error: ' + (data.error || `Save failed (${res.status})`));
        return;
    }

    closeEditServiceModal();
    // Refresh both services caches since name/category may have changed
    allServicesList = null;
    await renderServicesMaster();
}

async function deleteServiceFromModal() {
    const svcId = parseInt(document.getElementById('edit-svc-id').value);
    const name = document.getElementById('edit-svc-name').value;
    if (!confirm(`Delete service "${name}"?`)) return;

    const res = await fetch(`/api/services/${svcId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    closeEditServiceModal();
    allServicesList = null;
    await renderServicesMaster();
}

async function mergeService() {
    const svcId = parseInt(document.getElementById('edit-svc-id').value);
    const targetId = parseInt(document.getElementById('edit-svc-merge-target').value);
    if (!targetId) return;

    const sourceName = document.getElementById('edit-svc-name').value;
    const target = allServicesCache.find(s => s.id === targetId);
    if (!confirm(`Merge "${sourceName}" into "${target.name}"?\n\nAll transactions, rules, and subscriptions will be reassigned. "${sourceName}" will be deleted. This cannot be undone.`)) return;

    const res = await fetch(`/api/services/${svcId}/merge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_id: targetId }),
    });
    if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert('Error: ' + (data.error || `Merge failed (${res.status})`));
        return;
    }

    const data = await res.json();
    const m = data.merged;
    alert(`Merged "${m.source}" → "${m.target}"\n${m.transactions} txns, ${m.rules} rules, ${m.subscriptions} subs reassigned.`);

    closeEditServiceModal();
    allServicesList = null;
    await renderServicesMaster();
}

// ============================================================
// UTILITIES
// ============================================================

function debounce(fn, ms) {
    let timer;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), ms);
    };
}

function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function formatDate(isoDate) {
    // "2026-02-27" → "27 Feb 2026"
    if (!isoDate) return '';
    const [y, m, d] = isoDate.split('-');
    return `${parseInt(d)} ${MONTH_NAMES[parseInt(m)]} ${y}`;
}

function formatAmount(n) {
    if (n == null) return '0.00';
    return Number(n).toLocaleString('en-SG', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ============================================================
// SUBSCRIPTIONS
// ============================================================

async function loadSubscriptions() {
    const res = await fetch('/api/subscriptions');
    allSubs = await res.json();
    // Cache FX rate from API response
    if (allSubs.length && allSubs[0].fx_rate) subsFxRate = allSubs[0].fx_rate;
    renderSubsStats(allSubs);
    renderSubscriptions(allSubs);
    populateSubsCategoryDropdown();
    // Bind sortable headers (idempotent — uses onclick)
    document.querySelectorAll('#subs-table th.sortable').forEach(th => {
        th.onclick = () => sortSubs(th.dataset.sort);
    });
}

function setSubsFilter(filter) {
    subsFilter = filter;
    document.querySelectorAll('[data-subs-filter]').forEach(b => {
        b.classList.toggle('active', b.dataset.subsFilter === filter);
    });
    renderSubsStats(allSubs);
    renderSubscriptions(allSubs);
}

function setSubsSpend(spend) {
    subsSpend = spend;
    document.querySelectorAll('[data-subs-spend]').forEach(b => {
        b.classList.toggle('active', b.dataset.subsSpend === spend);
    });
    renderSubsStats(allSubs);
    renderSubscriptions(allSubs);
}

function renderSubsStats(subs) {
    // Apply spend filter to stats
    let active = subs.filter(s => s.status === 'active');
    if (subsSpend === 'personal') active = active.filter(s => s.is_personal !== 0);
    else if (subsSpend === 'moom') active = active.filter(s => s.is_personal === 0);

    const totalMonthly = active.reduce((sum, s) => sum + (s.monthly_sgd || 0), 0);
    const personalMonthly = active.filter(s => s.is_personal !== 0).reduce((sum, s) => sum + (s.monthly_sgd || 0), 0);
    const moomMonthly = active.filter(s => s.is_personal === 0).reduce((sum, s) => sum + (s.monthly_sgd || 0), 0);
    const fxRate = subs.length ? subs[0].fx_rate : 1.35;

    document.getElementById('subs-stats-row').innerHTML = `
        <div class="stat-card">
            <div class="stat-label">Monthly Burn</div>
            <div class="stat-value">S$${formatAmount(totalMonthly)}</div>
            <div class="stat-sub">${active.length} active subscriptions</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Personal</div>
            <div class="stat-value accent">S$${formatAmount(personalMonthly)}</div>
            <div class="stat-sub">S$${formatAmount(personalMonthly * 12)}/year</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Moom (Business)</div>
            <div class="stat-value moom">S$${formatAmount(moomMonthly)}</div>
            <div class="stat-sub">S$${formatAmount(moomMonthly * 12)}/year</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">USD → SGD Rate</div>
            <div class="stat-value" style="font-size:22px;">${fxRate.toFixed(4)}</div>
            <div class="stat-sub">Live rate</div>
        </div>
    `;
}

function formatDateDMY(dateStr) {
    if (!dateStr) return '—';
    const MMM = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d)) return dateStr;
    const dd = String(d.getDate()).padStart(2, '0');
    return `${dd}-${MMM[d.getMonth()]}-${String(d.getFullYear()).slice(2)}`;
}

function sortSubs(col) {
    if (subsSortCol === col) {
        subsSortAsc = !subsSortAsc;
    } else {
        subsSortCol = col;
        subsSortAsc = true;
    }
    renderSubscriptions(allSubs);
}

function renderSubscriptions(subs) {
    let filtered = subs;
    if (subsFilter === 'active') filtered = subs.filter(s => s.status === 'active');
    else if (subsFilter === 'deactivated') filtered = subs.filter(s => s.status !== 'active');

    // Spend filter
    if (subsSpend === 'personal') filtered = filtered.filter(s => s.is_personal !== 0);
    else if (subsSpend === 'moom') filtered = filtered.filter(s => s.is_personal === 0);

    // Sort
    filtered = [...filtered].sort((a, b) => {
        let va, vb;
        switch (subsSortCol) {
            case 'service':   va = a.service.toLowerCase(); vb = b.service.toLowerCase(); break;
            case 'category':  va = (a.display_category || '').toLowerCase(); vb = (b.display_category || '').toLowerCase(); break;
            case 'billed':    va = a.amount || 0; vb = b.amount || 0; break;
            case 'monthly':   va = a.monthly_sgd || 0; vb = b.monthly_sgd || 0; break;
            case 'frequency': va = a.frequency; vb = b.frequency; break;
            case 'card':      va = (a.account_short_name || a.card || '').toLowerCase(); vb = (b.account_short_name || b.card || '').toLowerCase(); break;
            case 'last_paid': va = a.tx_last_paid || a.last_paid || ''; vb = b.tx_last_paid || b.last_paid || ''; break;
            case 'renewal':   va = a.computed_renewal || a.renewal_date || ''; vb = b.computed_renewal || b.renewal_date || ''; break;
            default:          va = a.service.toLowerCase(); vb = b.service.toLowerCase();
        }
        if (va < vb) return subsSortAsc ? -1 : 1;
        if (va > vb) return subsSortAsc ? 1 : -1;
        return 0;
    });

    // Update header sort indicators
    document.querySelectorAll('#subs-table th.sortable').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sort === subsSortCol) {
            th.classList.add(subsSortAsc ? 'sort-asc' : 'sort-desc');
        }
    });

    const body = document.getElementById('subs-body');
    const empty = document.getElementById('subs-empty');

    if (!filtered.length) {
        body.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    body.innerHTML = filtered.map(s => {
        const freqAbbr = { monthly: 'mo', yearly: 'yr', quarterly: 'qt' };
        const freqShort = freqAbbr[s.frequency] || s.frequency;
        const freqLabel = s.periods > 1 ? `${s.periods}x ${freqShort}` : freqShort;
        const lastPaidRaw = s.tx_last_paid || s.last_paid || null;
        const linkHtml = s.link ? `<a href="${escapeHtml(s.link)}" target="_blank" class="subs-link" title="Manage">Manage</a>` : '';
        const statusClass = s.status === 'active' ? '' : 'subs-row-inactive';

        // Billed = configured amount per cycle (source of truth)
        const amt = s.amount || s.amount_sgd || 0;
        const cur = s.currency || 'SGD';
        const currPrefix = cur === 'USD' ? 'US$' : 'S$';
        const billedHtml = `${currPrefix}${formatAmount(amt)}`;

        // Monthly equivalent (always SGD)
        const monthlyLabel = s.is_variable
            ? `~S$${formatAmount(s.monthly_sgd)}`
            : `S$${formatAmount(s.monthly_sgd)}`;
        const monthlyTitle = s.is_variable
            ? `3-month avg from ${s.tx_months_90d} months`
            : '';

        // Last Paid: amount + date from actual transactions, with deep-link
        const patEsc = s.match_pattern ? escapeHtml(s.match_pattern).replace(/'/g, "\\'") : '';
        const hasTxData = !!s.tx_id;
        const lastPaidDot = lastPaidRaw
            ? (hasTxData ? '<span title="From transaction" style="color:var(--accent-camel);">●</span> ' : '<span title="Manual entry" style="color:var(--text-muted);">○</span> ')
            : '';
        let lastPaidHtml;
        if (hasTxData) {
            lastPaidHtml = `${lastPaidDot}<a href="#" class="subs-tx-link" onclick="navigateToTransaction('${s.tx_last_paid}','${patEsc}');return false;">S$${formatAmount(s.tx_amount)}</a> <span style="color:var(--text-tertiary);font-size:11px;">${formatDateDMY(s.tx_last_paid)}</span>`;
        } else if (lastPaidRaw) {
            lastPaidHtml = `${lastPaidDot}${formatDateDMY(lastPaidRaw)}`;
        } else {
            lastPaidHtml = '—';
        }

        return `<tr class="${statusClass}">
            <td>
                <div style="font-weight:600;font-size:13px;">${s.service_id ? `<a href="#" class="svc-link" onclick="navigateToService(${s.service_id});return false;">${escapeHtml(s.service)}</a>` : escapeHtml(s.service)}</div>
            </td>
            <td class="subs-col-hide-sm"><a href="#" class="cat-link" onclick="navigateToCategory('${escapeHtml(s.parent_name || s.category_name || '')}'${s.parent_name ? `,'${escapeHtml(s.category_name)}'` : ''});return false;">${escapeHtml(s.display_category)}</a></td>
            <td style="text-align:right;font-size:13px;">${billedHtml}</td>
            <td style="text-align:right;font-size:13px;font-weight:600;color:var(--accent-camel);" title="${escapeHtml(monthlyTitle)}">
                ${monthlyLabel}
            </td>
            <td class="subs-col-hide-sm" style="font-size:11px;color:var(--text-tertiary);white-space:nowrap;">${freqLabel}</td>
            <td class="subs-card-cell subs-col-hide-md" title="${escapeHtml(s.account_short_name || s.card || '')}">${escapeHtml(s.account_short_name || s.card || '')}</td>
            <td style="font-size:12px;white-space:nowrap;">${lastPaidHtml}</td>
            <td class="subs-col-hide-sm" style="font-size:12px;color:var(--text-tertiary);white-space:nowrap;">${formatDateDMY(s.computed_renewal || s.renewal_date)}</td>
            <td style="text-align:right;white-space:nowrap;">
                ${linkHtml}
                <button class="btn btn-sm" onclick="openEditSubModal(${s.id})" title="Edit">Edit</button>
                <button class="btn btn-sm" onclick="toggleSubStatus(${s.id}, '${s.status}')" title="${s.status === 'active' ? 'Deactivate' : 'Activate'}">
                    ${s.status === 'active' ? 'Deact' : 'Act'}
                </button>
            </td>
        </tr>`;
    }).join('');
}

function toggleSubsForm() {
    openAddSubModal();
}

// Populate a service <select> with searchable options from allServicesList
async function populateServiceDropdown(selectEl, selectedId) {
    if (!allServicesList || !allServicesList.length) {
        const res = await fetch('/api/services');
        allServicesList = await res.json();
    }
    const sorted = [...allServicesList].sort((a, b) => a.name.localeCompare(b.name));
    selectEl.innerHTML = '<option value="">— Select service —</option>';
    sorted.forEach(s => {
        const label = s.display_category ? `${s.name} (${s.display_category})` : s.name;
        selectEl.innerHTML += `<option value="${s.id}" data-category-id="${s.category_id || ''}">${label}</option>`;
    });
    if (selectedId) selectEl.value = String(selectedId);
}

// When service dropdown changes, auto-fill category
function wireServiceAutoFill(serviceEl, categoryEl) {
    serviceEl.addEventListener('change', () => {
        const opt = serviceEl.selectedOptions[0];
        if (!opt || !opt.value) return;
        const catId = opt.dataset.categoryId;
        if (catId && categoryEl) categoryEl.value = catId;
    });
}

// Get the first rule pattern for a service (for match_pattern auto-derivation)
function getServiceRulePattern(serviceId) {
    if (!allServicesList) return null;
    const svc = allServicesList.find(s => s.id === serviceId);
    if (svc && svc.rules && svc.rules.length) return svc.rules[0].pattern;
    return null;
}

async function openAddSubModal() {
    // Reset all fields
    document.getElementById('sub-category').value = '';
    document.getElementById('sub-amount').value = '';
    document.getElementById('sub-currency').value = 'SGD';
    document.getElementById('sub-frequency').value = 'monthly';
    document.getElementById('sub-periods').value = '1';
    document.getElementById('sub-card').value = '';
    document.getElementById('sub-renewal').value = '';
    document.getElementById('sub-status').value = 'active';
    document.getElementById('sub-link').value = '';
    document.getElementById('sub-notes').value = '';

    // Populate service dropdown
    const svcSel = document.getElementById('sub-service');
    await populateServiceDropdown(svcSel, null);

    // Populate category dropdown
    const sel = document.getElementById('sub-category');
    sel.innerHTML = '<option value="">—</option>';
    categories.sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name)).forEach(c => {
        sel.innerHTML += `<option value="${c.id}">${c.display_name || c.name}</option>`;
    });

    // Wire auto-fill: selecting a service sets category
    wireServiceAutoFill(svcSel, sel);

    // Populate card dropdown from accounts
    const cardSel = document.getElementById('sub-card');
    cardSel.innerHTML = '<option value="">—</option>';
    accounts.forEach(a => {
        cardSel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
    });

    // FX rate hint — show when USD selected
    const currSel = document.getElementById('sub-currency');
    const updateFxHint = () => {
        document.querySelectorAll('#add-sub-modal .fx-rate-hint').forEach(el => {
            el.textContent = currSel.value === 'USD' && subsFxRate ? `(1 USD = ${subsFxRate.toFixed(2)} SGD)` : '';
        });
    };
    currSel.onchange = updateFxHint;
    updateFxHint();

    const modal = document.getElementById('add-sub-modal');
    modal.style.display = 'flex';
    modal.onclick = (e) => { if (e.target === modal) closeAddSubModal(); };
    document.addEventListener('keydown', addSubEscHandler);
}

function addSubEscHandler(e) {
    if (e.key === 'Escape') closeAddSubModal();
}

function closeAddSubModal() {
    document.getElementById('add-sub-modal').style.display = 'none';
    document.removeEventListener('keydown', addSubEscHandler);
}

function populateSubsCategoryDropdown() {
    // Category dropdown is now populated when the Add modal opens (openAddSubModal)
    // This function is kept for any callers that still reference it
}

async function addSubscription() {
    const svcSel = document.getElementById('sub-service');
    const serviceId = svcSel.value ? parseInt(svcSel.value) : null;
    const serviceName = svcSel.selectedOptions[0]?.textContent?.split(' (')[0] || '';
    if (!serviceId) { alert('Please select a service'); return; }

    const body = {
        service: serviceName,
        service_id: serviceId,
        category_id: document.getElementById('sub-category').value ? parseInt(document.getElementById('sub-category').value) : null,
        amount: parseFloat(document.getElementById('sub-amount').value) || 0,
        currency: document.getElementById('sub-currency').value || 'SGD',
        frequency: document.getElementById('sub-frequency').value,
        periods: parseInt(document.getElementById('sub-periods').value) || 1,
        account_id: document.getElementById('sub-card').value ? parseInt(document.getElementById('sub-card').value) : null,
        renewal_date: document.getElementById('sub-renewal').value || null,
        match_pattern: getServiceRulePattern(serviceId) || serviceName.toUpperCase(),
        status: document.getElementById('sub-status').value || 'active',
        link: document.getElementById('sub-link').value.trim() || null,
        notes: document.getElementById('sub-notes').value.trim() || null,
    };

    const res = await fetch('/api/subscriptions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    closeAddSubModal();
    await loadSubscriptions();
}

async function toggleSubStatus(subId, currentStatus) {
    const newStatus = currentStatus === 'active' ? 'deactivated' : 'active';
    await fetch(`/api/subscriptions/${subId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
    });
    await loadSubscriptions();
}

function navigateToTransaction(txDate, matchPattern) {
    // Switch to Dashboard tab, set month/year, search by pattern, scroll to tx table
    const dashBtn = document.querySelector('[data-tab="dashboard"]');
    if (dashBtn) dashBtn.click();
    const [year, month] = txDate.split('-');
    const yearSel = document.getElementById('filter-year');
    const monthSel = document.getElementById('filter-month');
    const searchEl = document.getElementById('tx-search');
    if (yearSel) yearSel.value = year;
    if (monthSel) monthSel.value = month;
    if (searchEl) searchEl.value = matchPattern || '';
    txCurrentPage = 1;
    loadDashboard();
    // Load transactions with the search term, then scroll to table
    txPage(0);
    setTimeout(() => {
        const txTable = document.getElementById('tx-table');
        if (txTable) txTable.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 400);
}

async function enrichSubscriptions() {
    const btn = document.querySelector('#tab-subs .btn-sm');
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    try {
        const res = await fetch('/api/subscriptions/enrich', { method: 'POST' });
        const data = await res.json();
        await loadSubscriptions();
    } finally {
        btn.disabled = false;
        btn.textContent = 'Refresh from Transactions';
    }
}


// ============================================================
// EDIT SUBSCRIPTION MODAL
// ============================================================

async function openEditSubModal(subId) {
    const sub = allSubs.find(s => s.id === subId);
    if (!sub) return;

    document.getElementById('edit-sub-id').value = sub.id;

    // Populate service dropdown and select current
    const svcSel = document.getElementById('edit-sub-service');
    await populateServiceDropdown(svcSel, sub.service_id);

    document.getElementById('edit-sub-amount').value = sub.amount || '';
    document.getElementById('edit-sub-currency').value = sub.currency || 'SGD';
    document.getElementById('edit-sub-freq').value = sub.frequency || 'monthly';
    document.getElementById('edit-sub-periods').value = sub.periods || 1;
    document.getElementById('edit-sub-renewal').value = sub.renewal_date || '';
    document.getElementById('edit-sub-status').value = sub.status || 'active';
    document.getElementById('edit-sub-link').value = sub.link || '';
    document.getElementById('edit-sub-notes').value = sub.notes || '';

    // Populate category dropdown
    const catSel = document.getElementById('edit-sub-category');
    catSel.innerHTML = '<option value="">—</option>';
    const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
    parents.forEach(p => {
        catSel.innerHTML += `<option value="${p.id}">${p.name}</option>`;
        const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
        children.forEach(c => {
            catSel.innerHTML += `<option value="${c.id}">&nbsp;&nbsp;${p.name} > ${c.name}</option>`;
        });
    });
    if (sub.category_id) catSel.value = sub.category_id;

    // Populate card dropdown from accounts (uses account_id as value)
    const cardSel = document.getElementById('edit-sub-card');
    cardSel.innerHTML = '<option value="">—</option>';
    accounts.forEach(a => {
        cardSel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
    });
    if (sub.account_id) cardSel.value = sub.account_id;

    // Wire auto-fill: selecting a service updates category
    wireServiceAutoFill(svcSel, catSel);

    const modal = document.getElementById('edit-sub-modal');
    modal.style.display = 'flex';
    modal.onclick = (e) => { if (e.target === modal) closeEditSubModal(); };
    document.addEventListener('keydown', editSubEscHandler);

    // FX rate hint — show when USD selected
    const editCurrSel = document.getElementById('edit-sub-currency');
    const updateEditFxHint = () => {
        document.querySelectorAll('#edit-sub-modal .fx-rate-hint').forEach(el => {
            el.textContent = editCurrSel.value === 'USD' && subsFxRate ? `(1 USD = ${subsFxRate.toFixed(2)} SGD)` : '';
        });
    };
    editCurrSel.onchange = updateEditFxHint;
    updateEditFxHint();
}

function editSubEscHandler(e) {
    if (e.key === 'Escape') closeEditSubModal();
}

function closeEditSubModal() {
    document.getElementById('edit-sub-modal').style.display = 'none';
    document.removeEventListener('keydown', editSubEscHandler);
}

async function saveEditSub() {
    const subId = parseInt(document.getElementById('edit-sub-id').value);
    const svcSel = document.getElementById('edit-sub-service');
    const serviceId = svcSel.value ? parseInt(svcSel.value) : null;
    const serviceName = svcSel.selectedOptions[0]?.textContent?.split(' (')[0] || '';
    const body = {
        service: serviceName,
        service_id: serviceId,
        category_id: document.getElementById('edit-sub-category').value ? parseInt(document.getElementById('edit-sub-category').value) : null,
        amount: parseFloat(document.getElementById('edit-sub-amount').value) || 0,
        currency: document.getElementById('edit-sub-currency').value || 'SGD',
        frequency: document.getElementById('edit-sub-freq').value,
        periods: parseInt(document.getElementById('edit-sub-periods').value) || 1,
        account_id: document.getElementById('edit-sub-card').value ? parseInt(document.getElementById('edit-sub-card').value) : null,
        renewal_date: document.getElementById('edit-sub-renewal').value || null,
        match_pattern: getServiceRulePattern(serviceId) || serviceName.toUpperCase(),
        status: document.getElementById('edit-sub-status').value,
        link: document.getElementById('edit-sub-link').value.trim() || null,
        notes: document.getElementById('edit-sub-notes').value.trim() || null,
    };

    if (!serviceId) { alert('Please select a service'); return; }

    const res = await fetch(`/api/subscriptions/${subId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    closeEditSubModal();
    await loadSubscriptions();
}

async function deleteSubFromModal() {
    const subId = parseInt(document.getElementById('edit-sub-id').value);
    const svcSel = document.getElementById('edit-sub-service');
    const service = svcSel.selectedOptions[0]?.textContent?.split(' (')[0] || '';
    if (!confirm(`Delete subscription "${service}"?`)) return;

    const res = await fetch(`/api/subscriptions/${subId}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    closeEditSubModal();
    await loadSubscriptions();
}


// ============================================================
// ACCOUNTS TAB
// ============================================================

const ACCT_TYPE_LABELS = {
    credit_card: 'Credit Card',
    debit: 'Debit Card',
    bank: 'Bank Account',
};

let editingAcctId = null;  // Track which account row is being edited

function renderAccountsTab() {
    const body = document.getElementById('acct-body');
    const empty = document.getElementById('acct-empty');
    const count = document.getElementById('acct-count');

    count.textContent = `${accounts.length} account${accounts.length !== 1 ? 's' : ''}`;

    if (!accounts.length) {
        body.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    body.innerHTML = accounts.map(a => {
        if (a.id === editingAcctId) {
            return renderAcctEditRow(a);
        }
        return `<tr>
            <td style="font-size:13px;">${escapeHtml(a.name)}</td>
            <td style="font-size:12px;color:var(--text-tertiary);">${escapeHtml(a.short_name)}</td>
            <td><span class="acct-type-badge acct-type-${a.type}">${ACCT_TYPE_LABELS[a.type] || a.type}</span></td>
            <td style="font-size:12px;color:var(--text-tertiary);">${a.last_four || '—'}</td>
            <td style="font-size:12px;color:var(--text-tertiary);">${a.currency || 'SGD'}</td>
            <td style="text-align:right;white-space:nowrap;">
                <button class="btn btn-sm" onclick="startEditAcct(${a.id})">Edit</button>
                <button class="btn btn-sm" onclick="deleteAccount(${a.id})" title="Delete">Del</button>
            </td>
        </tr>`;
    }).join('');
}

function renderAcctEditRow(a) {
    return `<tr class="editing-row">
        <td><input type="text" id="edit-acct-name" value="${escapeHtml(a.name)}" style="width:100%;font-size:13px;"></td>
        <td><input type="text" id="edit-acct-short" value="${escapeHtml(a.short_name)}" style="width:100%;font-size:12px;"></td>
        <td>
            <select id="edit-acct-type" style="width:100%;font-size:12px;">
                <option value="credit_card" ${a.type === 'credit_card' ? 'selected' : ''}>Credit Card</option>
                <option value="debit" ${a.type === 'debit' ? 'selected' : ''}>Debit Card</option>
                <option value="bank" ${a.type === 'bank' ? 'selected' : ''}>Bank Account</option>
            </select>
        </td>
        <td><input type="text" id="edit-acct-last4" value="${a.last_four || ''}" maxlength="4" style="width:100%;font-size:12px;"></td>
        <td><input type="text" id="edit-acct-currency" value="${a.currency || 'SGD'}" style="width:60px;font-size:12px;"></td>
        <td style="text-align:right;white-space:nowrap;">
            <button class="btn btn-sm btn-primary" onclick="saveEditAcct(${a.id})">Save</button>
            <button class="btn btn-sm" onclick="cancelEditAcct()">Cancel</button>
        </td>
    </tr>`;
}

function toggleAcctForm() {
    document.getElementById('acct-form').classList.toggle('hidden');
}

async function addAccount() {
    const name = document.getElementById('acct-name').value.trim();
    if (!name) { alert('Account name is required'); return; }

    const body = {
        name,
        short_name: document.getElementById('acct-short').value.trim() || name,
        type: document.getElementById('acct-type').value,
        last_four: document.getElementById('acct-last4').value.trim() || null,
        currency: document.getElementById('acct-currency').value.trim() || 'SGD',
    };

    const res = await fetch('/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    // Reset form
    document.getElementById('acct-name').value = '';
    document.getElementById('acct-short').value = '';
    document.getElementById('acct-last4').value = '';
    document.getElementById('acct-currency').value = 'SGD';
    document.getElementById('acct-form').classList.add('hidden');

    // Reload accounts globally
    await reloadAccounts();
}

function startEditAcct(id) {
    editingAcctId = id;
    renderAccountsTab();
}

function cancelEditAcct() {
    editingAcctId = null;
    renderAccountsTab();
}

async function saveEditAcct(id) {
    const body = {
        name: document.getElementById('edit-acct-name').value.trim(),
        short_name: document.getElementById('edit-acct-short').value.trim(),
        type: document.getElementById('edit-acct-type').value,
        last_four: document.getElementById('edit-acct-last4').value.trim() || null,
        currency: document.getElementById('edit-acct-currency').value.trim() || 'SGD',
    };

    if (!body.name) { alert('Account name is required'); return; }

    const res = await fetch(`/api/accounts/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    editingAcctId = null;
    await reloadAccounts();
}

async function deleteAccount(id) {
    const acct = accounts.find(a => a.id === id);
    if (!confirm(`Delete account "${acct ? acct.name : id}"?`)) return;

    const res = await fetch(`/api/accounts/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.error) { alert('Error: ' + data.error); return; }

    await reloadAccounts();
}

async function reloadAccounts() {
    // Refresh the global accounts array and update all dependent UI
    const acctRes = await fetch('/api/accounts').then(r => r.json());
    accounts = acctRes;
    populateAccountFilter();
    renderAccountsTab();
}
