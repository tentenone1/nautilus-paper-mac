# Whale Expansion Plan: 11+ Whales Per Category

## Current State

| Metric | Value | Target |
|---|---|---|
| Tracked whales | 11 | **88+** (11/category × 8) |
| V1 DB available | 32 high-alpha | Seed 21 more |
| Categories covered | Sports only | Sports, Politics, Crypto, Geopolitics, Economics, Entertainment, Science/Tech, Business |
| Discovery pipeline | Not running | Continuous bg process |

## Polymarket's API Limitation

Confirmed: Neither Gamma API nor CLOB API exposes **individual wallet addresses** for market positions. We can't query "who holds this market". The data-api `/positions` endpoint only works with a known wallet address.

This means we find whales by **scanning known wallets**, not by scanning markets. So the strategy is:

## Phase 1: Seed All 32 Vetted Whales (immediate)

**File: `pipeline/wallet_scanner.py` → `_seed_from_v1_db()`**

The seed method already imports 32 whales into `whale_discovery.db`. But the WhaleTracker only loads from `config.py`'s `KNOWN_WHALES` list (11 entries).

**Fix: Make WhaleTracker load dynamically from DB**

**File: `strategies/whale_tracker_new.py` → `__init__()`**
- Instead of loading `config.KNOWN_WHALES`, query `whale_discovery.db` for ALL whales with alpha_score >= 70
- Apply category tags from V1 DB tags or derive from traded markets

## Phase 2: Scan Known Whales' Positions for All Categories

**File: `strategies/whale_tracker_new.py` → `_scan_whale_positions()`**
- When scanning the 32 whales, their positions include market titles
- Classify each position's market category using keyword inference
- Store the category alongside the signal

**Category classifier (keyword-based, no external API needed):**
```
sports keywords: vs., NBA, NFL, NHL, MLB, fight, match, spread, over/under, score
politics keywords: president, election, congress, senate, governor, party
crypto keywords: bitcoin, ethereum, BTC, ETH, crypto, token
geopolitics keywords: war, ceasefire, sanction, treaty, diplomatic, nuclear
economics keywords: GDP, inflation, fed, interest rate, recession, unemployment
entertainment keywords: oscar, grammy, emmy, box office, movie, album
science/tech keywords: spacex, launch, nasa, patent, AI, rocket
business keywords: IPO, acquisition, merger, stock, share, market cap
```

## Phase 3: Re-enable Gamma API Discovery

**File: `pipeline/wallet_scanner.py` → `discover_new_whales()`**
The Gamma API works now (SSL fixed). Re-enable the top-markets scan:

1. Fetch top 50 markets by volume (already works: `_get_top_markets()`)
2. Extract condition_ids
3. For each market condition, check if we already have whale signals for it
4. If not, add it as a candidate market for subscription
5. The WhaleTracker will detect whales trading these new markets via `on_trade_tick` + `_process_trade_buffer()`

## Phase 4: Expand Market Subscription

**File: `run_paper.py` → `load_whale_markets()`**
- Increase limit from 5 to 20 (was reduced for OOM, now safe with thread pool fix)
- Ensure category diversity: select top N markets per category, not just top N overall

**File: `strategies/whale_follower.py` → `__init__()`**
- Dynamic market loading: every scan cycle, check if new markets have whale activity and add them to subscription

## Files Changed Summary

| File | Change | Effort |
|---|---|---|
| `pipeline/config.py` | Expand KNOWN_WHALES to include all 32 V1 entries | Small |
| `pipeline/wallet_scanner.py` | Re-enable `_get_top_markets()` + `_get_large_holders()` in `discover_new_whales()` | Medium |
| `strategies/whale_tracker_new.py` | Load whales from `whale_discovery.db` instead of config list | Medium |
| `strategies/whale_tracker_new.py` | Add category inference to signal processing | Small |
| `run_paper.py` | Increase `limit=20` in `load_whale_markets()`, add `category_filter` | Small |
| `strategies/whale_follower.py` | Add category field to trade logging | Small |
