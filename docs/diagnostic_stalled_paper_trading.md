# Paper Trading Stall Diagnostic & Micro-Live Fix Plan

**Date:** 2026-05-11
**Service PID:** 708956 (started 09:53, running 71+ min)
**Diagnosis:** COMPLETE

---

## 1. ROOT CAUSE ANALYSIS

### Why Paper Trading Stalled (No Signals Since May 10 ~07:33)

The paper trading process IS running but is starved of new signals due to a combination of factors:

1. **Whale Position Dedup Exhaustion** (PRIMARY):
   - `WhaleTracker.scan_known_whales()` deduplicates by `{wallet}:{condition_id}:{outcome}` in `seen_positions`
   - TTL is 4 hours (`seen_position_ttl=14400`)
   - Known whales hold the same positions for days — after initial scan, every position is "seen"
   - After TTL expires and re-scanned, the same positions are still held → dedup blocks them again
   - **Result:** Zero new signals from whale tracking

2. **Autoresearch Signal Queue Empty**:
   - `research/autoresearch_signal_queue.json` has 0 signals
   - The autoresearch LLM pipeline hasn't produced new recommendations

3. **Sybil Signal Queue Has Stale Signals**:
   - 4 NBA Finals signals from 05:27 AM but they have malformed data (`group=?`, `action=?`)
   - These likely fail validation in `_check_sybil_signals()` and are silently discarded

4. **WebSocket Trade Buffer Empty**:
   - No large trades ($5k+) detected on subscribed instruments
   - With no new whale positions, the trade buffer never fills

### Why 55 Positions Are Stuck (No Exits Since May 10 12:08)

**CRITICAL BUG in `check_all_positions()`** (`wf_position_checks.py`, lines 110-126):

Positions exist in `_open_positions` dict but NOT in the Nautilus cache (because PaperExecClient bypasses Nautilus's matching engine). The code path is:

```python
open_pos_list = cache.positions_open(instrument_id=inst_id)
if not open_pos_list or open_pos_list[0].quantity.as_double() == 0:
    # Position NOT in Nautilus cache
    pos_info = open_positions.get(inst_key, {})
    if stale_age > max_hold * 3600:
        del open_positions[inst_key]  # ← BUG: Only deletes from memory!
    continue  # ← Never calls exit_position(), never updates trades.db
```

**Impact:** Stale positions are silently deleted from memory without:
- Updating `trades.db` with `exit_price`, `realized_pnl`, `exit_reason`
- Logging the exit
- Freeing up exposure for new positions

**Position Age Distribution:**
- ALL 55 positions exceed max_hold (4h)
- 40 positions are >48 hours old (some from May 6)
- Total stuck exposure: ~$5,682

### Open Position Breakdown:
| Category   | Count | Total Size | Oldest    |
|------------|-------|------------|-----------|
| general    | 32    | $3,459     | May 6     |
| geopolitics| 8     | $1,294     | May 7     |
| politics   | 5     | $1,962     | May 7     |
| sports     | 5     | $603       | May 8     |
| economics  | 2     | $153       | May 8     |
| technology | 2     | $152       | May 6     |
| crypto     | 1     | $11        | May 8     |

---

## 2. FIX PLAN

### Phase A: Unstall the System (Immediate)

**Step 1: Fix the silent exit bug** (`wf_position_checks.py`)
```
File: strategies/wf_position_checks.py, lines 110-126
Problem: Stale orphan positions deleted from memory but not exited via DB
Fix: When a stale orphan exceeds max_hold, call exit_position() with reason="stale_orphan_max_hold"
     instead of just deleting from memory. This ensures trades.db gets updated.
```

**Step 2: Kill and restart paper trading process**
```bash
cd ~/workspace/nautilus-trading
kill 708956
rm -f .run_paper.pid
# After fix from Step 1 is applied:
venv/bin/python run_paper.py &
```

**Step 3: Reconcile the 55 stuck positions**

Since many of these are days-old positions that would have resolved:
```python
# Run a reconciliation script that:
# 1. For each open position in trades.db with exit_price IS NULL:
#    a. Check if market is resolved via CLOB API
#    b. If resolved → update trades.db with exit_price=1.0/0.0, realized_pnl, exit_reason="resolved"
#    c. If not resolved AND age > max_hold → update trades.db with current midpoint, exit_reason="reconciled_max_hold"
#    d. If not resolved AND age <= max_hold → leave open for live tracking
```

### Phase B: Signal Generation Fixes

**Step 4: Reduce dedup TTL or add position change detection**
```
File: strategies/whale_tracker_new.py, line 382
Current: pos_key = f"{wallet}:{condition_id}:{outcome}"
Problem: Same position held for days → always deduped
Fix: Include position size or timestamp in dedup key:
     pos_key = f"{wallet}:{condition_id}:{outcome}:{size_bucket}"
     Or: Track size changes — if position size changed, it's a "new" signal
```

**Step 5: Add fallback signal sources**
- The autoresearch LLM queue was the best signal source (responsible for most recent activity)
- Need to restart/monitor the autoresearch pipeline
- Consider adding a "market scan" fallback that scans for new whale entries, not just position holds

**Step 6: Fix sybil signal queue parsing**
- The 4 signals in `sybil_signal_queue.json` have missing `group` and `action` fields
- Fix the sybil signal generator to populate required fields
- Add validation in `_check_sybil_signals()` to reject malformed signals with a log

### Phase C: Micro-Live Safety Verification

**Step 7: Credential Audit** (`run_micro_live.py` + `.env`)
```
REQUIRED CREDENTIALS:
- POLYMARKET_PK: ✅ SET (0x9708...db86c)
- POLYMARKET_API_KEY: ❌ PLACEHOLDER ("***")
- POLYMARKET_API_SECRET: ❌ PLACEHOLDER ("***")
- POLYMARKET_PASSPHRASE: ❌ PLACEHOLDER ("your_passphrase")

ACTION: User must fill in real API credentials before micro-live can start.
The validate_credentials() function in run_micro_live.py will exit with error
if these are not set.
```

**Step 8: Safety Checklist for $100 Micro-Live Test**

| Check | Status | Notes |
|-------|--------|-------|
| Bankroll config | ✅ MICRO_BANKROLL=$2000 (reduce to $100) | Change .env or use env override |
| Max position % | ✅ 8% of bankroll = $160 max | Reasonable for $100 |
| Stop loss | ✅ 15% | OK |
| Take profit | ✅ 20% | OK |
| Max hold hours | ✅ 6.0h | OK |
| Min confidence | ✅ 0.55 | Good — not too aggressive |
| Auto trade | ✅ true | Will trade automatically |
| Position limits | ✅ max_open_positions=50 | OK |
| Daily loss limit | ✅ $10,000 | Too high for $100 — set to $30 |
| Sports daily loss | ✅ $2,000 | Too high — set to $20 |
| Kill switch | ✅ Available in wf_position_checks | OK |
| Credentials | ❌ MISSING API key/secret/passphrase | BLOCKER |
| Reconciliation | ✅ Enabled in run_micro_live.py | OK |
| Graceful shutdown | ✅ SIGTERM/SIGINT handlers | OK |

**Step 9: Recommended Micro-Live Configuration**
```bash
# Override via environment for $100 test:
export MICRO_BANKROLL=100
export MICRO_MAX_POS_PCT=0.05       # $5 max per position
export MICRO_MIN_CONFIDENCE=0.65    # Higher quality threshold
export MICRO_MAX_TRADES_PER_SCAN=1  # One trade at a time
export MICRO_MAX_HOLD_HOURS=4.0     # Shorter holds
```

---

## 3. SAFETY VERIFICATION CHECKLIST (Pre-Live)

### Must Be Done Before Starting Micro-Live:

- [ ] **Fix silent exit bug** in `wf_position_checks.py` (Phase A, Step 1)
- [ ] **Reconcile 55 stuck positions** — update trades.db with proper exits (Phase A, Step 3)
- [ ] **Fill API credentials** — POLYMARKET_API_KEY, API_SECRET, PASSPHRASE in `.env` (Phase C, Step 7)
- [ ] **Verify Polymarket wallet balance** — ensure >= $100 USDC on the wallet
- [ ] **Reduce daily loss limits** — set daily_loss_limit=$30, sports_daily_loss_limit=$20 for $100 test
- [ ] **Test with paper trading first** — restart paper trading with fix applied, verify signals resume
- [ ] **Verify LLM endpoint** — confirmed working (HTTP 200 on port 8080)
- [ ] **Verify Polymarket API** — confirmed working (curl succeeds)
- [ ] **Clear stale sybil queue** — process or discard the 4 stale NBA signals
- [ ] **Set up monitoring** — ensure health checks/alerts are active
- [ ] **Confirm .guard/micro-live.ok exists** — ✅ exists (empty file)
- [ ] **Review whale blacklist** — ensure no false positives blocking good whales
- [ ] **Test credential validation** — run `python -c "from run_micro_live import validate_credentials; validate_credentials()"` to verify

### Recommended Additional Checks:
- [ ] Run `python -c "import sqlite3; conn = sqlite3.connect('research/trades.db'); print(f'Trades: {conn.execute(\"SELECT COUNT(*) FROM trades\").fetchone()[0]}')"`
- [ ] Verify whale_discovery.db has recent data (517 whales, 10 A/B tier)
- [ ] Check that no systemd service conflicts exist (paper-trading.service)

---

## 4. SUMMARY OF FILES TO MODIFY

| File | Change | Priority |
|------|--------|----------|
| `strategies/wf_position_checks.py` | Fix silent orphan deletion → call exit_position() | CRITICAL |
| `strategies/whale_tracker_new.py` | Improve dedup logic to detect position changes | HIGH |
| `.env` | Fill POLYMARKET_API_KEY, API_SECRET, PASSPHRASE | CRITICAL (live only) |
| `.env` | Adjust MICRO_BANKROLL=100, daily_loss_limit=30 | HIGH (live only) |
| `research/sybil_signal_queue.json` | Clear stale signals | MEDIUM |
| `research/trades.db` | Reconcile 55 stuck positions | CRITICAL |

---

## 5. EXECUTION ORDER

1. **Fix code** → `wf_position_checks.py` (silent exit bug)
2. **Reconcile DB** → Script to update 55 stuck positions in trades.db
3. **Clear stale queues** → Clean sybil_signal_queue.json
4. **Restart paper trading** → Kill PID 708956, restart with fix
5. **Verify signals resume** → Monitor for 30 minutes
6. **Fill credentials** → Update .env with real API keys
7. **Adjust micro config** → Set $100 limits in .env
8. **Final safety check** → Run through checklist above
9. **Start micro-live** → `venv/bin/python run_micro_live.py`
10. **Monitor** → Watch first 5 trades carefully
