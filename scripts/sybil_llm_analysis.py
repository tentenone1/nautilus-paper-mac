"""
Sybil LLM Strategy Analysis — feeds sybil historical trade data to local LLM
for pattern extraction and fade/follow recommendations.

Uses: 1700 llama-server (localhost:8080, Qwen3.6-35B-A3B IQ2_M, 32K ctx)
Input: research/sybil_positions.json + trades.db.backup
Output: research/sybil_llm_strategy.json
"""

import json
import logging
import os
import sqlite3
import urllib.request as ureq
from datetime import datetime, timezone

# Resolve relative to the script's location (works on both Mac and 1700)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NAUTILUS_ROOT = os.path.dirname(SCRIPT_DIR)  # goes up from scripts/ to nautilus-trading/
DB_PATH = os.path.join(NAUTILUS_ROOT, "research", "trades.db")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

from scripts.sybil_config import get_config

config = get_config()

LLM_URL = config.llm.url
LLM_MODEL = config.llm.model
LLM_TIMEOUT = config.llm.timeout

# DB_PATH is set above via relative path resolution

# Sybil wallet lists loaded from centralized config
_SYBIL_WALLETS_G1 = config.groups.get("sybil_group_1", None)
_SYBIL_WALLETS_G2 = config.groups.get("sybil_group_2", None)
_SYBIL_WALLETS_G3 = config.groups.get("sybil_group_3", None)


def _get_wallet_pseudonyms(group_def) -> list[str]:
    """Extract pseudonym list from a SybilGroupDef."""
    if group_def is None:
        return []
    return group_def.pseudonym_list()


def query_sybil_history(wallets: list[str]) -> dict:
    """Extract trade history for sybil wallets from backup DB."""
    if not os.path.exists(DB_PATH):
        logger.warning(f"Backup DB not found: {DB_PATH}")
        return {"error": "db_not_found", "wallets": wallets}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Use LIKE for flexible matching
    conditions = " OR ".join([f"whale_name LIKE ?" for _ in wallets])
    params = [f"%{w}%" for w in wallets]

    # Overall stats
    cursor.execute(f"""
        SELECT COUNT(*) as trades,
               ROUND(SUM(position_size_usd), 2) as total_volume,
               ROUND(AVG(position_size_usd), 2) as avg_bet,
               ROUND(SUM(CASE WHEN side='buy' THEN position_size_usd ELSE 0 END), 2) as buy_volume,
               ROUND(SUM(CASE WHEN side='sell' THEN position_size_usd ELSE 0 END), 2) as sell_volume,
               COUNT(DISTINCT condition_id) as markets,
               MIN(created_at) as first_trade,
               MAX(created_at) as last_trade
        FROM trades WHERE {conditions}
    """, params)
    row = cursor.fetchone()
    stats = {
        "trades": row[0] if row else 0,
        "total_volume": row[1] if row else 0,
        "avg_bet": row[2] if row else 0,
        "buy_volume": row[3] if row else 0,
        "sell_volume": row[4] if row else 0,
        "markets": row[5] if row else 0,
        "first_trade": row[6] if row else "",
        "last_trade": row[7] if row else "",
    }

    # Outcome distribution
    cursor.execute(f"""
        SELECT side, COUNT(*) as cnt, ROUND(SUM(position_size_usd), 2) as vol
        FROM trades WHERE {conditions}
        GROUP BY side
    """, params)
    stats["side_distribution"] = [
        {"side": r[0], "count": r[1], "volume": r[2]} for r in cursor.fetchall()
    ]

    # Market category distribution (handle column existence)
    try:
        cursor.execute(f"""
            SELECT market_category, COUNT(*) as cnt, ROUND(SUM(position_size_usd), 2) as vol
            FROM trades WHERE {conditions} AND market_category IS NOT NULL
            GROUP BY market_category
            ORDER BY vol DESC
            LIMIT 10
        """, params)
        stats["category_distribution"] = [
            {"category": r[0], "count": r[1], "volume": r[2]} for r in cursor.fetchall()
        ]
    except sqlite3.OperationalError:
        stats["category_distribution"] = []

    # Top 5 markets by volume
    cursor.execute(f"""
        SELECT market_title, COUNT(*) as cnt, ROUND(SUM(position_size_usd), 2) as vol
        FROM trades WHERE {conditions} AND market_title IS NOT NULL
        GROUP BY market_title
        ORDER BY vol DESC
        LIMIT 10
    """, params)
    stats["top_markets"] = [
        {"title": (r[0] or "unknown")[:80], "count": r[1], "volume": r[2]} for r in cursor.fetchall()
    ]

    # Win/loss for resolved trades
    cursor.execute(f"""
        SELECT 
            COUNT(CASE WHEN resolution_outcome='WON' THEN 1 END) as wins,
            COUNT(CASE WHEN resolution_outcome='LOST' THEN 1 END) as losses,
            ROUND(SUM(CASE WHEN resolution_outcome='WON' THEN COALESCE(actual_pnl, 0) ELSE 0 END), 2) as win_pnl,
            ROUND(SUM(CASE WHEN resolution_outcome='LOST' THEN COALESCE(actual_pnl, 0) ELSE 0 END), 2) as loss_pnl
        FROM trades WHERE {conditions} AND resolution_outcome IS NOT NULL
    """, params)
    row = cursor.fetchone()
    if row:
        stats["resolution"] = {
            "wins": row[0], "losses": row[1],
            "win_pnl": row[2], "loss_pnl": row[3],
        }

    conn.close()
    return stats


def call_llm(prompt: str) -> str:
    """Call local llama-server with reasoning suppression."""
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are a trading strategy analyst. Output ONLY valid JSON. No reasoning, no preamble."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.3,
        "reasoning_budget": 0,
        "reasoning_format": "none",
    }).encode()

    req = urllib.request.Request(
        LLM_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=LLM_TIMEOUT)
        data = json.loads(resp.read())
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return ""


def analyze_group(group_id: str, wallets: list[str]) -> dict:
    """Analyze a sybil group with the local LLM."""
    logger.info(f"Analyzing {group_id} ({len(wallets)} wallets)...")
    stats = query_sybil_history(wallets)

    prompt = f"""Analyze this sybil wallet group's trading history and recommend a strategy (FADE or FOLLOW) with reasoning.

Group: {group_id}
Wallets: {len(wallets)}

Statistics:
- Trades: {stats.get('trades', 0)}
- Total volume: ${stats.get('total_volume', 0):,.0f}
- Avg bet: ${stats.get('avg_bet', 0):,.0f}
- Buy volume: ${stats.get('buy_volume', 0):,.0f} / Sell volume: ${stats.get('sell_volume', 0):,.0f}
- Unique markets: {stats.get('markets', 0)}
- Active period: {stats.get('first_trade', '?')} to {stats.get('last_trade', '?')}

Side distribution: {json.dumps(stats.get('side_distribution', []))}
Category distribution: {json.dumps(stats.get('category_distribution', []))}
Top markets: {json.dumps(stats.get('top_markets', []))}
Resolution: {json.dumps(stats.get('resolution', {}))}

Output ONLY valid JSON in this format:
{{
    "group_id": "{group_id}",
    "strategy": "FADE or FOLLOW",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation",
    "bias": "YES-biased or NO-biased or balanced",
    "best_category": "category where they perform best",
    "worst_category": "category where they perform worst",
    "key_pattern": "notable behavioral pattern",
    "recommendation": "specific actionable recommendation"
}}"""

    response = call_llm(prompt)
    if not response:
        return {"error": "llm_failed", "stats": stats}

    # Extract JSON
    import re
    m = re.search(r"\{.*\}", response, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            result["stats"] = stats
            return result
        except json.JSONDecodeError:
            return {"error": "json_parse_failed", "raw": response[:500], "stats": stats}

    return {"error": "no_json_found", "raw": response[:500], "stats": stats}


def main():
    output_path = "/Users/tentenone/workspace/nautilus-trading/research/sybil_llm_strategy.json"

    logger.info("Starting sybil LLM strategy analysis...")
    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "groups": {},
    }

    for gid, group_def in [
        ("sybil_group_1", _SYBIL_WALLETS_G1),
        ("sybil_group_2", _SYBIL_WALLETS_G2),
        ("sybil_group_3", _SYBIL_WALLETS_G3),
    ]:
        wallets = _get_wallet_pseudonyms(group_def)
        result = analyze_group(gid, wallets)
        results["groups"][gid] = result
        logger.info(f"Group {gid} analysis complete")
        time.sleep(1)  # rate limit

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    # Print summary
    for gid, gdata in results["groups"].items():
        strat = gdata.get("strategy", "N/A")
        conf = gdata.get("confidence", 0)
        reason = gdata.get("reasoning", "")[:100]
        print(f"  {gid}: {strat} (conf={conf:.0%}) — {reason}")

    logger.info(f"Output: {output_path}")


if __name__ == "__main__":
    main()
