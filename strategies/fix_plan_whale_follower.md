# Whale Follower Fix Plan
## Trade Generation Disabled - Detailed Fix Plan

---

## Executive Summary

The nautilus trading system has two primary signal paths that generate trades:
1. **Primary (scan) path**: `_scan_whale_positions()` called from `_scan_whale_positions()` in `on_quote_tick()` - Currently **DISABLED** (commented out)
2. **Secondary (buffer) path**: `_process_trade_buffer()` for large trades ≥ $1,000 - Currently **LOW TRIGGER** (rarely fires)

**Root Cause**: The `_scan_whale_positions()` method calls `WhaleTracker.scan_known_whales()` which performs synchronous `requests.get()` calls, blocking the async event loop and causing OOM. The "fix" was to simply comment it out.

---

## Problem Analysis

### 1. Primary Signal Path (Currently Disabled)

**Location**: `whale_follower.py`, lines 231-237
```python
# DISABLED: Blocks event loop with 10+ sequential HTTP requests -> OOM
# if now - self._last_scan >= self.config.scan_interval_secs:
#     self._scan_whale_positions()
#     self._last_scan = now
```

**What it does**: 
- Calls `WhaleTracker.scan_known_whales()` every 30 seconds
- Polls Polymarket API for positions held by known whale wallets
- Generates trading signals based on whale activity

**The Problem**:
- `WhaleTracker.scan_known_whales()` calls `self._fetch_positions()` for each whale
- `_fetch_positions()` uses `requests.get()` which **blocks the async event loop**
- With 10+ whales, this creates a blocking bottleneck

---

### 2. Secondary Signal Path (Low Trigger)

**Location**: `whale_follower.py`, lines 255-277
```python
# Buffer large trades for batch processing
if usd >= 1000:  # Too high! Most Polymarket trades are < $1000
    self._trade_buffer.append({...})
    # Process buffer every 10 trades
    if len(self._trade_buffer) >= 10:
        self._process_trade_buffer()
```

**What it does**:
- Buffers TradeTick events for trades ≥ $1,000 USD
- Processes buffer every 10 trades or every 30 seconds
- Calls `_process_trade_buffer()` which calls `WhaleTracker.detect_large_trades()`

**The Problem**:
- $1,000 threshold is too high for most Polymarket activity
- Typical trades for small markets are $100-500
- Buffer only fires rarely, missing most whale activity

---

### 3. Data Quality Issues in `on_order_filled()`

**Location**: `whale_follower.py`, lines 323-349
```python
# Extract market title from instrument ID (hash-based format)
parts = inst_id.split('-')
market_title = parts[1][:80] if len(parts) > 1 else inst_id[:80]

conn.execute("""
    INSERT INTO trades (..., category, ..., signal_source, ...)
    VALUES (..., 'Unknown', ..., ...)  # Hardcoded!
""", (...))
```

**What's hardcoded**:
- `category = 'Unknown'` → Should come from market metadata
- `signal_source` → Column exists but never populated
- `market_title` extracts `parts[1][:80]` which is the **condition hash**, not the human-readable title

**What it should be**:
- `category`: "Crypto" | "Sports" | "Politics" | etc. (from market metadata)
- `market_title`: Human-readable title like "Election 2024 - Biden to win?"
- `signal_source`: "known_whale" | "large_trade" | etc.

---

## Fix Strategy

### Fix 1: Make HTTP Polling Non-Blocking (Primary Fix)

**Goal**: Move synchronous HTTP polling off the event loop

**Option A: `asyncio.to_thread()` (Recommended)**
- Simple, requires minimal code changes
- Works with both blocking and async functions
- Built into Python 3.9+

**Option B: `loop.run_in_executor()`**
- More flexible, works across versions
- Slightly more verbose

**Option C: Convert to async HTTP**
- Most elegant but requires refactoring
- Need `asyncio`-compatible HTTP client (e.g., `aiohttp`)

---

### Fix 2: Re-enable Scan Path with Rate Limiting

**Goal**: Re-enable `_scan_whale_positions()` with proper concurrency control

**Implementation**:
1. Wrap synchronous call in `asyncio.to_thread()` or `loop.run_in_executor()`
2. Add concurrency limit (max 3-5 concurrent requests)
3. Use `asyncio.Semaphore` to limit concurrent `requests.get()` calls
4. Re-enable the call in `on_quote_tick()` or the 30s exit timer

---

### Fix 3: Lower Trade Buffer Threshold

**Goal**: Make the buffer path more responsive

**Implementation**:
1. Change threshold from $1,000 to $200-500
2. Adjust buffer processing interval
3. Consider processing buffer after each TradeTick event (with rate limiting)

---

### Fix 4: Populate Data Quality Fields

**Goal**: Properly populate `category`, `market_title`, and `signal_source`

**Implementation**:
1. Fetch market metadata from Polymarket API
2. Parse metadata to extract human-readable fields
3. Map metadata to trade record columns

---

## Step-by-Step Implementation Plan

### Step 1: Fix `WhaleTracker.scan_known_whales()` for Non-Blocking Execution

**File**: `whale_tracker_new.py`, method `scan_known_whales()` (line 391-408)

**Current Code**:
```python
def scan_known_whales(self) -> list:
    """Backward compat: poll positions for known whales."""
    import time as _time
    now = _time.time()
    if now - self.last_scan_time < self.SCAN_INTERVAL:
        return []
    
    signals = []
    for wallet, whale in self.whales.items():
        positions = self._fetch_positions(wallet)  # BLOCKS EVENT LOOP
        for pos in positions:
            signal = self._process_position(pos, whale, now)
            if signal:
                signals.append(signal)
                self.signal_history.append(signal)
    
    self.last_scan_time = now
    return signals
```

**Fix**: Wrap in `asyncio.to_thread()` with rate limiting
```python
def scan_known_whales(self) -> list:
    """Backward compat: poll positions for known whales."""
    import time as _time
    import asyncio
    now = _time.time()
    if now - self.last_scan_time < self.SCAN_INTERVAL:
        return []
    
    # Use thread pool to avoid blocking event loop
    loop = asyncio.get_event_loop()
    
    # Limit concurrent requests (max 3-5 at a time)
    semaphore = asyncio.Semaphore(3)
    
    async def fetch_with_semaphore(wallet):
        async with semaphore:
            return await loop.run_in_executor(
                None,
                lambda: self._fetch_positions(wallet)
            )
    
    # Run all fetches concurrently but with semaphore limit
    tasks = [fetch_with_semaphore(wallet) for wallet in self.whales.keys()]
    results = await asyncio.gather(*tasks)
    
    signals = []
    for wallet, positions, whale in zip(
        self.whales.keys(),
        results,
        self.whales.values()
    ):
        for pos in positions:
            signal = self._process_position(pos, whale, now)
            if signal:
                signals.append(signal)
                self.signal_history.append(signal)
    
    self.last_scan_time = now
    return signals
```

---

### Step 2: Update `on_quote_tick()` to Re-enable Scan Path

**File**: `whale_follower.py`, method `on_quote_tick()` (line 223-248)

**Current Code**:
```python
def on_quote_tick(self, tick: QuoteTick) -> None:
    bid = tick.bid_price.as_double()
    ask = tick.ask_price.as_double()
    mid = (bid + ask) / 2
    
    self._check_stop_loss(mid)
    self._check_take_profit(mid)
    
    # DISABLED: Blocks event loop with 10+ sequential HTTP requests -> OOM
    # if now - self._last_scan >= self.config.scan_interval_secs:
    #     self._scan_whale_positions()
    #     self._last_scan = now
```

**Fix**: Re-enable with rate-limited call
```python
def on_quote_tick(self, tick: QuoteTick) -> None:
    bid = tick.bid_price.as_double()
    ask = tick.ask_price.as_double()
    mid = (bid + ask) / 2
    
    self._check_stop_loss(mid)
    self._check_take_profit(mid)
    
    # Periodic whale position scanning (now non-blocking!)
    now = time.time()
    if now - self._last_scan >= self.config.scan_interval_secs:
        self._scan_whale_positions()
        self._last_scan = now
```

---

### Step 3: Add Rate Limiting to `_scan_whale_positions()`

**File**: `whale_follower.py`, method `_scan_whale_positions()` (line 356-391)

**Current Code**:
```python
def _scan_whale_positions(self) -> None:
    """Poll known whale positions."""
    if not self._tracker or not self.config.auto_trade:
        return
    
    # Reset per-scan trade counter
    self._trades_this_scan = 0
    
    # Clear expired dedup entries (TTL-based re-scan)
    now = time.time()
    ttl = self.config.seen_position_ttl
    if self._tracker.seen_positions:
        expired = [
            k for k, v in self._tracker.seen_positions.items()
            if now - v > ttl
        ]
        if expired:
            for k in expired:
                del self._tracker.seen_positions[k]
            self.log.info(f"Cleared {len(expired)} expired dedup entries (TTL={ttl/3600:.0f}h)")
    
    try:
        signals = self._tracker.scan_known_whales()
        for signal in signals:
            if self._trades_this_scan >= self.config.max_trades_per_scan:
                self.log.info(
                    f"Scan trade limit reached ({self.config.max_trades_per_scan}), "
                    f"skipping {len(signals) - self._trades_this_scan} remaining signals"
                )
                break
            self._on_signal(signal)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        self.log.error(f"Whale scan error: {e}\n{tb}")
```

**Enhanced Fix**: Add explicit rate limiting
```python
def _scan_whale_positions(self) -> None:
    """Poll known whale positions with rate limiting."""
    if not self._tracker or not self.config.auto_trade:
        return
    
    # Reset per-scan trade counter
    self._trades_this_scan = 0
    
    # Clear expired dedup entries (TTL-based re-scan)
    now = time.time()
    ttl = self.config.seen_position_ttl
    if self._tracker.seen_positions:
        expired = [
            k for k, v in self._tracker.seen_positions.items()
            if now - v > ttl
        ]
        if expired:
            for k in expired:
                del self._tracker.seen_positions[k]
            self.log.info(f"Cleared {len(expired)} expired dedup entries (TTL={ttl/3600:.0f}h)")
    
    try:
        signals = self._tracker.scan_known_whales()
        
        if signals:
            self.log.info(
                f"Whale scan complete: {len(signals)} new signals detected "
                f"from {len(self._tracker.whales)} tracked whales"
            )
        
        for signal in signals:
            if self._trades_this_scan >= self.config.max_trades_per_scan:
                self.log.info(
                    f"Scan trade limit reached ({self.config.max_trades_per_scan}), "
                    f"skipping {len(signals) - self._trades_this_scan} remaining signals"
                )
                break
            self._on_signal(signal)
            self._trades_this_scan += 1  # Track trades processed
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        self.log.error(f"Whale scan error: {e}\n{tb}")
```

---

### Step 4: Lower Trade Buffer Threshold

**File**: `whale_follower.py`, method `on_trade_tick()` (line 249-277)

**Current Code**:
```python
def on_trade_tick(self, tick: TradeTick) -> None:
    size = tick.size.as_double()
    price = tick.price.as_double()
    usd = size * price
    self._trade_count += 1
    
    # Buffer large trades for batch processing
    if usd >= 1000:  # Too high!
        self._trade_buffer.append({
            "size": size,
            "price": price,
            "side": tick.aggressor_side.name,
            "timestamp": time.time(),
        })
        # Process buffer every 10 trades
        if len(self._trade_buffer) >= 10:
            self._process_trade_buffer()
    
    # Timer-based flush: process buffer every N seconds even if not full
    now = time.time()
    if now - self._last_trade_flush >= self.config.trade_buffer_flush_secs:
        if self._trade_buffer:
            self.log.info(
                f"Trade buffer flush: {len(self._trade_buffer)} trades, "
                f"total received: {self._trade_count}"
            )
            self._process_trade_buffer()
        self._last_trade_flush = now
```

**Fix**: Lower threshold and add concurrent execution
```python
def on_trade_tick(self, tick: TradeTick) -> None:
    size = tick.size.as_double()
    price = tick.price.as_double()
    usd = size * price
    self._trade_count += 1
    
    # Buffer trades >= $200 (lowered from $1000 for better responsiveness)
    if usd >= 200:  # Lowered threshold for small markets
        self._trade_buffer.append({
            "size": size,
            "price": price,
            "side": tick.aggressor_side.name,
            "timestamp": time.time(),
        })
        # Process buffer every 5 trades (was 10)
        if len(self._trade_buffer) >= 5:
            self._process_trade_buffer()
    
    # Timer-based flush: process buffer every N seconds even if not full
    now = time.time()
    if now - self._last_trade_flush >= self.config.trade_buffer_flush_secs:
        if self._trade_buffer:
            self.log.info(
                f"Trade buffer flush: {len(self._trade_buffer)} trades, "
                f"total received: {self._trade_count}"
            )
            self._process_trade_buffer()
        self._last_trade_flush = now
```

---

### Step 5: Populate Data Quality Fields in `on_order_filled()`

**File**: `whale_follower.py`, method `on_order_filled()` (line 278-355)

**Current Code**:
```python
def on_order_filled(self, event) -> None:
    """Log filled orders to the trades database."""
    try:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).parent.parent / "research" / "trades.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                whale_name TEXT,
                whale_address TEXT,
                category TEXT NOT NULL,
                market_title TEXT,
                condition_id TEXT,
                token_id TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                position_size_usd REAL,
                ...
                signal_source TEXT,
                ...
            )
        """)
        
        # Extract trade details from the fill event
        inst_id = str(event.instrument_id)
        entry_price = event.last_px.as_double() if hasattr(event, 'last_px') and event.last_px else 0.5
        qty = event.last_qty.as_double() if hasattr(event, 'last_qty') and event.last_qty else 125
        size_usd = qty * entry_price
        
        # Look up whale name from pending dict
        whale_name = self._pending_whales.pop(str(event.client_order_id), "unknown")
        
        # Extract market title from instrument ID (hash-based format)
        parts = inst_id.split('-')
        market_title = parts[1][:80] if len(parts) > 1 else inst_id[:80]
        
        conn.execute("""
            INSERT OR IGNORE INTO trades (trade_id, timestamp, whale_name, market_title, side, entry_price, position_size_usd, category, signal_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            str(datetime.now(timezone.utc)),
            whale_name,
            market_title,  # Still hash-based!
            event.order_side.name if hasattr(event, 'order_side') else 'BUY',
            entry_price,
            size_usd,
            'Unknown',  # Still hardcoded!
            SignalSource.KNOWN_WHALE,  # Fixed!
        ))
        conn.commit()
        conn.close()
        
        self.log.info(f"[DB] Logged trade: {whale_name} | {market_title[:40]} | ${size_usd:.0f}")
    except Exception as e:
        self.log.error(f"[DB] Failed to log trade: {e}")
```

**Fix**: Fetch market metadata and populate proper fields
```python
def on_order_filled(self, event) -> None:
    """Log filled orders to the trades database with proper metadata."""
    try:
        import sqlite3
        from pathlib import Path
        from nautilus_trader.adapters.polymarket.common.symbol import get_polymarket_instrument_id
        from nautilus_trader.adapters.polymarket.common.metadata import get_market_metadata
        
        db_path = Path(__file__).parent.parent / "research" / "trades.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(str(db_path))
        
        # Extract trade details from the fill event
        inst_id = str(event.instrument_id)
        entry_price = event.last_px.as_double() if hasattr(event, 'last_px') and event.last_px else 0.5
        qty = event.last_qty.as_double() if hasattr(event, 'last_qty') and event.last_qty else 125
        size_usd = qty * entry_price
        
        # Look up whale name from pending dict
        whale_name = self._pending_whales.pop(str(event.client_order_id), "unknown")
        
        # Get Polymarket condition ID and metadata
        polymarket_inst = get_polymarket_instrument_id(inst_id)
        condition_id = polymarket_inst.condition_id
        token_id = polymarket_inst.token_id
        
        # Fetch market metadata for proper category and title
        try:
            metadata = get_market_metadata(polymarket_inst)
            if metadata:
                category = metadata.get('category', 'Unknown')
                market_title = metadata.get('title', inst_id[:80])  # Fallback to hash
            else:
                # Fallback: extract from instrument ID
                parts = inst_id.split('-')
                market_title = parts[1][:80] if len(parts) > 1 else inst_id[:80]
                category = 'Unknown'
        except Exception as e:
            # API call failed, use fallback
            parts = inst_id.split('-')
            market_title = parts[1][:80] if len(parts) > 1 else inst_id[:80]
            category = 'Unknown'
        
        conn.execute("""
            INSERT OR IGNORE INTO trades (trade_id, timestamp, whale_name, market_title, side, entry_price, position_size_usd, category, signal_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()),
            str(datetime.now(timezone.utc)),
            whale_name,
            market_title,  # Now properly populated!
            event.order_side.name if hasattr(event, 'order_side') else 'BUY',
            entry_price,
            size_usd,
            category,  # Now properly populated!
            SignalSource.KNOWN_WHALE,  # Fixed!
        ))
        conn.commit()
        conn.close()
        
        self.log.info(f"[DB] Logged trade: {whale_name} | {category} | {market_title[:40]} | ${size_usd:.0f}")
    except Exception as e:
        self.log.error(f"[DB] Failed to log trade: {e}")
```

---

## Implementation Order

1. **Step 1**: Fix `WhaleTracker.scan_known_whales()` for non-blocking execution
2. **Step 2**: Update `on_quote_tick()` to re-enable scan path
3. **Step 3**: Add rate limiting to `_scan_whale_positions()`
4. **Step 4**: Lower trade buffer threshold
5. **Step 5**: Populate data quality fields in `on_order_filled()`

---

## Testing Checklist

After implementing fixes:

1. **Unit Tests**:
   - Verify `scan_known_whales()` doesn't block event loop
   - Verify concurrent request limit (max 3-5)
   - Verify trade buffer fires for trades ≥ $200
   - Verify `market_title` and `category` are properly populated

2. **Integration Tests**:
   - Verify 10+ whales scanned without OOM
   - Verify 30-second scan interval works correctly
   - Verify trade buffer processes trades from small markets
   - Verify database records have correct metadata

3. **Performance Tests**:
   - Monitor event loop latency with 10+ concurrent fetches
   - Verify rate limiting prevents API throttling
   - Check memory usage during extended runs

---

## Expected Results After Fixes

1. **Primary path**: 10+ whales scanned every 30 seconds, 3-5 concurrent requests, no blocking
2. **Secondary path**: Buffer fires for trades ≥ $200, more responsive to small market activity
3. **Data quality**: `category` and `market_title` properly populated, `signal_source` correctly set

---

## Files Modified

1. `strategies/whale_tracker_new.py` - `scan_known_whales()` method
2. `strategies/whale_follower.py` - `on_quote_tick()`, `_scan_whale_positions()`, `on_trade_tick()`, `on_order_filled()`

---

## Dependencies

- Python 3.9+ for `asyncio.to_thread()`
- Nautilus Trading framework for metadata access
- Polymarket Data API for market metadata

---

## Rollback Plan

If issues occur:
1. Re-comment out the scan path temporarily
2. Revert trade buffer threshold to $1,000
3. Use fallback hash-based `market_title`

---

## Monitoring After Deployment

1. **Logs to watch**:
   - "Whale scan complete: X new signals"
   - "Trade buffer flush: X trades"
   - "[DB] Logged trade: {whale_name} | {category} | {market_title[:40]}"

2. **Metrics to track**:
   - Event loop latency (< 10ms per tick)
   - Concurrent request count (3-5)
   - Trade buffer trigger rate
   - API rate limit headers

---

**Estimated Implementation Time**: 2-3 hours
**Risk Level**: Medium (requires careful async/await handling)
**Testing Required**: Yes (unit + integration)
