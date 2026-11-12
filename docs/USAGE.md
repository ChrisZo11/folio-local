# Folio

A private portfolio platform for Indonesian stocks and US ETFs. FastAPI and React provide the local dashboard; the CLI and a read-only MCP server use the same SQLite database and reporting engine. No account, broker connection, trade execution, or hosted service.

## Start

Python 3.11+ and Node.js 20.19+ are required.

```powershell
.\start.ps1
```

Open **http://127.0.0.1:8000**. Stop the server with Ctrl+C. The first start is empty: add your transactions or preview a CSV import. Stockbit and GoTrade portfolios and common tickers are pre-registered, without invented holdings or balances.

To explore synthetic examples instead, stop the real server and run:

```powershell
.\start.ps1 -Demo
```

Demo data lives in `data/demo.db`. Your real data lives in `data/portfolio.db`. Demo quotes cannot be refreshed from Yahoo, and the dashboard labels everything as synthetic. Both databases and provider caches are excluded from version control. Example CSV/JSON files are synthetic too; do not import them as real broker records.

On a fresh checkout, `start.ps1` creates the environment, installs packages, builds React, and starts the server. Alternatively:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test]"
npm ci --prefix web
npm run build --prefix web
.venv\Scripts\python.exe -m portfolio serve
```

## What works

- Overview with portfolio value, profit, annualized security XIRR, estimated annual income, actual historical value chart, allocation donut/table, recent activity, FX status, and value hiding.
- Portfolio and IDR/USD display switchers. Holdings search, sorting, CSV export, sold positions, per-share prices, cost basis, realized/unrealized gains, weights, and quote timestamps.
- Common, Diversification, and Dividends analytics. Sector, country, currency, asset-class and holding allocations; top weights and HHI; XIRR and daily TWR for 1M, 3M, YTD, 1Y, 3Y and all time.
- Create, edit, and delete trades, confirmed dividends, cash deposits/withdrawals, expenses, opening balances, and split events. Enter trades by shares or gross amount; IDX lots explicitly convert to 100 shares.
- Gross/net dividends, withholding tax, trailing income, provider proposals, share-count discrepancy flags, and forward estimates. Confirming a proposal opens an editable entry form, never silently books income.
- CSV preview and explicit confirmation, per-row validation, duplicate detection, estimated source fees, import history, and undo. A buy cannot be deleted/undone if it would invalidate a later sale. Manually edited imported rows are retained on undo.
- FIFO default and moving-average cost. Recalculation does not change transactions. Custom instruments, manual price histories, additional portfolios, configurable ticker mappings and quote TTL, consistent SQLite backups.
- Yahoo quotes/history/dividends and USD/IDR, bounded parallel refresh, retry/backoff, persistent cache, on-demand quote refresh, and offline cached reporting. Custom assets use manual valuations. Provider code is isolated in `portfolio/market.py`.
- All 11 MCP tools, including compact summaries, safe errors, units/currency, timestamps and assumptions. An actual MCP stdio client handshake is included in the test suite.

## Import your records

Use **Imports → Get CSV template**, select a file (UTF-8, up to 2 MB), preview it, review errors, then confirm the valid rows. Invalid rows are not written. A re-import skips existing rows. Files can be newest-first; chronological validation protects the ledger.

Canonical columns:

```csv
date,portfolio,type,symbol,quantity,quantity_unit,price,fee,tax,currency,fx_rate,note
2025-01-15,Stockbit,buy,BBCA,1,lot,9000,1350,0,IDR,1,Replace this example
```

| Field | Meaning |
| --- | --- |
| `date` | Actual operation/payment date, `YYYY-MM-DD`; future bookings are rejected. |
| `portfolio` | Existing portfolio name, case insensitive. Create accounts in Settings. |
| `type` | `buy`, `sell`, `dividend`, `split`, `fee`, `deposit`, `withdrawal`, `opening`. |
| `symbol` | Registered ticker. Cash-only operations may leave it empty. Add instruments before importing unknown symbols. |
| `quantity` | Decimal shares or explicit IDX lots. For splits, new/old ratio (2 means a 2:1 split). For dividends, optional broker-reported shares for reconciliation. |
| `quantity_unit` | `share` or `lot`; required for buys, sells and opening balances unless an explicit import fallback is selected. Never inferred from broker name. |
| `price` | Trade/opening price **per share**, or **total gross cash amount** for dividends/deposits/withdrawals/expenses. Split price can be 0. |
| `fee`, `tax` | Nonnegative amounts in the operation currency. A missing fee may use a source default rate if explicitly selected; 0 is an actual zero fee. |
| `currency` | `IDR` or `USD`; must match the instrument. |
| `fx_rate` | Actual **IDR per trading-currency unit on the operation date**. IDR is always 1. USD requires the actual historical rate and is never guessed. |
| `note` | Optional text. Quote CSV fields containing commas. |

Optional canonical columns `affects_cash` and `fee_estimated` accept true/false. Security operations update cash only when explicitly enabled; deposits, withdrawals and cash expenses always update the tracked balance. Keep the option consistent with how you record broker cash.

For holdings-only imports, use `examples/holdings-only.csv`: `average_cost` is the native per-share average and rows become `opening` operations. Dates, currency, FX and units remain required. Value and gains work; full XIRR/TWR is explicitly unavailable. Alternatively choose **Opening balance** in the manual form.

Source adapters accept canonical columns and these exact aliases (headers are case insensitive; spaces become underscores):

| Source | Aliases |
| --- | --- |
| generic | ticker→symbol, average_cost→price (holdings-only opening) |
| stockbit | ticker/stock→symbol, transaction_date→date, action→type, shares→quantity, unit→quantity_unit |
| gotrade | ticker→symbol, transaction_date→date, action→type, shares→quantity, unit→quantity_unit, commission→fee |
| snowball | ticker→symbol, operation→type, account→portfolio, commission→fee, unit→quantity_unit |

`purchase`, `sale`, and `income` operation aliases map to buy, sell and dividend. Actual broker export formats vary and must be reviewed against these columns. No unverified proprietary layout is guessed. For **Stockbit PDF statements or screenshots**, use Claude to extract canonical CSV, then review it in Imports. The platform does not upload PDFs or extract screenshots automatically.

The duplicate identity uses date, portfolio, ticker, operation, normalized quantity and price plus an occurrence counter. Two otherwise identical rows within a file remain two trades, and re-importing that file adds nothing. Overlapping partial exports with indistinguishable identical trades need manual reconciliation; the file format has no broker transaction ID. A matching saved trade with different fees/tax/FX is flagged rather than overwritten.

## Calculations and interpretation

- Lot accounting uses `Decimal`, including fractional shares and historical trade FX. FIFO is the default; average cost uses a moving weighted-average pool per portfolio/instrument. Buys capitalize fees/tax. Partial sells release proportional lot cost. Splits change shares and per-share basis without changing total cost.
- Base cost is stored through each trade's historical FX. IDR P&L separates the asset gain from currency effect on current USD holdings. USD display of stored IDR cost/realized amounts uses current cached FX; USD XIRR instead converts cash flows using their historical currency/rates.
- Profit includes unrealized gains, realized gains, and confirmed net dividends, less separately recorded cash expenses. The percentage card divides that lifetime profit by **current open-position cost**, not cumulative contributions; use period XIRR/TWR to measure investment performance.
- The summary XIRR is the annualized return of security cash flows and excludes idle cash. Period performance includes tracked cash. Untracked buys are contributions; untracked sells/dividends are distributions. Daily TWR assumes external flows at end of day. The first end-of-day contribution starts TWR, so initial intraday movement is excluded and explicitly warned about. XIRR returns null if no unique root is found in the supported log-rate search range.
- Daily valuation uses unadjusted economic historical prices and the explicit share ledger. Yahoo split-adjusted closes/dividends are restored for pre-split shares; dividend yields/growth use the comparable adjusted payouts. Do not record the same split twice. Daily prices/FX carry forward at most seven calendar days; gaps prevent performance output instead of filling invented returns. Contributor P&L uses the selected period, historical valuations, and security cash flows; unassigned cash expenses are excluded.
- Cash movements from trades/dividends are opt-in. Missing deposits can create negative tracked cash and change performance. Review cash balances against the broker. A holdings-only balance means incomplete investment history, and its limitation applies to the aggregate too.
- Quotes and FX carry source/as-of and cache-fetch timestamps. IDX is labeled **delayed ~10 min** and US data provider-delayed; neither is claimed to be real-time. Freshness uses weekday sessions in Jakarta/New York. **Exchange holidays and IDX midday sessions are not modeled**, so freshness warnings can be conservative. FX is the provider's daily USD/IDR observation, not an executable conversion rate.
- Missing quotes or FX return null values and mark results incomplete. Overall value/profit is withheld when incomplete; `known_value` exposes the available subtotal. Allocation weights only cover priced securities and exclude cash. Stale cached values remain available with warnings.
- Confirmed dividends come from your entries, never automatically from Yahoo. Provider estimates use shares held **before** the ex-date. Nearby confirmations use a 90-day payment window; unusual or overlapping payouts need review. Broker/ledger share discrepancies preserve both counts. Forward gross income uses the latest comparable payout and inferred 1/2/4/12 annual frequency; it is always an estimate. Dashboard annual income uses trailing per-share payouts × current shares. TTM actual income is independent of the selected report period.
- Five-year dividend growth requires an actual payout total five calendar years ago and a full prior-year total; absent data shows “—”. A manual custom valuation carries forward for at most seven days in performance charts; record regular updates for longer performance history.

## Claude / MCP

Merge `claude-desktop.example.json` into your **existing** Claude Desktop MCP configuration; preserve any other servers. Replace the placeholder paths with the absolute Python and database paths on your machine. Restart Claude after applying it. No Claude settings have been changed automatically.

The server command is:

```powershell
.venv\Scripts\python.exe -m portfolio --db "C:\path\to\folio\data\portfolio.db" mcp
```

Launch it from this directory or set `PYTHONPATH` as in the example. MCP communicates over stdio; stdout is reserved for protocol traffic and diagnostics go to stderr. The web server does not need to be running for MCP. Both use the same database. To deliberately inspect the sample, change the configured database to `data/demo.db`.

Tools: `list_portfolios`, `get_holdings`, `get_transactions`, `get_quote`, `get_portfolio_value`, `get_performance`, `get_allocation`, `get_concentration`, `get_dividends`, `refresh_prices`, `data_status`. The refresh tool only updates market caches. No MCP tool changes trades or confirmed income. Portfolio parameters are integer IDs; omitting the parameter aggregates portfolios. Try “What is my portfolio worth, and are any prices stale?”

SDK reference: [official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/). Provider reference: [yfinance API](https://ranaroussi.github.io/yfinance/reference/index.html).

## CLI

```powershell
# Preview first; this does not write transactions
.venv\Scripts\python.exe -m portfolio import my-records.csv --source generic
.venv\Scripts\python.exe -m portfolio import my-records.csv --source generic --confirm

# Refresh current and historical public market data
.venv\Scripts\python.exe -m portfolio refresh

# JSON report; defaults to aggregate IDR
.venv\Scripts\python.exe -m portfolio report --currency IDR

# Manual canonical JSON; operations share the web import validation
.venv\Scripts\python.exe -m portfolio add my-transaction.json
.venv\Scripts\python.exe -m portfolio edit 1 corrected-transaction.json
.venv\Scripts\python.exe -m portfolio delete 1

# Consistent backup without overwriting an existing file
.venv\Scripts\python.exe -m portfolio backup data/backups/portfolio-backup.db
```

An optional `--db PATH` belongs **before** the command; `FOLIO_DB` also sets the default. Protect the SQLite file and backups with your computer's normal filesystem permissions. Backups are local, not encrypted or uploaded. Stop clients before restoring a backup to a new database path, then point both web and MCP at that file.

## Development and checks

```powershell
.venv\Scripts\python.exe -m pip install -e ".[test]"
.venv\Scripts\python.exe -m pytest -q
npm run build --prefix web
```

The test suite covers irregular-date hand-calculated XIRR, daily cash-flow-adjusted TWR, split/partial-sale lot accounting, FX attribution, fractional shares/lots, invalid/negative/nonfinite inputs, rollback on invalid edits/deletes, import preview/deduplication/conflicts/undo, opening balances, offline retention, manual valuations, origin/host protection, HTTP/MCP numerical agreement and a real stdio initialization/tool call.

For frontend development, run `python -m portfolio serve` using the environment Python, and `npm run dev --prefix web` in a second terminal. Vite runs on 127.0.0.1:5173 and proxies `/api` to port 8000. Production serves React and API from port 8000. Local API documentation is at http://127.0.0.1:8000/docs. No external fonts, analytics scripts, or portfolio telemetry are loaded.

## Boundaries and remaining verification

Real broker statements and independent portfolio reports must be reconciled separately before relying on imported totals. All supplied fixtures are labeled synthetic. Yahoo coverage and broker-derived dividend proposals need verification against your actual records. This is an informational portfolio ledger, not tax reporting or investment advice.

The app binds only to loopback, checks Host and browser mutation origins, and has no login. Do not expose it to your LAN or the public internet. Phone access, public hosting/HTTP MCP, tax reporting, broker sync, what-if/rebalancing, targets, alerts, automatic provider fallback, and the later Growth/Metrics/Report UI are outside this v1 build. The two-week reliability milestone requires actual ongoing use and cannot be certified during setup.
