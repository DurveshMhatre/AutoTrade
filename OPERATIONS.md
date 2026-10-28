# Running the repaired bot

This checkout defaults to paper trading. Profitability is not established. The live adapter
has offline lifecycle tests; authenticated testnet execution is still required before live use.

## Windows commands

The existing `automated_trading_env` was left untouched. A working Python 3.13 environment
is available in `.venv-bot`:

```powershell
.\.venv-bot\Scripts\python.exe main.py --check
.\.venv-bot\Scripts\python.exe -m pytest tests -q
.\.venv-bot\Scripts\python.exe main.py --paper --once
.\.venv-bot\Scripts\python.exe main.py --paper
```

`--check` is offline. `--paper` overrides any live setting in `.env`. `--once` without
`--paper` uses the configured mode and CAN place orders. Ctrl+C stops the process;
acknowledged exchange protective stops are deliberately left active.

For a fresh installation use Python 3.13, create a virtual environment, and install
`requirements-lock.txt` with pip. The existing pandas-ta dependency is incompatible with
the machine's Python 3.14. Local environment folders are ignored by Git.

## Preserved data and behavior changes

- The original `trading_bot.db`, `.env`, existing virtual environment, and reference
  projects were not replaced. Pre-edit snapshots of the principal modified files are
  in `.review-backups/` (ignored by Git).
- New ledgers are `trading_bot_paper.db`, `trading_bot_testnet.db`, and
  `trading_bot_live.db`. There is no automatic adoption of old/manual positions.
  Live balance discrepancies block entries; reconcile existing holdings explicitly.
- Paper cash and fills persist across restarts. Paper mode uses public mainnet data,
  estimated 0.1% fees and 0.1% adverse execution slippage. It is a simulation, not a
  queue-position or market-impact model.
- Entry execution now uses one bounded IOC limit order instead of unattended layered
  GTC orders. Partial IOC fills are accounted for; unfilled quantity is not a position.
- Orders have durable client IDs. Submission timeouts are queried by ID, never blindly
  resubmitted. Unknown outcomes stop new entries. Inspect `execution_orders`, exchange
  orders and fills before resolving an ambiguity; do not delete journal rows to unblock.
- Live fills without commission details wait for trade-history reconciliation. Third-token
  fees are conservatively estimated in USDT; base/quote fees use exchange values.
- Exits run independently on a five-second monitor. Long spot positions receive an
  exchange `STOP_LOSS` order outside paper mode. Definite protection rejection triggers
  an emergency close attempt. Ambiguous protection outcomes block competing orders.
  Exchange outages, gaps, and stop execution slippage remain possible.
- Quantity/price precision and exchange minimums are checked. A partial take-profit
  below minimum is consolidated into a full exit where the full remainder is tradable.
  Non-tradable dust is logged and blocks new exposure rather than being silently erased.
- Daily loss uses marked strategy equity against the first observed UTC-day equity,
  persists across restarts, and latches entries off for the day. It is an entry circuit
  breaker, not a guaranteed maximum loss or automatic portfolio liquidation.
- Stops and target state survive restarts. Both profit targets in one tick produce one
  exit transaction. The trailing ATR reference is derived from the stored initial risk.
- Decisions use closed candles, with one attempt per symbol/bar. A failed or vetoed
  candidate is not repeatedly retried within that bar.
- The old standalone executor helpers remain for compatibility; `main.py` uses
  `execution/journal.py`. Use the runtime for persistent paper/live operation.

## Groq review (optional)

Use a currently available model from your Groq console. Add to `.env`:

```dotenv
LLM_REVIEW_PROVIDER=groq
LLM_REVIEW_MODEL=your-supported-model-id
GROQ_API_KEY=your-key
```

Leave `LLM_REVIEW_PROVIDER=none` to run without an LLM. Existing Claude strategy-agent
mode remains controlled by `USE_AI_AGENTS` in `config.py`; Groq review does not replace
every Claude agent. It reviews each BUY candidate approved by the strategy, before the
final deterministic risk checks. It cannot increase quantity, modify stops, or authorize
an otherwise prohibited trade. JSON is validated; errors, missing keys, HTTP 429, or a
15-second timeout skip the entry. Exit monitoring does not depend on the LLM.

A free API key changes cost and rate limits, not predictive reliability. The model only
sees supplied data; it does not independently know current prices/news. Do not interpret
approval or confidence as a calibrated probability of profit. See:
https://console.groq.com/docs/rate-limits and https://console.groq.com/docs/models

## Dashboard and research

The dashboard chooses the ledger using `DRY_RUN` and `BINANCE_TESTNET`; `DB_PATH` can
override it. To inspect forced-paper runs while your environment selects live/testnet,
set `DB_PATH` to the absolute `trading_bot_paper.db` path for the dashboard process.
Database access is read-only. Positions use their own asset prices and report unavailable
quotes explicitly. Closed trade quantities display original size; database quantities
continue to represent remaining inventory. Equity charts use cash-event realized P&L,
not a complete historical mark-to-market equity series.

`backtest.py` is a **long-only trend benchmark**, not a proof of the complete runtime's
profitability. It includes both-side fees, adverse slippage, next-bar entry, daily resets,
cash-limited sizing and non-overlapping scored windows. The walk-forward helper does
not perform nested training/parameter selection. Hyperopt/evolver outputs must be
validated on untouched data; the runtime does not automatically promote them.

## Before using real money

Validate authenticated testnet IOC fills, protection placement/cancel races, base-token
commission handling, reconnect/restart recovery and rejected orders on the target account.
Then collect a sufficient paper/testnet track record and compare net results with simple
baselines across market regimes. Offline tests and successful public-data startup do not
prove exchange-specific live execution or profitability.
