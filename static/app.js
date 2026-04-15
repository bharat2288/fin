/* fin — app.js
   Frontend logic for Dashboard, Import, Subscriptions, Services, and Masters tabs */

// ============================================================
// STATE
// ============================================================

let categories = [];          // [{id, name, parent_id, parent_name, display_name, is_personal, scope}, ...]
let accounts = [];            // [{id, name, ...}, ...]
let currentImportId = null;   // Active import preview
let currentImportData = null; // Preview data from upload
let importServices = [];      // [{id, name, category_id}, ...] from upload response
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
const subsSortState = { col: 'service', asc: true };
// sortSubs toggler created after renderSubscriptions is defined (line ~3400)
let subsFxRate = 1.35;         // USD→SGD rate from subscriptions API
let noteModalTxId = null;      // Transaction ID being edited in note modal
let noteModalIconEl = null;    // Icon element to update after save
// Chart-table linking state — selections[] model (any combo of category + period)
// Each entry: { category: string, period: string|null }
// period=null means "all periods" (from donut click)
let chartFilter = { selections: [] };
let monthlyPeriods = [];       // Raw period strings for chart click lookup
let catFilterSelections = [];  // Multi-select category filter selections
let allServicesList = [];      // Cached services list (shared by resolve modal, services master, subs)
let categoryColorMap = {};     // category name → hex color (shared between bar + donut)

// Category color palette (Dark Neutral chart tokens)
const CAT_COLORS = [
    '#e8e0d8', '#ff6b6b', '#a09890', '#4aba6a', '#d4a85c',
    '#7090c0', '#c090b0', '#80b8a8', '#e8e0d8', '#ff6b6b',
    '#a09890', '#4aba6a', '#d4a85c', '#7090c0', '#c090b0',
    '#80b8a8', '#707070', '#a0a0a0', '#505050', '#333333',
    '#e8e0d8', '#ff6b6b',
];

const MONTH_NAMES = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// ============================================================
// SHARED HELPERS
// ============================================================

// Build hierarchical category <option> HTML from the global categories array.
// Options: placeholder (default "—"), includeNew (appends "+ New category..."), selectedId
function buildCategoryDropdownHtml({placeholder = '—', includeNew = false, selectedId = null} = {}) {
    let html = `<option value="">${placeholder}</option>`;
    const parents = categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name));
    parents.forEach(p => {
        html += `<option value="${p.id}"${p.id == selectedId ? ' selected' : ''}>${escapeHtml(p.name)}</option>`;
        const children = categories.filter(c => c.parent_id === p.id).sort((a, b) => a.name.localeCompare(b.name));
        children.forEach(c => {
            html += `<option value="${c.id}"${c.id == selectedId ? ' selected' : ''}>&nbsp;&nbsp;${escapeHtml(p.name)} > ${escapeHtml(c.name)}</option>`;
        });
    });
    if (includeNew) html += '<option value="__new__">+ New category...</option>';
    return html;
}

// Populate a <select> element with hierarchical category options.
// parentSelectId: optional ID of a parent-only dropdown to populate alongside (for "new category" flows)
function populateCategorySelect(selectId, {placeholder, includeNew, selectedId, parentSelectId} = {}) {
    const sel = document.getElementById(selectId);
    sel.innerHTML = buildCategoryDropdownHtml({placeholder, includeNew, selectedId});
    if (parentSelectId) {
        const parentSel = document.getElementById(parentSelectId);
        if (parentSel) {
            parentSel.innerHTML = '<option value="">-- Top-level --</option>';
            categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name))
                .forEach(p => { parentSel.innerHTML += `<option value="${p.id}">${p.name}</option>`; });
        }
    }
}

function categoryDisplayName(categoryId) {
    if (!categoryId) return '';
    const cat = categories.find(c => c.id === categoryId);
    if (!cat) return '';
    if (!cat.parent_id) return cat.name;
    const parent = categories.find(c => c.id === cat.parent_id);
    return parent ? `${parent.name} > ${cat.name}` : cat.name;
}

// Factory: create a sort toggler for a table section.
// stateObj must have { col, asc } properties. renderFn is called after toggling.
function createSortToggler(stateObj, renderFn) {
    return function(col) {
        if (stateObj.col === col) {
            stateObj.asc = !stateObj.asc;
        } else {
            stateObj.col = col;
            stateObj.asc = true;
        }
        renderFn();
    };
}

// Update sort indicator CSS classes on sortable table headers.
function updateSortIndicators(tableSelector, sortState) {
    document.querySelectorAll(`${tableSelector} th.sortable`).forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.sort === sortState.col) {
            th.classList.add(sortState.asc ? 'sort-asc' : 'sort-desc');
        }
    });
}

// Resolve a category select that may have "__new__" selected.
// Returns { id: number|null, abort: boolean }. abort=true means validation failed (caller should return).
async function resolveCategory(selectId, prefix) {
    const catVal = document.getElementById(selectId).value;
    if (catVal === '__new__') {
        const newCatName = document.getElementById(`${prefix}-new-cat-name`).value.trim();
        if (!newCatName) { alert('Please enter a category name.'); return { id: null, abort: true }; }
        const parentId = document.getElementById(`${prefix}-new-cat-parent`).value || null;
        const isPersonal = parseInt(document.getElementById(`${prefix}-new-cat-type`).value);
        const catData = await apiFetch('/api/categories', {
            method: 'POST', body: { name: newCatName, parent_id: parentId ? parseInt(parentId) : null, is_personal: isPersonal }
        });
        if (!catData) return { id: null, abort: true };
        await loadReferenceData();
        showToast(`Created category "${newCatName}"`, 'info');
        return { id: catData.id, abort: false };
    }
    return { id: catVal ? parseInt(catVal) : null, abort: false };
}

// Toggle new-category-row visibility when category select changes to/from "__new__".
function toggleNewCategoryRow(selectId, newCatRowId, focusFieldId) {
    const val = document.getElementById(selectId).value;
    const row = document.getElementById(newCatRowId);
    if (val === '__new__') { row.classList.remove('hidden'); document.getElementById(focusFieldId).focus(); }
    else { row.classList.add('hidden'); }
}

// Open a modal: display flex, optional backdrop close and Escape key handler.
// Returns a close function. Stores handler refs on the element for closeModal().
function openModalEl(modalId, closeFn) {
    const modal = document.getElementById(modalId);
    modal.style.display = 'flex';
    const backdropHandler = (e) => { if (e.target === modal) closeFn(); };
    const escHandler = (e) => { if (e.key === 'Escape') closeFn(); };
    modal.onclick = backdropHandler;
    document.addEventListener('keydown', escHandler);
    modal._escHandler = escHandler;
}

function closeModalEl(modalId) {
    const modal = document.getElementById(modalId);
    modal.style.display = 'none';
    if (modal._escHandler) {
        document.removeEventListener('keydown', modal._escHandler);
        modal._escHandler = null;
    }
    modal.onclick = null;
}

// Setup FX rate hint on a currency <select> within a modal.
function setupFxHint(modalId, currencySelectId) {
    const currSel = document.getElementById(currencySelectId);
    const update = () => {
        document.querySelectorAll(`#${modalId} .fx-rate-hint`).forEach(el => {
            el.textContent = currSel.value === 'USD' && subsFxRate ? `(1 USD = ${subsFxRate.toFixed(2)} SGD)` : '';
        });
    };
    currSel.onchange = update;
    update();
}

// Compute renewal date from start + frequency + periods.
function calcRenewalDate(startVal, freq, periods) {
    const start = new Date(startVal);
    if (freq === 'yearly') start.setFullYear(start.getFullYear() + periods);
    else if (freq === 'half-yearly') start.setMonth(start.getMonth() + 6 * periods);
    else if (freq === 'quarterly') start.setMonth(start.getMonth() + 3 * periods);
    else if (freq === 'biweekly') start.setDate(start.getDate() + 14 * periods);
    else if (freq === 'weekly') start.setDate(start.getDate() + 7 * periods);
    else start.setMonth(start.getMonth() + periods);
    return start.toISOString().split('T')[0];
}

// Update a service's category and show toast with recategorization count.
// Returns the recategorized count (0 if failed/skipped).
async function cascadeServiceCategory(serviceId, categoryId, serviceName, verb) {
    if (!serviceId || !categoryId) return 0;
    const svcData = await apiFetch(`/api/services/${serviceId}`, {
        method: 'PUT', body: { category_id: categoryId }
    });
    if (svcData && svcData.recategorized) {
        showToast(`${verb} "${serviceName}" + re-categorized ${svcData.recategorized} transactions`, 'info');
        return svcData.recategorized;
    }
    return 0;
}

function getScope(item) {
    if (!item) return 'personal';
    if (item.scope) return item.scope;
    return item.is_personal === 0 ? 'moom' : 'personal';
}

function scopeLabel(scope) {
    if (scope === 'moom') return 'Moom';
    if (scope === 'kalesh') return 'Kalesh';
    return 'Personal';
}

function matchesScope(filter, item) {
    return filter === 'all' || getScope(item) === filter;
}

function scopeBadgeHtml(scope) {
    if (!scope || scope === 'personal') return '';
    return ` <span class="badge badge-muted">${escapeHtml(scopeLabel(scope))}</span>`;
}

// Build a visibility-change hint for toast when a transaction moves between spend filters.
function spendFilterHint(categoryId) {
    const cat = categories.find(c => c.id === categoryId);
    if (!cat) return null;
    const nextScope = getScope(cat);
    if (spendFilter !== 'all' && nextScope !== spendFilter) {
        return `Moved to ${scopeLabel(nextScope)} — switch to "All" to see it`;
    }
    return null;
}

// Resolve service+category from a picker: validate, create new service if needed.
// Returns { serviceId, categoryId, serviceName } or null if aborted.
async function resolveSubService(pickerFn, catSelectId, catPrefix) {
    const picker = pickerFn();
    let { id: serviceId, name: serviceName } = picker.getValue();
    if (!serviceName) { alert('Please select or create a service'); return null; }

    const { id: categoryId, abort } = await resolveCategory(catSelectId, catPrefix);
    if (abort) return null;

    if (!serviceId && serviceName) {
        if (!categoryId) { alert('Please select a category for the new service.'); return null; }
        const svcData = await apiFetch('/api/services', {
            method: 'POST', body: { name: serviceName, category_id: categoryId }
        });
        if (!svcData) return null;
        serviceId = svcData.id;
        allServicesList = null;
        showToast(`Created service "${serviceName}"`, 'info');
    }
    return { serviceId, categoryId, serviceName };
}

// Read subscription form fields. prefix: 'sub' for add, 'edit-sub' for edit.
function readSubFormBody(prefix, serviceId, categoryId, serviceName) {
    const el = (id) => document.getElementById(`${prefix}-${id}`);
    return {
        service_id: serviceId,
        category_id: categoryId,
        amount: parseFloat(el('amount').value) || 0,
        currency: el('currency').value || 'SGD',
        frequency: el(prefix === 'sub' ? 'frequency' : 'freq').value,
        periods: parseInt(el('periods').value) || 1,
        account_id: el('card').value ? parseInt(el('card').value) : null,
        renewal_date: el('renewal').value || null,
        match_pattern: getServiceRulePattern(serviceId) || serviceName.toUpperCase(),
        status: el('status').value || 'active',
        link: el('link').value.trim() || null,
        notes: el('notes').value.trim() || null,
    };
}

// Fetch JSON from an API endpoint with standard error handling.
// Returns parsed JSON on success, or null if data.error was present (alert shown).
// For non-JSON responses or network errors, throws.
async function apiFetch(url, options = {}) {
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
        options.headers = {'Content-Type': 'application/json', ...options.headers};
        options.body = JSON.stringify(options.body);
    }
    const res = await fetch(url, options);
    const data = await res.json();
    if (data.error) {
        alert('Error: ' + data.error);
        return null;
    }
    return data;
}

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
        // Restore saved view mode
        if (txViewMode !== 'flat') setTxView(txViewMode);
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

    populateCatMultiSelect();
    populateAccountFilter();
    await populateYearDropdown();
}

function populateAccountFilter() {
    // Dashboard account filter
    const dashSel = document.getElementById('filter-account');
    if (dashSel) {
        const current = dashSel.value;
        dashSel.innerHTML = '<option value="">All Accounts</option>';
        accounts.forEach(a => {
            dashSel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
        });
        if (current) dashSel.value = current;
    }

    // Subscription card dropdowns (add form + edit modal)
    const activeAccounts = accounts.filter(a => a.status !== 'archived');
    ['sub-card', 'edit-sub-card'].forEach(id => {
        const sel = document.getElementById(id);
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = '<option value="">All Accounts</option>';
        activeAccounts.forEach(a => {
            sel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
        });
        if (current) sel.value = current;
    });
}

async function populateYearDropdown() {
    const [oldest, newest] = await Promise.all([
        fetch('/api/transactions?per_page=1&sort=date&sort_dir=asc').then(r => r.json()),
        fetch('/api/transactions?per_page=1&sort=date&sort_dir=desc').then(r => r.json()),
    ]);
    const sel = document.getElementById('filter-year');
    sel.innerHTML = '<option value="">All Years</option>';

    if (oldest.transactions.length && newest.transactions.length) {
        const startYear = parseInt(oldest.transactions[0].date.substring(0, 4));
        const endYear = parseInt(newest.transactions[0].date.substring(0, 4));
        for (let y = endYear; y >= startYear; y--) {
            sel.innerHTML += `<option value="${y}">${y}</option>`;
        }
        if (!sel.value) {
            if (sel.querySelector('option[value="2026"]')) sel.value = '2026';
            else sel.value = String(endYear);
        }
    }
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
    if (tabName === 'dashboard') loadDashboard(true);
    if (tabName === 'import') { loadCoverage(); loadHistory(); }
    if (tabName === 'subs') loadSubscriptions();
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

async function loadDashboard(preserveChartFilter) {
    // Clear chart selection when dashboard filters change (not when navigating back)
    if (!preserveChartFilter && chartFilter.selections.length) {
        chartFilter = { selections: [] };
        renderFilterChip();
    }

    const params = buildFilterParams();           // full filter (includes month narrowing)
    const chartFilterParams = buildChartParams();  // year-level only (no month narrowing)
    const granularity = 'monthly';
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
    if (spendFilter !== 'all') statParams += (statParams ? '&' : '') + 'scope=' + spendFilter;
    if (document.getElementById('filter-one-off').checked) statParams += (statParams ? '&' : '') + 'exclude_one_off=true';
    const accountId = document.getElementById('filter-account')?.value;
    if (accountId) statParams += (statParams ? '&' : '') + 'account_id=' + accountId;

    const [statCards, monthly, catData] = await Promise.all([
        fetch('/api/dashboard/stat-cards?' + statParams).then(r => r.json()),
        fetch('/api/dashboard/monthly?' + chartParams).then(r => r.json()),
        fetch('/api/dashboard/categories?' + catChartParams).then(r => r.json()),
    ]);

    renderStatCards(statCards);
    renderMonthlyChart(monthly, granularity);
    renderCategoryChart(catData);

    // Load transaction area via txPage (includes search, chart filter, category filter)
    txCurrentPage = 1;
    txPage(0);
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

    if (spendFilter !== 'all') p.set('scope', spendFilter);
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
    if (spendFilter !== 'all') p.set('scope', spendFilter);
    if (document.getElementById('filter-one-off').checked) p.set('exclude_one_off', 'true');
    return p.toString();
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
    // Auto-toggle subcategories: on for business scopes, off for Personal/All
    if ((filter === 'moom' || filter === 'kalesh') && !showSubcategories) {
        showSubcategories = true;
        const btn = document.getElementById('subcategory-toggle');
        if (btn) btn.classList.add('active');
    } else if (filter !== 'moom' && filter !== 'kalesh' && showSubcategories) {
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
        return `<span class="stat-delta ${cls}">${sign}${pct.toFixed(0)}%</span>`;
    }

    function avgLine(avg) {
        if (!avg || avg === 0) return '';
        return `<div class="stat-sub">3mo avg: S$${formatAmount(avg)}</div>`;
    }

    document.getElementById('stats-row').innerHTML = `
        <div class="stat-card">
            <div class="stat-label">${d.ref_label} Spend</div>
            <div class="stat-value">S$${formatAmount(d.spend)} ${delta(d.spend, d.avg_spend)}</div>
            <div class="stat-sub">${d.tx_count} transactions</div>
            ${avgLine(d.avg_spend)}
        </div>
        <div class="stat-card">
            <div class="stat-label">Personal</div>
            <div class="stat-value accent">S$${formatAmount(d.personal)} ${delta(d.personal, d.avg_personal)}</div>
            ${avgLine(d.avg_personal)}
        </div>
        <div class="stat-card">
            <div class="stat-label">Moom</div>
            <div class="stat-value moom">S$${formatAmount(d.moom)} ${delta(d.moom, d.avg_moom)}</div>
            ${avgLine(d.avg_moom)}
        </div>
        <div class="stat-card">
            <div class="stat-label">Kalesh</div>
            <div class="stat-value" style="color:var(--accent-pop);">S$${formatAmount(d.kalesh)} ${delta(d.kalesh, d.avg_kalesh)}</div>
            ${avgLine(d.avg_kalesh)}
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

    // Build shared color map so donut uses same colors as bar chart
    categoryColorMap = {};
    catList.forEach((cat, i) => {
        categoryColorMap[cat] = CAT_COLORS[i % CAT_COLORS.length];
    });

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
                    labels: { color: '#a0a0a0', font: { size: 11 }, boxWidth: 12, padding: 12 },
                },
                tooltip: {
                    backgroundColor: '#1a1a1a',
                    borderColor: '#333333',
                    borderWidth: 1,
                    titleColor: '#ededed',
                    bodyColor: '#a0a0a0',
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
                    ticks: { color: '#707070', font: { size: 11 } },
                    grid: { display: false },
                },
                y: {
                    stacked: true,
                    ticks: {
                        color: '#707070',
                        font: { size: 11 },
                        callback: v => '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'K' : v),
                    },
                    grid: { color: '#222222', drawBorder: false },
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
            borderColor: '#505050',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            borderDash: [4, 3],
            tension: 0.3,
            pointRadius: 2,
            pointHoverRadius: 5,
            pointBackgroundColor: '#505050',
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
                if (category === 'Others') return; // can't filter on aggregate rollup
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
                        color: '#a0a0a0',
                        font: { size: 11 },
                        boxWidth: 12,
                        padding: 12,
                        usePointStyle: true,
                        pointStyle: 'line',
                    },
                },
                tooltip: {
                    backgroundColor: '#1a1a1a',
                    borderColor: '#333333',
                    borderWidth: 1,
                    titleColor: '#ededed',
                    bodyColor: '#a0a0a0',
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
                    ticks: { color: '#707070', font: { size: 11 } },
                    grid: { display: false },
                },
                y: {
                    ticks: {
                        color: '#707070',
                        font: { size: 11 },
                        callback: v => '$' + (v >= 1000 ? (v / 1000).toFixed(0) + 'K' : v),
                    },
                    grid: { color: '#222222', drawBorder: false },
                },
            },
        },
    });
}

function renderCategoryChart(data) {
    let filtered = data;
    if (spendFilter !== 'all') filtered = data.filter(d => matchesScope(spendFilter, d));

    // Top 10 + "Other"
    const top = filtered.slice(0, 10);
    const rest = filtered.slice(10);
    if (rest.length) {
        top.push({
            category: 'Others',
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
                backgroundColor: top.map(d => categoryColorMap[d.category] || CAT_COLORS[0]),
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
                    labels: { color: '#a0a0a0', font: { size: 11 }, boxWidth: 10, padding: 8 },
                },
                tooltip: {
                    backgroundColor: '#1a1a1a',
                    borderColor: '#333333',
                    borderWidth: 1,
                    titleColor: '#ededed',
                    bodyColor: '#a0a0a0',
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
    const catName = tx.parent_category || tx.category;
    const badges = (() => {
        if (!tx.category) return '<span class="badge badge-warning">Uncategorized</span>';
        if (tx.parent_category) {
            return `<span class="badge badge-parent">${escapeHtml(tx.parent_category)}</span><span class="badge badge-sub">${escapeHtml(tx.category)}</span>`;
        }
        return `<span class="badge badge-success">${escapeHtml(tx.category)}</span>`;
    })();
    // Category text navigates to By Category view; uncategorized opens Resolve Modal directly
    if (!catName) {
        return `<span class="tx-cat-editable" onclick="showCategoryPicker(${tx.id}, this)" title="Click to categorize">${badges}</span>`;
    }
    return `<a href="#" class="tx-cat-link" onclick="navigateToCategory('${escapeHtml(catName)}');return false;" title="View in By Category">${badges}</a>`;
}

function renderTransactions(data) {
    const tbody = document.getElementById('tx-body');
    tbody.innerHTML = '';

    data.transactions.forEach(tx => {
        if (tx.flow_type !== 'expense' && tx.flow_type !== 'refund') return;
        const tr = document.createElement('tr');
        tr.dataset.description = tx.description || '';
        // Note indicator: small icon after description, clickable to edit
        const noteIcon = tx.notes
            ? `<span class="tx-note-icon has-note" title="${escapeHtml(tx.notes)}" onclick="editNote(${tx.id}, this)">&#9998;</span>`
            : `<span class="tx-note-icon" title="Add note" onclick="editNote(${tx.id}, this)">&#9998;</span>`;
        const oneOffClass = tx.is_one_off ? 'tx-one-off active' : 'tx-one-off';
        const oneOffTitle = tx.is_one_off ? 'Marked as one-off (click to unmark)' : 'Mark as one-off (excludes from burn rate)';
        tr.innerHTML = `
            <td class="col-date">${formatDate(tx.date)}</td>
            <td>${tx.service_id ? `<a href="#" class="svc-link" onclick="navigateToService(${tx.service_id});return false;">${escapeHtml(tx.service_name)}</a>` : `<span class="text-tertiary" style="font-size:12px;">${escapeHtml(tx.description).substring(0, 30)}</span>`}</td>
            <td>${renderCategoryBadges(tx)}</td>
            <td class="text-secondary" style="font-size:12px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(tx.description)}">${escapeHtml(tx.description)}${noteIcon}</td>
            <td class="text-secondary" style="font-size:12px;">${tx.account_name || ''}</td>
            <td class="col-amount ${tx.amount_sgd < 0 ? 'text-success' : ''}">${tx.amount_sgd < 0 ? '-' : ''}S$${formatAmount(Math.abs(tx.amount_sgd))}</td>
            <td style="text-align:center;"><span class="${oneOffClass}" title="${oneOffTitle}" onclick="toggleTxOneOff(${tx.id}, this)">1x</span></td>
            <td style="text-align:center;"><span class="tx-edit-icon" title="Resolve / edit transaction" onclick="showCategoryPicker(${tx.id}, this)">&#9998;</span></td>
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
    // If not in flat view, delegate to accordion loaders
    if (txViewMode === 'service') { loadServiceAccordion(); return; }
    if (txViewMode === 'category') { loadCategoryAccordion(); return; }

    txCurrentPage += delta;
    if (txCurrentPage < 1) txCurrentPage = 1;
    const params = buildFilterParams();
    const search = document.getElementById('tx-search').value;

    let url = `/api/transactions?${params}&expense_only=true&per_page=50&page=${txCurrentPage}&sort=${txSortCol}&sort_dir=${txSortDir}`;
    if (search) url += '&search=' + encodeURIComponent(search);

    // Category filter: chart selections take precedence over multi-select dropdown
    const chartCats = getChartFilterCategories();
    const activeCats = chartCats.length > 0 ? chartCats : catFilterSelections;
    if (activeCats.length) {
        url += '&categories=' + encodeURIComponent(activeCats.join(','));
    }

    // Chart-driven date narrowing from selections with specific periods
    const chartDateRange = getChartFilterDateRange();
    if (chartDateRange.start) url += '&chart_start=' + chartDateRange.start;
    if (chartDateRange.end) url += '&chart_end=' + chartDateRange.end;

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
        await apiFetch(`/api/transactions/${noteModalTxId}`, {
            method: 'PUT', body: { notes: note || null }
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
        await apiFetch(`/api/transactions/${noteModalTxId}`, {
            method: 'PUT', body: { notes: null }
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

async function toggleTxOneOff(txId, el) {
    const isActive = el.classList.contains('active');
    const newVal = isActive ? 0 : 1;
    try {
        await apiFetch(`/api/transactions/${txId}`, {
            method: 'PUT', body: { is_one_off: newVal }
        });
        el.classList.toggle('active');
        el.title = newVal
            ? 'Marked as one-off (click to unmark)'
            : 'Mark as one-off (excludes from burn rate)';
    } catch (_) {
        // Silently fail — UI state unchanged
    }
}

function showCategoryPicker(txId, containerEl) {
    // Open the unified resolve modal instead of inline category picker
    const row = containerEl.closest('tr');
    const desc = row?.dataset.description || '';
    openResolveModal(txId, desc);
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

    // Services master search debounce
    const svcMasterSearchEl = document.getElementById('svc-master-search');
    if (svcMasterSearchEl) {
        svcMasterSearchEl.addEventListener('input', debounce(renderServicesMaster, 300));
    }

    // Close modals on Escape and overlay click (note-modal, resolve-modal, edit-rule-modal)
    // add-service, edit-service, add-sub, edit-sub modals use openModalEl which handles its own Escape/backdrop
    const _staticModals = {
        'note-modal': closeNoteModal,
        'resolve-modal': closeResolveModal,
        'edit-rule-modal': closeEditRuleModal,
    };
    document.addEventListener('keydown', e => {
        if (e.key !== 'Escape') return;
        for (const [id, fn] of Object.entries(_staticModals)) {
            if (document.getElementById(id).style.display !== 'none') { fn(); return; }
        }
    });
    for (const [id, fn] of Object.entries(_staticModals)) {
        document.getElementById(id)?.addEventListener('mousedown', e => {
            if (e.target === e.currentTarget) fn();
        });
    }
});

// ============================================================
// RULE CREATION MODAL (from category picker)
// ============================================================

function isTransferLikeDescription(description) {
    const normalized = (description || '').toUpperCase().replace(/\s+/g, ' ').trim();
    if (!normalized) return false;
    if (normalized.includes('PAYNOW') || normalized.includes('PAYLAH') || normalized.includes('I-BANK') || normalized.includes(':IB')) {
        return true;
    }
    return /^FT\d+[A-Z0-9-]*/.test(normalized);
}

function suggestPattern(description) {
    if (isTransferLikeDescription(description)) {
        return '';
    }
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

// ---- Resolve Transaction Modal ----
// Unified flow: pick/create service → category auto-fills → rule pattern → one save

let resolveModalTxId = null;
let resolveCascadeCountToken = 0;
async function openResolveModal(txId, description) {
    resolveModalTxId = txId;

    // Show the description being resolved
    document.getElementById('resolve-modal-desc').textContent =
        `Resolve: "${description}"`;

    // Auto-suggest rule pattern from description
    document.getElementById('resolve-modal-pattern').value = suggestPattern(description);
    document.getElementById('resolve-modal-match').value = 'contains';

    // Initialize picker and clear
    const picker = getResolveServicePicker();
    picker.clear();
    document.getElementById('resolve-cat-hint').style.display = 'none';
    const defaultScope = document.querySelector('input[name="resolve-scope"][value="transaction"]');
    if (defaultScope) defaultScope.checked = true;

    // Populate category dropdown (for new services or manual override)
    populateCategorySelect('resolve-modal-category', {
        placeholder: '-- Select category --', includeNew: true, parentSelectId: 'resolve-new-cat-parent'
    });

    // Try to auto-match service from description
    await autoMatchService(description);
    updateResolveCascade();

    // Show modal and focus service input
    document.getElementById('resolve-modal').style.display = 'flex';
    picker.input.focus();
}

function onResolveCategoryChange() {
    toggleNewCategoryRow('resolve-modal-category', 'resolve-new-cat-row', 'resolve-new-cat-name');
    updateResolveCascade();
}

function updateResolveCascade() {
    const cascadeEl = document.getElementById('resolve-cascade');
    const warningEl = document.getElementById('resolve-cascade-warning');
    const countEl = document.getElementById('resolve-cascade-count');
    const picker = getResolveServicePicker();
    const { id: serviceId, name: serviceName } = picker.getValue();
    const categoryRaw = document.getElementById('resolve-modal-category').value;
    const categoryId = categoryRaw && categoryRaw !== '__new__' ? parseInt(categoryRaw) : null;
    const pattern = document.getElementById('resolve-modal-pattern').value.trim();
    const ruleRadio = document.querySelector('input[name="resolve-scope"][value="rule"]');
    const txRadio = document.querySelector('input[name="resolve-scope"][value="transaction"]');
    const serviceDefaultRadio = document.querySelector('input[name="resolve-scope"][value="service_default"]');

    const existingService = serviceId
        ? allServicesList.find(s => s.id === serviceId)
        : allServicesList.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
    const defaultCategory = existingService?.display_category || categoryDisplayName(existingService?.category_id);
    const selectedCategory = categoryDisplayName(categoryId);

    if (ruleRadio) {
        ruleRadio.disabled = !pattern;
        if (!pattern && ruleRadio.checked && txRadio) txRadio.checked = true;
    }

    if (existingService && categoryId && existingService.category_id !== categoryId) {
        warningEl.textContent =
            `Service "${existingService.name}" defaults to "${defaultCategory || 'Uncategorized'}". ` +
            `You selected "${selectedCategory}".`;
    } else if (existingService) {
        warningEl.textContent = defaultCategory
            ? `Service "${existingService.name}" defaults to "${defaultCategory}".`
            : `Service "${existingService.name}" does not have a default category yet.`;
    } else if (serviceName && selectedCategory) {
        warningEl.textContent = pattern
            ? `Create "${serviceName}" and choose whether "${selectedCategory}" applies only here, to this pattern, or as the service default.`
            : `Create "${serviceName}" and keep it transaction-only for now, unless you want a reusable rule later.`;
    } else {
        warningEl.textContent = pattern
            ? 'Choose whether this category is transaction-only, a reusable rule override, or the new service default.'
            : 'Transaction-only is safest when you are not creating a reusable rule.';
    }

    countEl.textContent = '';
    cascadeEl.classList.remove('hidden');

    const countToken = ++resolveCascadeCountToken;
    if (existingService && serviceDefaultRadio) {
        fetch(`/api/services/${existingService.id}/transactions`)
            .then(r => r.json())
            .then(data => {
                if (countToken !== resolveCascadeCountToken) return;
                const count = Array.isArray(data) ? data.length : 0;
                countEl.textContent = count ? ` (${count} linked transactions)` : '';
            }).catch(() => {});
    }
}

async function autoMatchService(description) {
    // Try to find an existing service whose name appears in the description
    await ensureServicesListLoaded();
    // Services cache populated for picker

    const descUpper = description.toUpperCase();
    const sorted = [...allServicesList].sort((a, b) => b.name.length - a.name.length);
    for (const svc of sorted) {
        if (descUpper.includes(svc.name.toUpperCase())) {
            const picker = getResolveServicePicker();
            picker.setValue(svc.name, svc.id);
            onResolveServiceChange(svc.name);
            return;
        }
    }
}

function onResolveServiceChange(value) {
    const trimmed = (value || '').trim();
    const match = allServicesList.find(
        s => s.name.toLowerCase() === trimmed.toLowerCase()
    );
    const catSelect = document.getElementById('resolve-modal-category');
    const catHint = document.getElementById('resolve-cat-hint');

    if (match && match.category_id) {
        catSelect.value = String(match.category_id);
        catHint.style.display = 'block';
    } else {
        catHint.style.display = 'none';
    }
    // Hide new-cat row when service changes (might not need new cat anymore)
    document.getElementById('resolve-new-cat-row').classList.add('hidden');
    updateResolveCascade();
}

function closeResolveModal() {
    document.getElementById('resolve-modal').style.display = 'none';
    resolveModalTxId = null;
    document.getElementById('resolve-new-cat-row').classList.add('hidden');
    document.getElementById('resolve-new-cat-name').value = '';
    document.getElementById('resolve-cascade-warning').textContent = '';
    document.getElementById('resolve-cascade-count').textContent = '';
}

// In-app confirm dialog (replaces browser confirm()). Returns a promise that resolves true/false.
function showConfirmDialog(title, bodyHtml, {okLabel = 'Continue', cancelLabel = 'Cancel'} = {}) {
    return new Promise(resolve => {
        document.getElementById('confirm-dialog-title').textContent = title;
        document.getElementById('confirm-dialog-body').innerHTML = bodyHtml;
        const okBtn = document.getElementById('confirm-dialog-ok');
        const cancelBtn = document.getElementById('confirm-dialog-cancel');
        okBtn.textContent = okLabel;
        cancelBtn.textContent = cancelLabel;

        const cleanup = (result) => {
            closeModalEl('confirm-dialog');
            resolve(result);
        };
        okBtn.onclick = () => cleanup(true);
        cancelBtn.onclick = () => cleanup(false);
        openModalEl('confirm-dialog', () => cleanup(false));
    });
}

function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

async function saveResolveModal() {
    const picker = getResolveServicePicker();
    const { id: pickedServiceId, name: serviceName } = picker.getValue();
    let categoryId = parseInt(document.getElementById('resolve-modal-category').value);
    const pattern = document.getElementById('resolve-modal-pattern').value.trim();
    const matchType = document.getElementById('resolve-modal-match').value;

    if (!serviceName) { alert('Please enter a service name.'); return; }

    const btn = document.getElementById('resolve-modal-save');
    btn.disabled = true;
    btn.textContent = 'Resolving...';

    try {
        const { id: newCatId, abort } = await resolveCategory('resolve-modal-category', 'resolve');
        if (abort) return;
        if (newCatId) categoryId = newCatId;
        if (!categoryId) { alert('Please select a category.'); return; }

        const existingService = pickedServiceId
            ? allServicesList.find(s => s.id === pickedServiceId)
            : allServicesList.find(s => s.name.toLowerCase() === serviceName.toLowerCase());
        const applyScope = document.querySelector('input[name="resolve-scope"]:checked')?.value || 'transaction';
        if (applyScope === 'rule' && !pattern) {
            alert('Pattern is required for a rule override.');
            return;
        }

        // Resolve the transaction (create service/rule if needed)
        const payload = {
            tx_id: resolveModalTxId,
            service_name: serviceName,
            pattern,
            match_type: matchType,
            category_id: categoryId,
            apply_scope: applyScope,
        };
        if (existingService) payload.service_id = existingService.id;

        const data = await apiFetch('/api/transactions/resolve', { method: 'POST', body: payload });
        if (!data) return;

        // Toast feedback
        const parts = [];
        const scopeMessage = {
            transaction: 'Saved as transaction-only',
            rule: 'Saved as a rule override',
            service_default: 'Updated the service default',
        }[applyScope];
        if (scopeMessage) parts.push(scopeMessage);
        if (!existingService) parts.push(`Created service "${serviceName}"`);
        if (data.backfilled > 0) parts.push(`${data.backfilled} matching transactions updated`);
        const hint = spendFilterHint(categoryId);
        if (hint) parts.push(hint);
        if (parts.length) showToast(parts.join('. '), 'info', 5000);

        allServicesList = null;
        allServicesCache = [];
        closeResolveModal();
        txPage(0);

    } finally {
        btn.disabled = false;
        btn.textContent = 'Resolve';
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
        importServices = data.services || [];
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
                            <th>Service</th>
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
                <td class="col-service">
                    <input type="text" class="svc-input" list="svc-datalist"
                        value="${escapeHtml(tx.service_name || '')}"
                        placeholder="Type to search..."
                        data-gi="${gi}" data-ti="${ti}"
                        onchange="setServiceOverride(${gi}, ${ti}, this.value)"
                        oninput="setServiceOverride(${gi}, ${ti}, this.value)">
                </td>
                <td>
                    <select class="cat-select ${tx.status === 'uncategorized' ? 'unresolved' : ''}" data-gi="${gi}" data-ti="${ti}" onchange="onImportCategoryChange(${gi}, ${ti}, this)">
                        ${buildCategoryDropdownHtml({placeholder: '-- Select --', includeNew: true, selectedId: tx.category_id})}
                    </select>
                </td>
                <td><span class="badge badge-${tx.status === 'categorized' ? 'success' : tx.status === 'transfer' ? 'muted' : 'warning'}">${tx.status}</span></td>
            `;
            tbody.appendChild(tr);
        });
    });

    // Add shared datalist for service input autocomplete
    let datalist = document.getElementById('svc-datalist');
    if (datalist) datalist.remove();
    datalist = document.createElement('datalist');
    datalist.id = 'svc-datalist';
    importServices.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.name;
        opt.label = s.category_name || '';
        datalist.appendChild(opt);
    });
    document.body.appendChild(datalist);

    updateConfirmBar();
}

// Service picker: when user types/selects a service, auto-fill category
function setServiceOverride(gi, ti, value) {
    const tx = currentImportData.groups[gi].transactions[ti];
    const trimmed = value.trim();

    // Look up in existing services (case-insensitive)
    const match = importServices.find(s => s.name.toLowerCase() === trimmed.toLowerCase());
    if (match) {
        tx.service_id = match.id;
        tx.service_name = match.name;
        tx._new_service = null;
        // Auto-fill category from service
        if (match.category_id) {
            tx.category_id = match.category_id;
            const catSelect = document.querySelector(`select.cat-select[data-gi="${gi}"][data-ti="${ti}"]`);
            if (catSelect) catSelect.value = match.category_id;
            tx.status = 'categorized';
            updateStatusBadge(gi, ti, 'categorized');
        }
    } else if (trimmed) {
        // New service — will be created on commit
        tx.service_id = null;
        tx.service_name = trimmed;
        tx._new_service = trimmed;
    } else {
        tx.service_id = null;
        tx.service_name = null;
        tx._new_service = null;
    }
}

function updateStatusBadge(gi, ti, status) {
    const row = document.querySelectorAll(`#preview-group-${gi} tr`)[ti];
    if (!row) return;
    const badge = row.querySelector('.badge');
    if (badge) {
        badge.className = `badge badge-${status === 'categorized' ? 'success' : status === 'transfer' ? 'muted' : 'warning'}`;
        badge.textContent = status;
    }
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

// Import preview: handle category dropdown change, intercept __new__
let _importNewCatTrigger = null; // {gi, ti, selectEl} — which row triggered the modal
function onImportCategoryChange(gi, ti, selectEl) {
    if (selectEl.value === '__new__') {
        _importNewCatTrigger = {gi, ti, selectEl};
        openImportNewCatModal();
        // Reset dropdown to blank while modal is open
        selectEl.value = '';
    } else {
        setCategoryOverride(gi, ti, selectEl.value);
    }
}

function openImportNewCatModal() {
    document.getElementById('import-new-cat-name').value = '';
    // Populate parent dropdown with top-level categories
    const parentSel = document.getElementById('import-new-cat-parent');
    parentSel.innerHTML = '<option value="">-- Top-level --</option>';
    categories.filter(c => !c.parent_id).sort((a, b) => a.name.localeCompare(b.name)).forEach(c => {
        parentSel.innerHTML += `<option value="${c.id}">${escapeHtml(c.name)}</option>`;
    });
    document.getElementById('import-new-cat-type').value = '1';
    openModalEl('import-new-cat-modal', closeImportNewCatModal);
    document.getElementById('import-new-cat-name').focus();
}

function closeImportNewCatModal() {
    closeModalEl('import-new-cat-modal');
    _importNewCatTrigger = null;
}

async function saveImportNewCategory() {
    const name = document.getElementById('import-new-cat-name').value.trim();
    if (!name) { alert('Please enter a category name.'); return; }
    const parentId = document.getElementById('import-new-cat-parent').value || null;
    const isPersonal = parseInt(document.getElementById('import-new-cat-type').value);

    const catData = await apiFetch('/api/categories', {
        method: 'POST', body: { name, parent_id: parentId ? parseInt(parentId) : null, is_personal: isPersonal }
    });
    if (!catData) return;

    await loadReferenceData();
    showToast(`Created category "${name}"`, 'info');

    // Auto-select the new category in the triggering row
    if (_importNewCatTrigger) {
        const {gi, ti, selectEl} = _importNewCatTrigger;
        // Rebuild dropdown options with the new category
        selectEl.innerHTML = buildCategoryDropdownHtml({placeholder: '-- Select --', includeNew: true, selectedId: catData.id});
        setCategoryOverride(gi, ti, catData.id.toString());
    }

    closeImportNewCatModal();
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

    // Warn about uncategorized transactions
    let uncatCount = 0, activeCount = 0;
    for (const g of currentImportData.groups) {
        for (const tx of g.transactions) {
            if (tx._skip) continue;
            activeCount++;
            if (!tx.category_id) uncatCount++;
        }
    }
    if (uncatCount > 0) {
        const proceed = await showConfirmDialog(
            'Uncategorized Transactions',
            `<strong>${uncatCount}</strong> of ${activeCount} transactions have no category assigned.<br><br>They'll be imported as uncategorized — you can resolve them later from the Dashboard.`,
            {okLabel: 'Import Anyway', cancelLabel: 'Go Back'}
        );
        if (!proceed) return;
    }

    // Collect new services to create (deduped by name)
    const newServicesMap = {};
    for (const g of currentImportData.groups) {
        for (const tx of g.transactions) {
            if (tx._skip) continue;
            if (tx._new_service && tx.category_id) {
                const key = tx._new_service.toLowerCase();
                if (!newServicesMap[key]) {
                    newServicesMap[key] = {
                        name: tx._new_service,
                        category_id: tx.category_id,
                        description: tx.description,
                    };
                }
            }
        }
    }

    const body = {
        import_id: currentImportId,
        groups: currentImportData.groups.map(g => ({
            account: g.account,
            transactions: g.transactions,
        })),
        new_rules: [],
        new_services: Object.values(newServicesMap),
    };

    const confirmBtn = document.querySelector('.confirm-bar .btn-primary');
    confirmBtn.textContent = 'Committing...';
    confirmBtn.disabled = true;

    try {
        const result = await apiFetch('/api/import/confirm', { method: 'POST', body });
        if (!result) return;

        const dupMsg = result.duplicates_skipped ? ` (${result.duplicates_skipped} duplicates skipped)` : '';
        const svcMsg = result.services_created ? ` ${result.services_created} new services created.` : '';
        const guardrailMsg = result.rules_skipped_generic
            ? ` ${result.rules_skipped_generic} generic transfer rule${result.rules_skipped_generic === 1 ? '' : 's'} skipped.`
            : '';
        showToast(`Committed ${result.transactions_saved} transactions to ${result.accounts.length} accounts.${dupMsg}${svcMsg}${guardrailMsg}`, 'success', 6000);
        discardImport();
        await loadReferenceData();
    } catch (err) {
        showToast('Commit failed: ' + err.message, 'error', 6000);
    } finally {
        confirmBtn.textContent = 'Confirm & Commit';
        confirmBtn.disabled = false;
    }
}

// ============================================================
// STATEMENT COVERAGE
// ============================================================

async function loadCoverage() {
    const container = document.getElementById('coverage-matrix');
    const summary = document.getElementById('coverage-summary');

    try {
        const res = await fetch('/api/statements/coverage?months=6');
        const data = await res.json();

        // Summary line — targets previous month (last closed billing cycle)
        const { target_month, covered, total } = data.summary;
        const monthLabel = formatMonthLabel(target_month);
        const missing = total - covered;
        if (missing > 0) {
            summary.innerHTML = `<strong>${covered}</strong> of <strong>${total}</strong> accounts covered for ${monthLabel} — <span class="coverage-gap">${missing} missing</span>`;
        } else {
            summary.innerHTML = `<strong>${covered}</strong> of <strong>${total}</strong> accounts covered for ${monthLabel} — all clear`;
        }

        // Build table
        const monthHeaders = data.months.map(m => {
            const cls = m === target_month ? ' class="coverage-target-month"' : '';
            return `<th${cls}>${formatMonthLabel(m)}</th>`;
        }).join('');
        let rows = '';
        for (const acct of data.accounts) {
            const typeLabel = acct.type === 'bank' ? 'bank' : 'cc';
            let cells = '';
            for (const m of data.months) {
                const cell = data.matrix[acct.id]?.[m];
                const isTarget = m === target_month;
                if (cell && cell.imported) {
                    const tooltip = [cell.filename, cell.date ? formatCoverageDate(cell.date) : ''].filter(Boolean).join(' — ');
                    cells += `<td><span class="coverage-cell-ok" title="${escapeHtml(tooltip)}">&#10003;</span></td>`;
                } else {
                    cells += `<td><span class="coverage-cell-missing${isTarget ? ' coverage-target' : ''}">&#9675;</span></td>`;
                }
            }
            rows += `<tr><td>${escapeHtml(acct.short_name)}<span class="coverage-acct-type">${typeLabel}</span></td>${cells}</tr>`;
        }

        container.innerHTML = `
            <table class="coverage-table">
                <thead><tr><th>Account</th>${monthHeaders}</tr></thead>
                <tbody>${rows}</tbody>
            </table>
        `;
    } catch (err) {
        container.innerHTML = '<div class="text-muted">Failed to load coverage data</div>';
    }
}

function formatMonthLabel(ym) {
    // "2026-03" → "Mar 26"
    const [y, m] = ym.split('-');
    return MONTH_NAMES[parseInt(m)] + ' ' + y.slice(2);
}

function formatCoverageDate(dateStr) {
    // "2026-03-07 11:38:40" → "7 Mar"
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d)) return '';
    return d.getDate() + ' ' + MONTH_NAMES[d.getMonth() + 1];
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
        ${imp.result ? `<tr><td colspan="6"><div class="history-detail">${escapeHtml(JSON.stringify(imp.result))}</div></td></tr>` : ''}
    `).join('');
}

// ============================================================
// MERCHANT RULES
// ============================================================

let allRules = [];

async function ensureServicesListLoaded() {
    if (!allServicesList || !allServicesList.length) {
        const res = await fetch('/api/services');
        allServicesList = await res.json();
    }
}

async function loadRules() {
    const res = await fetch('/api/rules');
    allRules = await res.json();
    renderRules(allRules);

    // Service picker datalist populated lazily on focus
}

function filterRules() {
    const q = document.getElementById('rules-search').value.toLowerCase();
    if (!q) {
        renderRules(allRules);
        return;
    }
    const filtered = allRules.filter(r =>
        r.pattern.toLowerCase().includes(q) ||
        (r.service_name || '').toLowerCase().includes(q) ||
        (r.category_name || '').toLowerCase().includes(q)
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
                            <th style="width:28%;">Pattern</th>
                            <th style="width:18%;">Service</th>
                            <th style="width:10%;">Match</th>
                            <th style="width:5%;">Pri</th>
                            <th style="width:14%;">Amount</th>
                            <th style="width:8%;">Conf</th>
                            <th style="width:17%;text-align:right;">Actions</th>
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
                            const serviceHtml = `${escapeHtml(r.service_name || '')}${
                                r.category_override_id
                                    ? ' <span class="badge badge-muted" style="font-size:10px;margin-left:6px;">override</span>'
                                    : ''
                            }`;
                            return `
                            <tr>
                                <td class="text-mono" style="font-size:12px;">${escapeHtml(r.pattern)}</td>
                                <td style="font-size:12px;">${serviceHtml}</td>
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

function toggleRuleForm() {
    openCreateRuleModal();
}

async function openCreateRuleModal() {
    await openRuleModal();
}

async function editRule(ruleId) {
    const rule = allRules.find(r => r.id === ruleId);
    if (!rule) return;

    await openRuleModal(rule);
}

async function openRuleModal(rule = null) {
    await ensureServicesListLoaded();
    const isCreate = !rule;
    const ruleFields = {
        id: rule?.id || '',
        pattern: rule?.pattern || '',
        match: rule?.match_type || 'contains',
        priority: rule?.priority || 0,
        min: rule?.min_amount || '',
        max: rule?.max_amount || '',
    };
    for (const [k, v] of Object.entries(ruleFields)) document.getElementById(`edit-rule-${k}`).value = v;
    populateCategorySelect('edit-rule-category-override', {
        placeholder: 'Use service default', selectedId: rule?.category_override_id || ''
    });

    const picker = getEditRuleServicePicker();
    if (rule) {
        picker.setValue(rule.service_name || '', rule.service_id || null);
    } else {
        picker.clear();
    }
    updateEditRuleServiceMeta(rule?.service_id || null);

    document.getElementById('rule-modal-title').textContent = isCreate ? '/ New Merchant Rule' : '/ Edit Merchant Rule';
    document.getElementById('rule-modal-save').textContent = isCreate ? 'Create Rule' : 'Save';

    document.getElementById('edit-rule-modal').style.display = 'flex';
    document.getElementById('edit-rule-pattern').focus();
}

function updateEditRuleServiceMeta(serviceId) {
    const svc = (allServicesList || []).find(s => s.id === serviceId);
    document.getElementById('edit-rule-cat-display').textContent =
        svc?.display_category
            ? `Service default: ${svc.display_category}. Leave override blank to inherit it.`
            : 'Leave override blank to use the service default.';
}

function closeEditRuleModal() {
    document.getElementById('edit-rule-modal').style.display = 'none';
}

async function saveRuleModal() {
    const ruleId = document.getElementById('edit-rule-id').value;
    const picker = getEditRuleServicePicker();
    const { id: serviceId } = picker.getValue();

    const pattern = document.getElementById('edit-rule-pattern').value.trim();
    if (!pattern) {
        alert('Pattern is required.');
        return;
    }
    if (!serviceId) {
        alert('Service is required.');
        return;
    }

    const payload = {
        pattern,
        match_type: document.getElementById('edit-rule-match').value,
        priority: parseInt(document.getElementById('edit-rule-priority').value) || 0,
        min_amount: parseFloat(document.getElementById('edit-rule-min').value) || null,
        max_amount: parseFloat(document.getElementById('edit-rule-max').value) || null,
        category_override_id: parseInt(document.getElementById('edit-rule-category-override').value) || null,
    };
    if (serviceId) payload.service_id = serviceId;

    if (ruleId) {
        await apiFetch(`/api/rules/${ruleId}`, { method: 'PUT', body: payload });
    } else {
        await apiFetch('/api/rules', { method: 'POST', body: payload });
    }
    closeEditRuleModal();
    await loadRules();
}

async function deleteRule(ruleId) {
    if (!confirm('Delete this rule?')) return;
    await apiFetch(`/api/rules/${ruleId}`, { method: 'DELETE' });
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
    // Uses the parent-select-only path of populateCategorySelect
    // by populating a dummy and leveraging parentSelectId — but this is simpler inline:
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
            const typeLabel = scopeBadgeHtml(getScope(p));
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
            const typeLabel = scopeBadgeHtml(getScope(p));
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

    const data = await apiFetch('/api/categories', { method: 'POST', body });
    if (!data) return;

    document.getElementById('cat-new-name').value = '';
    await loadReferenceData();
    renderCategoryTree();
}

// ============================================================
// CHART-TABLE LINKING
// ============================================================

function toggleChartFilter(category, period, source) {
    // Donut click: period=null (all periods for this category)
    // Bar click: period=specific month

    // If donut click for a category that already has a period-null entry, remove it (toggle off)
    // If bar click for same category that has period-null, narrow to this specific period
    const sel = chartFilter.selections;

    if (source === 'doughnut') {
        // Check if this category already has an all-periods entry
        const allIdx = sel.findIndex(s => s.category === category && s.period === null);
        if (allIdx >= 0) {
            // Toggle off
            sel.splice(allIdx, 1);
        } else {
            // Remove any period-specific entries for this category (donut replaces them)
            for (let i = sel.length - 1; i >= 0; i--) {
                if (sel[i].category === category) sel.splice(i, 1);
            }
            sel.push({ category, period: null });
        }
    } else {
        // Bar click — specific period
        // If category has an all-periods entry, narrow to this specific period
        const allIdx = sel.findIndex(s => s.category === category && s.period === null);
        if (allIdx >= 0) {
            sel.splice(allIdx, 1);
            sel.push({ category, period });
        } else {
            // Toggle this specific (category, period) pair
            const exactIdx = sel.findIndex(s => s.category === category && s.period === period);
            if (exactIdx >= 0) {
                sel.splice(exactIdx, 1);
            } else {
                sel.push({ category, period });
            }
        }
    }

    if (sel.length === 0) {
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
    chartFilter = { selections: [] };
    renderFilterChip();
    updateChartHighlights();
    updateCatFilterDisplay();
    txCurrentPage = 1;
    txPage(0);
}

// Helper: extract unique categories from selections
function getChartFilterCategories() {
    return [...new Set(chartFilter.selections.map(s => s.category))];
}

// Helper: compute date range from period-specific selections
// If any selection has period=null, no date narrowing (all periods)
function getChartFilterDateRange() {
    if (chartFilter.selections.length === 0) return {};
    // If any selection is all-periods, don't narrow dates
    if (chartFilter.selections.some(s => s.period === null)) return {};
    // Collect all unique periods, compute union date range
    const periods = [...new Set(chartFilter.selections.map(s => s.period))];
    let earliest = null, latest = null;
    for (const p of periods) {
        const range = periodToDateRange(p);
        if (range.start && (!earliest || range.start < earliest)) earliest = range.start;
        if (range.end && (!latest || range.end > latest)) latest = range.end;
    }
    return { start: earliest, end: latest };
}

function updateChartHighlights() {
    const hasFilter = chartFilter.selections.length > 0;
    const selectedCats = getChartFilterCategories();
    const selectedPeriods = chartFilter.selections
        .filter(s => s.period !== null)
        .map(s => s.period);
    const hasAllPeriods = chartFilter.selections.some(s => s.period === null);

    // Bar chart highlights
    if (monthlyChart) {
        monthlyChart.data.datasets.forEach((ds, i) => {
            const baseColor = categoryColorMap[ds.label] || CAT_COLORS[i % CAT_COLORS.length];
            if (!hasFilter) {
                ds.backgroundColor = baseColor;
                ds.borderColor = 'transparent';
                ds.borderWidth = 0;
            } else if (selectedCats.includes(ds.label)) {
                // This category is selected — highlight its bars
                // If all-periods or no period filter, highlight all bars
                // If period-specific, only highlight matching period bars
                const catSelections = chartFilter.selections.filter(s => s.category === ds.label);
                const catAllPeriods = catSelections.some(s => s.period === null);
                if (catAllPeriods) {
                    // All bars for this category highlighted
                    ds.backgroundColor = baseColor;
                    ds.borderColor = '#ededed';
                    ds.borderWidth = 1.5;
                } else {
                    // Only highlight bars at selected periods
                    const catPeriods = catSelections.map(s => s.period);
                    ds.backgroundColor = monthlyPeriods.map(p =>
                        catPeriods.includes(p) ? baseColor : hexToRgba(baseColor, 0.15)
                    );
                    ds.borderColor = monthlyPeriods.map(p =>
                        catPeriods.includes(p) ? '#ededed' : 'transparent'
                    );
                    ds.borderWidth = monthlyPeriods.map(p =>
                        catPeriods.includes(p) ? 1.5 : 0
                    );
                }
            } else {
                ds.backgroundColor = hexToRgba(baseColor, 0.15);
                ds.borderColor = 'transparent';
                ds.borderWidth = 0;
            }
        });
        monthlyChart.update();
    }

    // Doughnut highlights
    if (categoryChart) {
        const ds = categoryChart.data.datasets[0];
        const labels = categoryChart.data.labels;
        if (!hasFilter) {
            ds.backgroundColor = labels.map(label => categoryColorMap[label] || CAT_COLORS[0]);
            ds.offset = 0;
        } else {
            ds.backgroundColor = labels.map(label => {
                const base = categoryColorMap[label] || CAT_COLORS[0];
                return selectedCats.includes(label) ? base : hexToRgba(base, 0.15);
            });
            ds.offset = labels.map(label =>
                selectedCats.includes(label) ? 8 : 0
            );
        }
        categoryChart.update();
    }
}

function renderFilterChip() {
    const el = document.getElementById('chart-filter-chip');
    if (!el) return;

    if (!chartFilter.selections.length) {
        el.classList.add('hidden');
        return;
    }

    // Build chips: group by category, show period if specific
    const chips = chartFilter.selections.map(s => {
        const label = s.period ? `${escapeHtml(s.category)} · ${escapeHtml(formatPeriodLabel(s.period))}` : escapeHtml(s.category);
        const key = s.period ? `${s.category}|${s.period}` : s.category;
        return `<span class="chip-cat" onclick="removeChartSelection('${escapeHtml(s.category)}', ${s.period ? "'" + escapeHtml(s.period) + "'" : 'null'})">${label}<span class="chip-x">&times;</span></span>`;
    }).join('');

    el.innerHTML = `
        <span class="chip-label">Showing</span>
        <span class="chip-cats">${chips}</span>
        <button class="chip-clear" onclick="clearChartFilter()" title="Clear filter">&times;</button>
    `;
    el.classList.remove('hidden');
}

function removeChartSelection(category, period) {
    const idx = chartFilter.selections.findIndex(s =>
        s.category === category && s.period === period
    );
    if (idx >= 0) chartFilter.selections.splice(idx, 1);

    if (chartFilter.selections.length === 0) {
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
// DASHBOARD 3-VIEW TOGGLE (Flat / By Service / By Category)
// ============================================================

let txViewMode = localStorage.getItem('fin-tx-view') || 'flat';

function setTxView(mode) {
    txViewMode = mode;
    localStorage.setItem('fin-tx-view', mode);
    // Update toggle buttons
    document.querySelectorAll('.tx-view-toggle .btn-toggle').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.txview === mode);
    });
    // Show/hide containers
    document.getElementById('tx-view-flat').style.display = mode === 'flat' ? '' : 'none';
    document.getElementById('tx-view-service').style.display = mode === 'service' ? '' : 'none';
    document.getElementById('tx-view-category').style.display = mode === 'category' ? '' : 'none';
    // Load data for accordion views
    if (mode === 'flat') {
        txPage(0);
    } else if (mode === 'service') {
        loadServiceAccordion();
    } else if (mode === 'category') {
        loadCategoryAccordion();
    }
}

// Build the full transaction URL for accordion views (shared by service + category accordions).
function buildAccordionUrl() {
    const params = buildFilterParams();
    const search = document.getElementById('tx-search').value;
    let url = `/api/transactions?${params}&expense_only=true&per_page=5000&sort=date&sort_dir=desc`;
    if (search) url += '&search=' + encodeURIComponent(search);
    const chartCats = getChartFilterCategories();
    const activeCats = chartCats.length > 0 ? chartCats : catFilterSelections;
    if (activeCats.length) url += '&categories=' + encodeURIComponent(activeCats.join(','));
    const chartDateRange = getChartFilterDateRange();
    if (chartDateRange.start) url += '&chart_start=' + chartDateRange.start;
    if (chartDateRange.end) url += '&chart_end=' + chartDateRange.end;
    return url;
}

// Build an accordion group HTML block (header + collapsible body).
function buildAccordionGroup(label, meta, bodyHtml, extraClasses) {
    const cls = extraClasses ? `${extraClasses} svc-accordion-item` : 'svc-accordion-item';
    const hdrCls = extraClasses ? `${extraClasses.replace('-item', '-header').replace('-group', '-header')} svc-accordion-header` : 'svc-accordion-header';
    return `<div class="${cls}">
        <div class="${hdrCls}" onclick="this.parentElement.classList.toggle('open')">
            <span class="svc-accordion-arrow">&#9654;</span>
            ${label} <span class="svc-accordion-meta">${meta}</span>
        </div>
        <div class="svc-accordion-body">${bodyHtml}</div>
    </div>`;
}

// Build a compact transaction table for accordion bodies.
function accordionTxTable(txns) {
    return `<table class="data-table" style="font-size:12px;">
        <tbody>${txns.map(tx => renderAccordionTxRow(tx)).join('')}</tbody>
    </table>`;
}

async function loadServiceAccordion() {
    const data = await fetch(buildAccordionUrl()).then(r => r.json());
    const txns = data.transactions.filter(tx => tx.flow_type === 'expense' || tx.flow_type === 'refund');

    // Group by service
    const groups = {};
    const noService = [];
    txns.forEach(tx => {
        if (tx.service_id) {
            const key = tx.service_id;
            if (!groups[key]) groups[key] = { name: tx.service_name, category: tx.display_category || tx.category, txns: [], total: 0 };
            groups[key].txns.push(tx);
            groups[key].total += tx.amount_sgd > 0 ? tx.amount_sgd : 0;
        } else {
            noService.push(tx);
        }
    });

    const sorted = Object.entries(groups).sort((a, b) => a[1].name.localeCompare(b[1].name));
    const container = document.getElementById('tx-view-service');
    let html = '';
    for (const [svcId, g] of sorted) {
        html += buildAccordionGroup(
            `<span class="svc-accordion-name">${escapeHtml(g.name)}</span>`,
            `${escapeHtml(g.category)} &middot; ${g.txns.length} txns &middot; S$${formatAmount(g.total)}`,
            accordionTxTable(g.txns));
    }
    if (noService.length) {
        html += buildAccordionGroup(
            '<span class="svc-accordion-name text-muted">Unlinked</span>',
            `${noService.length} txns`, accordionTxTable(noService));
    }
    container.innerHTML = html || '<div class="empty-state"><div class="empty-state-text">No transactions</div></div>';
}

async function loadCategoryAccordion() {
    const data = await fetch(buildAccordionUrl()).then(r => r.json());
    const txns = data.transactions.filter(tx => tx.flow_type === 'expense' || tx.flow_type === 'refund');

    // Build 3-level: parent category → subcategory → service → transactions
    const tree = {};
    txns.forEach(tx => {
        const parentCat = tx.parent_category || tx.category || 'Other';
        const subCat = tx.parent_category ? tx.category : null;
        const svcName = tx.service_name || 'Unlinked';
        const svcId = tx.service_id || 0;

        if (!tree[parentCat]) tree[parentCat] = { total: 0, subs: {} };
        tree[parentCat].total += tx.amount_sgd > 0 ? tx.amount_sgd : 0;

        const subKey = subCat || '__direct__';
        if (!tree[parentCat].subs[subKey]) tree[parentCat].subs[subKey] = { total: 0, services: {} };
        tree[parentCat].subs[subKey].total += tx.amount_sgd > 0 ? tx.amount_sgd : 0;

        const svcKey = `${svcId}|${svcName}`;
        if (!tree[parentCat].subs[subKey].services[svcKey]) tree[parentCat].subs[subKey].services[svcKey] = { name: svcName, txns: [], total: 0 };
        tree[parentCat].subs[subKey].services[svcKey].txns.push(tx);
        tree[parentCat].subs[subKey].services[svcKey].total += tx.amount_sgd > 0 ? tx.amount_sgd : 0;
    });

    const container = document.getElementById('tx-view-category');
    const sortedCats = Object.entries(tree).sort((a, b) => b[1].total - a[1].total);
    let html = '';
    for (const [catName, catData] of sortedCats) {
        let innerHtml = '';
        const sortedSubs = Object.entries(catData.subs).sort((a, b) => b[1].total - a[1].total);
        for (const [subKey, subData] of sortedSubs) {
            const sortedSvcs = Object.entries(subData.services).sort((a, b) => b[1].total - a[1].total);
            let svcsHtml = '';
            for (const [svcKey, svcData] of sortedSvcs) {
                svcsHtml += buildAccordionGroup(
                    escapeHtml(svcData.name),
                    `${svcData.txns.length} txns &middot; S$${formatAmount(svcData.total)}`,
                    accordionTxTable(svcData.txns), 'cat-l3-item');
            }

            if (subKey === '__direct__') {
                innerHtml += svcsHtml;
            } else {
                innerHtml += buildAccordionGroup(
                    escapeHtml(subKey), `S$${formatAmount(subData.total)}`,
                    svcsHtml, 'cat-l2-group');
            }
        }

        html += buildAccordionGroup(
            `<strong>${escapeHtml(catName)}</strong>`, `S$${formatAmount(catData.total)}`,
            innerHtml, 'cat-l1-group');
    }
    container.innerHTML = html || '<div class="empty-state"><div class="empty-state-text">No transactions</div></div>';
}

function renderAccordionTxRow(tx) {
    const oneOffClass = tx.is_one_off ? 'tx-one-off active' : 'tx-one-off';
    const oneOffTitle = tx.is_one_off ? 'Marked as one-off (click to unmark)' : 'Mark as one-off (excludes from burn rate)';
    return `<tr>
        <td style="width:90px;">${formatDate(tx.date)}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escapeHtml(tx.description)}">${escapeHtml(tx.description)}</td>
        <td>${renderCategoryBadges(tx)}</td>
        <td class="text-secondary">${tx.account_name || ''}</td>
        <td style="text-align:right;" class="${tx.amount_sgd < 0 ? 'text-success' : ''}">${tx.amount_sgd < 0 ? '-' : ''}S$${formatAmount(Math.abs(tx.amount_sgd))}</td>
        <td style="width:28px;text-align:center;"><span class="${oneOffClass}" title="${oneOffTitle}" onclick="toggleTxOneOff(${tx.id}, this)">1x</span></td>
        <td style="width:28px;text-align:center;"><span class="tx-edit-icon" title="Resolve / edit" onclick="showCategoryPicker(${tx.id}, this)">&#9998;</span></td>
    </tr>`;
}

// Navigate to Dashboard service view and expand a specific service
async function navigateToService(serviceId) {
    switchTab('dashboard', { pushHistory: false });

    // Get service name for search filter
    let svcName = null;
    if (allServicesList && allServicesList.length) {
        const svc = allServicesList.find(s => s.id === serviceId);
        if (svc) svcName = svc.name;
    }
    if (!svcName) {
        // Fetch if not cached
        try {
            const res = await fetch('/api/services');
            allServicesList = await res.json();
            const svc = allServicesList.find(s => s.id === serviceId);
            if (svc) svcName = svc.name;
        } catch (e) { /* proceed without name */ }
    }

    // Set search to service name so accordion filters to it
    if (svcName) {
        document.getElementById('tx-search').value = svcName;
    }

    // Switch to By Service view
    txViewMode = 'service';
    localStorage.setItem('fin-tx-view', 'service');
    document.querySelectorAll('.tx-view-toggle .btn-toggle').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.txview === 'service');
    });
    document.getElementById('tx-view-flat').style.display = 'none';
    document.getElementById('tx-view-service').style.display = '';
    document.getElementById('tx-view-category').style.display = 'none';

    await loadServiceAccordion();

    // Auto-expand the matching accordion item
    const container = document.getElementById('tx-view-service');
    const items = container.querySelectorAll('.svc-accordion-item');
    for (const item of items) {
        const nameEl = item.querySelector('.svc-accordion-name');
        if (!nameEl) continue;
        if (svcName && nameEl.textContent.trim() === svcName) {
            item.classList.add('open');
            item.scrollIntoView({ behavior: 'smooth', block: 'start' });
            break;
        }
    }
}

async function navigateToCategory(categoryName) {
    switchTab('dashboard', { pushHistory: false });

    // Set search to category name — API searches c.name and p.name
    document.getElementById('tx-search').value = categoryName;

    // Switch to By Category view
    txViewMode = 'category';
    localStorage.setItem('fin-tx-view', 'category');
    document.querySelectorAll('.tx-view-toggle .btn-toggle').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.txview === 'category');
    });
    document.getElementById('tx-view-flat').style.display = 'none';
    document.getElementById('tx-view-service').style.display = 'none';
    document.getElementById('tx-view-category').style.display = '';

    await loadCategoryAccordion();

    // Auto-expand the matching category
    const container = document.getElementById('tx-view-category');
    const items = container.querySelectorAll('.cat-l1-group');
    for (const item of items) {
        const header = item.querySelector('.cat-l1-header');
        if (header && header.textContent.includes(categoryName)) {
            item.classList.add('open');
            item.scrollIntoView({ behavior: 'smooth', block: 'start' });
            break;
        }
    }
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
    if (chartFilter.selections.length > 0) return;

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

    const chartCats = getChartFilterCategories();
    if (chartCats.length > 0) {
        display.textContent = `Chart: ${chartCats.join(', ')}`;
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
// CATEGORIES MASTER TAB
// ============================================================

function renderCategoriesMaster() {
    renderCategoryTree();
}

// ============================================================
// SERVICES MASTER TAB (CRUD)
// ============================================================

let allServicesCache = [];
const svcSortState = { col: 'name', asc: true };
const sortServicesMaster = createSortToggler(svcSortState, () => renderServicesMaster(true));

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
        switch (svcSortState.col) {
            case 'category': va = (a.display_category || '').toLowerCase(); vb = (b.display_category || '').toLowerCase(); break;
            case 'notes':    va = (a.notes || '').toLowerCase(); vb = (b.notes || '').toLowerCase(); break;
            default:         va = a.name.toLowerCase(); vb = b.name.toLowerCase();
        }
        if (va < vb) return svcSortState.asc ? -1 : 1;
        if (va > vb) return svcSortState.asc ? 1 : -1;
        return 0;
    });

    updateSortIndicators('#services-master-table', svcSortState);

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
    document.getElementById('add-svc-new-cat-row').classList.add('hidden');

    // Populate category dropdown with "+ New category..." option
    populateCategorySelect('add-svc-category', {includeNew: true, parentSelectId: 'add-svc-new-cat-parent'});

    openModalEl('add-service-modal', closeAddServiceModal);
    document.getElementById('add-svc-name').focus();
}

function closeAddServiceModal() {
    closeModalEl('add-service-modal');
}

async function saveNewService() {
    const name = document.getElementById('add-svc-name').value.trim();
    if (!name) { alert('Service name is required'); return; }

    const { id: categoryId, abort } = await resolveCategory('add-svc-category', 'add-svc');
    if (abort) return;

    const body = {
        name,
        category_id: categoryId,
        notes: document.getElementById('add-svc-notes').value.trim() || null,
    };

    const data = await apiFetch('/api/services', { method: 'POST', body });
    if (!data) return;

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

    // Populate category dropdown with "+ New category..." option
    populateCategorySelect('edit-svc-category', {selectedId: svc.category_id, includeNew: true, parentSelectId: 'edit-svc-new-cat-parent'});
    document.getElementById('edit-svc-new-cat-row').classList.add('hidden');

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

    openModalEl('edit-service-modal', closeEditServiceModal);
}

function closeEditServiceModal() {
    closeModalEl('edit-service-modal');
}

async function saveEditService() {
    const svcId = parseInt(document.getElementById('edit-svc-id').value);
    const name = document.getElementById('edit-svc-name').value.trim();
    if (!name) { alert('Service name is required'); return; }

    const { id: categoryId, abort } = await resolveCategory('edit-svc-category', 'edit-svc');
    if (abort) return;

    const body = {
        name,
        category_id: categoryId,
        notes: document.getElementById('edit-svc-notes').value.trim() || null,
        is_one_off: document.getElementById('edit-svc-one-off').checked ? 1 : 0,
    };

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

    const data = await apiFetch(`/api/services/${svcId}`, { method: 'DELETE' });
    if (!data) return;

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
// SERVICE PICKER (reusable searchable dropdown)
// ============================================================

class ServicePicker {
    constructor(containerEl, options = {}) {
        this.container = typeof containerEl === 'string' ? document.getElementById(containerEl) : containerEl;
        this.input = this.container.querySelector('.svc-picker-input');
        this.hiddenInput = this.container.querySelector('.svc-picker-id');
        this.dropdown = this.container.querySelector('.svc-picker-dropdown');
        this.allowCreate = options.allowCreate || false;
        this.onSelect = options.onSelect || null;
        this.highlightIndex = -1;
        this.filteredItems = [];
        this.selectedId = null;
        this.selectedName = '';

        this.input.addEventListener('input', () => this._onInput());
        this.input.addEventListener('focus', () => this._onInput());
        this.input.addEventListener('keydown', e => this._onKeydown(e));
        // Close on outside click
        document.addEventListener('mousedown', e => {
            if (!this.container.contains(e.target)) this._close();
        });
    }

    async _ensureServices() {
        if (!allServicesList || !allServicesList.length) {
            const res = await fetch('/api/services');
            allServicesList = await res.json();
        }
    }

    async _onInput() {
        await this._ensureServices();
        const query = this.input.value.trim().toLowerCase();

        // Filter and sort — exact prefix matches first, then contains
        let items = allServicesList
            .map(s => ({
                id: s.id,
                name: s.name,
                category: s.display_category || s.category_name || '',
            }))
            .filter(s => !query || s.name.toLowerCase().includes(query) || s.category.toLowerCase().includes(query))
            .sort((a, b) => {
                if (query) {
                    const aStarts = a.name.toLowerCase().startsWith(query) ? 0 : 1;
                    const bStarts = b.name.toLowerCase().startsWith(query) ? 0 : 1;
                    if (aStarts !== bStarts) return aStarts - bStarts;
                }
                return a.name.localeCompare(b.name);
            });

        // Limit to 30 for performance
        const shown = items.slice(0, 30);
        this.filteredItems = shown;
        this.highlightIndex = -1;

        let html = '';
        if (shown.length === 0 && !this.allowCreate) {
            html = '<div class="svc-picker-empty">No services found</div>';
        } else {
            html = shown.map((s, i) => {
                // Highlight matching text
                let nameHtml = escapeHtml(s.name);
                if (query) {
                    const idx = s.name.toLowerCase().indexOf(query);
                    if (idx >= 0) {
                        nameHtml = escapeHtml(s.name.substring(0, idx))
                            + '<span class="svc-picker-item-match">' + escapeHtml(s.name.substring(idx, idx + query.length)) + '</span>'
                            + escapeHtml(s.name.substring(idx + query.length));
                    }
                }
                return `<div class="svc-picker-item" data-index="${i}" data-id="${s.id}">
                    <span class="svc-picker-item-name">${nameHtml}</span>
                    <span class="svc-picker-item-cat">${escapeHtml(s.category)}</span>
                </div>`;
            }).join('');

            if (this.allowCreate && query && !items.some(s => s.name.toLowerCase() === query)) {
                html += `<div class="svc-picker-item create-new" data-index="${shown.length}" data-create="true">
                    + Create "${escapeHtml(this.input.value.trim())}"
                </div>`;
                this.filteredItems.push({ id: null, name: this.input.value.trim(), category: '', isNew: true });
            }
        }

        this.dropdown.innerHTML = html;
        this.dropdown.classList.add('open');

        // Wire click handlers
        this.dropdown.querySelectorAll('.svc-picker-item').forEach(el => {
            el.addEventListener('mousedown', e => {
                e.preventDefault(); // prevent input blur
                const idx = parseInt(el.dataset.index);
                this._selectIndex(idx);
            });
        });
    }

    _onKeydown(e) {
        if (!this.dropdown.classList.contains('open')) return;
        const maxIdx = this.filteredItems.length - 1;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            this.highlightIndex = Math.min(this.highlightIndex + 1, maxIdx);
            this._updateHighlight();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            this.highlightIndex = Math.max(this.highlightIndex - 1, 0);
            this._updateHighlight();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (this.highlightIndex >= 0) {
                this._selectIndex(this.highlightIndex);
            }
        } else if (e.key === 'Escape') {
            this._close();
        }
    }

    _updateHighlight() {
        this.dropdown.querySelectorAll('.svc-picker-item').forEach((el, i) => {
            el.classList.toggle('highlighted', i === this.highlightIndex);
            if (i === this.highlightIndex) {
                el.scrollIntoView({ block: 'nearest' });
            }
        });
    }

    _selectIndex(idx) {
        const item = this.filteredItems[idx];
        if (!item) return;
        this.selectedId = item.id;
        this.selectedName = item.name;
        this.input.value = item.name;
        this.input.classList.add('has-value');
        this.hiddenInput.value = item.id || '';
        this._close();
        if (this.onSelect) this.onSelect(item);
    }

    _close() {
        this.dropdown.classList.remove('open');
        this.highlightIndex = -1;
    }

    setValue(name, id) {
        this.selectedId = id;
        this.selectedName = name;
        this.input.value = name || '';
        this.hiddenInput.value = id || '';
        this.input.classList.toggle('has-value', !!name);
    }

    getValue() {
        return { id: this.selectedId, name: this.input.value.trim() };
    }

    clear() {
        this.selectedId = null;
        this.selectedName = '';
        this.input.value = '';
        this.hiddenInput.value = '';
        this.input.classList.remove('has-value');
    }
}

// Generic lazy ServicePicker factory — caches by container ID
const _pickerCache = {};
function getServicePicker(containerId, options) {
    if (!_pickerCache[containerId]) {
        _pickerCache[containerId] = new ServicePicker(containerId, options || {});
    }
    return _pickerCache[containerId];
}

function getResolveServicePicker() {
    return getServicePicker('resolve-svc-picker', {
        allowCreate: true,
        onSelect: (item) => {
            if (item.isNew) {
                document.getElementById('resolve-cat-hint').style.display = 'none';
            } else {
                onResolveServiceChange(item.name);
            }
        },
    });
}

function getAddRuleServicePicker() {
    return getServicePicker('rule-svc-picker');
}

function getEditRuleServicePicker() {
    return getServicePicker('edit-rule-svc-picker', {
        onSelect: (item) => updateEditRuleServiceMeta(item.id),
    });
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
    if (subsSpend !== 'all') active = active.filter(s => matchesScope(subsSpend, s));

    const totalMonthly = active.reduce((sum, s) => sum + (s.monthly_sgd || 0), 0);
    const personalMonthly = active.filter(s => getScope(s) === 'personal').reduce((sum, s) => sum + (s.monthly_sgd || 0), 0);
    const moomMonthly = active.filter(s => getScope(s) === 'moom').reduce((sum, s) => sum + (s.monthly_sgd || 0), 0);
    const kaleshMonthly = active.filter(s => getScope(s) === 'kalesh').reduce((sum, s) => sum + (s.monthly_sgd || 0), 0);
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
            <div class="stat-label">Moom</div>
            <div class="stat-value moom">S$${formatAmount(moomMonthly)}</div>
            <div class="stat-sub">S$${formatAmount(moomMonthly * 12)}/year</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Kalesh</div>
            <div class="stat-value" style="color:var(--accent-pop);">S$${formatAmount(kaleshMonthly)}</div>
            <div class="stat-sub">S$${formatAmount(kaleshMonthly * 12)}/year</div>
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
    const d = new Date(dateStr + 'T00:00:00');
    if (isNaN(d)) return dateStr;
    const dd = String(d.getDate()).padStart(2, '0');
    return `${dd}-${MONTH_NAMES[d.getMonth() + 1]}-${String(d.getFullYear()).slice(2)}`;
}

const sortSubs = createSortToggler(subsSortState, () => renderSubscriptions(allSubs));

function renderSubscriptions(subs) {
    let filtered = subs;
    if (subsFilter === 'active') filtered = subs.filter(s => s.status === 'active');
    else if (subsFilter === 'deactivated') filtered = subs.filter(s => s.status !== 'active');

    // Spend filter
    if (subsSpend !== 'all') filtered = filtered.filter(s => matchesScope(subsSpend, s));

    // Sort
    filtered = [...filtered].sort((a, b) => {
        let va, vb;
        switch (subsSortState.col) {
            case 'service':   va = (a.service_name || '').toLowerCase(); vb = (b.service_name || '').toLowerCase(); break;
            case 'category':  va = (a.display_category || '').toLowerCase(); vb = (b.display_category || '').toLowerCase(); break;
            case 'billed':    va = a.amount || 0; vb = b.amount || 0; break;
            case 'monthly':   va = a.monthly_sgd || 0; vb = b.monthly_sgd || 0; break;
            case 'frequency': va = a.frequency; vb = b.frequency; break;
            case 'card':      va = (a.account_short_name || '').toLowerCase(); vb = (b.account_short_name || '').toLowerCase(); break;
            case 'last_paid': va = a.tx_last_paid || a.last_paid || ''; vb = b.tx_last_paid || b.last_paid || ''; break;
            case 'renewal':   va = a.computed_renewal || a.renewal_date || ''; vb = b.computed_renewal || b.renewal_date || ''; break;
            default:          va = (a.service_name || '').toLowerCase(); vb = (b.service_name || '').toLowerCase();
        }
        if (va < vb) return subsSortState.asc ? -1 : 1;
        if (va > vb) return subsSortState.asc ? 1 : -1;
        return 0;
    });

    updateSortIndicators('#subs-table', subsSortState);

    const body = document.getElementById('subs-body');
    const empty = document.getElementById('subs-empty');

    if (!filtered.length) {
        body.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    body.innerHTML = filtered.map(s => {
        const freqAbbr = { weekly: 'wk', biweekly: '2wk', monthly: 'mo', quarterly: 'qt', 'half-yearly': '6mo', yearly: 'yr' };
        const freqShort = freqAbbr[s.frequency] || s.frequency;
        const freqLabel = s.periods > 1 ? `${s.periods}x ${freqShort}` : freqShort;
        const lastPaidRaw = s.tx_last_paid || s.last_paid || null;
        const linkHtml = s.link ? `<a href="${escapeHtml(s.link)}" target="_blank" class="subs-link" title="Manage">Manage</a>` : '';
        const statusClass = s.status === 'active' ? '' : 'subs-row-inactive';

        // Billed = configured amount per cycle (source of truth)
        const amt = s.amount || 0;
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

        // Highlight rows with renewal in next 3 days
        const renewalDate = s.computed_renewal || s.renewal_date;
        let renewalSoon = false;
        if (renewalDate && s.status === 'active') {
            const diff = (new Date(renewalDate) - new Date()) / (1000 * 60 * 60 * 24);
            renewalSoon = diff >= 0 && diff <= 3;
        }
        const rowClass = [statusClass, renewalSoon ? 'subs-row-renewal-soon' : ''].filter(Boolean).join(' ');

        return `<tr class="${rowClass}">
            <td>
                <div style="font-weight:600;font-size:13px;">${s.service_id ? `<a href="#" class="svc-link" onclick="navigateToService(${s.service_id});return false;">${escapeHtml(s.service_name || '')}</a>` : escapeHtml(s.service_name || '')}</div>
            </td>
            <td class="subs-col-hide-sm"><a href="#" class="cat-link" onclick="navigateToCategory('${escapeHtml(s.parent_name || s.category_name || '')}');return false;">${escapeHtml(s.display_category)}</a></td>
            <td style="text-align:right;font-size:13px;">${billedHtml}</td>
            <td style="text-align:right;font-size:13px;font-weight:600;color:var(--accent-camel);" title="${escapeHtml(monthlyTitle)}">
                ${monthlyLabel}
            </td>
            <td class="subs-col-hide-sm" style="font-size:11px;color:var(--text-tertiary);white-space:nowrap;">${freqLabel}</td>
            <td class="subs-card-cell subs-col-hide-md" title="${escapeHtml(s.account_short_name || '')}">${escapeHtml(s.account_short_name || '')}</td>
            <td style="font-size:12px;white-space:nowrap;">${lastPaidHtml}</td>
            <td class="subs-col-hide-sm" style="font-size:12px;white-space:nowrap;${renewalSoon ? 'color:var(--accent-pop);font-weight:600;' : 'color:var(--text-tertiary);'}">${formatDateDMY(renewalDate)}${renewalSoon ? ' ●' : ''}</td>
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

// Get the first rule pattern for a service (for match_pattern auto-derivation)
function getServiceRulePattern(serviceId) {
    if (!allServicesList) return null;
    const svc = allServicesList.find(s => s.id === serviceId);
    if (svc && svc.rules && svc.rules.length) return svc.rules[0].pattern;
    return null;
}

// Shared: populate a category dropdown with hierarchy + "+ New category..."
function populateSubCategoryDropdown(selectId, selectedId) {
    const prefix = selectId === 'sub-category' ? 'add-sub' : 'edit-sub';
    populateCategorySelect(selectId, {
        includeNew: true, selectedId, parentSelectId: `${prefix}-new-cat-parent`
    });
}

function onSubCategoryChange(mode) {
    const prefix = mode === 'add' ? 'add-sub' : 'edit-sub';
    const selId = mode === 'add' ? 'sub-category' : 'edit-sub-category';
    toggleNewCategoryRow(selId, `${prefix}-new-cat-row`, `${prefix}-new-cat-name`);
}

// Subscription ServicePicker — auto-fill category on selection
function _subPickerOnSelect(catSelectId, catHintId) {
    return (item) => {
        if (!item.isNew && allServicesList) {
            const svc = allServicesList.find(s => s.id === item.id);
            if (svc && svc.category_id) {
                document.getElementById(catSelectId).value = String(svc.category_id);
                document.getElementById(catHintId).style.display = 'block';
                return;
            }
        }
        document.getElementById(catHintId).style.display = 'none';
    };
}

function getAddSubServicePicker() {
    return getServicePicker('add-sub-svc-picker', {
        allowCreate: true,
        onSelect: _subPickerOnSelect('sub-category', 'sub-cat-hint'),
    });
}

function getEditSubServicePicker() {
    return getServicePicker('edit-sub-svc-picker', {
        allowCreate: true,
        onSelect: _subPickerOnSelect('edit-sub-category', 'edit-sub-cat-hint'),
    });
}

async function openAddSubModal() {
    // Reset all fields
    const defaults = { 'sub-category': '', 'sub-amount': '', 'sub-currency': 'SGD', 'sub-frequency': 'monthly',
        'sub-periods': '1', 'sub-card': '', 'sub-renewal': '', 'sub-status': 'active',
        'sub-link': '', 'sub-notes': '', 'sub-start-date': '' };
    for (const [id, val] of Object.entries(defaults)) document.getElementById(id).value = val;
    document.getElementById('add-sub-new-cat-row').classList.add('hidden');
    document.getElementById('sub-cat-hint').style.display = 'none';

    // Initialize ServicePicker
    const picker = getAddSubServicePicker();
    picker.clear();

    // Populate category dropdown
    populateSubCategoryDropdown('sub-category', null);

    // Populate card dropdown from active accounts only
    const cardSel = document.getElementById('sub-card');
    cardSel.innerHTML = '<option value="">—</option>';
    accounts.filter(a => a.status !== 'archived').forEach(a => {
        cardSel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
    });

    setupFxHint('add-sub-modal', 'sub-currency');

    // Auto-suggest renewal from start date + frequency + periods (only if empty)
    const suggestRenewal = () => {
        const startVal = document.getElementById('sub-start-date').value;
        const renewalEl = document.getElementById('sub-renewal');
        if (!startVal || renewalEl.value) return;
        renewalEl.value = calcRenewalDate(startVal,
            document.getElementById('sub-frequency').value,
            parseInt(document.getElementById('sub-periods').value) || 1);
    };
    document.getElementById('sub-start-date').onchange = suggestRenewal;
    document.getElementById('sub-frequency').addEventListener('change', suggestRenewal);
    document.getElementById('sub-periods').addEventListener('change', suggestRenewal);

    openModalEl('add-sub-modal', closeAddSubModal);
}

function closeAddSubModal() {
    closeModalEl('add-sub-modal');
}

async function addSubscription() {
    const resolved = await resolveSubService(getAddSubServicePicker, 'sub-category', 'add-sub');
    if (!resolved) return;
    const { serviceId, categoryId, serviceName } = resolved;

    const body = readSubFormBody('sub', serviceId, categoryId, serviceName);
    const data = await apiFetch('/api/subscriptions', { method: 'POST', body });
    if (!data) return;

    const recat = await cascadeServiceCategory(serviceId, categoryId, serviceName, 'Added subscription');
    if (!recat) showToast(`Added subscription "${serviceName}"`, 'info');

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

async function navigateToTransaction(txDate, matchPattern) {
    // Switch to Dashboard tab, set month/year, search by service/pattern
    switchTab('dashboard', { pushHistory: false });

    // Ensure flat view for transaction-level navigation
    setTxView('flat');

    // Set period filter to the transaction's month
    const [year, month] = txDate.split('-');
    const yearSel = document.getElementById('filter-year');
    const monthSel = document.getElementById('filter-month');
    if (yearSel) yearSel.value = year;
    if (monthSel) monthSel.value = month;

    // Set search to match pattern
    const searchEl = document.getElementById('tx-search');
    if (searchEl) searchEl.value = matchPattern || '';

    // Reload dashboard with new filters (preserves search)
    txCurrentPage = 1;
    await loadDashboard(true);

    // Scroll to table
    setTimeout(() => {
        const txTable = document.getElementById('tx-table');
        if (txTable) txTable.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 300);
}

async function enrichSubscriptions() {
    const btn = document.querySelector('#tab-subs .btn-sm');
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    try {
        const res = await fetch('/api/subscriptions/enrich', { method: 'POST' });
        if (!res.ok) {
            showToast('Failed to refresh subscriptions', 'warn');
            return;
        }
        const data = await res.json();
        if (data.error) {
            showToast('Error: ' + data.error, 'warn');
            return;
        }
        await loadSubscriptions();
        showToast(`Refreshed ${data.updated || 0} subscriptions`, 'info');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Refresh from Txns';
    }
}


// ============================================================
// EDIT SUBSCRIPTION MODAL
// ============================================================

async function openEditSubModal(subId) {
    const sub = allSubs.find(s => s.id === subId);
    if (!sub) return;

    document.getElementById('edit-sub-id').value = sub.id;
    document.getElementById('edit-sub-new-cat-row').classList.add('hidden');
    document.getElementById('edit-sub-cat-hint').style.display = 'none';

    // Initialize ServicePicker with current service
    const picker = getEditSubServicePicker();
    picker.setValue(sub.service_name || '', sub.service_id || null);

    const fieldMap = { amount: sub.amount || '', currency: sub.currency || 'SGD', freq: sub.frequency || 'monthly',
        periods: sub.periods || 1, 'start-date': sub.start_date || '', renewal: sub.renewal_date || '',
        status: sub.status || 'active', link: sub.link || '', notes: sub.notes || '' };
    for (const [k, v] of Object.entries(fieldMap)) document.getElementById(`edit-sub-${k}`).value = v;

    // Populate category dropdown with current selection
    populateSubCategoryDropdown('edit-sub-category', sub.category_id);

    // Populate card dropdown from accounts (include archived if sub uses one)
    const cardSel = document.getElementById('edit-sub-card');
    cardSel.innerHTML = '<option value="">—</option>';
    accounts.forEach(a => {
        if (a.status !== 'archived' || a.id === sub.account_id) {
            cardSel.innerHTML += `<option value="${a.id}">${a.short_name}</option>`;
        }
    });
    if (sub.account_id) cardSel.value = sub.account_id;

    openModalEl('edit-sub-modal', closeEditSubModal);

    setupFxHint('edit-sub-modal', 'edit-sub-currency');

    // Auto-suggest renewal (always recalculate for edits)
    const suggestEditRenewal = () => {
        const startVal = document.getElementById('edit-sub-start-date').value;
        if (!startVal) return;
        document.getElementById('edit-sub-renewal').value = calcRenewalDate(startVal,
            document.getElementById('edit-sub-freq').value,
            parseInt(document.getElementById('edit-sub-periods').value) || 1);
    };
    document.getElementById('edit-sub-start-date').onchange = suggestEditRenewal;
}

function closeEditSubModal() {
    closeModalEl('edit-sub-modal');
}

async function saveEditSub() {
    const subId = parseInt(document.getElementById('edit-sub-id').value);
    const resolved = await resolveSubService(getEditSubServicePicker, 'edit-sub-category', 'edit-sub');
    if (!resolved) return;
    const { serviceId, categoryId, serviceName } = resolved;

    if (!serviceId) { alert('Please select a service'); return; }

    const body = readSubFormBody('edit-sub', serviceId, categoryId, serviceName);
    const data = await apiFetch(`/api/subscriptions/${subId}`, { method: 'PUT', body });
    if (!data) return;

    const recat = await cascadeServiceCategory(serviceId, categoryId, serviceName, 'Updated subscription');
    if (!recat) showToast(`Updated subscription "${serviceName}"`, 'info');

    closeEditSubModal();
    await loadSubscriptions();
}

async function deleteSubFromModal() {
    const subId = parseInt(document.getElementById('edit-sub-id').value);
    const picker = getEditSubServicePicker();
    const { name: service } = picker.getValue();
    if (!confirm(`Delete subscription "${service}"?`)) return;

    const data = await apiFetch(`/api/subscriptions/${subId}`, { method: 'DELETE' });
    if (!data) return;

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

    const activeAccts = accounts.filter(a => a.status !== 'archived');
    const archivedAccts = accounts.filter(a => a.status === 'archived');
    const showArchived = document.getElementById('acct-show-archived')?.checked;
    const visible = showArchived ? accounts : activeAccts;

    count.textContent = `${activeAccts.length} active${archivedAccts.length ? `, ${archivedAccts.length} archived` : ''}`;

    if (!visible.length) {
        body.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');

    body.innerHTML = visible.map(a => {
        if (a.id === editingAcctId) {
            return renderAcctEditRow(a);
        }
        const isArchived = a.status === 'archived';
        const archiveBtn = isArchived
            ? `<button class="btn btn-sm" onclick="toggleArchiveAcct(${a.id}, 'active')" title="Restore">Restore</button>`
            : `<button class="btn btn-sm" onclick="toggleArchiveAcct(${a.id}, 'archived')" title="Archive">Archive</button>`;
        return `<tr${isArchived ? ' style="opacity:0.5;"' : ''}>
            <td style="font-size:13px;">${escapeHtml(a.name)}${isArchived ? ' <span class="badge badge-muted" style="font-size:10px;margin-left:6px;">archived</span>' : ''}</td>
            <td style="font-size:12px;color:var(--text-tertiary);">${escapeHtml(a.short_name)}</td>
            <td><span class="acct-type-badge acct-type-${a.type}">${ACCT_TYPE_LABELS[a.type] || a.type}</span></td>
            <td style="font-size:12px;color:var(--text-tertiary);">${a.last_four || '—'}</td>
            <td style="font-size:12px;color:var(--text-tertiary);">${a.currency || 'SGD'}</td>
            <td style="text-align:right;white-space:nowrap;">
                <button class="btn btn-sm" onclick="startEditAcct(${a.id})">Edit</button>
                ${archiveBtn}
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

    const data = await apiFetch('/api/accounts', { method: 'POST', body });
    if (!data) return;

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

    const data = await apiFetch(`/api/accounts/${id}`, { method: 'PUT', body });
    if (!data) return;

    editingAcctId = null;
    await reloadAccounts();
}

async function toggleArchiveAcct(id, newStatus) {
    const acct = accounts.find(a => a.id === id);
    const action = newStatus === 'archived' ? 'Archive' : 'Restore';
    if (!confirm(`${action} account "${acct ? acct.short_name : id}"?`)) return;

    const data = await apiFetch(`/api/accounts/${id}`, { method: 'PUT', body: { status: newStatus } });
    if (!data) return;

    await reloadAccounts();
}

async function deleteAccount(id) {
    const acct = accounts.find(a => a.id === id);
    if (!confirm(`Delete account "${acct ? acct.name : id}"?`)) return;

    const data = await apiFetch(`/api/accounts/${id}`, { method: 'DELETE' });
    if (!data) return;

    await reloadAccounts();
}

async function reloadAccounts() {
    // Refresh the global accounts array and update all dependent UI
    const acctRes = await fetch('/api/accounts').then(r => r.json());
    accounts = acctRes;
    populateAccountFilter();
    renderAccountsTab();
}
