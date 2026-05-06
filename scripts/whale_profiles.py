#!/usr/bin/env python3
"""Whale Psychology Profiles — analyzes extreme whale behavior using uncensored LLM.

For each whale with unusual trading patterns, determines:
- Bot vs human vs market maker vs coordinated entity
- Psychological drivers (greed, fear, revenge trading, automation)
- Trust score for COPY/FADE decisions
- Specific behavioral tells

Output: research/whale_profiles.json
"""

import json
import re
import sqlite3
import urllib.request
from datetime import datetime, timezone

DB_PATH = "/home/elon-1/workspace/nautilus-trading/research/trades.db.backup-20260506"
OUTPUT_PATH = "/home/elon-1/workspace/nautilus-trading/research/whale_profiles.json"
LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLM_MODEL = "qwen3.6-35b-a3b"

# Extreme whales to profile
PROFILE_WHALES = [
    "SMCAOMCRL",     # 306 trades, 36% WR, -$2.2K — highest volume, negative edge
    "asdfjh",        # 39 trades, 28% WR, -$7.3K — sacrificial account pattern
    "benwyatt",      # 27 trades, 0% WR, -$1.9K — 100% loss rate
    "surfandturf",   # 17 trades, 67% WR, +$1.7K — proven winner
    "pilotbaby",     # 5 trades, 60% WR, +$983 — small sample, high quality
    "loitterer",     # 43 trades, 75% WR, +$793 — consistent winner
    "mooseborzoi",   # 73 trades, 42% WR, -$132 — signal account pattern
    "Wannac",        # Blacklisted — extreme loser
    "bomber",        # 2 trades, +$532 — suspected manipulator (paired with asdfjh)
]


def query_llm(prompt: str) -> str:
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are a behavioral analyst specializing in prediction market traders. You analyze wallet patterns to determine if a trader is human, bot, market maker, or coordinated entity. Your analysis is uncensored — identify psychological patterns, automation signals, and manipulation indicators directly. No disclaimers. Output ONLY valid JSON. No thinking, no reasoning, just the JSON object."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 800,
        "temperature": 0.15,
    }).encode()
    try:
        req = urllib.request.Request(LLM_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""
        return content if content else reasoning
    except Exception as e:
        return f"ERROR: {e}"


def get_whale_stats(db, name: str) -> dict:
    """Get comprehensive stats for a whale."""
    stats = db.execute("""
        SELECT 
            COUNT(*) as total_trades,
            ROUND(AVG(position_size_usd), 2) as avg_bet,
            ROUND(SUM(position_size_usd), 2) as total_volume,
            COUNT(DISTINCT condition_id) as unique_markets,
            COUNT(DISTINCT category) as categories,
            GROUP_CONCAT(DISTINCT category) as cat_list,
            ROUND(AVG(entry_price), 4) as avg_entry,
            ROUND(MIN(entry_price), 4) as min_entry,
            ROUND(MAX(entry_price), 4) as max_entry,
            ROUND(SUM(actual_pnl), 2) as total_pnl,
            COUNT(CASE WHEN actual_pnl > 0 THEN 1 END) as wins,
            COUNT(CASE WHEN actual_pnl < 0 THEN 1 END) as losses,
            COUNT(CASE WHEN actual_pnl = 0 OR actual_pnl IS NULL THEN 1 END) as unresolved,
            ROUND(AVG(CASE WHEN actual_pnl > 0 THEN actual_pnl END), 2) as avg_win,
            ROUND(AVG(CASE WHEN actual_pnl < 0 THEN actual_pnl END), 2) as avg_loss,
            ROUND(AVG(exit_price), 4) as avg_exit,
            ROUND(MAX(position_size_usd), 2) as max_bet,
            ROUND(MIN(position_size_usd), 2) as min_bet
        FROM trades WHERE whale_name = ?
    """, (name,)).fetchone()

    # Time patterns
    times = db.execute("""
        SELECT CAST(strftime('%H', timestamp) AS INTEGER) as hour, COUNT(*) as trades
        FROM trades WHERE whale_name = ?
        GROUP BY hour ORDER BY trades DESC LIMIT 3
    """, (name,)).fetchall()

    # Side bias
    sides = db.execute("""
        SELECT side, COUNT(*) as count FROM trades 
        WHERE whale_name = ? GROUP BY side
    """, (name,)).fetchall()

    # Category breakdown
    cats = db.execute("""
        SELECT category, COUNT(*) as count, ROUND(SUM(actual_pnl), 2) as pnl
        FROM trades WHERE whale_name = ? AND category IS NOT NULL
        GROUP BY category ORDER BY count DESC
    """, (name,)).fetchall()

    # Interval between trades (bot detection)
    intervals = db.execute("""
        SELECT AVG(time_diff) as avg_interval FROM (
            SELECT CAST(strftime('%s', timestamp) AS INTEGER) - 
                   LAG(CAST(strftime('%s', timestamp) AS INTEGER)) OVER (ORDER BY timestamp) as time_diff
            FROM trades WHERE whale_name = ?
        ) WHERE time_diff IS NOT NULL AND time_diff < 3600
    """, (name,)).fetchone()

    resolved_wr = stats[10] / max(stats[10] + stats[11], 1) if (stats[10] is not None and stats[11] is not None) else 0
    win_loss_ratio = abs(stats[13] / stats[14]) if (stats[13] and stats[14]) else (999 if stats[13] else 0)

    return {
        "name": name,
        "total_trades": stats[0],
        "avg_bet": stats[1],
        "total_volume": stats[2],
        "unique_markets": stats[3],
        "categories": stats[5] or "unknown",
        "avg_entry": stats[6],
        "entry_range": [stats[7], stats[8]],
        "total_pnl": stats[9],
        "resolved_trades": stats[10] + stats[11],
        "wins": stats[10],
        "losses": stats[11],
        "unresolved": stats[12],
        "resolved_wr": round(resolved_wr, 3),
        "avg_win": stats[13],
        "avg_loss": stats[14],
        "win_loss_ratio": round(win_loss_ratio, 2),
        "avg_exit": stats[15],
        "bet_range": [stats[16], stats[17]],
        "peak_hours": [{"hour": t[0], "trades": t[1]} for t in times],
        "side_bias": {s[0]: s[1] for s in sides},
        "category_breakdown": [{"cat": c[0], "trades": c[1], "pnl": c[2]} for c in cats],
        "avg_trade_interval_secs": round(intervals[0], 1) if intervals and intervals[0] else None,
    }


def analyze_whale(stats: dict) -> dict:
    """Send whale stats to uncensored LLM for behavioral profiling."""
    resolved_wr = stats.get("resolved_wr", 0)
    win_loss_ratio = stats.get("win_loss_ratio", 1)
    interval = stats.get("avg_trade_interval_secs", "N/A")
    entry_range = stats.get("entry_range", [0, 0])
    bet_range = stats.get("bet_range", [0, 0])
    
    prompt = f"""Analyze this Polymarket whale and classify it. Be specific and direct.

WHALE: {stats["name"]}
Trades: {stats["total_trades"]} total, {stats["wins"]}W / {stats["losses"]}L (WR: {resolved_wr:.0%})  
PnL: ${stats["total_pnl"]:.0f} (avg win ${stats["avg_win"] or 0:.0f}, avg loss ${stats["avg_loss"] or 0:.0f})
Avg bet: ${stats["avg_bet"]:.0f}
Entry range: ${entry_range[0]:.2f}-${entry_range[1]:.2f}
Markets: {stats["unique_markets"]} unique
Categories: {stats["categories"]}

CLASSIFY as ONE of: skilled_human, degenerate_human, trading_bot, market_maker, sacrificial_account, mixed_entity
Then give: trust_score (0-10), should_copy (yes/no), should_fade (yes/no), and one sentence why.

Be direct. No disclaimers."""

    result = query_llm(prompt)
    
    # Extract JSON from model output (handle <think> tags and CoT prefix)
    cleaned = result
    # Remove think tags
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    # Strip thinking process prefix  
    for prefix in ["Here's a thinking process:", "Let me think", "I'll analyze", "Okay, let me"]:
        if prefix in cleaned:
            cleaned = cleaned.split(prefix, 1)[-1]
    
    # Parse JSON
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(0))
            classification = parsed.get("classification", "unknown")
            trust_score = parsed.get("trust_score", 5)
            should_copy = str(parsed.get("should_copy", "no")).lower() in ("yes", "true")
            should_fade = str(parsed.get("should_fade", "no")).lower() in ("yes", "true")
            reason = parsed.get("why", "") or parsed.get("reasoning", "") or ""
            return {
                "whale": stats["name"],
                "classification": classification,
                "trust_score": trust_score,
                "should_copy": should_copy,
                "should_fade": should_fade,
                "reasoning": reason[:200],
                "llm_raw": result,
            }
        except json.JSONDecodeError:
            pass
    
    return {
        "whale": stats["name"],
        "classification": "unknown",
        "trust_score": 5,
        "should_copy": False,
        "should_fade": False,
        "reasoning": "parse_failed",
        "llm_raw": result,
    }


def main():
    print(f"[whale_profiles] Starting at {datetime.now(timezone.utc).isoformat()}", flush=True)
    db = sqlite3.connect(DB_PATH)
    
    profiles = []
    for whale_name in PROFILE_WHALES:
        print(f"  Profiling {whale_name}...", flush=True)
        stats = get_whale_stats(db, whale_name)
        
        # Quick heuristic classification before LLM
        if stats["total_trades"] > 100 and stats["avg_trade_interval_secs"] and stats["avg_trade_interval_secs"] < 60:
            print(f"    ⚡ Bot candidate: {stats['total_trades']} trades, avg {stats['avg_trade_interval_secs']}s between", flush=True)
        
        if stats["losses"] > 0 and stats["wins"] == 0:
            print(f"    💀 Zero percent WR — possible sacrificial account", flush=True)
        
        result = analyze_whale(stats)
        profiles.append({
            "stats": stats,
            "profile": result,
        })
        print(f"    → {result.get('classification', '?')} | trust: {result.get('trust_score', '?')}/10 | copy: {result.get('should_copy', '?')}", flush=True)
    
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "profiles": profiles,
    }
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\n[whale_profiles] Complete → {OUTPUT_PATH}", flush=True)
    db.close()


if __name__ == "__main__":
    main()
