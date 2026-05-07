#!/usr/bin/env python3
"""Signal Validation Tracker — records autoresearch signals and checks outcomes.

For each trade recommendation:
1. Records the signal with a unique ID and timestamp
2. After 24h, checks the actual market outcome (midpoint price or resolution)
3. Tracks: predicted vs actual, win/loss, profit if entered
4. Builds a track record for the autoresearch strategy

Output: research/signal_validation.json
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Optional

TRADE_RECS_FILE = "/home/elon-1/workspace/nautilus-trading/research/trade_recommendations.json"
TRACKER_FILE = "/home/elon-1/workspace/nautilus-trading/research/signal_validation.json"
CLOB_API = "https://clob.polymarket.com"

NOISE_TITLES = ["highest temperature", "Bitcoin Up or Down", "bitcoin", "temperature", "weather"]


def fetch_json(url: str, timeout: int = 10) -> Optional[dict | list]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def get_current_price(condition_id: str) -> Optional[float]:
    """Get current YES token price from CLOB /markets/{condition_id}."""
    url = f"{CLOB_API}/markets/{condition_id}"
    data = fetch_json(url)
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens", [])
    yes_token = next((t for t in tokens if t.get("outcome", "").lower() == "yes"), None)
    if yes_token:
        price = yes_token.get("price")
        if price is not None:
            try:
                return float(price)
            except (TypeError, ValueError):
                pass
    # Fallback: check outcomePrices field
    prices_str = data.get("outcomePrices", "")
    if prices_str:
        try:
            parsed = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            if isinstance(parsed, list) and len(parsed) > 0:
                return float(parsed[0])
        except (json.JSONDecodeError, TypeError, ValueError, IndexError):
            pass
    return None


def get_winning_outcome(condition_id: str) -> Optional[str]:
    """Get the name of the winning outcome from CLOB API."""
    url = f"{CLOB_API}/markets/{condition_id}"
    data = fetch_json(url)
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens", [])
    for t in tokens:
        if t.get("winner") is True:
            return t.get("outcome", "")
    return None


def check_resolution(condition_id: str) -> Optional[str]:
    """Check if a market has resolved via CLOB /markets/{condition_id}."""
    url = f"{CLOB_API}/markets/{condition_id}"
    data = fetch_json(url)
    if not isinstance(data, dict):
        return None  # API error — can't determine

    tokens = data.get("tokens", [])
    winners = [t for t in tokens if t.get("winner") is True]
    losers = [t for t in tokens if t.get("winner") is False]

    if len(winners) == 1 and len(losers) >= 1:
        winner_outcome = winners[0].get("outcome", "")
        if "yes" in winner_outcome.lower():
            return "YES"
        elif "no" in winner_outcome.lower():
            return "NO"
        else:
            return f"SETTLED({winner_outcome})"

    # Check closed-but-unresolved (disputed)
    if data.get("closed", False) and not winners:
        prices_str = data.get("outcomePrices", "")
        if prices_str:
            try:
                parsed = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                if isinstance(parsed, list) and len(parsed) >= 2:
                    return f"DISPUTED({float(parsed[0]):.2f}/{float(parsed[1]):.2f})"
            except (json.JSONDecodeError, TypeError, ValueError, IndexError):
                pass
        return "UNRESOLVED"

    # Not yet resolved
    return "open"


def main():
    print(f"[signal_validator] Starting at {datetime.now(timezone.utc).isoformat()}", flush=True)
    
    # Load trade recommendations
    recs = []
    if os.path.exists(TRADE_RECS_FILE):
        with open(TRADE_RECS_FILE) as f:
            recs = json.load(f)
    
    # Load existing tracker
    tracker = {}
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE) as f:
            tracker = json.load(f)
    
    now = datetime.now(timezone.utc)
    updated = 0
    
    for rec in recs:
        market = rec.get("market", "Unknown")
        cid = rec.get("condition_id", "")
        timestamp = rec.get("timestamp", "")
        decision = rec.get("decision", "SKIP")
        
        # Create tracker entry if new
        if cid not in tracker:
            # Skip noise markets
            if any(n in market.lower() for n in NOISE_TITLES):
                continue
                
            tracker[cid] = {
                "market": market,
                "signal_timestamp": timestamp,
                "decision": decision,
                "confidence": rec.get("confidence", 0),
                "entry_suggested": rec.get("entry_price", 0),
                "target_suggested": rec.get("target_price", 0),
                "kelly_fraction": rec.get("kelly_fraction", 0),
                "recommendation_reason": rec.get("reason", ""),
                # Outcome tracking
                "last_checked": None,
                "current_price": None,
                "resolution": None,
                "would_profit": None,  # +/0/- based on entry vs current
                "actual_outcome_pnl": None,
                "status": "pending",
            }
            updated += 1
            
            print(f"  ➕ NEW: {market[:50]}... ({decision})", flush=True)
        
        # Check existing trackers for updates
        entry = tracker.get(cid, {})
        if entry.get("status") == "pending":
            # Get current price
            price = get_current_price(cid)
            resolution = check_resolution(cid)
            
            if price is not None:
                entry["current_price"] = price
                entry["last_checked"] = now.isoformat()
                
                # Calculate would-profit
                suggested_entry = entry.get("entry_suggested", 0.5)
                if suggested_entry > 0 and price > 0:
                    potential_return = (price - suggested_entry) / suggested_entry
                    entry["potential_return_pct"] = round(potential_return * 100, 1)
                    
                    if potential_return > 0.1:  # 10%+ profit
                        entry["would_profit"] = "profit"
                        entry["status"] = "winning"
                    elif potential_return < -0.1:
                        entry["would_profit"] = "loss"
                        entry["status"] = "losing"
                    else:
                        entry["would_profit"] = "breakeven"
                        entry["status"] = "open"
            
            if resolution and resolution not in ("open", "UNRESOLVED"):
                entry["resolution"] = resolution
                entry["status"] = "resolved"
                
                # Calculate actual outcome PnL based on winning token
                suggested_entry = entry.get("entry_suggested", 0.5)
                if resolution == "NO":
                    # NO token won — full loss
                    entry["actual_outcome_pnl"] = -100.0
                elif resolution == "YES" or resolution.startswith("SETTLED("):
                    # Fetch token data: first token (index 0) is the YES position
                    tokens_data = fetch_json(f"{CLOB_API}/markets/{cid}")
                    yes_token_winner = False
                    if isinstance(tokens_data, dict):
                        tokens = tokens_data.get("tokens", [])
                        if tokens and len(tokens) > 0:
                            yes_token_winner = tokens[0].get("winner") is True
                    
                    if yes_token_winner:
                        # YES token won — BUY would profit
                        if suggested_entry > 0:
                            entry["actual_outcome_pnl"] = round((1 - suggested_entry) / suggested_entry * 100, 1)
                    else:
                        # NO token won — full loss
                        entry["actual_outcome_pnl"] = -100.0
                
                print(f"  ✅ RESOLVED: {market[:40]}... → {resolution} (would PnL: {entry.get('actual_outcome_pnl', '?')}%)", flush=True)
            
            tracker[cid] = entry
    
    # Save tracker
    with open(TRACKER_FILE, "w") as f:
        json.dump(tracker, f, indent=2)
    
    # Summary stats
    total = len(tracker)
    pending = sum(1 for v in tracker.values() if v.get("status") == "pending")
    winning = sum(1 for v in tracker.values() if v.get("status") == "winning")
    losing = sum(1 for v in tracker.values() if v.get("status") == "losing")
    resolved = sum(1 for v in tracker.values() if v.get("status") == "resolved")
    
    print(f"\n[signal_validator] Tracked: {total} signals", flush=True)
    print(f"  Pending: {pending} | Winning: {winning} | Losing: {losing} | Resolved: {resolved}", flush=True)
    
    if resolved > 0:
        wins = sum(1 for v in tracker.values() if v.get("status") == "resolved" and v.get("resolution") == "YES")
        print(f"  Resolved outcomes: {wins}/{resolved} winning ({(wins/max(resolved,1))*100:.0f}%)", flush=True)
    
    if updated > 0:
        print(f"  New signals tracked: {updated}", flush=True)
    
    print(f"[signal_validator] Complete — {TRACKER_FILE}", flush=True)


if __name__ == "__main__":
    main()
