# Polymarket Whale Follower

NautilusTrader-based strategy that follows whale wallet trades on Polymarket.

## Why nautilus_trader?

We built custom trading systems from scratch (trading-v2, polymarket-executor) and spent weeks debugging infrastructure: path mismatches, state file schemas, duplicate processes, API timeouts. The alpha (whale signals, Kelly sizing) was real but drowned in plumbing issues.

nautilus_trader gives us:
- **Production-grade execution engine** — Rust core, 22.3k stars, 18k commits
- **Built-in Polymarket adapter** — data + execution, no custom API code
- **Same code for backtest and live** — deterministic event-driven architecture
- **Position tracking, PnL, risk management** — handled by the framework
- **Multi-venue support** — Polymarket, Bybit, Binance, dYdX, etc.

We focus on alpha. The framework handles plumbing.

## Setup

```bash
cd ~/workspace/nautilus-trading
cp .env.example .env
# Fill in .env with your Polymarket API credentials
```

## Usage

### Live Trading
```bash
source venv/bin/activate
python run_live.py
```

### Backtesting
```bash
python run_backtest.py --days 30 --bankroll 10000 --kelly 0.25
```

## Strategy: Whale Follower

**Logic:**
1. Subscribe to Polymarket market data (quotes + trades)
2. When a tracked whale makes a large trade → enter position
3. Position size = Kelly criterion (fractional, conservative)
4. Exit on stop loss (configurable %) or market resolution

**Config:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `BANKROLL` | 10000 | Paper bankroll (USD) |
| `KELLY_FRACTION` | 0.25 | Fractional Kelly (conservative) |
| `WHALE_WIN_RATE` | 0.55 | Expected whale win rate |
| `STOP_LOSS_PCT` | 0.15 | Stop loss threshold |
| `MAX_POSITION_PCT` | 0.10 | Max position as % of bankroll |

## Architecture

```
run_live.py
  └── TradingNode (nautilus_trader)
        ├── PolymarketDataClient (WebSocket feed)
        ├── PolymarketExecClient (CLOB API)
        └── WhaleFollower (Strategy)
              ├── on_quote_tick() → check stop loss
              ├── on_trade_tick() → log trades
              ├── enter_position() → Kelly-sized limit order
              └── exit_position() → close position
```

## Polymarket Credentials

Get from: https://polymarket.com → Settings → API Keys

Required env vars:
- `POLYMARKET_PK` — Polygon wallet private key
- `POLYMARKET_API_KEY` — API key
- `POLYMARKET_API_SECRET` — API secret
- `POLYMARKET_PASSPHRASE` — API passphrase

## Market Selection

Default: GTA VI released before June 2026

To trade a different market, set:
```bash
POLYMARKET_CONDITION_ID=0x... POLYMARKET_TOKEN_ID=0x... python run_live.py
```

Find active markets:
```bash
python nautilus_trader/adapters/polymarket/scripts/active_markets.py
```
