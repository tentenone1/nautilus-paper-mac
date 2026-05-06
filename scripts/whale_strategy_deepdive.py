#!/usr/bin/env python3
"""Whale Strategy Deep-Dive — analyzes top COPY whale patterns using uncensored LLM.

Extracts detailed trade history from backup DB, builds strategy profiles,
and feeds to Qwen3.6-35B-Uncensored for unfiltered strategy extraction.
Outputs structured "recipe cards" for each whale.

Schedule: on-demand
"""

import json
import sqlite3
import urllib.request as ureq
from datetime import datetime, timezone

DB_PATH = "/home/elon-1/workspace/nautilus-trading/research/trades.db.backup-20260506"
OUTPUT_PATH = "/home/elon-1/workspace/nautilus-trading/research/whale_strategy_deepdive.json"
LLM_URL = "http://192.168.50.148:1234/v1/chat/completions"
LLM_MODEL = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
TOP_WHALES = ["surfandturf", "matanovik", "pilotbaby", "RJW1", "p150-0xba389f", "loitterer", "Deep7"]


def query_llm(prompt: str) -> str:
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert betting strategy analyst. Extract concrete, actionable patterns from whale trade data. Be specific with numbers. NO moralizing or disclaimers. Output the requested JSON format exactly."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }).encode()
    try:
        req = ureq.Request(LLM_URL, data=payload, headers={"Content-Type": "application/json"})
        with ureq.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""
        return content if content else reasoning
    except Exception as e:
        return f"LLM error: {e}"


def build_whale_profile(db, whale_name: str) -> dict:
    """Extract full trade data for a whale from the backup DB."""
    
    # Basic stats
    stats = db.execute("""
        SELECT 
            COUNT(*) as total_trades,
            ROUND(AVG(position_size_usd), 2) as avg_bet,
            ROUND(MAX(position_size_usd), 2) as max_bet,
            ROUND(MIN(position_size_usd), 2) as min_bet,
            COUNT(DISTINCT condition_id) as unique_markets,
            COUNT(DISTINCT category) as categories,
            GROUP_CONCAT(DISTINCT category) as cat_list
        FROM trades WHERE whale_name = ?
    """, (whale_name,)).fetchone()
    
    win_loss = db.execute("""
        SELECT 
            COUNT(CASE WHEN actual_pnl > 0 THEN 1 END) as wins,
            COUNT(CASE WHEN actual_pnl < 0 THEN 1 END) as losses,
            COUNT(CASE WHEN actual_pnl = 0 OR actual_pnl IS NULL THEN 1 END) as unresolved,
            ROUND(AVG(CASE WHEN actual_pnl > 0 THEN actual_pnl END), 2) as avg_win,
            ROUND(AVG(CASE WHEN actual_pnl < 0 THEN actual_pnl END), 2) as avg_loss,
            ROUND(SUM(actual_pnl), 2) as total_pnl,
            ROUND(AVG(entry_price), 4) as avg_entry
        FROM trades WHERE whale_name = ?
    """, (whale_name,)).fetchone()
    
    time_pattern = db.execute("""
        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour,
               COUNT(*) as trades,
               ROUND(AVG(actual_pnl), 2) as avg_pnl
        FROM trades WHERE whale_name = ?
        GROUP BY hour ORDER BY trades DESC LIMIT 3
    """, (whale_name,)).fetchall()
    
    entry_ranges = db.execute("""
        SELECT 
            CASE 
                WHEN entry_price < 0.2 THEN 'deep_underdog'
                WHEN entry_price < 0.4 THEN 'underdog'
                WHEN entry_price < 0.6 THEN 'even'
                WHEN entry_price < 0.8 THEN 'favorite'
                ELSE 'heavy_favorite'
            END as range_name,
            COUNT(*) as trades,
            ROUND(SUM(actual_pnl), 2) as pnl,
            ROUND(AVG(position_size_usd), 2) as avg_bet
        FROM trades WHERE whale_name = ?
        GROUP BY range_name ORDER BY trades DESC
    """, (whale_name,)).fetchall()
    
    # Top market types
    markets = db.execute("""
        SELECT DISTINCT market_title, actual_pnl, entry_price, position_size_usd
        FROM trades WHERE whale_name = ? AND actual_pnl IS NOT NULL AND actual_pnl != 0
        ORDER BY ABS(actual_pnl) DESC LIMIT 10
    """, (whale_name,)).fetchall()
    
    return {
        "name": whale_name,
        "total_trades": stats[0],
        "avg_bet": stats[1],
        "max_bet": stats[2],
        "min_bet": stats[3],
        "unique_markets": stats[4],
        "categories": stats[6] or "unknown",
        "wins": win_loss[0],
        "losses": win_loss[1],
        "unresolved": win_loss[2],
        "avg_win": win_loss[3],
        "avg_loss": win_loss[4],
        "total_pnl": win_loss[5],
        "avg_entry": win_loss[6],
        "best_hours": [{"hour": h[0], "trades": h[1], "avg_pnl": h[2]} for h in time_pattern],
        "entry_ranges": [{"range": r[0], "trades": r[1], "pnl": r[2], "avg_bet": r[3]} for r in entry_ranges],
        "top_markets": [{"title": m[0][:80], "pnl": m[1], "entry": m[2], "bet": m[3]} for m in markets],
    }


def analyze_whale_strategy(profile: dict) -> str:
    """Feed whale profile to uncensored LLM for strategy extraction."""
    resolved_wr = profile["wins"] / max(profile["wins"] + profile["losses"], 1)
    win_loss_ratio = abs(profile["avg_win"] / profile["avg_loss"]) if profile["avg_loss"] else 999
    
    prompt = f"""Analyze this Polymarket whale's trading strategy and output a structured JSON recipe card. No other text, just JSON.

WHALE: {profile["name"]}
Total trades: {profile["total_trades"]}
Resolved trades: {profile["wins"] + profile["losses"]} (W: {profile["wins"]} L: {profile["losses"]}, unresolved: {profile["unresolved"]})
Resolved win rate: {resolved_wr:.0%}
Avg win: ${profile["avg_win"]:.2f}  Avg loss: ${profile["avg_loss"]:.2f}  Win/Loss ratio: {win_loss_ratio:.2f}x
Total resolved PnL: ${profile["total_pnl"]:.2f}
Avg position: ${profile["avg_bet"]:.2f}  (range ${profile["min_bet"]:.2f} - ${profile["max_bet"]:.2f})
Unique markets: {profile["unique_markets"]}
Categories: {profile["categories"]}
Avg entry price: {profile["avg_entry"]}
Best hours: {profile["best_hours"]}
Entry ranges: {profile["entry_ranges"]}

TOP MARKETS (biggest PnL resolved trades):
{chr(10).join(f'- {m["title"]}: ${m["pnl"]:.2f} PnL at ${m["entry"]:.2f} entry' for m in profile["top_markets"][:5])}

OUTPUT FORMAT (JSON only):
{{
  "whale": "name",
  "strategy_type": "one of: longshot_specialist / favorite_bettor / arbitrage / momentum / halftimer / mixed",
  "edge_source": "what gives them the edge",
  "best_conditions": {{
    "entry_range": "price range where they win most",
    "market_types": ["preferred market types"],
    "timing": "best time/conditions to trade",
    "position_sizing": "how they size bets"
  }},
  "red_flags": "conditions where they lose",
  "actionable_recipe": "specific strategy to COPY in 1-2 sentences",
  "copy_confidence": 0.0-1.0
}}"""
    
    return query_llm(prompt)


def main():
    db = sqlite3.connect(DB_PATH)
    
    profiles = {}
    strategies = []
    
    for whale_name in TOP_WHALES:
        print(f"[deepdive] Building profile for {whale_name}...", flush=True)
        profile = build_whale_profile(db, whale_name)
        profiles[whale_name] = profile
        
        print(f"[deepdive] Analyzing {whale_name} strategy via LLM...", flush=True)
        result = analyze_whale_strategy(profile)
        
        # Try to parse JSON from the result
        import re
        json_match = re.search(r"\{.*\}", result, re.DOTALL)
        parsed = None
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        
        strategies.append({
            "whale": whale_name,
            "profile": profile,
            "llm_raw": result if not parsed else None,
            "strategy": parsed,
        })
        print(f"[deepdive] ✓ {whale_name} done", flush=True)
    
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "whales_analyzed": len(strategies),
        "strategies": strategies,
    }
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n[deepdive] Complete → {OUTPUT_PATH}", flush=True)
    
    # Print summary
    for s in strategies:
        if s["strategy"]:
            st = s["strategy"]
            print(f"\n{'='*60}")
            print(f"  {st.get('whale', s['whale'])}")
            print(f"  Type: {st.get('strategy_type', '?')}")
            print(f"  Edge: {st.get('edge_source', '?')}")
            print(f"  Recipe: {st.get('actionable_recipe', '?')}")
            print(f"  Confidence: {st.get('copy_confidence', '?')}")
        else:
            print(f"\n  {s['whale']}: FAILED to parse strategy JSON")
    
    db.close()


if __name__ == "__main__":
    main()
