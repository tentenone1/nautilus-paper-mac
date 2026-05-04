# AGENTS.md — Nautilus Trading (1700)

## Operating Rules

**Production Code Standards**: ALL code MUST pass the `production-code-standards` skill checklist (10 rules). Load with `skill_view(name='production-code-standards')` before any coding work.

**Aider by Default**: All changes >5 lines or >1 file MUST use Aider. The `patch` tool is RESTRICTED to <3 line single-file emergency fixes only. No direct file editing.

**Never Delete Bitable Records**: Set Status=Cancelled only. NO record deletion ever. See `~/wiki/trading/bitable-deletion-incident-2026-05-04.md`.

## Architecture

- **Paper trading**: `run_paper.py` → sandbox execution with Polymarket live data
- **Micro-live**: `run_micro_live.py` → real Polymarket CLOB (guarded by `.guard/micro-live.ok`)
- **Strategy**: `strategies/whale_follower.py` — main signal processing and position management
- **Config**: `config/whale_tiers.json` — whale tiering, Kelly sizing, position caps
- **Dashboard**: `dashboard.py` — Streamlit UI on :8502
- **Resolution tracking**: `components/resolution_poller.py` — polls CLOB for market settlements
- **Reconciliation**: `components/position_reconciler.py` — paper vs live position alignment
- **DB**: `research/trades.db` — all trade records (`.gitignore`d, backed up separately)

## Key Constants (whale_follower.py lines 54-91)
- `MAX_SANE_RETURN = 2.0` — ±200% P&L cap to filter sandbox artifacts
- `EXIT_TIMER_INTERVAL_SECS = 30.0` — position exit checks
- `MEMORY_PRESSURE_MB = 2500` — graceful shutdown threshold
- `max_open_positions = 50` — config field, not constant

## Monitoring
- `nautilus-paper.service` — systemd user service
- `dashboard.service` — user service on :8502
- Signal-trade gap detection: `.signal_trade_gap_state.json`
- Daily loss kill switch: env `DAILY_LOSS_LIMIT` (default 500)

## Services
- `nautilus-paper.service` — paper trading (always running)
- `nautilus-live.service` — micro-live (guarded, needs `.guard/micro-live.ok`)
- `dashboard.service` — Streamlit dashboard

## Git
- Remote: `git@github.com:tentenone1/nautilus-trading.git`
- DB files and logs are .gitignore'd
- Commit any production code changes immediately
