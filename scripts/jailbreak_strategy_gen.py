#!/usr/bin/env python3
"""Generate actionable trading strategies using uncensored reasoning model.

Runs weekly via cron. Uses 1700 local llama-server (port 8080).
Combines trades.db trade history with whale_profiles.json intelligence.
Output: research/jailbreak_strategies.json
"""
import json
import os
import sqlite3
import sys
import re
from datetime import datetime

API_URL = "http://localhost:8080/v1/chat/completions"
MODEL = "Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ2_M.gguf"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_DB = os.path.join(BASE_DIR, "research", "trades.db")
PROFILES_PATH = os.path.join(BASE_DIR, "research", "whale_profiles.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "research", "jailbreak_strategies.json")


def load_whale_profiles() -> dict:
    """Load whale profiles for classification + should_fade info."""
    if not os.path.exists(PROFILES_PATH):
        return {"profiles": []}
    with open(PROFILES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_whale_trade_data(db_path: str, min_trades: int = 5) -> list[dict]:
    """Get whales with trade history. Lowered threshold from 10 to 5."""
    if not os.path.exists(db_path):
        print(f"[WARN] trades.db not found at {db_path}", flush=True)
        return []

    conn = sqlite3.connect(db_path)
    whales = conn.execute("""
        SELECT whale_name, COUNT(*) as trades,
               AVG(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100 as win_rate_pct,
               SUM(position_size_usd) as volume,
               AVG(edge_score) as avg_edge,
               SUM(realized_pnl) as total_pnl
        FROM trades WHERE whale_name IS NOT NULL AND whale_name != "unknown_unnamed"
        GROUP BY whale_name HAVING trades >= ? ORDER BY total_pnl DESC
    """, (min_trades,)).fetchall()
    conn.close()

    return [
        {
            "name": w[0],
            "trades": w[1],
            "win_rate": round(w[2], 1) if w[2] else 0,
            "volume_usd": int(w[3]) if w[3] else 0,
            "edge": round(w[4], 2) if w[4] else 0,
            "pnl": int(w[5]) if w[5] else 0,
        }
        for w in whales
    ]


def get_profile_summary(profiles: dict) -> str:
    """Extract key classification info from whale profiles for LLM context."""
    lines = []
    for p in profiles.get("profiles", [])[:50]:
        stats = p.get("stats", {})
        profile = p.get("profile", {})
        name = stats.get("name", "unknown")
        classification = profile.get("classification", "unknown")
        trust = profile.get("trust_score", 0)
        should_fade = profile.get("should_fade", False)
        should_copy = profile.get("should_copy", False)
        reasoning = (profile.get("reasoning", "") or "")[:120]
        lines.append(
            f"  {name}: {classification} (trust={trust}, "
            f"copy={should_copy}, fade={should_fade}) — {reasoning}"
        )
    return "\n".join(lines)


def main():
    print(f"[jailbreak_strategy_gen] Starting at {datetime.now().isoformat()}", flush=True)

    # 1. Load trade data
    trade_whales = get_whale_trade_data(TRADES_DB, min_trades=5)
    print(f"[jailbreak_strategy_gen] {len(trade_whales)} whales with >=5 trades", flush=True)

    # 2. Load profile data
    profiles = load_whale_profiles()
    profile_summary = get_profile_summary(profiles)
    profile_count = len(profiles.get("profiles", []))
    print(f"[jailbreak_strategy_gen] {profile_count} profiles loaded", flush=True)

    # 3. Build combined context
    trade_json = json.dumps(trade_whales[:20], indent=2) if trade_whales else "[]"
    losers = json.dumps(trade_whales[-5:], indent=2) if len(trade_whales) >= 5 else "[]"

    prompt = f"""Analyze Polymarket whale trading patterns and classifications. Find exploitable edges.

TOP WHALES BY PNL (from trade history):
{trade_json}

WHALE PROFILES (classification + trust scores):
{profile_summary}

TASK: Generate 5 actionable trading strategies. Focus on:
1. Which whales to FOLLOW (high win rate, consistent timing, should_copy=True)
2. Which whales to FADE (should_fade=True, sacrificial accounts, low trust)
3. Timing patterns (when do winners enter?)
4. Market category edges (sports vs crypto vs politics)
5. Incorporate profile classifications into strategy

OUTPUT ONLY a JSON object with this structure:
{{
  "strategies": [
    {{"name": "...", "action": "FOLLOW|FADE", "target": "...", "confidence": 0.X}}
  ],
  "follow": ["whale1", "whale2"],
  "fade": ["whale3", "whale4"],
  "edge_signals": ["signal1", "signal2"]
}}

No reasoning in output. Just the JSON."""

    # 4. Call LLM
    try:
        import requests
        r = requests.post(API_URL, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 5000,
            "extra_body": {"reasoning_format": "none"},
        }, timeout=300)

        result = r.json()
        msg = result["choices"][0]["message"]
        raw = msg.get("content") or msg.get("reasoning_content") or ""

        # Strip thinking tags
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL)
        raw = re.sub(r'Thinking.*?(?:\n|$)', '', raw)

        # Extract JSON — find outermost braces
        depth = 0
        start = -1
        for i, ch in enumerate(raw):
            if ch == '{':
                if start == -1:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = raw[start:i+1]
                    if '"strategies"' in candidate:
                        try:
                            strategies = json.loads(candidate)
                            break
                        except json.JSONDecodeError:
                            pass
                    start = -1
        else:
            # Fallback: try broader match
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                try:
                    strategies = json.loads(json_match.group())
                except json.JSONDecodeError:
                    strategies = {"parse_error": "Could not parse JSON", "raw": raw[:500]}
            else:
                strategies = {"no_json": True, "raw": raw[:1000]}
    except Exception as e:
        print(f"[ERROR] LLM call failed: {e}", flush=True)
        strategies = {"error": str(e)}

    # 5. Save output
    output = {
        "generated": datetime.now().isoformat(),
        "whales_analyzed_trades": len(trade_whales),
        "profiles_loaded": profile_count,
        "strategies": strategies,
        "model": MODEL,
    }
    try:
        # Try to get token usage
        output["tokens_used"] = result.get("usage", {}).get("total_tokens", 0)  # type: ignore[possibly-undefined]
    except (UnboundLocalError, NameError, AttributeError):
        output["tokens_used"] = 0

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"[jailbreak_strategy_gen] Saved to {OUTPUT_PATH}", flush=True)
    print(json.dumps(strategies, indent=2)[:2000], flush=True)
    print(f"[jailbreak_strategy_gen] Done", flush=True)


if __name__ == "__main__":
    main()
