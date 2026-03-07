/* fin — app.js
   Frontend logic for all 4 tabs: Dashboard, Import, History, Merchant Rules */

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
let spendFilter = 'all';      // 'all', 'personal', 'moom'
let selectedFiles = [];
let txSortCol = 'date';       // Current sort column
let txSortDir = 'desc';       // Current sort direction
let showSubcategories = false; // Charts: roll up to parent by default

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
    await loadDashboard();
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
    const selects = document.querySelectorAll('#rule-category, #tx-category-filter');
    selects.forEach(sel => {
        const isFilter = sel.id === 'tx-category-filter';
        const current = sel.value;
        sel.innerHTML = isFilter ? '<option value="">All Categories</option>' : '';

        // Group: parents first, then subcategories indented
        const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
        parents.forEach(p => {
            sel.innerHTML += `<option value="${isFilter ? p.name : p.id}">${p.name}</option>`;
            // Add subcategories indented
            const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
            children.forEach(c => {
                sel.innerHTML += `<option value="${isFilter ? c.name : c.id}">&nbsp;&nbsp;${p.name} > ${c.name}</option>`;
            });
        });
        if (current) sel.value = current;
    });
}

function populateAccountFilter() {
    const sel = document.getElementById('tx-account-filter');
    sel.innerHTML = '<option value="">All Accounts</option>';
    accounts.forEach(a => {
        sel.innerHTML += `<option value="${a.id}">${a.name}</option>`;
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
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

            // Load tab data on switch
            if (btn.dataset.tab === 'dashboard') loadDashboard();
            if (btn.dataset.tab === 'history') loadHistory();
            if (btn.dataset.tab === 'rules') { loadRules(); renderCategoryTree(); }
        });
    });
}

// ============================================================
// DASHBOARD
// ============================================================

async function loadDashboard() {
    const params = buildFilterParams();
    const granularity = getGranularity();
    const monthlyParams = params + (params ? '&' : '') + 'granularity=' + granularity;

    // group_parent=true rolls subcategories into parent in charts
    const groupParent = showSubcategories ? 'false' : 'true';
    const chartParams = monthlyParams + '&group_parent=' + groupParent;
    const catChartParams = params + (params ? '&' : '') + 'group_parent=' + groupParent;

    const [summary, monthly, catData, txData] = await Promise.all([
        fetch('/api/dashboard/summary?' + params).then(r => r.json()),
        fetch('/api/dashboard/monthly?' + chartParams).then(r => r.json()),
        fetch('/api/dashboard/categories?' + catChartParams).then(r => r.json()),
        fetch('/api/transactions?' + params + '&per_page=50&page=1&sort=' + txSortCol + '&sort_dir=' + txSortDir).then(r => r.json()),
    ]);

    renderStatCards(summary);
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

    if (spendFilter === 'personal') p.set('personal_only', 'true');
    if (spendFilter === 'moom') p.set('moom_only', 'true');
    if (document.getElementById('filter-anomaly').checked) p.set('exclude_anomaly', 'true');
    return p.toString();
}

function getGranularity() {
    const month = document.getElementById('filter-month').value;
    if (month) return 'weekly';
    return 'monthly';
}

function applyDashboardFilters() { loadDashboard(); }

function onFilterPresetChange() {
    // Auto-apply when year/month changes
    loadDashboard();
}

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
    loadDashboard();
}

function renderStatCards(data) {
    document.getElementById('stats-row').innerHTML = `
        <div class="stat-card">
            <div class="stat-label">Total Spend</div>
            <div class="stat-value">S$${formatAmount(data.total_spend)}</div>
            <div class="stat-sub">${data.total_transactions} transactions</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Personal</div>
            <div class="stat-value accent">S$${formatAmount(data.personal_spend)}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Moom (Business)</div>
            <div class="stat-value moom">S$${formatAmount(data.moom_spend)}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Uncategorized</div>
            <div class="stat-value ${data.uncategorized > 0 ? 'text-warning' : ''}">${data.uncategorized}</div>
        </div>
    `;
}

function renderMonthlyChart(data, granularity) {
    const periods = Object.keys(data).sort();
    if (!periods.length) return;

    // Update chart title based on granularity
    const titleEl = document.getElementById('chart-trend-title');
    if (granularity === 'weekly') {
        titleEl.textContent = 'Weekly Spending Trend';
    } else if (granularity === 'quarterly') {
        titleEl.textContent = 'Quarterly Spending Trend';
    } else {
        titleEl.textContent = 'Monthly Spending Trend';
    }

    // Collect all categories across periods
    const allCats = new Set();
    periods.forEach(p => Object.keys(data[p]).forEach(c => allCats.add(c)));

    // Filter categories by spend toggle
    let catList = [...allCats].sort();
    if (spendFilter === 'moom') {
        catList = catList.filter(c => c === 'Moom');
    } else if (spendFilter === 'personal') {
        catList = catList.filter(c => c !== 'Moom');
    }

    // Format labels for readability
    const labels = periods.map(p => {
        if (granularity === 'weekly') {
            // "2025-W42" → "W42"
            return p.split('-W')[1] ? 'W' + p.split('-W')[1] : p;
        }
        return p;
    });

    const datasets = catList.map((cat, i) => ({
        label: cat,
        data: periods.map(p => data[p][cat] || 0),
        backgroundColor: CAT_COLORS[i % CAT_COLORS.length],
        borderRadius: 3,
    }));

    const ctx = document.getElementById('chart-monthly');
    if (monthlyChart) monthlyChart.destroy();

    monthlyChart = new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
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
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '55%',
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
        // Note indicator: small icon after description, clickable to edit
        const noteIcon = tx.notes
            ? `<span class="tx-note-icon has-note" title="${escapeHtml(tx.notes)}" onclick="editNote(${tx.id}, this)">&#9998;</span>`
            : `<span class="tx-note-icon" title="Add note" onclick="editNote(${tx.id}, this)">&#9998;</span>`;
        tr.innerHTML = `
            <td class="col-date">${formatDate(tx.date)}</td>
            <td class="col-desc">${escapeHtml(tx.description)}${noteIcon}</td>
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
    const cat = document.getElementById('tx-category-filter').value;
    const acct = document.getElementById('tx-account-filter').value;
    let url = `/api/transactions?${params}&per_page=50&page=${txCurrentPage}&sort=${txSortCol}&sort_dir=${txSortDir}`;
    if (search) url += '&search=' + encodeURIComponent(search);
    if (cat) url += '&category=' + encodeURIComponent(cat);
    if (acct) url += '&account_id=' + acct;
    fetch(url).then(r => r.json()).then(renderTransactions);
}

async function editNote(txId, iconEl) {
    // Simple prompt for now — could upgrade to inline editor later
    const current = iconEl.classList.contains('has-note') ? iconEl.title : '';
    const note = prompt('Transaction note:', current);
    if (note === null) return; // cancelled

    await fetch(`/api/transactions/${txId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: note || null }),
    });

    // Update icon state inline without full reload
    if (note) {
        iconEl.classList.add('has-note');
        iconEl.title = note;
    } else {
        iconEl.classList.remove('has-note');
        iconEl.title = 'Add note';
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

    // On selection, save and refresh
    select.addEventListener('change', async () => {
        const catId = parseInt(select.value);
        if (!catId) return;
        await fetch(`/api/transactions/${txId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ category_id: catId }),
        });
        txPage(0); // reload current page to show updated category
    });

    // On blur (click away), cancel
    select.addEventListener('blur', () => {
        txPage(0); // reload to restore badges
    });
}

// Wire up search/filter inputs
document.addEventListener('DOMContentLoaded', () => {
    ['tx-search', 'tx-category-filter', 'tx-account-filter'].forEach(id => {
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
});

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
                            <th style="width:50%;">Pattern</th>
                            <th style="width:18%;">Match</th>
                            <th style="width:18%;">Confidence</th>
                            <th style="width:14%;text-align:right;">Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${g.rules.map(r => `
                            <tr>
                                <td class="text-mono" style="font-size:12px;">${escapeHtml(r.pattern)}</td>
                                <td style="font-size:12px;color:var(--text-tertiary);">${r.match_type}</td>
                                <td style="font-size:12px;color:var(--text-tertiary);">${r.confidence}</td>
                                <td style="text-align:right;white-space:nowrap;">
                                    <button class="btn btn-sm" onclick="editRule(${r.id})" style="margin-right:4px;">Edit</button>
                                    <button class="btn btn-sm btn-danger" onclick="deleteRule(${r.id})">Del</button>
                                </td>
                            </tr>
                        `).join('')}
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

    if (!pattern || !categoryId) {
        alert('Pattern and category are required');
        return;
    }

    const res = await fetch('/api/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pattern, category_id: parseInt(categoryId), match_type: matchType }),
    });
    const data = await res.json();

    if (data.error) {
        alert('Error: ' + data.error);
        return;
    }

    document.getElementById('rule-pattern').value = '';
    await loadRules();
}

async function editRule(ruleId) {
    const rule = allRules.find(r => r.id === ruleId);
    if (!rule) return;

    const newPattern = prompt('Edit pattern:', rule.pattern);
    if (newPattern === null) return;

    const catNames = categories.map(c => c.name);
    const newCat = prompt(`Edit category (${catNames.join(', ')}):`, rule.category_name);
    if (newCat === null) return;

    const cat = categories.find(c => c.name.toLowerCase() === newCat.toLowerCase());
    if (!cat) {
        alert('Unknown category: ' + newCat);
        return;
    }

    await fetch(`/api/rules/${ruleId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pattern: newPattern, category_id: cat.id }),
    });
    await loadRules();
}

async function deleteRule(ruleId) {
    if (!confirm('Delete this rule?')) return;
    await fetch(`/api/rules/${ruleId}`, { method: 'DELETE' });
    await loadRules();
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
            html += `<div class="cat-group">
                <div class="cat-group-name">
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
// UTILITIES
// ============================================================

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
