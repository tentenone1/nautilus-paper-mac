#!/usr/bin/env python3
"""
Whale Category Expansion v3 — discovers whales in crypto, politics, and geopolitics.

Strategy:
  1. Fetch non-sports markets from Gamma API (tags: crypto, politics, geopolitics)
  2. Build a lookup map: conditionId → category tag
  3. For each existing whale, query their trades to find non-sports activity
  4. For undiscovered whales, scan non-sports market trades for new addresses
  5. Score candidates via Qwen3.6-Plus
  6. Write new/updated whales to whale_discovery.db

Uses only WORKING Polymarket API endpoints:
  - Gamma API: /markets?tag=...&closed=false  (category-based market discovery)
  - Data API:  /v1/trades?user=...              (per-wallet trade history)
  - Data API:  /v1/trades?conditionId=...       (per-market trade activity)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone

import requests
from strategies.whale_tiering import WhaleTiering

# ---------------------------------------------------------------------------
# Paths & Constants
# ---------------------------------------------------------------------------
BASE = Path(__file__).resolve().parents[1]
DB_PATH = BASE / "pipeline" / "data" / "whale_discovery.db"

ENV_PATH = Path("/opt/data/.env")

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

DASHSCOPE_URL = "https://coding.dashscope.aliyuncs.com/v1/chat/completions"
DASHSCOPE_MODEL = "qwen3.6-plus"

CATEGORIES = ["crypto", "politics", "geopolitics"]

MIN_TRADE_VOL = 500       # $500 minimum estimated trade volume for a new whale
LLM_BATCH = 50
RATE_LIMIT = 0.5
WHALE_TIERING = WhaleTiering()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Read DASHSCOPE_API_KEY from /opt/data/.env."""
    if ENV_PATH.exists():
        text = ENV_PATH.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("DASHSCOPE_API_KEY=") and "=" in line:
                key = line.split("=", 1)[1].strip().strip("'\"")
                if key:
                    return key
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        print("ERROR: DASHSCOPE_API_KEY not found", file=sys.stderr)
        sys.exit(1)
    return key


def ensure_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whales (
            address TEXT PRIMARY KEY,
            name TEXT,
            alpha_score REAL DEFAULT 0,
            pnl REAL DEFAULT 0,
            volume REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            last_seen TEXT,
            tags TEXT DEFAULT '[]',
            discovered_at TEXT,
            updated_at TEXT,
            market_category TEXT DEFAULT 'unknown'
        )
    """)
    try:
        conn.execute("ALTER TABLE whales ADD COLUMN source TEXT DEFAULT 'leaderboard'")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def get_existing_addresses(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT address FROM whales").fetchall()
    return {r[0].lower() for r in rows}


def rate_limit(last_call: list[float]):
    if last_call:
        elapsed = time.time() - last_call[0]
        if elapsed < RATE_LIMIT:
            time.sleep(RATE_LIMIT - elapsed)
    last_call[0] = time.time()


# ---------------------------------------------------------------------------
# Phase 1: Market Discovery & ConditionId → Category Map
# ---------------------------------------------------------------------------

def build_category_map(categories: list[str]) -> dict[str, str]:
    """Query Gamma API for non-sports markets, return {conditionId: category}."""
    cid_map: dict[str, str] = {}
    for tag in categories:
        url = f"{GAMMA_API}/markets?tag={tag}&closed=false&limit=100"
        try:
            print(f"  [{tag}] Fetching markets from Gamma API...")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            markets = resp.json()
            if isinstance(markets, list):
                for m in markets:
                    cid = m.get("conditionId", "")
                    if cid:
                        cid_map[cid] = tag
                print(f"  [{tag}] Found {len(markets)} markets")
            else:
                print(f"  [{tag}] Unexpected response (not a list)")
        except Exception as e:
            print(f"  [{tag}] ERROR: {e}")
    print(f"  Total non-sports conditionIds in map: {len(cid_map)}")
    return cid_map


# ---------------------------------------------------------------------------
# Phase 2a: Reclassify existing whales via trade analysis
# ---------------------------------------------------------------------------

def classify_existing_whales(
    conn: sqlite3.Connection,
    existing: set[str],
    cid_map: dict[str, str],
) -> dict[str, str]:
    """Query trades for each existing whale. If non-sports activity found, reclassify."""
    print(f"\nPhase 2a: Analyzing trade categories for {len(existing)} existing whales...")
    updated: dict[str, str] = {}
    last_call = [0.0]
    now = datetime.now(timezone.utc).isoformat()

    # Get current categories
    current = {}
    rows = conn.execute("SELECT address, market_category FROM whales").fetchall()
    for addr, cat in rows:
        current[addr.lower()] = cat or "unknown"

    # How many already non-sports
    already_non_sports = sum(1 for c in current.values() if c in CATEGORIES)

    for i, addr in enumerate(existing):
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  Progress: {i+1}/{len(existing)}")

        # Skip if already non-sports
        if current.get(addr, "unknown") in CATEGORIES:
            continue

        url = f"{DATA_API}/v1/trades?user={addr}&limit=200"
        try:
            rate_limit(last_call)
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            trades = resp.json()
        except Exception:
            continue

        if not isinstance(trades, list) or not trades:
            continue

        # Check if any trades hit non-sports markets
        seen_categories = set()
        for t in trades:
            cid = t.get("conditionId", "")
            if cid in cid_map:
                seen_categories.add(cid_map[cid])

        if seen_categories:
            primary = max(seen_categories, key=len)  # longest tag name = most specific
            updated[addr] = primary
            conn.execute(
                "UPDATE whales SET market_category = ?, source = 'reclassified', updated_at = ? WHERE address = ?",
                (primary, now, addr),
            )

    conn.commit()
    print(f"  Reclassified {len(updated)} existing whales to non-sports categories")
    print(f"  Total non-sports whales now: {already_non_sports + len(updated)}")
    return updated


# ---------------------------------------------------------------------------
# Phase 2b: Discover new whales from non-sports market trades
# ---------------------------------------------------------------------------

def discover_new_whales(
    cid_map: dict[str, str],
    existing: set[str],
) -> list[dict]:
    """Scan each non-sports market for active traders not in the DB."""
    print(f"\nPhase 2b: Scanning {len(cid_map)} non-sports markets for new whales...")
    wallet_data: dict[str, dict] = {}
    last_call = [0.0]

    cid_list = list(cid_map.items())
    for i, (cid, category) in enumerate(cid_list):
        if (i + 1) % 30 == 0 or i == 0:
            print(f"  Progress: {i+1}/{len(cid_list)} markets")

        url = f"{DATA_API}/v1/trades?conditionId={cid}&limit=200"
        try:
            rate_limit(last_call)
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            trades = resp.json()
        except Exception:
            continue

        if not isinstance(trades, list):
            continue

        for t in trades:
            addr = t.get("proxyWallet", "").lower()
            if not addr or addr in existing:
                continue

            size = float(t.get("size", 0))
            price = float(t.get("price", 0))
            vol = size * price

            if addr not in wallet_data:
                wallet_data[addr] = {
                    "address": addr,
                    "categories": set(),
                    "total_volume": 0.0,
                    "market_count": 0,
                    "trade_count": 0,
                }
            wallet_data[addr]["categories"].add(category)
            wallet_data[addr]["total_volume"] += vol
            wallet_data[addr]["market_count"] += 1
            wallet_data[addr]["trade_count"] = wallet_data[addr].get("trade_count", 0) + 1

    # Filter for meaningful candidates
    candidates = []
    for addr, info in sorted(wallet_data.items(), key=lambda x: x[1]["total_volume"], reverse=True):
        if info["total_volume"] >= MIN_TRADE_VOL:
            candidates.append({
                "address": addr,
                "category": max(info["categories"], key=len),
                "total_volume_est": round(info["total_volume"], 2),
                "market_count": info["market_count"],
                "trade_count": info.get("trade_count", 0),
            })

    print(f"  Found {len(candidates)} new whale candidates (vol >= ${MIN_TRADE_VOL})")
    return candidates


# ---------------------------------------------------------------------------
# Phase 3: Aggregated Trade Analysis
# ---------------------------------------------------------------------------

def analyze_wallet_trades(candidates: list[dict], cid_map: dict[str, str]) -> list[dict]:
    """Deep-dive each candidate wallet: get all trades and compute stats."""
    profiles = []
    last_call = [0.0]
    total = len(candidates)

    print(f"\nPhase 3: Analyzing {total} candidates' full trade history...")
    for i, cand in enumerate(candidates):
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  Progress: {i+1}/{total}")

        addr = cand["address"]
        url = f"{DATA_API}/v1/trades?user={addr}&limit=500"
        try:
            rate_limit(last_call)
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            trades = resp.json()
        except Exception:
            continue

        if not isinstance(trades, list) or not trades:
            continue

        total_vol = 0.0
        buys = 0
        sells = 0
        non_sports_vol = 0.0
        wins = 0
        losses = 0
        found_categories = set()

        for t in trades:
            size = float(t.get("size", 0))
            price = float(t.get("price", 0))
            vol = size * price
            total_vol += vol

            side = t.get("side", "")
            if side == "BUY":
                buys += 1
            elif side == "SELL":
                sells += 1

            # Simple P&L heuristic: if they traded both sides, assume profitable
            # For now, just count directional trades
            if vol * price > 100:
                wins += 1
            else:
                losses += 1

            cid = t.get("conditionId", "")
            if cid in cid_map:
                cat = cid_map[cid]
                found_categories.add(cat)
                non_sports_vol += vol

        total_trades = len(trades)
        win_rate = wins / max(total_trades, 1)

        if non_sports_vol < MIN_TRADE_VOL / 2:
            continue

        profiles.append({
            "address": addr,
            "total_volume": round(total_vol, 2),
            "non_sports_volume": round(non_sports_vol, 2),
            "total_trades": total_trades,
            "buys": buys,
            "sells": sells,
            "win_rate": round(win_rate, 3),
            "categories_seen": list(found_categories),
            "category": max(found_categories, key=len) if found_categories else cand["category"],
        })

    print(f"  {len(profiles)} candidates have confirmed non-sports trading activity")
    return profiles


# ---------------------------------------------------------------------------
# Phase 4: LLM Scoring
# ---------------------------------------------------------------------------

def build_scoring_prompt(batch: list[dict]) -> str:
    candidates_json = json.dumps([
        {
            "address": c["address"],
            "total_volume_usd": c["total_volume"],
            "non_sports_volume": c["non_sports_volume"],
            "total_trades": c["total_trades"],
            "buys": c["buys"],
            "sells": c["sells"],
            "win_rate": c["win_rate"],
            "categories": c["categories_seen"],
        }
        for c in batch
    ], indent=2)

    return (
        "You are a Polymarket whale analyst. Score these trader profiles on a 0-100 scale based on:\n"
        "- Win rate (estimated, weight: 30%)\n"
        "- Non-sports trading volume (weight: 30%)\n"
        "- Category specialization (crypto/politics/geopolitics focus, weight: 25%)\n"
        "- Total activity level (weight: 15%)\n\n"
        "Return ONLY a JSON array of objects with keys: "
        '"address", "name", "score", "category", "summary". '
        "Score 70+ = strong whale candidate. Score < 30 = noise.\n\n"
        f"Candidates:\n{candidates_json}"
    )


def extract_json_array(text: str) -> list[dict]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    match = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return []


def score_candidates(candidates: list[dict], api_key: str) -> tuple[list[dict], int]:
    all_scored = []
    total_tokens = 0

    for i in range(0, len(candidates), LLM_BATCH):
        batch = candidates[i:i + LLM_BATCH]
        batch_num = i // LLM_BATCH + 1
        total_batches = (len(candidates) + LLM_BATCH - 1) // LLM_BATCH
        print(f"  Scoring batch {batch_num}/{total_batches} ({len(batch)} candidates)...")

        prompt = build_scoring_prompt(batch)
        try:
            resp = requests.post(
                DASHSCOPE_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": DASHSCOPE_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4000,
                },
                timeout=300,
            )
            resp.raise_for_status()
            result = resp.json()
            usage = result.get("usage", {})
            tokens = usage.get("total_tokens", 0)
            total_tokens += tokens
            content = result["choices"][0]["message"]["content"]
            scored = extract_json_array(content)
            if not scored:
                print(f"  WARNING: Failed to parse LLM response for batch {batch_num}")
                print(f"  Response: {content[:200]}")
                continue
            all_scored.extend(scored)
            print(f"  -> {len(scored)} scored, ~{tokens} tokens")
        except Exception as e:
            print(f"  ERROR scoring batch {batch_num}: {e}")

    return all_scored, total_tokens


# ---------------------------------------------------------------------------
# Phase 5: DB Write
# ---------------------------------------------------------------------------

def write_whales(conn: sqlite3.Connection, scored: list[dict]) -> dict:
    """Write scored whales to DB. Returns counts of new vs updated."""
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    update_count = 0

    for whale in scored:
        addr = whale.get("address", "").lower()
        if not addr:
            continue

        existing = conn.execute("SELECT 1 FROM whales WHERE address = ?", (addr,)).fetchone()
        if existing:
            new_score = float(whale.get("score", 0))
            old = conn.execute("SELECT alpha_score FROM whales WHERE address = ?", (addr,)).fetchone()
            if old and new_score > old[0]:
                conn.execute("""
                    UPDATE whales SET alpha_score = ?, name = COALESCE(?, name),
                    market_category = ?, source = 'category_expansion', updated_at = ?
                    WHERE address = ?
                """, (new_score, whale.get("name"), whale.get("category", "unknown"), now, addr))
                update_count += 1
            continue

        name = whale.get("name", addr[:10])
        score = float(whale.get("score", 0))
        category = whale.get("category", "unknown")
        capital_tier = WHALE_TIERING.classify_capital(whale.get("estimated_volume", 0))
        precision_tier = WHALE_TIERING.classify_precision(whale.get("estimated_win_rate", 0.5))
        conn.execute("""
            INSERT OR IGNORE INTO whales (address, name, alpha_score, pnl, volume, win_rate,
                total_trades, last_seen, source, market_category, tags, discovered_at, updated_at,
                capital_tier, precision_tier)
            VALUES (?, ?, ?, 0, 0, 0, 0, ?, 'category_expansion', ?, '[]', ?, ?, ?, ?)
        """, (addr, name, score, now, category, now, now, capital_tier, precision_tier))
        new_count += 1

    conn.commit()
    return {"new": new_count, "updated": update_count}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(" Whale Category Expansion v3")
    print("=" * 60)
    start = time.time()

    api_key = get_api_key()
    conn = ensure_db()
    existing = get_existing_addresses(conn)
    print(f"[DB] {len(existing)} existing whales")
    print(f"[DB] Path: {DB_PATH}")
    print(f"[API] Model: {DASHSCOPE_MODEL}")

    # Phase 1: Build conditionId → category map
    print(f"\nPhase 1: Fetching non-sports markets...")
    cid_map = build_category_map(CATEGORIES)
    if not cid_map:
        print("ERROR: No non-sports markets found. Check API.")
        sys.exit(1)

    # Phase 2a: Reclassify existing whales
    reclassified = classify_existing_whales(conn, existing, cid_map)

    # Phase 2b: Discover new whales
    new_candidates = discover_new_whales(cid_map, existing)

    if not reclassified and not new_candidates:
        print("\nNo category changes or new whales found. Exiting.")
        conn.close()
        sys.exit(0)

    # Phase 3: Deep analysis
    all_candidates = [{"address": a, "category": c} for a, c in reclassified.items()]
    all_candidates.extend(new_candidates)

    profiles = analyze_wallet_trades(all_candidates, cid_map)

    if not profiles:
        print("\nNo candidates with sufficient non-sports activity. Exiting.")
        conn.close()
        sys.exit(0)

    # Phase 4: LLM scoring
    print(f"\nPhase 4: Scoring {len(profiles)} profiles...")
    scored, total_tokens = score_candidates(profiles, api_key)
    print(f"  Scored: {len(scored)}, ~{total_tokens} tokens")

    if not scored:
        print("No whales scored.")
        conn.close()
        sys.exit(0)

    # Phase 5: DB write
    print(f"\nPhase 5: Writing to database...")
    counts = write_whales(conn, scored)

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f" Pipeline Complete in {elapsed:.0f}s")
    print(f"  Reclassified existing: {len(reclassified)}")
    print(f"  New candidates found:  {len(new_candidates)}")
    print(f"  Deep-analyzed:         {len(profiles)}")
    print(f"  LLM scored:            {len(scored)}")
    print(f"  DB new:                {counts['new']}")
    print(f"  DB updated:            {counts['updated']}")
    print(f"{'=' * 60}")

    conn.close()


if __name__ == "__main__":
    main()
