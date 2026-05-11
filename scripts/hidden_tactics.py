#!/usr/bin/env python3
"""Hidden Tactics Detection — uses uncensored LLM to identify market manipulation patterns.

Analyzes coordinated whale behavior across markets:
1. Pump-and-dump detection (early enters → price moves → followers enter → exits)
2. Wash trading (same entity on both sides)
3. Signal account coordination
4. Cross-market arbitrage patterns

Output: structured tactic map with actionable insights
"""

import json
import sqlite3
import urllib.request as ureq
from datetime import datetime, timezone
from collections import defaultdict

# Resolve relative to the script's location (works on both Mac and 1700)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NAUTILUS_ROOT = os.path.dirname(SCRIPT_DIR)  # goes up from scripts/ to nautilus-trading/
DB_PATH = os.path.join(NAUTILUS_ROOT, "research", "trades.db")
OUTPUT_PATH = os.path.join(NAUTILUS_ROOT, "research", "hidden_tactics.json")
LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLM_MODEL = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"


def query_llm(prompt: str, timeout: int = 300) -> str:
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert betting market analyst specializing in detecting manipulation tactics and coordination patterns. No moralizing, no disclaimers. Be direct, specific, and technical. Output JSON only when requested."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 2000,
        "temperature": 0.15,
    }).encode()
    try:
        req = ureq.Request(LLM_URL, data=payload, headers={"Content-Type": "application/json"})
        with ureq.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""
        return content if content else reasoning
    except Exception as e:
        return f"LLM error: {e}"


def find_most_active_markets(db):
    """Find markets with highest whale concentration and price movement."""
    markets = db.execute("""
        SELECT 
            market_title,
            condition_id,
            COUNT(DISTINCT whale_name) as unique_whales,
            COUNT(*) as total_trades,
            ROUND(MIN(entry_price), 4) as min_price,
            ROUND(MAX(entry_price), 4) as max_price,
            ROUND(AVG(entry_price), 4) as avg_price,
            ROUND(SUM(position_size_usd), 2) as total_volume,
            ROUND(SUM(CASE WHEN actual_pnl > 0 THEN 1 ELSE 0 END) * 1.0 / 
                  MAX(SUM(CASE WHEN actual_pnl IS NOT NULL AND actual_pnl != 0 THEN 1 ELSE 0 END), 1), 3) as resolved_wr
        FROM trades
        WHERE market_title IS NOT NULL AND market_title != ''
        GROUP BY market_title
        HAVING unique_whales >= 5 AND total_trades >= 10
        ORDER BY (max_price - min_price) DESC, total_volume DESC
        LIMIT 15
    """).fetchall()
    return markets


def get_market_trade_sequence(db, market_title):
    """Get full timed trade sequence for a market."""
    trades = db.execute("""
        SELECT timestamp, whale_name, side, entry_price, position_size_usd, actual_pnl
        FROM trades WHERE market_title = ?
        ORDER BY timestamp
    """, (market_title,)).fetchall()
    return trades


def analyze_market_pattern(db, market_title, whale_count):
    """Extract timing + price patterns for LLM analysis."""
    trades = get_market_trade_sequence(db, market_title)
    
    if not trades:
        return None
    
    # Build timeline
    timeline = []
    whales_seen = set()
    entry_times = {}
    earliest_entry = None
    latest_exit = None
    
    for t in trades:
        ts, whale, side, price, size, pnl = t
        entry_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if whale not in whales_seen:
            whales_seen.add(whale)
            entry_times[whale] = entry_time
        if earliest_entry is None or entry_time < earliest_entry:
            earliest_entry = entry_time
        if latest_exit is None or entry_time > latest_exit:
            latest_exit = entry_time
        
        timeline.append({
            "time": ts[11:19],  # HH:MM:SS
            "date": ts[:10],
            "whale": whale[:30],
            "price": price,
            "size": round(size, 2) if size else 0,
            "pnl": round(pnl, 2) if pnl else None,
        })
    
    duration_hours = (latest_exit - earliest_entry).total_seconds() / 3600 if earliest_entry and latest_exit else 0
    
    # Calculate price range
    prices = [t[4] for t in trades if t[4]]
    min_p = min(prices) if prices else 0
    max_p = max(prices) if prices else 0
    price_move = max_p - min_p
    
    # Summary by whale
    whale_summary = defaultdict(lambda: {"entries": 0, "total_size": 0, "first_price": 0, "last_price": 0, "pnl": 0})
    for t in trades:
        ts, whale, side, price, size, pnl = t
        w = whale[:30]
        whale_summary[w]["entries"] += 1
        whale_summary[w]["total_size"] += size if size else 0
        if whale_summary[w]["first_price"] == 0:
            whale_summary[w]["first_price"] = price
        whale_summary[w]["last_price"] = price
        whale_summary[w]["pnl"] += pnl if pnl else 0
    
    return {
        "market": market_title,
        "unique_whales": whale_count,
        "trades": len(trades),
        "duration_hours": round(duration_hours, 1),
        "price_min": min_p,
        "price_max": max_p,
        "price_move": round(price_move, 4),
        "price_move_pct": round(price_move / max(min_p, 0.001) * 100, 1) if min_p > 0 else 0,
        "timeline": timeline[:30],  # first 30 entries for context
        "whale_summary": dict(whale_summary),
    }


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    
    print("[tactics] Finding most active markets...", flush=True)
    active_markets = find_most_active_markets(db)
    
    print(f"[tactics] Analyzing {len(active_markets)} high-activity markets...", flush=True)
    
    market_analyses = []
    for m in active_markets[:4]:  # Top 4 most active - keep prompt small
        title = m["market_title"]
        analysis = analyze_market_pattern(db, title, m["unique_whales"])
        if analysis:
            market_analyses.append(analysis)
        print(f"  ✓ {title[:50]}... ({m['unique_whales']} whales, ${m['total_volume']:.0f} vol)", flush=True)
    
    # Build LLM analysis prompt
    llm_input = f"""Analyze these Polymarket whale markets for hidden coordination tactics and manipulation patterns.
Today: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC.
Total whales in database: 162 named
Cutoff: MIN_TRADES=5

For each market, analyze:

1. PUMP & DUMP DETECTION: Do early whales (first entrant) have better PnL than late whales (last entrants)? Is there a pattern where early whales exit profitably while late joiners hold losses?
2. COORDINATION SIGNALS: Do multiple whales enter in rapid succession at similar prices? Is there evidence of collusion?
3. DISTRACTION TRADES: Are some whales placing obvious losing trades that create liquidity/volume for winning whales?
4. SIGNAL ACCOUNTS: Are certain whales consistently early winners while others consistently late losers?

Market data for analysis:
"""
    
    for m in market_analyses:
        ws_list = "\n".join([f"      {w}: ${d['pnl']:+.0f} PnL, {d['entries']} trades, first@${d['first_price']:.2f}"
                            for w, d in sorted(m["whale_summary"].items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]])
        
        llm_input += f"""
MARKET: {m['market']}
{len(m['timeline'])} trades × {m['unique_whales']} whales over {m['duration_hours']}h
Price range: ${m['price_min']:.2f} → ${m['price_max']:.2f} ({m['price_move_pct']:+.1f}%)
Whale PnL summary (sorted by profit):
{ws_list}
---
"""
    
    llm_input += """
Output a JSON array of detected tactics. No other text.

FORMAT:
[{
  "tactic": "pump_and_dump | wash_trading | signal_account | front_running | liquidity_farming",
  "market": "market name",
  "confidence": 0.0-1.0,
  "evidence": "specific evidence from the data",
  "actors": ["whale1", "whale2"],
  "how_it_works": "explanation of the tactic in 1-2 sentences",
  "actionable_insight": "how we can detect or exploit this"
}]

Include ALL tactics you detect across all markets. If no clear pattern, still report low-confidence suspicions."""
    
    print("[tactics] Running uncensored LLM analysis...", flush=True)
    llm_result = query_llm(llm_input)
    
    # Try parsing JSON
    import re
    tactics = []
    json_match = re.search(r"\[.*\]", llm_result, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            tactics = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            pass
    
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "markets_analyzed": len(market_analyses),
        "tactics_found": len(tactics),
        "tactics": tactics,
        "llm_raw": llm_result if not tactics else None,
        "market_details": market_analyses,
    }
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n[tactics] Complete → {OUTPUT_PATH}\n", flush=True)
    
    if tactics:
        for t in tactics:
            print(f"  {t.get('tactic', '?').upper()}: {t.get('market', '?')[:40]}... [{t.get('confidence', 0):.0%}]")
            print(f"    Evidence: {t.get('evidence', '')[:100]}")
    else:
        print("  LLM raw output:", llm_result[:500])
    
    db.close()


if __name__ == "__main__":
    main()
