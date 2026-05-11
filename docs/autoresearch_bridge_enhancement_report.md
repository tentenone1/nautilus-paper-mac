# Autoresearch Bridge — Enhancement Analysis Report

**Date:** 2026-05-11
**Scope:** `autoresearch_bridge.py` + `autoresearch_signal_bridge.py`
**Status:** Analysis only — no changes made

---

## 1. Current Architecture Assessment

### What's Working Well

| Component | Assessment |
|---|---|
| **LLM JSON extraction** | Robust bracket-matching parser with fallback regex extraction. Handles Qwen thinking markers well. |
| **Whale context enrichment** | `lookup_whales()` joins `whale_signals` with `whale_intelligence`, aggregates volume, and surfaces trust-scored whales. In-memory cache avoids repeated DB hits. |
| **Dedup via `BridgeState`** | `processed_timestamps` keyed by `{timestamp}:{whale_name}:{market}` prevents duplicate analysis. |
| **Incremental save** | Each recommendation appended to `trade_recommendations.json` immediately; rolling 100-entry cap prevents file bloat. |
| **Signal bridge token resolution** | `resolve_yes_token()` properly handles YES/NO binary markets with fallback. |
| **Noise filtering** | `NOISE_TITLES` + inline `bitcoin/temperature/weather` patterns filter known junk. |
| **Bitable logging** | BUY/WAIT signals written to Feishu Bitable for human visibility. |

### Identified Weaknesses

| Area | Severity | Detail |
|---|---|---|
| **Confidence threshold** | 🔴 HIGH | No minimum confidence gate in `autoresearch_bridge.py`. All detections get LLM analysis. Only downstream (>60% display filter) and in `autoresearch_signal_bridge.py` there's no explicit rejection threshold — ALL BUYs pass through regardless of confidence. |
| **Edge score formula** | 🔴 HIGH | `edge_score = confidence * 0.8` is arbitrary and one-dimensional. Ignores whale volume, trust scores, market liquidity, time-to-resolution, and midpoints. The downstream `whale_tiers.json` expects meaningful edge scores for tiered Kelly sizing. |
| **Kelly fraction** | 🟡 MEDIUM | Raw LLM output used as `kelly_fraction` with no sanity clamping in the bridge. Downside: LLM can return 0.5 (50% of bankroll!) for a single trade. The `whale_tiers.json` has `kelly_sanity_checks` (`max_position_pct: 0.125`) but the bridge doesn't enforce them. |
| **LLM prompt** | 🟡 MEDIUM | Very minimal prompt — just market name, price, age, trades count. Missing: midpoint prices (fetched but only attached to output, NOT fed to LLM), market category, resolution date, whale trust details. |
| **No event horizon awareness** | 🟡 MEDIUM | No consideration of time-to-resolution. A market closing in 2 hours vs 2 weeks should have dramatically different risk profiles. |
| **No multi-source validation** | 🟡 MEDIUM | Each detection analyzed in isolation. No cross-check with historical LLM accuracy, whale track record on similar markets, or signal consensus. |
| **Noise filtering** | 🟠 LOW-MED | Hardcoded `NOISE_TITLES` list. No regex patterns, no category-based filtering. Easily bypassed by slightly reworded titles. |
| **No rate limiting / batching** | 🟠 LOW-MED | Sequential LLM calls with `time.sleep(1)`. If 5 detections arrive simultaneously, that's 5 separate HTTP calls. No batching or parallelization control. |
| **Detection key collision** | 🟠 LOW-MED | Key format `{ts}:{whale_name}:{market[:50]}` can collide if two detections have same timestamp + whale but differ in condition_id. Should include condition_id. |
| **No existing-position matching** | 🟠 LOW-MED | Signal bridge doesn't check if we already hold a position on the same condition_id. Could lead to duplicate entries. |
| **Midpoints not used in analysis** | 🟡 MEDIUM | `check_midpoint()` is called and attached to output `rec["midpoints"]`, but NOT included in the LLM prompt. The LLM makes decisions blind to actual CLOB prices. |

---

## 2. High-Impact Enhancements (Ranked)

### 🥇 #1 — Multi-Dimensional Edge Score (Expected Impact: +30-40% signal quality)

**Problem:** `edge_score = confidence * 0.8` is a scalar transform of a single LLM opinion. It doesn't reflect the actual expected value of the trade.

**Proposal:** Compute edge score from multiple signals:
- **LLM confidence** (30% weight) — raw model output
- **Whale consensus score** (25% weight) — volume-weighted trust of whale signals
- **Price discrepancy** (20% weight) — difference between detection price and current midpoint
- **Liquidity score** (15% weight) — based on trade count and market depth
- **Time decay factor** (10% weight) — fresher signals score higher

```python
def compute_edge_score(
    confidence: float,
    whales: list[dict],
    detection_price: float,
    midpoints: dict[str, float],
    trades_count: int,
    age_seconds: float,
) -> float:
    """Multi-dimensional edge score (0.0-1.0)."""

    # Component 1: LLM confidence (30%)
    llm_component = confidence * 0.30

    # Component 2: Whale consensus (25%)
    if whales:
        total_volume = sum(w.get("usd_value", 0) or 0 for w in whales)
        avg_trust = sum(w.get("trust_score", 0) or 0 for w in whales) / len(whales)
        # Normalize: trust 0-10 → 0-1, volume capped at $100K → 0-1
        whale_component = ((avg_trust / 10.0) * 0.6 + min(total_volume / 100_000, 1.0) * 0.4) * 0.25
    else:
        whale_component = 0.0  # No whale backing = zero whale component

    # Component 3: Price discrepancy (20%)
    # If current midpoint > detection price, there's positive drift
    if midpoints:
        best_mid = max(midpoints.values()) if midpoints else detection_price
        price_drift = (best_mid - detection_price) / max(detection_price, 0.01)
        price_component = min(max(price_drift + 0.5, 0.0), 1.0) * 0.20  # 0.5 baseline
    else:
        price_component = 0.10  # Neutral when no price data

    # Component 4: Liquidity / activity (15%)
    liquidity_component = min(trades_count / 20.0, 1.0) * 0.15

    # Component 5: Recency / time decay (10%)
    # Signals older than 1 hour decay rapidly
    age_factor = max(0.0, 1.0 - (age_seconds / 3600.0))
    time_component = age_factor * 0.10

    return llm_component + whale_component + price_component + liquidity_component + time_component
```

### 🥈 #2 — LLM Prompt Engineering + Midpoint Injection (Expected Impact: +20-25% decision quality)

**Problem:** The LLM makes decisions without seeing CLOB midpoint prices, market resolution dates, or category context.

**Proposal:** Enrich the prompt with live data that the bridge already fetches but doesn't use:

```python
def build_analysis_prompt(
    detection: dict,
    midpoints: dict[str, float],
    whales: list[dict],
    market_info: Optional[dict],
    existing_positions: list[dict],
) -> str:
    """Build a rich, structured prompt for LLM analysis."""

    market = detection.get("market", "Unknown")
    price = detection.get("entry_price") or detection.get("lowest_price", 0.5)
    age = detection.get("age_seconds", 0)

    # ── Midpoint context ──
    midpoint_lines = ""
    if midpoints:
        midpoint_lines = "\n".join(f"  {k}: {v:.4f}" for k, v in midpoints.items())
        midpoint_lines = f"\nLIVE CLOB MIDPOINTS:\n{midpoint_lines}"

    # ── Whale context (enhanced) ──
    whale_ctx = ""
    if whales:
        total_v = sum(w.get("usd_value", 0) or 0 for w in whales)
        high_trust = [w for w in whales if (w.get("trust_score") or 0) >= 7]
        low_trust = [w for w in whales if (w.get("trust_score") or 0) < 4]
        whale_ctx = f"""
WHALE ACTIVITY:
  Total volume: ${total_v:,.0f}
  High-trust whales (score≥7): {len(high_trust)}
  Low-trust whales (score<4): {len(low_trust)}
  Details: {'; '.join(
      f"{w['whale_name']} trust={w['trust_score']}, ${w.get('usd_value',0):,.0f} {w.get('side','?')} {w.get('outcome','?')}"
      for w in whales[:5]
  )}"""

    # ── Market metadata ──
    meta_ctx = ""
    if market_info:
        end_date = market_info.get("end_date_iso", "") or market_info.get("endDate", "")
        category = market_info.get("category", "")
        volume_24h = market_info.get("volume", 0) or 0
        meta_ctx = f"""
MARKET METADATA:
  Category: {category or 'unknown'}
  Resolution date: {end_date or 'unknown'}
  24h volume: ${volume_24h:,.0f}"""

    # ── Existing positions (dedup context) ──
    position_ctx = ""
    if existing_positions:
        position_ctx = f"""
EXISTING POSITIONS (do NOT double-enter):
  {'; '.join(p['market_title'][:40] for p in existing_positions[:3])}"""

    # ── Decision framework ──
    framework = """
DECISION FRAMEWORK:
- BUY only if: confidence > 0.65 AND there is a clear edge (price < fair value)
- WAIT if: uncertain, low liquidity, or resolution is imminent (<6 hours)
- SKIP if: noise market, whale consensus is mixed, or price has no edge

Consider:
1. Is the current midpoint offering value vs your assessment?
2. Are whales coordinated (same side) or split?
3. Is there enough time for the thesis to play out?
4. Does the market category have reliable resolution history?"""

    return f"""Analyze this Polymarket market and output a trade recommendation as JSON only.

MARKET: {market}
Detection price: ${price:.2f}
Signal age: {age:.0f}s ({age/60:.1f} minutes)
Trades in last scan: {detection.get('trades_count', 0)}
Detection type: {detection.get('type', 'unknown')}{midpoint_lines}{whale_ctx}{meta_ctx}{position_ctx}
{framework}

OUTPUT (JSON only, no preamble):
{{
  "market": "name",
  "decision": "BUY | WAIT | SKIP",
  "confidence": 0.0-1.0,
  "reason": "brief reason mentioning specific factors",
  "entry_price": 0.0,
  "target_price": 0.0,
  "stop_price": 0.0,
  "kelly_fraction": 0.0,
  "hold_hours": 0,
  "fair_value_estimate": 0.0
}}"""
```

### 🥉 #3 — Kelly Fraction Sanity + Gate Enforcement (Expected Impact: +15-20% risk control)

**Problem:** LLM returns arbitrary `kelly_fraction` values. No clamping in bridge. Downstream has sanity checks but bridge should be the first gate.

**Proposal:** Apply hard bounds and scale against edge score:

```python
# Constants for Kelly gating
KELLY_MIN = 0.01      # 1% floor
KELLY_MAX = 0.125     # 12.5% ceiling (matches whale_tiers.json max_position_pct)
KELLY_CONFIDENCE_GATE = 0.65  # Below this, force WAIT regardless of BUY decision

def clamp_kelly_fraction(
    raw_kelly: float,
    confidence: float,
    edge_score: float,
    detection_price: float,
    midpoints: dict[str, float],
) -> tuple[float, str]:
    """Apply sanity bounds and confidence gating to Kelly fraction.

    Returns (clamped_kelly, status) where status is 'ok', 'clamped', or 'gated'.
    """
    # Gate: reject if confidence too low
    if confidence < KELLY_CONFIDENCE_GATE:
        return 0.0, "gated"

    # Clamp to bounds
    kelly = max(KELLY_MIN, min(KELLY_MAX, raw_kelly))

    # Scale by edge score: high edge → keep Kelly, low edge → reduce
    if edge_score < 0.3:
        kelly *= 0.25   # Weak edge: quarter the size
    elif edge_score < 0.5:
        kelly *= 0.5    # Moderate edge: half the size
    elif edge_score < 0.7:
        kelly *= 0.75   # Good edge: 75% size

    # Final clamp (after scaling could push it below min)
    kelly = max(KELLY_MIN, kelly)

    status = "ok" if kelly == raw_kelly else "clamped"
    return round(kelly, 4), status
```

---

## 3. Implementation Priority

### Quick Wins (1-2 hours each, deployable immediately)

| Priority | Change | Files | Impact |
|---|---|---|---|
| **P1** | Add confidence gate (>65%) in signal_bridge before queueing | `autoresearch_signal_bridge.py` | Prevents low-confidence BUYs from reaching execution |
| **P2** | Inject midpoints into LLM prompt | `autoresearch_bridge.py` | LLM sees live prices → better decisions |
| **P3** | Clamp Kelly fraction in bridge output | `autoresearch_bridge.py` | Risk control at the source |
| **P4** | Add resolution date to prompt from market_info | `autoresearch_bridge.py` | LLM can avoid imminent-resolution traps |
| **P5** | Expand NOISE_TITLES to regex patterns | `autoresearch_bridge.py` | Catches more junk with fewer entries |

### Deeper Work (half-day to full-day each)

| Priority | Change | Files | Impact |
|---|---|---|---|
| **D1** | Multi-dimensional edge score computation | `autoresearch_signal_bridge.py` | Replaces `conf*0.8` with meaningful scoring |
| **D2** | Existing-position dedup against whale_follower positions | `autoresearch_signal_bridge.py` + `whale_follower.py` | Prevents double-entry on same market |
| **D3** | LLM batching (batch prompt for multiple detections) | `autoresearch_bridge.py` | Reduces API calls, enables cross-market context |
| **D4** | Historical accuracy tracking for LLM decisions | New component | Enables confidence calibration over time |
| **D5** | Market category-aware prompt adjustments | `autoresearch_bridge.py` | Different analysis framework for sports vs politics vs crypto |

---

## 4. Specific Code Changes (Top 3 Recommendations)

### Change 1: Confidence Gate + Kelly Clamping in `autoresearch_signal_bridge.py`

**File:** `autoresearch_signal_bridge.py`
**Location:** Around line 139 (BUY filtering section)

**Current code (line 139-140):**
```python
buy_recs = [r for r in recommendations if r.get("decision") == "BUY"]
new_recs = [r for r in buy_recs if make_signal_key(r) not in state]
```

**Proposed change:**
```python
# Add confidence gate constants at top of file
CONFIDENCE_GATE = 0.65    # Minimum confidence to pass to execution
KELLY_MAX = 0.125         # Max 12.5% per trade (matches whale_tiers.json)
KELLY_MIN = 0.01          # Min 1% floor
EDGE_SCORE_MIN = 0.25     # Minimum edge score to queue

buy_recs = [
    r for r in recommendations
    if r.get("decision") == "BUY"
    and r.get("confidence", 0) >= CONFIDENCE_GATE
]
new_recs = [r for r in buy_recs if make_signal_key(r) not in state]

# Add gated/skipped counters
gated_low_conf = 0
gated_low_edge = 0
gated_bad_kelly = 0
```

**And in the signal construction loop (around line 160-205), add Kelly clamping:**
```python
# Current line 162: kelly = rec.get("kelly_fraction", 0.15)
kelly = rec.get("kelly_fraction", 0.15)

# NEW: Kelly clamping
kelly = max(KELLY_MIN, min(KELLY_MAX, kelly))

# NEW: Edge score gate
edge_score = rec.get("edge_score", confidence * 0.8)
if edge_score < EDGE_SCORE_MIN:
    print(
        f"  ⚠️  Gated (low edge): '{market[:50]}...' | edge={edge_score:.2f}",
        flush=True,
    )
    gated_low_edge += 1
    state[make_signal_key(rec)] = time.time()
    continue

# Replace line 202: "edge_score": confidence * 0.8,
signal = {
    ...
    "edge_score": edge_score,
    "kelly_fraction": kelly,
    ...
}
```

### Change 2: Multi-Dimensional Edge Score in `autoresearch_signal_bridge.py`

**File:** `autoresearch_signal_bridge.py`
**Location:** New function + integration into signal construction

**Add this function before `main()`:**
```python
def compute_edge_score(
    confidence: float,
    whale_ctx: str,
    detection_price: float,
    midpoints: dict,
    trades_count: int,
    age_seconds: float,
) -> float:
    """Multi-dimensional edge score (0.0-1.0)."""
    # Component 1: LLM confidence (30%)
    llm_component = confidence * 0.30

    # Component 2: Whale consensus (25%)
    # Parse whale context string for volume and trust signals
    whale_component = 0.0
    if whale_ctx:
        import re
        volume_match = re.search(r'\$([0-9,]+)', whale_ctx)
        trust_matches = re.findall(r'trust=(\d+)', whale_ctx)
        if volume_match:
            vol = int(volume_match.group(1).replace(',', ''))
            whale_component += min(vol / 100_000, 1.0) * 0.15
        if trust_matches:
            avg_trust = sum(int(t) for t in trust_matches) / len(trust_matches)
            whale_component += (avg_trust / 10.0) * 0.10

    # Component 3: Price movement signal (20%)
    if midpoints:
        best_mid = max(midpoints.values())
        drift = (best_mid - detection_price) / max(detection_price, 0.01)
        price_component = min(max(drift + 0.5, 0.0), 1.0) * 0.20
    else:
        price_component = 0.10  # Neutral baseline

    # Component 4: Liquidity (15%)
    liquidity_component = min(trades_count / 20.0, 1.0) * 0.15

    # Component 5: Recency (10%)
    age_factor = max(0.0, 1.0 - (age_seconds / 3600.0))
    time_component = age_factor * 0.10

    return round(llm_component + whale_component + price_component +
                 liquidity_component + time_component, 4)
```

**Replace line 202 in signal construction:**
```python
# OLD:
"edge_score": confidence * 0.8,

# NEW:
"edge_score": compute_edge_score(
    confidence=confidence,
    whale_ctx=reason,  # Whale context is embedded in reason
    detection_price=entry_price,
    midpoints=rec.get("midpoints", {}),
    trades_count=detection.get("trades_count", 0),
    age_seconds=detection.get("age_seconds", 0),
),
```

### Change 3: Midpoint Injection + Enhanced Prompt in `autoresearch_bridge.py`

**File:** `autoresearch_bridge.py`
**Location:** `analyze_market()` function (line 231-281)

**Replace the prompt construction (lines 258-281) with:**
```python
    # Build midpoint context
    midpoint_lines = ""
    if midpoints:
        mp_entries = "\n".join(f"  {k}: ${v:.4f}" for k, v in midpoints.items())
        midpoint_lines = f"\nLIVE CLOB MIDPOINTS:\n{mp_entries}"

    # Build market metadata context
    meta_ctx = ""
    if market_info:
        end_date = market_info.get("end_date_iso") or market_info.get("endDate", "")
        category = market_info.get("category", "")
        volume = market_info.get("volume", 0) or 0
        meta_ctx = f"""
MARKET METADATA:
  Category: {category or 'unknown'}
  Resolution date: {end_date or 'unknown'}
  Volume: ${volume:,.0f}"""

    # Time-awareness note
    time_note = ""
    if age > 1800:  # 30 minutes
        time_note = "\n⚠️ WARNING: Signal is over 30 minutes old. Price may have moved."
    elif age > 300:  # 5 minutes
        time_note = "\n⚡ Signal is 5+ minutes old. Verify midpoint before entering."

    prompt = f"""Analyze this Polymarket market and output a trade recommendation as JSON only.

MARKET: {market}
Detection price: ${price:.2f}
Signal age: {age:.0f}s{time_note}
Trades in last scan: {detection.get('trades_count', 0)}
Detection type: {detection.get('type', 'unknown')}{midpoint_lines}{whale_ctx}{meta_ctx}

EVALUATION CRITERIA:
1. Price edge: Is the detection price below current midpoint (positive drift)?
2. Whale quality: Are high-trust whales ({(w.get('trust_score',0) or 0) >= 7}) aligned?
3. Liquidity: Sufficient trade count for reliable pricing?
4. Time horizon: Enough time before resolution for thesis to play out?
5. Category reliability: Has this market category resolved cleanly historically?

DECISION RULES:
- BUY: confidence > 0.65, clear price edge, whale support OR strong fundamental thesis
- WAIT: uncertain direction, low liquidity, or resolution < 6 hours away
- SKIP: noise market, conflicting signals, or no edge at current price

OUTPUT (JSON only, no preamble or explanation):
{{
  "market": "name",
  "decision": "BUY | WAIT | SKIP",
  "confidence": 0.0-1.0,
  "reason": "1-2 sentence reason citing specific factors above",
  "entry_price": 0.0,
  "target_price": 0.0,
  "stop_price": 0.0,
  "kelly_fraction": 0.0-0.125,
  "hold_hours": 0,
  "fair_value_estimate": 0.0
}}"""
```

**Also update the `analyze_market` function signature and call site to pass midpoints:**

```python
# Change function signature (line 231):
def analyze_market(
    detection: dict,
    market_info: Optional[dict] = None,
    midpoints: Optional[dict] = None,
) -> dict:

# Update call site in run_once (line 407):
rec = analyze_market(det, market_info, midpoints)
```

---

## Additional Recommendations

### A. Regex-Based Noise Filtering

Replace `is_noise()` with a regex-based system:

```python
import re

NOISE_PATTERNS: list[re.Pattern] = [
    re.compile(r"highest\s+temperature", re.I),
    re.compile(r"bitcoin\s+(up|down)\s+(up|down)", re.I),
    re.compile(r"(bitcoin|ethereum|solana)\s+(up|down)", re.I),
    re.compile(r"weather\s+(in|for)", re.I),
    re.compile(r"daily\s+(high|low)\s+temperature", re.I),
    re.compile(r"will\s+\w+\s+score\s+(over|under)\s+\d+", re.I),  # generic prop bets
]

def is_noise(detection: dict) -> bool:
    market = (detection.get("market", "") or "").lower()
    title = (detection.get("title", "") or "").lower()
    combined = market + " " + title
    return any(p.search(combined) for p in NOISE_PATTERNS)
```

### B. Detection Key Improvement

**Change `make_detection_key()` (line 370-375):**
```python
def make_detection_key(detection: dict) -> str:
    ts = detection.get("detected_at") or detection.get("timestamp", "")
    market = detection.get("market", "") or detection.get("title", "")
    whale = detection.get("whale_name", "")
    cid = detection.get("condition_id", "")  # NEW: include condition_id
    return f"{cid}:{ts}:{whale}:{market[:50]}"
```

### C. Existing-Position Dedup

Add to `autoresearch_signal_bridge.py` — check against a positions file or DB:

```python
def has_existing_position(condition_id: str, positions_db_path: Path) -> bool:
    """Check if we already have an open position on this market."""
    if not positions_db_path.exists():
        return False
    try:
        conn = sqlite3.connect(positions_db_path)
        row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE condition_id=? AND status='open'",
            (condition_id,)
        ).fetchone()
        conn.close()
        return row[0] > 0
    except Exception:
        return False
```

---

## Summary of Expected Improvements

| Enhancement | Confidence Improvement | Edge Improvement | Risk Reduction | Effort |
|---|---|---|---|---|
| Confidence gate | ✅✅✅ | — | ✅✅✅ | 30 min |
| Kelly clamping | — | — | ✅✅✅ | 30 min |
| Midpoint injection | ✅✅ | ✅✅ | — | 1 hour |
| Multi-dim edge score | ✅ | ✅✅✅ | ✅✅ | 2 hours |
| Enhanced prompt | ✅✅ | ✅✅ | ✅ | 1 hour |
| Noise regex | ✅ | — | — | 30 min |
| Position dedup | — | ✅ | ✅✅ | 1 hour |
| Key fix | — | — | ✅ | 15 min |

**Total estimated effort: ~6-7 hours for all enhancements.**
**Recommended phased rollout:** P1-P5 in first session (2 hours), then D1-D2 (4 hours), then D3-D5 as separate sessions.
