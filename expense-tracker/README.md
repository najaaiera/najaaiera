# Expense & Transfer Tracker

A clean, mobile-first personal finance app for logging daily **expenses**, **income** and **transfers between your own accounts** (e.g. Maybank → CIMB, Maybank → Touch 'n Go), with receipt photos attached.

It is a Progressive Web App written in plain HTML, CSS and JavaScript. There is no build step and no server-side code. All data stays on the device in IndexedDB, and it works offline once opened.

## Run it

Serve the folder with any static web server, then open it in a browser:

```bash
cd expense-tracker
python3 -m http.server 8080
# open http://localhost:8080
```

On a phone, open the hosted URL and use **Add to Home Screen** to install it like a native app. You can host it on GitHub Pages, Netlify or any static host. Camera capture, offline mode and install need HTTPS (or `localhost`).

## Transaction fields

| Field | Notes |
|---|---|
| Transaction ID | Auto-generated, e.g. `TXN-20261008-4F7A2C` |
| Date & time | Defaults to now, editable |
| Type | Expense · Income · Transfer |
| Source account | Maybank, CIMB, Touch 'n Go and Cash are created on first run; add or edit more |
| Destination account | Shown and required **only** for transfers; must differ from the source |
| Category › Sub-category | Separate lists for expense, income and transfer (e.g. Savings › Emergency Fund) |
| Amount | Stored as integer cents and formatted in the chosen currency (default MYR, shown as RM) |
| Payee / merchant / description | Free text, with suggestions from earlier entries |
| Receipt | Camera capture or gallery/file upload (images or PDF). Large photos are downscaled. Multiple receipts are allowed |
| Status | Cleared / Pending reconciliation toggle |

## Features

- **Dashboard**: cards for Total Balance (all accounts), Monthly Expenses and Monthly Income, with a month switcher. Also shows account balances and a recent-transactions list colour-coded red (expense), green (income) and blue ⇄ (transfer, shown as `Bank A → Bank B`).
- **Transfer logic**: a transfer lowers the source account balance and raises the destination by the same amount. It is **never** counted as income or expense, so total balance and income/expense totals stay unchanged.
- **Add / edit form**: the account fields change with the type. Transfer shows From and To with a swap button; Expense and Income show a single Account. You can also duplicate or delete a transaction, or mark it cleared or pending.
- **Receipts**: thumbnails appear in the transaction detail. Tap one to view it full-screen or download it.
- **Filtering & search**: filter by date range (custom From/To, or This month / Last month / Last 30 days / All time), type, account (source or destination) and status. Free-text search covers payee, category, account, amount and ID. Shows running totals for the filtered results.
- **Accounts**: add or edit accounts with an opening balance, type and colour. Accounts used by any transaction can't be deleted.
- **Data**: export to CSV (Excel/Sheets), full JSON backup including receipt images, restore, sample data and erase-all.
- Light and dark themes, responsive layout (bottom tab bar on mobile, sidebar on desktop), keyboard and screen-reader friendly.

## Files

```
index.html            App shell
css/styles.css        Design tokens, light/dark themes, responsive layout
js/data.js            Categories, sub-categories, account types, currencies
js/db.js              IndexedDB wrapper (accounts, transactions, receipts, settings)
js/app.js             Views, forms, balance logic, filters, import/export
sw.js                 Offline cache
manifest.webmanifest  Install metadata and icons
```

> Data is stored only in this browser on this device. Use **Settings → Backup** regularly, or before clearing browser data or switching phones.
