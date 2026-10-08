/* Expense & Transfer Tracker — main application. */
(function () {
  'use strict';

  const { CATEGORIES, ACCOUNT_TYPES, DEFAULT_ACCOUNTS, CURRENCIES } = window.TRACKER_DATA;
  const ACCOUNT_COLORS = ['#f5b301', '#c8102e', '#005abb', '#16a34a', '#7c3aed', '#0891b2', '#ea580c', '#64748b'];
  const MAX_FILE_BYTES = 15 * 1024 * 1024;
  const TYPE_LABEL = { expense: 'Expense', income: 'Income', transfer: 'Transfer' };

  const state = {
    accounts: [],
    txns: [],
    settings: { currency: 'MYR', theme: 'system' },
    dashMonth: currentMonth(),
    filters: { from: '', to: '', type: 'all', account: 'all', status: 'all', q: '' }
  };

  /* ---------- Helpers ---------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function pad(n) { return String(n).padStart(2, '0'); }

  function localDate(d = new Date()) { return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
  function localDateTime(d = new Date()) { return `${localDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`; }
  function currentMonth() { return localDate().slice(0, 7); }
  function shiftMonth(m, delta) {
    const [y, mo] = m.split('-').map(Number);
    const d = new Date(y, mo - 1 + delta, 1);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
  }
  function monthLabel(m) {
    const [y, mo] = m.split('-').map(Number);
    return new Date(y, mo - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
  }
  function monthBounds(m) {
    const [y, mo] = m.split('-').map(Number);
    return { from: `${m}-01`, to: localDate(new Date(y, mo, 0)) };
  }
  function dayLabel(iso) {
    const today = localDate();
    const y = new Date(); y.setDate(y.getDate() - 1);
    if (iso === today) return 'Today';
    if (iso === localDate(y)) return 'Yesterday';
    const d = new Date(iso + 'T00:00');
    const sameYear = d.getFullYear() === new Date().getFullYear();
    return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: sameYear ? undefined : 'numeric' });
  }
  function timeLabel(dt) {
    const d = new Date(dt);
    return isNaN(d) ? '' : d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }
  function fullDateTime(dt) {
    const d = new Date(dt);
    return isNaN(d) ? dt : d.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function randomHex(len) {
    const bytes = new Uint8Array(Math.ceil(len / 2));
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('').slice(0, len).toUpperCase();
  }
  function uid(prefix) { return `${prefix}-${Date.now().toString(36).toUpperCase()}${randomHex(6)}`; }
  function newTxnId(datetime) {
    const ymd = (datetime || localDateTime()).slice(0, 10).replace(/-/g, '');
    let id;
    do { id = `TXN-${ymd}-${randomHex(6)}`; } while (state.txns.some((t) => t.id === id));
    return id;
  }

  /** "1,234.50" → 123450 (integer cents). Returns NaN when invalid. */
  function parseAmount(str) {
    const s = String(str == null ? '' : str).replace(/[,\s]/g, '');
    if (!/^\d+(\.\d{0,2})?$|^\.\d{1,2}$/.test(s)) return NaN;
    return Math.round(parseFloat(s) * 100);
  }
  function parseSignedAmount(str) {
    const s = String(str == null ? '' : str).trim();
    const neg = /^[-−]/.test(s);
    const v = parseAmount(s.replace(/^[-−+]/, ''));
    return neg ? -v : v;
  }
  function centsToInput(c) { return (c / 100).toFixed(2); }

  let numberFormat = null;
  function fmt(cents, { sign = false } = {}) {
    if (!numberFormat) {
      try {
        numberFormat = new Intl.NumberFormat(undefined, { style: 'currency', currency: state.settings.currency, currencyDisplay: 'narrowSymbol' });
      } catch (e) {
        numberFormat = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      }
    }
    const s = numberFormat.format(Math.abs(cents) / 100);
    if (cents < 0) return '−' + s;
    return (sign && cents > 0 ? '+' : '') + s;
  }
  function currencySymbol() {
    try {
      const parts = new Intl.NumberFormat(undefined, { style: 'currency', currency: state.settings.currency, currencyDisplay: 'narrowSymbol' }).formatToParts(0);
      return (parts.find((p) => p.type === 'currency') || {}).value || state.settings.currency;
    } catch (e) { return state.settings.currency; }
  }

  const accById = (id) => state.accounts.find((a) => a.id === id);
  const accName = (id) => (accById(id) || {}).name || 'Deleted account';
  const accType = (type) => ACCOUNT_TYPES.find((t) => t.id === type) || ACCOUNT_TYPES[0];
  function catFor(type, id) {
    return (CATEGORIES[type] || []).find((c) => c.id === id) || { id: '', label: 'Uncategorised', icon: '•', subs: [] };
  }

  let toastTimer;
  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2400);
  }

  function sortTxns() {
    state.txns.sort((a, b) => (b.datetime || '').localeCompare(a.datetime || '') || (b.createdAt || 0) - (a.createdAt || 0));
  }

  /* ---------- Calculations ---------- */
  function balances() {
    const bal = {};
    for (const a of state.accounts) bal[a.id] = a.openingBalance || 0;
    for (const t of state.txns) {
      if (t.type === 'expense' && t.accountId in bal) bal[t.accountId] -= t.amount;
      else if (t.type === 'income' && t.accountId in bal) bal[t.accountId] += t.amount;
      else if (t.type === 'transfer') {
        // A transfer only moves money between own accounts.
        if (t.accountId in bal) bal[t.accountId] -= t.amount;
        if (t.toAccountId in bal) bal[t.toAccountId] += t.amount;
      }
    }
    return bal;
  }

  /** Income/expense totals. Transfers are tracked separately and never counted as income or expense. */
  function totals(txns) {
    const r = { income: 0, expense: 0, transfer: 0, nIncome: 0, nExpense: 0, nTransfer: 0 };
    for (const t of txns) {
      if (t.type === 'income') { r.income += t.amount; r.nIncome++; }
      else if (t.type === 'expense') { r.expense += t.amount; r.nExpense++; }
      else if (t.type === 'transfer') { r.transfer += t.amount; r.nTransfer++; }
    }
    return r;
  }

  function inMonth(t, m) { return (t.datetime || '').slice(0, 7) === m; }

  function filteredTxns() {
    const f = state.filters;
    const q = f.q.trim().toLowerCase();
    return state.txns.filter((t) => {
      const day = (t.datetime || '').slice(0, 10);
      if (f.from && day < f.from) return false;
      if (f.to && day > f.to) return false;
      if (f.type !== 'all' && t.type !== f.type) return false;
      if (f.account !== 'all' && t.accountId !== f.account && t.toAccountId !== f.account) return false;
      if (f.status !== 'all' && t.status !== f.status) return false;
      if (q) {
        const cat = catFor(t.type, t.category);
        const hay = [t.id, t.payee, cat.label, t.subcategory, accName(t.accountId), t.toAccountId ? accName(t.toAccountId) : '', (t.amount / 100).toFixed(2)].join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  /* ---------- Rendering: shared pieces ---------- */
  function txnRow(t) {
    const cat = catFor(t.type, t.category);
    const receipts = (t.receiptIds || []).length;
    let icon, title, meta, amount;
    if (t.type === 'transfer') {
      icon = '⇄';
      title = t.payee || cat.label || 'Transfer';
      meta = `<span class="txn-route">${esc(accName(t.accountId))} <span aria-hidden="true">→</span><span class="sr-only">to</span> ${esc(accName(t.toAccountId))}</span>`;
      amount = `<span class="amt-transfer">${fmt(t.amount)}</span>`;
    } else {
      icon = cat.icon;
      title = t.payee || t.subcategory || cat.label;
      meta = `<span>${esc(t.subcategory || cat.label)}</span><span aria-hidden="true">·</span><span>${esc(accName(t.accountId))}</span>`;
      amount = t.type === 'expense'
        ? `<span class="amt-expense">−${fmt(t.amount)}</span>`
        : `<span class="amt-income">+${fmt(t.amount)}</span>`;
    }
    const badges = [
      t.status === 'pending' ? '<span class="badge pending">Pending</span>' : '',
      receipts ? `<span class="badge receipt" title="${receipts} receipt(s)">📎 ${receipts}</span>` : ''
    ].join('');
    return `<li><button type="button" class="txn ${t.type}" data-open-txn="${esc(t.id)}">
      <span class="txn-icon" aria-hidden="true">${icon}</span>
      <span class="txn-main">
        <span class="txn-title">${esc(title)}</span>
        <span class="txn-meta"><span class="sr-only">${TYPE_LABEL[t.type]}.</span>${meta}${badges}</span>
      </span>
      <span class="txn-amt num">${amount}<small>${esc(timeLabel(t.datetime))}</small></span>
    </button></li>`;
  }

  function groupedList(txns) {
    if (!txns.length) return '';
    const groups = [];
    for (const t of txns) {
      const day = (t.datetime || '').slice(0, 10);
      if (!groups.length || groups[groups.length - 1].day !== day) groups.push({ day, items: [] });
      groups[groups.length - 1].items.push(t);
    }
    return groups.map((g) => {
      const net = g.items.reduce((s, t) => s + (t.type === 'income' ? t.amount : t.type === 'expense' ? -t.amount : 0), 0);
      return `<section class="txn-group">
        <div class="txn-day"><span>${esc(dayLabel(g.day))}</span><span class="num">${net ? fmt(net, { sign: true }) : ''}</span></div>
        <ul class="txn-list card">${g.items.map(txnRow).join('')}</ul>
      </section>`;
    }).join('');
  }

  function emptyState(icon, title, body, action) {
    return `<div class="card empty"><div class="big" aria-hidden="true">${icon}</div><b>${esc(title)}</b><p>${esc(body)}</p>${action || ''}</div>`;
  }

  /* ---------- Views ---------- */
  function viewDashboard() {
    const bal = balances();
    const total = Object.values(bal).reduce((a, b) => a + b, 0);
    const m = state.dashMonth;
    const monthTx = state.txns.filter((t) => inMonth(t, m));
    const tt = totals(monthTx);
    const pending = state.txns.filter((t) => t.status === 'pending').length;
    const recent = state.txns.slice(0, 12);

    const accounts = state.accounts.map((a) => `
      <button type="button" class="acct-chip" data-account-filter="${esc(a.id)}">
        <span class="name"><span class="acct-bar" style="background:${esc(a.color)}"></span>${esc(a.name)}</span>
        <div class="bal num">${fmt(bal[a.id] || 0)}</div>
      </button>`).join('');

    return `
      <div class="page-head">
        <div><h1>Dashboard</h1><p class="muted">${esc(new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }))}</p></div>
        <div class="month-switch" aria-label="Summary month">
          <button type="button" data-dash-month="-1" aria-label="Previous month">‹</button>
          <span>${esc(monthLabel(m))}</span>
          <button type="button" data-dash-month="1" aria-label="Next month">›</button>
        </div>
      </div>

      <div class="summary">
        <div class="card stat hero">
          <span class="stat-label">Total balance</span>
          <span class="stat-value num">${fmt(total)}</span>
          <span class="stat-sub">Across ${state.accounts.length} account${state.accounts.length === 1 ? '' : 's'}</span>
        </div>
        <div class="card stat">
          <span class="stat-label"><span class="dot expense" aria-hidden="true">↑</span>Monthly expenses</span>
          <span class="stat-value num">${fmt(tt.expense)}</span>
          <span class="stat-sub">${tt.nExpense} transaction${tt.nExpense === 1 ? '' : 's'} · ${esc(monthLabel(m))}</span>
        </div>
        <div class="card stat">
          <span class="stat-label"><span class="dot income" aria-hidden="true">↓</span>Monthly income</span>
          <span class="stat-value num">${fmt(tt.income)}</span>
          <span class="stat-sub">${tt.nIncome} transaction${tt.nIncome === 1 ? '' : 's'} · net ${fmt(tt.income - tt.expense, { sign: true })}</span>
        </div>
      </div>
      <p class="hint" style="margin:10px 4px 0">${tt.nTransfer ? `${tt.nTransfer} transfer${tt.nTransfer === 1 ? '' : 's'} (${fmt(tt.transfer)}) this month — moved between your own accounts, not counted as income or expense.` : 'Transfers between your own accounts are never counted as income or expense.'}
      ${pending ? ` · <button type="button" class="link" data-show-pending>${pending} pending reconciliation</button>` : ''}</p>

      <section class="section">
        <div class="section-head"><h3>Accounts</h3><a href="#/accounts">Manage</a></div>
        ${state.accounts.length ? `<div class="acct-strip">${accounts}</div>`
          : emptyState('🏦', 'No accounts yet', 'Add a bank, e-wallet or cash account to start tracking.', '<button class="btn primary" data-action="new-account">Add account</button>')}
      </section>

      <section class="section">
        <div class="section-head"><h3>Recent transactions</h3>${state.txns.length ? '<a href="#/transactions">See all</a>' : ''}</div>
        ${recent.length ? groupedList(recent)
          : emptyState('🧾', 'Nothing logged yet', 'Log an expense, income or a transfer between your accounts.', '<button class="btn primary" data-action="new-txn">Add transaction</button>')}
      </section>`;
  }

  function viewTransactions() {
    const f = state.filters;
    const accountOpts = state.accounts.map((a) => `<option value="${esc(a.id)}" ${f.account === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('');
    const typeChip = (v, label) => `<button type="button" class="chip" data-filter-type="${v}" aria-pressed="${f.type === v}">${label}</button>`;
    const rangeChip = (v, label) => `<button type="button" class="chip" data-range="${v}" aria-pressed="${activeRange() === v}">${label}</button>`;
    return `
      <div class="page-head"><h1>Transactions</h1>
        <button type="button" class="btn sm" data-action="export-filtered">Export CSV</button>
      </div>
      <div class="card filters" role="search">
        <div class="row">
          <label class="field search"><span class="sr-only">Search</span>
            <input type="search" id="f-q" placeholder="Search payee, category, ID…" value="${esc(f.q)}"></label>
          <label class="field"><span>From</span><input type="date" id="f-from" value="${esc(f.from)}"></label>
          <label class="field"><span>To</span><input type="date" id="f-to" value="${esc(f.to)}"></label>
          <label class="field"><span>Account</span>
            <select id="f-account"><option value="all">All accounts</option>${accountOpts}</select></label>
        </div>
        <div class="chips" aria-label="Date range">
          ${rangeChip('this-month', 'This month')}${rangeChip('last-month', 'Last month')}${rangeChip('30d', 'Last 30 days')}${rangeChip('all', 'All time')}
        </div>
        <div class="chips" aria-label="Transaction type">
          ${typeChip('all', 'All types')}${typeChip('expense', '🔴 Expense')}${typeChip('income', '🟢 Income')}${typeChip('transfer', '🔵 Transfer')}
          <label class="field" style="margin-left:auto;min-width:150px"><span class="sr-only">Status</span>
            <select id="f-status">
              <option value="all" ${f.status === 'all' ? 'selected' : ''}>Any status</option>
              <option value="cleared" ${f.status === 'cleared' ? 'selected' : ''}>Cleared</option>
              <option value="pending" ${f.status === 'pending' ? 'selected' : ''}>Pending</option>
            </select></label>
        </div>
      </div>
      <div id="txn-results"></div>`;
  }

  function renderTxnResults() {
    const box = $('#txn-results');
    if (!box) return;
    const list = filteredTxns();
    const tt = totals(list);
    const anyFilter = state.filters.q || state.filters.from || state.filters.to || state.filters.type !== 'all' || state.filters.account !== 'all' || state.filters.status !== 'all';
    box.innerHTML = `
      <div class="filter-summary num" aria-live="polite">
        <span><b>${list.length}</b> result${list.length === 1 ? '' : 's'}</span>
        <span>Income <b class="amt-income">${fmt(tt.income)}</b></span>
        <span>Expenses <b class="amt-expense">${fmt(tt.expense)}</b></span>
        <span>Transfers <b class="amt-transfer">${fmt(tt.transfer)}</b></span>
        ${anyFilter ? '<button type="button" class="link" data-clear-filters>Clear filters</button>' : ''}
      </div>
      ${list.length ? groupedList(list) : emptyState('🔍', 'No matching transactions', anyFilter ? 'Try widening the date range or clearing filters.' : 'Tap + to log your first one.')}`;
  }

  function activeRange() {
    const { from, to } = state.filters;
    if (!from && !to) return 'all';
    const tm = monthBounds(currentMonth());
    if (from === tm.from && to === tm.to) return 'this-month';
    const lm = monthBounds(shiftMonth(currentMonth(), -1));
    if (from === lm.from && to === lm.to) return 'last-month';
    const d = new Date(); d.setDate(d.getDate() - 29);
    if (from === localDate(d) && to === localDate()) return '30d';
    return 'custom';
  }
  function applyRange(r) {
    const f = state.filters;
    if (r === 'all') { f.from = ''; f.to = ''; }
    else if (r === 'this-month') Object.assign(f, monthBounds(currentMonth()));
    else if (r === 'last-month') Object.assign(f, monthBounds(shiftMonth(currentMonth(), -1)));
    else if (r === '30d') { const d = new Date(); d.setDate(d.getDate() - 29); f.from = localDate(d); f.to = localDate(); }
  }

  function viewAccounts() {
    const bal = balances();
    const total = Object.values(bal).reduce((a, b) => a + b, 0);
    const rows = state.accounts.map((a) => {
      const n = state.txns.filter((t) => t.accountId === a.id || t.toAccountId === a.id).length;
      const pending = state.txns.filter((t) => t.status === 'pending' && (t.accountId === a.id || t.toAccountId === a.id)).length;
      return `<li class="acct-row">
        <span class="txn-icon" aria-hidden="true" style="box-shadow: inset 4px 0 0 ${esc(a.color)}">${accType(a.type).icon}</span>
        <button type="button" class="link" style="text-align:left;color:var(--ink)" data-account-filter="${esc(a.id)}">
          <b>${esc(a.name)}</b><br><span class="muted" style="font-weight:500;font-size:.8rem">${esc(accType(a.type).label)} · ${n} txn${n === 1 ? '' : 's'}${pending ? ` · ${pending} pending` : ''}</span>
        </button>
        <span class="bal num">${fmt(bal[a.id] || 0)}<small>Opening ${fmt(a.openingBalance || 0)}</small></span>
        <button type="button" class="icon-btn" data-edit-account="${esc(a.id)}" aria-label="Edit ${esc(a.name)}">✎</button>
      </li>`;
    }).join('');
    return `
      <div class="page-head"><div><h1>Accounts</h1><p class="muted num">Total ${fmt(total)}</p></div>
        <button type="button" class="btn primary sm" data-action="new-account">+ Add account</button></div>
      ${state.accounts.length ? `<ul class="acct-list card">${rows}</ul>` : emptyState('🏦', 'No accounts yet', 'Add Maybank, CIMB, Touch \'n Go, Cash or any other account.', '<button class="btn primary" data-action="new-account">Add account</button>')}
      ${state.accounts.length >= 2 ? '<div class="section"><button type="button" class="btn block" data-action="new-transfer">⇄ Transfer between accounts</button></div>' : ''}`;
  }

  function viewSettings() {
    const cur = CURRENCIES.includes(state.settings.currency) ? CURRENCIES : [state.settings.currency, ...CURRENCIES];
    return `
      <div class="page-head"><h1>Settings</h1></div>
      <div class="card settings-list">
        <div class="setting"><div class="s-text"><b>Currency</b><span>Used to format all amounts</span></div>
          <select id="s-currency" aria-label="Currency">${cur.map((c) => `<option ${c === state.settings.currency ? 'selected' : ''}>${c}</option>`).join('')}</select></div>
        <div class="setting"><div class="s-text"><b>Appearance</b><span>Light, dark or follow your device</span></div>
          <select id="s-theme" aria-label="Appearance">
            ${['system', 'light', 'dark'].map((t) => `<option value="${t}" ${t === state.settings.theme ? 'selected' : ''}>${t[0].toUpperCase() + t.slice(1)}</option>`).join('')}
          </select></div>
      </div>

      <div class="section"><h3 style="margin:0 4px 10px">Your data</h3>
      <div class="card settings-list">
        <div class="setting"><div class="s-text"><b>Export to CSV</b><span>All transactions, for Excel or Google Sheets</span></div>
          <button type="button" class="btn sm" data-action="export-all">Export</button></div>
        <div class="setting"><div class="s-text"><b>Backup</b><span>Full backup including receipt images (.json)</span></div>
          <button type="button" class="btn sm" data-action="backup">Download</button></div>
        <div class="setting"><div class="s-text"><b>Restore</b><span>Replace all data with a backup file</span></div>
          <label class="btn sm">Choose file<input type="file" id="s-restore" accept="application/json,.json" hidden></label></div>
        <div class="setting"><div class="s-text"><b>Load sample data</b><span>Adds demo transactions so you can explore</span></div>
          <button type="button" class="btn sm" data-action="sample">Load</button></div>
        <div class="setting"><div class="s-text"><b>Erase everything</b><span>Deletes all accounts, transactions and receipts</span></div>
          <button type="button" class="btn sm danger" data-action="erase">Erase</button></div>
      </div>
      <p class="hint" id="storage-info" style="margin:10px 4px">Data is stored privately on this device only.</p>
      </div>`;
  }

  /* ---------- Router ---------- */
  const ROUTES = { dashboard: viewDashboard, transactions: viewTransactions, accounts: viewAccounts, settings: viewSettings };
  function currentRoute() {
    const r = (location.hash.match(/^#\/(\w+)/) || [])[1];
    return ROUTES[r] ? r : 'dashboard';
  }
  function render() {
    const route = currentRoute();
    $('#view').innerHTML = ROUTES[route]();
    $$('.nav a').forEach((a) => {
      const on = a.dataset.route === route;
      a.classList.toggle('active', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    if (route === 'transactions') renderTxnResults();
    if (route === 'settings') showStorageInfo();
  }
  function go(route) {
    if (currentRoute() === route && location.hash) render();
    else location.hash = '#/' + route;
  }

  /* ---------- Sheet / dialog ---------- */
  let sheetCleanup = null;
  let lastFocus = null;
  function openSheet(html, onClose) {
    closeSheet(true);
    lastFocus = document.activeElement;
    const root = $('#sheet-root');
    root.innerHTML = `<div class="backdrop" data-close></div><div class="sheet" role="dialog" aria-modal="true">${html}</div>`;
    root.hidden = false;
    document.body.style.overflow = 'hidden';
    sheetCleanup = onClose || null;
    const sheet = $('.sheet', root);
    const h = $('h2', sheet);
    if (h) { h.id = 'sheet-title'; sheet.setAttribute('aria-labelledby', 'sheet-title'); }
    setTimeout(() => { if (sheet.contains(document.activeElement)) return; const first = $('[autofocus]', sheet) || $('button, input, select', sheet); if (first) first.focus({ preventScroll: true }); }, 30);
    return sheet;
  }
  function closeSheet(silent) {
    const root = $('#sheet-root');
    if (root.hidden) return;
    if (sheetCleanup) { const fn = sheetCleanup; sheetCleanup = null; fn(); }
    root.hidden = true;
    root.innerHTML = '';
    document.body.style.overflow = '';
    if (!silent && lastFocus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  /* ---------- Receipts ---------- */
  async function prepareReceipt(file) {
    if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name} is larger than 15 MB`);
    const isImage = /^image\//.test(file.type) && !/gif|svg/.test(file.type);
    if (isImage) {
      // Downscale large photos so they don't fill up device storage.
      try {
        const bmp = await createImageBitmap(file);
        const scale = Math.min(1, 1800 / Math.max(bmp.width, bmp.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(bmp.width * scale);
        canvas.height = Math.round(bmp.height * scale);
        canvas.getContext('2d').drawImage(bmp, 0, 0, canvas.width, canvas.height);
        if (bmp.close) bmp.close();
        const blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.82));
        if (blob && blob.size < file.size) {
          return { blob, mime: 'image/jpeg', name: (file.name || 'receipt').replace(/\.\w+$/, '') + '.jpg' };
        }
      } catch (e) { /* fall back to original file */ }
    }
    if (!/^image\//.test(file.type) && file.type !== 'application/pdf') throw new Error(`${file.name}: only images or PDFs are supported`);
    return { blob: file, mime: file.type, name: file.name || 'receipt' };
  }

  function thumbHtml(r, removable) {
    const inner = /^image\//.test(r.mime)
      ? `<img src="${r.url}" alt="Receipt ${esc(r.name)}" loading="lazy">`
      : `<span class="pdf"><b aria-hidden="true">📄</b>${esc(r.name)}</span>`;
    return `<div class="thumb">
      <button type="button" class="thumb" style="border:0;width:100%;height:100%" data-view-receipt="${esc(r.id)}" aria-label="View receipt ${esc(r.name)}">${inner}</button>
      ${removable ? `<button type="button" class="thumb-remove" data-remove-receipt="${esc(r.id)}" aria-label="Remove receipt">×</button>` : ''}
    </div>`;
  }

  async function loadReceipts(ids) {
    const out = [];
    for (const id of ids || []) {
      const r = await DB.get('receipts', id);
      if (r) out.push({ ...r, url: URL.createObjectURL(r.blob) });
    }
    return out;
  }

  function openViewer(r) {
    const root = $('#viewer-root');
    const isImg = /^image\//.test(r.mime);
    root.innerHTML = `<div class="backdrop" data-close-viewer></div>
      <div class="viewer" role="dialog" aria-modal="true" aria-label="Receipt">
        ${isImg ? `<img src="${r.url}" alt="Receipt ${esc(r.name)}">` : `<div class="card empty"><div class="big">📄</div><b>${esc(r.name)}</b><p>PDF receipt</p></div>`}
        <div class="btn-row">
          ${isImg ? '' : `<a class="btn" href="${r.url}" target="_blank" rel="noopener">Open PDF</a>`}
          <a class="btn" href="${r.url}" download="${esc(r.name)}">Download</a>
          <button type="button" class="btn primary" data-close-viewer>Close</button>
        </div>
      </div>`;
    root.hidden = false;
    $('[data-close-viewer].btn', root).focus();
  }
  function closeViewer() { const root = $('#viewer-root'); root.hidden = true; root.innerHTML = ''; }

  /* ---------- Transaction detail ---------- */
  async function openTxnDetail(id) {
    const t = state.txns.find((x) => x.id === id);
    if (!t) return;
    const cat = catFor(t.type, t.category);
    const receipts = await loadReceipts(t.receiptIds);
    const color = t.type === 'expense' ? 'var(--expense-bg)' : t.type === 'income' ? 'var(--income-bg)' : 'var(--transfer-bg)';
    const amountHtml = t.type === 'expense' ? `<span class="amt-expense">−${fmt(t.amount)}</span>`
      : t.type === 'income' ? `<span class="amt-income">+${fmt(t.amount)}</span>`
      : `<span class="amt-transfer">${fmt(t.amount)}</span>`;
    const sheet = openSheet(`
      <div class="sheet-head"><h2>${TYPE_LABEL[t.type]}</h2><button type="button" class="icon-btn" data-close aria-label="Close">×</button></div>
      <div class="detail-hero">
        <div class="big-icon" style="background:${color}" aria-hidden="true">${t.type === 'transfer' ? '⇄' : cat.icon}</div>
        <div class="amount num">${amountHtml}</div>
        ${t.payee ? `<div style="font-weight:600;margin-top:4px">${esc(t.payee)}</div>` : ''}
        ${t.type === 'transfer' ? `<div class="route">${esc(accName(t.accountId))} <span aria-hidden="true">→</span><span class="sr-only">to</span> ${esc(accName(t.toAccountId))}</div>` : ''}
      </div>
      <dl class="kv card">
        <div><dt>Transaction ID</dt><dd><code>${esc(t.id)}</code></dd></div>
        <div><dt>Date & time</dt><dd>${esc(fullDateTime(t.datetime))}</dd></div>
        <div><dt>Type</dt><dd>${TYPE_LABEL[t.type]}${t.type === 'transfer' ? ' <span class="muted" style="font-weight:500">(not income/expense)</span>' : ''}</dd></div>
        ${t.type === 'transfer'
          ? `<div><dt>From account</dt><dd>${esc(accName(t.accountId))}</dd></div><div><dt>To account</dt><dd>${esc(accName(t.toAccountId))}</dd></div>`
          : `<div><dt>Account</dt><dd>${esc(accName(t.accountId))}</dd></div>`}
        <div><dt>Category</dt><dd>${esc(cat.label)}${t.subcategory ? ` › ${esc(t.subcategory)}` : ''}</dd></div>
        <div><dt>Payee / description</dt><dd>${esc(t.payee || '—')}</dd></div>
        <div><dt>Status</dt><dd>${t.status === 'pending' ? '<span class="badge pending">● Pending reconciliation</span>' : '<span class="badge cleared">✓ Cleared</span>'}
          <button type="button" class="link" style="margin-left:8px" data-toggle-status="${esc(t.id)}">Mark ${t.status === 'pending' ? 'cleared' : 'pending'}</button></dd></div>
      </dl>
      <div class="section">
        <h3 style="margin:0 4px 10px">Receipts (${receipts.length})</h3>
        ${receipts.length ? `<div class="receipt-grid">${receipts.map((r) => thumbHtml(r, false)).join('')}</div>` : '<p class="muted" style="margin:0 4px">No receipt attached.</p>'}
      </div>
      <div class="sheet-foot">
        <button type="button" class="btn danger" data-delete-txn="${esc(t.id)}">Delete</button>
        <button type="button" class="btn" data-duplicate-txn="${esc(t.id)}">Duplicate</button>
        <button type="button" class="btn primary" data-edit-txn="${esc(t.id)}">Edit</button>
      </div>`, () => receipts.forEach((r) => URL.revokeObjectURL(r.url)));
    sheet.addEventListener('click', (e) => {
      const v = e.target.closest('[data-view-receipt]');
      if (v) openViewer(receipts.find((r) => r.id === v.dataset.viewReceipt));
    });
  }

  /* ---------- Transaction form ---------- */
  async function openTxnForm({ id = null, type = null, duplicateOf = null } = {}) {
    if (!state.accounts.length) { toast('Add an account first'); openAccountForm(); return; }
    const existing = id ? state.txns.find((t) => t.id === id) : null;
    const source = existing || (duplicateOf ? state.txns.find((t) => t.id === duplicateOf) : null);
    const form = {
      type: (source && source.type) || type || 'expense',
      receipts: existing ? await loadReceipts(existing.receiptIds) : [],
      removed: []
    };
    const accOpts = (sel) => state.accounts.map((a) => `<option value="${esc(a.id)}" ${a.id === sel ? 'selected' : ''}>${esc(a.name)}</option>`).join('');
    const defaultFrom = (source && source.accountId) || (state.filters.account !== 'all' && accById(state.filters.account) ? state.filters.account : state.accounts[0].id);
    const defaultTo = (source && source.toAccountId) || (state.accounts.find((a) => a.id !== defaultFrom) || {}).id || '';

    const sheet = openSheet(`
      <form class="form" id="txn-form" novalidate>
        <div class="sheet-head"><h2>${existing ? 'Edit transaction' : 'New transaction'}</h2><button type="button" class="icon-btn" data-close aria-label="Close">×</button></div>
        ${existing ? `<p class="hint" style="margin:-8px 0 0">ID <code>${esc(existing.id)}</code></p>` : ''}

        <div class="seg" role="radiogroup" aria-label="Transaction type">
          <button type="button" role="radio" data-type="expense">Expense</button>
          <button type="button" role="radio" data-type="income">Income</button>
          <button type="button" role="radio" data-type="transfer">⇄ Transfer</button>
        </div>

        <label class="field"><span>Amount</span>
          <span class="amount-field"><span class="cur">${esc(currencySymbol())}</span>
          <input type="text" name="amount" inputmode="decimal" autocomplete="off" placeholder="0.00" autofocus
            value="${source ? centsToInput(source.amount) : ''}" aria-describedby="form-error"></span>
        </label>

        <div class="row">
          <label class="field full"><span>Date & time</span>
            <input type="datetime-local" name="datetime" value="${esc(existing ? existing.datetime : localDateTime())}" required></label>
        </div>

        <div data-show="single">
          <label class="field"><span>Account</span><select name="accountId">${accOpts(defaultFrom)}</select></label>
        </div>
        <div data-show="transfer">
          <div class="transfer-route">
            <label class="field"><span>From account</span><select name="fromId">${accOpts(defaultFrom)}</select></label>
            <button type="button" class="swap-btn" data-swap aria-label="Swap accounts">⇄</button>
            <label class="field"><span>To account</span><select name="toId">${accOpts(defaultTo)}</select></label>
          </div>
          ${state.accounts.length < 2 ? '<p class="hint" style="margin:8px 0 0">You need at least two accounts to make a transfer. <button type="button" class="link" data-action="new-account">Add account</button></p>' : ''}
        </div>

        <div class="row">
          <label class="field"><span>Category</span><select name="category"></select></label>
          <label class="field"><span>Sub-category</span><select name="subcategory"></select></label>
        </div>

        <label class="field"><span>Payee / merchant / description</span>
          <input type="text" name="payee" maxlength="140" placeholder="e.g. Jaya Grocer, TNB bill, Salary Oct" value="${esc(source ? source.payee : '')}" list="payee-list" autocomplete="off"></label>
        <datalist id="payee-list">${[...new Set(state.txns.map((t) => t.payee).filter(Boolean))].slice(0, 50).map((p) => `<option value="${esc(p)}">`).join('')}</datalist>

        <div data-show="transfer"><div class="transfer-note">Transfers move money between your own accounts. They change account balances but are not counted as income or expense.</div></div>

        <div class="field"><span>Receipt</span>
          <div class="receipt-grid" id="receipt-grid"></div>
          <div class="attach-row">
            <label class="attach-btn"><span aria-hidden="true">📷</span> Camera<input type="file" accept="image/*" capture="environment" data-attach></label>
            <label class="attach-btn"><span aria-hidden="true">🖼️</span> Gallery / file<input type="file" accept="image/*,application/pdf" multiple data-attach></label>
          </div>
        </div>

        <label class="toggle">
          <span class="t-text"><b id="status-label">Cleared</b><span class="hint">Turn off if it still needs to be reconciled with your bank statement</span></span>
          <input type="checkbox" name="cleared" ${!source || source.status !== 'pending' ? 'checked' : ''}>
          <span class="switch" aria-hidden="true"></span>
        </label>

        <p class="form-error" id="form-error" role="alert"></p>
        <div class="sheet-foot">
          <button type="button" class="btn" data-close>Cancel</button>
          <button type="submit" class="btn primary">${existing ? 'Save changes' : 'Save'}</button>
        </div>
      </form>`, () => form.receipts.forEach((r) => r.url && URL.revokeObjectURL(r.url)));

    const f = $('#txn-form', sheet);
    const el = (n) => f.elements[n];

    function fillCategories(selectedCat, selectedSub) {
      const cats = CATEGORIES[form.type];
      const cat = cats.find((c) => c.id === selectedCat) || cats[0];
      el('category').innerHTML = cats.map((c) => `<option value="${c.id}" ${c.id === cat.id ? 'selected' : ''}>${c.icon} ${esc(c.label)}</option>`).join('');
      fillSubs(selectedSub);
    }
    function fillSubs(selectedSub) {
      const cat = catFor(form.type, el('category').value);
      const subs = cat.subs.slice();
      if (selectedSub && !subs.includes(selectedSub)) subs.push(selectedSub);
      el('subcategory').innerHTML = subs.map((s) => `<option ${s === selectedSub ? 'selected' : ''}>${esc(s)}</option>`).join('');
    }
    function setType(type, keepCategory) {
      form.type = type;
      $$('.seg button', f).forEach((b) => b.setAttribute('aria-checked', String(b.dataset.type === type)));
      const isTransfer = type === 'transfer';
      $$('[data-show="transfer"]', f).forEach((n) => { n.hidden = !isTransfer; });
      $$('[data-show="single"]', f).forEach((n) => { n.hidden = isTransfer; });
      if (keepCategory && source) fillCategories(source.category, source.subcategory);
      else fillCategories();
    }
    function renderReceipts() {
      $('#receipt-grid', f).innerHTML = form.receipts.map((r) => thumbHtml(r, true)).join('');
    }
    function syncStatusLabel() { $('#status-label', f).textContent = el('cleared').checked ? 'Cleared' : 'Pending reconciliation'; }

    setType(form.type, true);
    renderReceipts();
    syncStatusLabel();

    f.addEventListener('click', (e) => {
      const seg = e.target.closest('.seg button');
      if (seg) { setType(seg.dataset.type, false); return; }
      if (e.target.closest('[data-swap]')) { const a = el('fromId').value; el('fromId').value = el('toId').value; el('toId').value = a; return; }
      const rm = e.target.closest('[data-remove-receipt]');
      if (rm) {
        const idx = form.receipts.findIndex((r) => r.id === rm.dataset.removeReceipt);
        if (idx > -1) {
          const [r] = form.receipts.splice(idx, 1);
          if (!r.isNew) form.removed.push(r.id);
          URL.revokeObjectURL(r.url);
          renderReceipts();
        }
        return;
      }
      const v = e.target.closest('[data-view-receipt]');
      if (v) openViewer(form.receipts.find((r) => r.id === v.dataset.viewReceipt));
    });
    f.addEventListener('keydown', (e) => {
      const seg = e.target.closest('.seg button');
      if (seg && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
        const order = ['expense', 'income', 'transfer'];
        const next = order[(order.indexOf(form.type) + (e.key === 'ArrowRight' ? 1 : 2)) % 3];
        setType(next, false);
        $(`.seg button[data-type="${next}"]`, f).focus();
      }
    });
    el('category').addEventListener('change', () => fillSubs());
    el('cleared').addEventListener('change', syncStatusLabel);
    $$('[data-attach]', f).forEach((input) => input.addEventListener('change', async () => {
      const files = Array.from(input.files || []);
      input.value = '';
      for (const file of files) {
        try {
          const p = await prepareReceipt(file);
          form.receipts.push({ id: uid('RCPT'), ...p, size: p.blob.size, isNew: true, url: URL.createObjectURL(p.blob) });
        } catch (err) { toast(err.message); }
      }
      renderReceipts();
    }));

    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = $('#form-error', f);
      err.textContent = '';
      const amount = parseAmount(el('amount').value);
      if (!(amount > 0)) { err.textContent = 'Enter an amount greater than zero (up to 2 decimals).'; el('amount').focus(); return; }
      const datetime = el('datetime').value;
      if (!datetime) { err.textContent = 'Choose a date and time.'; el('datetime').focus(); return; }
      let accountId, toAccountId = null;
      if (form.type === 'transfer') {
        accountId = el('fromId').value;
        toAccountId = el('toId').value;
        if (state.accounts.length < 2) { err.textContent = 'Add a second account to make a transfer.'; return; }
        if (!accountId || !toAccountId || accountId === toAccountId) { err.textContent = 'From and To accounts must be different.'; el('toId').focus(); return; }
      } else {
        accountId = el('accountId').value;
      }

      const now = Date.now();
      const txn = {
        id: existing ? existing.id : newTxnId(datetime),
        datetime,
        type: form.type,
        accountId,
        toAccountId,
        category: el('category').value,
        subcategory: el('subcategory').value,
        amount,
        payee: el('payee').value.trim(),
        status: el('cleared').checked ? 'cleared' : 'pending',
        receiptIds: form.receipts.map((r) => r.id),
        createdAt: existing ? existing.createdAt : now,
        updatedAt: now
      };
      const ops = [{ store: 'transactions', put: txn }];
      for (const r of form.receipts.filter((x) => x.isNew)) {
        ops.push({ store: 'receipts', put: { id: r.id, txnId: txn.id, blob: r.blob, name: r.name, mime: r.mime, size: r.size, createdAt: now } });
      }
      for (const rid of form.removed) ops.push({ store: 'receipts', delete: rid });
      try {
        await DB.write(ops);
      } catch (ex) {
        err.textContent = 'Could not save: ' + (ex && ex.message ? ex.message : 'storage error') + '. Your device may be out of space.';
        return;
      }
      if (existing) Object.assign(existing, txn); else state.txns.push(txn);
      sortTxns();
      closeSheet();
      render();
      toast(existing ? 'Transaction updated' : `${TYPE_LABEL[txn.type]} saved`);
    });
  }

  async function deleteTxn(id) {
    const t = state.txns.find((x) => x.id === id);
    if (!t || !confirm('Delete this transaction and its receipts? This cannot be undone.')) return;
    await DB.write([{ store: 'transactions', delete: id }, ...(t.receiptIds || []).map((r) => ({ store: 'receipts', delete: r }))]);
    state.txns = state.txns.filter((x) => x.id !== id);
    closeSheet();
    render();
    toast('Transaction deleted');
  }

  async function toggleStatus(id) {
    const t = state.txns.find((x) => x.id === id);
    if (!t) return;
    const updated = { ...t, status: t.status === 'pending' ? 'cleared' : 'pending', updatedAt: Date.now() };
    await DB.write([{ store: 'transactions', put: updated }]);
    Object.assign(t, updated);
    render();
    openTxnDetail(id);
    toast(t.status === 'cleared' ? 'Marked as cleared' : 'Marked as pending');
  }

  /* ---------- Account form ---------- */
  function openAccountForm(id) {
    const a = id ? accById(id) : null;
    const used = a ? state.txns.some((t) => t.accountId === a.id || t.toAccountId === a.id) : false;
    const color = a ? a.color : ACCOUNT_COLORS[state.accounts.length % ACCOUNT_COLORS.length];
    const sheet = openSheet(`
      <form class="form" id="acct-form" novalidate>
        <div class="sheet-head"><h2>${a ? 'Edit account' : 'New account'}</h2><button type="button" class="icon-btn" data-close aria-label="Close">×</button></div>
        <label class="field"><span>Account name</span><input type="text" name="name" maxlength="40" required placeholder="e.g. Maybank Savings" value="${esc(a ? a.name : '')}" autofocus></label>
        <label class="field"><span>Type</span><select name="type">${ACCOUNT_TYPES.map((t) => `<option value="${t.id}" ${a && a.type === t.id ? 'selected' : ''}>${t.icon} ${t.label}</option>`).join('')}</select></label>
        <label class="field"><span>Opening balance</span><input type="text" inputmode="decimal" name="opening" placeholder="0.00" value="${a ? centsToInput(a.openingBalance || 0) : ''}">
          <span class="hint">Balance before your first tracked transaction. Use a minus sign for credit card debt.</span></label>
        <fieldset class="field" style="border:0;padding:0;margin:0"><legend style="margin-bottom:6px">Colour</legend>
          <div class="color-row">${ACCOUNT_COLORS.map((c, i) => `<label><input type="radio" name="color" value="${c}" ${c === color ? 'checked' : ''} aria-label="Colour ${i + 1}"><span style="background:${c}"></span></label>`).join('')}</div>
        </fieldset>
        <p class="form-error" id="acct-error" role="alert"></p>
        <div class="sheet-foot">
          ${a ? `<button type="button" class="btn danger" data-delete-account="${esc(a.id)}" ${used ? 'title="Account has transactions"' : ''}>Delete</button>` : '<button type="button" class="btn" data-close>Cancel</button>'}
          <button type="submit" class="btn primary">Save</button>
        </div>
      </form>`);
    const f = $('#acct-form', sheet);
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = $('#acct-error', f);
      const name = f.elements.name.value.trim();
      if (!name) { err.textContent = 'Give the account a name.'; f.elements.name.focus(); return; }
      if (state.accounts.some((x) => x.name.toLowerCase() === name.toLowerCase() && (!a || x.id !== a.id))) { err.textContent = 'You already have an account with that name.'; return; }
      const openingRaw = f.elements.opening.value.trim();
      const opening = openingRaw ? parseSignedAmount(openingRaw) : 0;
      if (isNaN(opening)) { err.textContent = 'Opening balance must be a number, e.g. 1500.00'; f.elements.opening.focus(); return; }
      const acc = {
        id: a ? a.id : uid('ACC'),
        name,
        type: f.elements.type.value,
        openingBalance: opening,
        color: (f.querySelector('input[name="color"]:checked') || {}).value || ACCOUNT_COLORS[0],
        createdAt: a ? a.createdAt : Date.now()
      };
      await DB.write([{ store: 'accounts', put: acc }]);
      if (a) Object.assign(a, acc); else state.accounts.push(acc);
      closeSheet();
      render();
      toast(a ? 'Account updated' : 'Account added');
    });
  }

  async function deleteAccount(id) {
    const a = accById(id);
    if (!a) return;
    const n = state.txns.filter((t) => t.accountId === id || t.toAccountId === id).length;
    if (n) { alert(`"${a.name}" is used by ${n} transaction${n === 1 ? '' : 's'}. Delete or move those first.`); return; }
    if (!confirm(`Delete the account "${a.name}"?`)) return;
    await DB.write([{ store: 'accounts', delete: id }]);
    state.accounts = state.accounts.filter((x) => x.id !== id);
    if (state.filters.account === id) state.filters.account = 'all';
    closeSheet();
    render();
    toast('Account deleted');
  }

  /* ---------- Import / export ---------- */
  function download(name, content, mime) {
    const blob = content instanceof Blob ? content : new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  function exportCsv(txns) {
    const cell = (v) => {
      let s = String(v == null ? '' : v);
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; // guard against spreadsheet formula injection
      return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const header = ['Transaction ID', 'Date', 'Time', 'Type', 'Source Account', 'Destination Account', 'Category', 'Sub-category', `Amount (${state.settings.currency})`, 'Payee / Description', 'Status', 'Receipts'];
    const rows = txns.map((t) => [
      t.id, t.datetime.slice(0, 10), t.datetime.slice(11, 16), TYPE_LABEL[t.type], accName(t.accountId),
      t.type === 'transfer' ? accName(t.toAccountId) : 'N/A', catFor(t.type, t.category).label, t.subcategory,
      (t.type === 'expense' ? -t.amount : t.amount) / 100, t.payee, t.status === 'pending' ? 'Pending' : 'Cleared', (t.receiptIds || []).length
    ].map(cell).join(','));
    download(`transactions-${localDate()}.csv`, '﻿' + [header.map(cell).join(','), ...rows].join('\r\n'), 'text/csv;charset=utf-8');
    toast(`Exported ${txns.length} transaction${txns.length === 1 ? '' : 's'}`);
  }

  const blobToDataUrl = (blob) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(r.error); r.readAsDataURL(blob); });

  async function backup() {
    toast('Preparing backup…');
    const receipts = await DB.getAll('receipts');
    const data = {
      app: 'expense-transfer-tracker', version: 1, exportedAt: new Date().toISOString(),
      settings: state.settings, accounts: state.accounts, transactions: state.txns,
      receipts: await Promise.all(receipts.map(async (r) => ({ ...r, blob: undefined, data: await blobToDataUrl(r.blob) })))
    };
    download(`tracker-backup-${localDate()}.json`, JSON.stringify(data), 'application/json');
  }

  async function restore(file) {
    let data;
    try { data = JSON.parse(await file.text()); } catch (e) { alert('That file is not a valid backup.'); return; }
    if (!data || data.app !== 'expense-transfer-tracker' || !Array.isArray(data.accounts) || !Array.isArray(data.transactions)) { alert('That file is not an Expense & Transfer Tracker backup.'); return; }
    if (!confirm(`Replace all current data with this backup (${data.accounts.length} accounts, ${data.transactions.length} transactions)?`)) return;
    const receipts = [];
    for (const r of data.receipts || []) {
      if (!r || !r.id || typeof r.data !== 'string' || !r.data.startsWith('data:')) continue;
      const blob = await (await fetch(r.data)).blob();
      receipts.push({ id: r.id, txnId: r.txnId, name: r.name, mime: r.mime || blob.type, size: blob.size, createdAt: r.createdAt, blob });
    }
    const ops = ['accounts', 'transactions', 'receipts'].map((s) => ({ store: s, clear: true }));
    data.accounts.forEach((a) => ops.push({ store: 'accounts', put: a }));
    data.transactions.forEach((t) => ops.push({ store: 'transactions', put: t }));
    receipts.forEach((r) => ops.push({ store: 'receipts', put: r }));
    if (data.settings) ops.push({ store: 'meta', put: { key: 'settings', value: { ...state.settings, ...data.settings } } });
    await DB.write(ops);
    await loadAll();
    render();
    toast('Backup restored');
  }

  async function loadSample() {
    if (!confirm('Add sample accounts and transactions for the last two months?')) return;
    const ops = [];
    const ensure = (name, type, opening) => {
      let a = state.accounts.find((x) => x.name === name);
      if (!a) {
        a = { id: uid('ACC'), name, type, openingBalance: opening, color: ACCOUNT_COLORS[state.accounts.length % ACCOUNT_COLORS.length], createdAt: Date.now() };
        state.accounts.push(a);
      } else if (!a.openingBalance) {
        a.openingBalance = opening;
      }
      ops.push({ store: 'accounts', put: a });
      return a.id;
    };
    const mb = ensure('Maybank', 'bank', 520000);
    const cimb = ensure('CIMB', 'bank', 180000);
    const tng = ensure("Touch 'n Go", 'ewallet', 5000);
    const cash = ensure('Cash', 'cash', 20000);
    const d = (daysAgo, h, m) => { const x = new Date(); x.setDate(x.getDate() - daysAgo); x.setHours(h, m, 0, 0); return localDateTime(x); };
    const S = [
      [0, 8, 15, 'expense', tng, null, 'food', 'Coffee & Snacks', 1250, 'ZUS Coffee', 'cleared'],
      [0, 12, 40, 'expense', mb, null, 'food', 'Restaurants', 2890, 'Nasi Kandar Pelita', 'pending'],
      [1, 9, 0, 'transfer', mb, tng, 'topup', 'Reload', 10000, 'TNG reload', 'cleared'],
      [1, 18, 30, 'expense', tng, null, 'transport', 'Toll', 830, 'PLUS toll', 'cleared'],
      [2, 10, 5, 'expense', mb, null, 'utilities', 'Electricity', 18740, 'TNB bill', 'cleared'],
      [3, 19, 10, 'expense', cimb, null, 'food', 'Groceries', 15625, 'Jaya Grocer', 'cleared'],
      [4, 9, 30, 'transfer', mb, cimb, 'savings', 'Emergency Fund', 50000, 'Monthly savings', 'cleared'],
      [5, 13, 0, 'expense', cash, null, 'food', 'Restaurants', 1500, 'Mamak lunch', 'cleared'],
      [6, 8, 45, 'expense', mb, null, 'transport', 'Fuel', 6000, 'Petronas', 'pending'],
      [7, 17, 0, 'transfer', mb, cash, 'cash', 'ATM', 20000, 'ATM withdrawal', 'cleared'],
      [9, 21, 0, 'expense', cimb, null, 'leisure', 'Subscriptions', 1790, 'Netflix', 'cleared'],
      [12, 10, 0, 'income', mb, null, 'refunds', 'Cashback', 1550, 'Card cashback', 'cleared'],
      [14, 9, 0, 'income', mb, null, 'business', 'Freelance', 80000, 'Website project', 'pending'],
      [20, 11, 20, 'expense', mb, null, 'utilities', 'Internet', 12900, 'Unifi', 'cleared'],
      [25, 9, 0, 'income', mb, null, 'salary', 'Monthly Salary', 450000, 'Salary', 'cleared'],
      [31, 10, 0, 'expense', mb, null, 'housing', 'Rent', 120000, 'Rent', 'cleared'],
      [38, 15, 0, 'expense', cimb, null, 'shopping', 'Clothing', 8990, 'Uniqlo', 'cleared'],
      [44, 9, 0, 'transfer', mb, cimb, 'savings', 'Emergency Fund', 50000, 'Monthly savings', 'cleared'],
      [55, 9, 0, 'income', mb, null, 'salary', 'Monthly Salary', 450000, 'Salary', 'cleared']
    ];
    const now = Date.now();
    S.forEach(([ago, h, m, type, acc, to, cat, sub, amt, payee, status], i) => {
      const datetime = d(ago, h, m);
      const t = { id: newTxnId(datetime), datetime, type, accountId: acc, toAccountId: to, category: cat, subcategory: sub, amount: amt, payee, status, receiptIds: [], createdAt: now + i, updatedAt: now + i };
      state.txns.push(t);
      ops.push({ store: 'transactions', put: t });
    });
    await DB.write(ops);
    sortTxns();
    go('dashboard');
    toast('Sample data added');
  }

  async function eraseAll() {
    if (!confirm('Erase ALL accounts, transactions and receipts on this device?')) return;
    if (!confirm('Are you sure? Consider downloading a backup first.')) return;
    await DB.write(['accounts', 'transactions', 'receipts', 'meta'].map((s) => ({ store: s, clear: true })));
    await DB.write([{ store: 'meta', put: { key: 'seeded', value: true } }, { store: 'meta', put: { key: 'settings', value: state.settings } }]);
    state.accounts = []; state.txns = [];
    go('dashboard');
    toast('All data erased');
  }

  async function showStorageInfo() {
    const el = $('#storage-info');
    if (!el || !navigator.storage || !navigator.storage.estimate) return;
    try {
      const { usage, quota } = await navigator.storage.estimate();
      const persisted = navigator.storage.persisted ? await navigator.storage.persisted() : false;
      const mb = (b) => (b / 1048576).toFixed(1) + ' MB';
      el.textContent = `Data is stored privately on this device only · using ${mb(usage || 0)} of ${mb(quota || 0)}${persisted ? ' · protected from automatic cleanup' : ''}.`;
    } catch (e) { /* ignore */ }
  }

  /* ---------- Settings ---------- */
  function applyTheme() {
    const t = state.settings.theme;
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
    else delete document.documentElement.dataset.theme;
    const dark = t === 'dark' || (t === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
    $('meta[name="theme-color"]').setAttribute('content', dark ? '#0b0f17' : '#0f172a');
  }
  async function saveSettings() {
    await DB.write([{ store: 'meta', put: { key: 'settings', value: state.settings } }]);
  }

  /* ---------- Events ---------- */
  function bindEvents() {
    window.addEventListener('hashchange', () => { closeSheet(true); render(); window.scrollTo(0, 0); });

    document.addEventListener('click', (e) => {
      const t = e.target;
      const q = (sel) => t.closest(sel);
      let n;
      if (q('[data-close-viewer]')) { closeViewer(); return; }
      if (q('[data-close]')) { closeSheet(); return; }
      if ((n = q('[data-action]'))) {
        const a = n.dataset.action;
        if (a === 'new-txn') openTxnForm();
        else if (a === 'new-transfer') openTxnForm({ type: 'transfer' });
        else if (a === 'new-account') openAccountForm();
        else if (a === 'export-all') exportCsv(state.txns);
        else if (a === 'export-filtered') exportCsv(filteredTxns());
        else if (a === 'backup') backup();
        else if (a === 'sample') loadSample();
        else if (a === 'erase') eraseAll();
        return;
      }
      if ((n = q('[data-open-txn]'))) { openTxnDetail(n.dataset.openTxn); return; }
      if ((n = q('[data-edit-txn]'))) { const id = n.dataset.editTxn; closeSheet(true); openTxnForm({ id }); return; }
      if ((n = q('[data-duplicate-txn]'))) { const id = n.dataset.duplicateTxn; closeSheet(true); openTxnForm({ duplicateOf: id }); return; }
      if ((n = q('[data-delete-txn]'))) { deleteTxn(n.dataset.deleteTxn); return; }
      if ((n = q('[data-toggle-status]'))) { toggleStatus(n.dataset.toggleStatus); return; }
      if ((n = q('[data-edit-account]'))) { openAccountForm(n.dataset.editAccount); return; }
      if ((n = q('[data-delete-account]'))) { deleteAccount(n.dataset.deleteAccount); return; }
      if ((n = q('[data-account-filter]'))) {
        Object.assign(state.filters, { from: '', to: '', type: 'all', status: 'all', q: '', account: n.dataset.accountFilter });
        go('transactions'); return;
      }
      if (q('[data-show-pending]')) {
        Object.assign(state.filters, { from: '', to: '', type: 'all', account: 'all', q: '', status: 'pending' });
        go('transactions'); return;
      }
      if ((n = q('[data-dash-month]'))) { state.dashMonth = shiftMonth(state.dashMonth, Number(n.dataset.dashMonth)); render(); return; }
      if ((n = q('[data-filter-type]'))) { state.filters.type = n.dataset.filterType; refreshFilters(); return; }
      if ((n = q('[data-range]'))) { applyRange(n.dataset.range); refreshFilters(true); return; }
      if (q('[data-clear-filters]')) { Object.assign(state.filters, { from: '', to: '', type: 'all', account: 'all', status: 'all', q: '' }); render(); return; }
    });

    document.addEventListener('input', (e) => {
      if (e.target.id === 'f-q') { state.filters.q = e.target.value; renderTxnResults(); }
    });
    document.addEventListener('change', async (e) => {
      const id = e.target.id;
      if (id === 'f-from' || id === 'f-to') {
        state.filters[id === 'f-from' ? 'from' : 'to'] = e.target.value;
        if (state.filters.from && state.filters.to && state.filters.from > state.filters.to) {
          // Keep the range valid by moving the other end.
          if (id === 'f-from') state.filters.to = state.filters.from; else state.filters.from = state.filters.to;
          refreshFilters(true);
        } else refreshFilters();
      } else if (id === 'f-account') { state.filters.account = e.target.value; refreshFilters(); }
      else if (id === 'f-status') { state.filters.status = e.target.value; refreshFilters(); }
      else if (id === 's-currency') { state.settings.currency = e.target.value; numberFormat = null; await saveSettings(); toast('Currency updated'); }
      else if (id === 's-theme') { state.settings.theme = e.target.value; applyTheme(); await saveSettings(); }
      else if (id === 's-restore' && e.target.files[0]) { const file = e.target.files[0]; e.target.value = ''; restore(file); }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (!$('#viewer-root').hidden) closeViewer();
      else if (!$('#sheet-root').hidden) closeSheet();
    });

    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);
  }

  /** Update filter chips/inputs in place, then the list, without stealing focus from the search box. */
  function refreshFilters(syncDates) {
    $$('[data-filter-type]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.filterType === state.filters.type)));
    const r = activeRange();
    $$('[data-range]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.range === r)));
    if (syncDates) { const a = $('#f-from'), b = $('#f-to'); if (a) a.value = state.filters.from; if (b) b.value = state.filters.to; }
    renderTxnResults();
  }

  /* ---------- Boot ---------- */
  async function loadAll() {
    const [accounts, txns, meta] = await Promise.all([DB.getAll('accounts'), DB.getAll('transactions'), DB.getAll('meta')]);
    state.accounts = accounts.sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
    state.txns = txns;
    sortTxns();
    const settings = meta.find((m) => m.key === 'settings');
    if (settings) state.settings = { ...state.settings, ...settings.value };
    numberFormat = null;
    applyTheme();
    if (!meta.find((m) => m.key === 'seeded') && !accounts.length) {
      // First run: create the common Malaysian accounts so users can start logging immediately.
      const now = Date.now();
      state.accounts = DEFAULT_ACCOUNTS.map((a, i) => ({ id: uid('ACC'), name: a.name, type: a.type, openingBalance: 0, color: ACCOUNT_COLORS[i], createdAt: now + i }));
      await DB.write([...state.accounts.map((a) => ({ store: 'accounts', put: a })), { store: 'meta', put: { key: 'seeded', value: true } }]);
    }
  }

  async function init() {
    bindEvents();
    try {
      await loadAll();
    } catch (e) {
      $('#view').innerHTML = `<div class="banner">Storage is unavailable (${esc(e.message)}). Private browsing may block saving data — open the app in a normal window.</div>`;
      return;
    }
    render();
    if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
    if ('serviceWorker' in navigator && location.protocol !== 'file:') {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    }
  }

  init();
})();
