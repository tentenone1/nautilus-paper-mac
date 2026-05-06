#!/usr/bin/env python3
"""Jailbreak Whale Monitor — tracks COPY/FADE whale trades from jailbreak analysis.

Queries Polymarket data API for each tracked whale's recent trades and
outputs actionable alerts when new positions are detected.

Output: ~/workspace/nautilus-trading/research/jailbreak_whale_alerts.json
Schedule: every 30 minutes
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone

# ── Tracked Whales ──────────────────────────────────────────────────────────
# Sources: jailbreak_analysis.py (COPY) + jailbreak_strategy_gen.py (Follow)

COPY_WHALES = {
    "RJW1": "0x85f031d069de300055900c4055c1baeb6bde3f67",
    "surfandturf": "0x9f2fe025f84839ca81dd8e0338892605702d2ca8",
    "matanovik": "0x39d3c773be30fcc73161fc6768f46d563a779ef0",
    "p150-0xba389f": "0xba389f76b0119aed07c53c9029852664bd97e406",
    "pilotbaby": "0x6815040a7176c958e6ff8818bfe188e80dbd9edb",
    "Countryside": "0xbddf61af533ff524d27154e589d2d7a81510c684",
}

FADE_WHALES = {
    "asdfjh": "0x0eb568f307e9a48af2c3e688ad6074236712c494",
    "SMCAOMCRL": "0x3b5c629f114098b0dee345fb78b7a3a013c7126e",
    "benwyatt": "0x1117eade222413335b7ec959e5b48c1d3dbc3532",
    "JPMorgan101": "0xb6d6e99d3bfe055874a04279f659f009fd57be17",
    "bossoskil1": "0xa5ea13a81d2b7e8e424b182bdc1db08e756bd96a",
    "trade-via-Gravia": "0xe48109602719f95c247fec255ffb71bab3f985a3",
}

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "research", "jailbreak_whale_state.json")
ALERTS_FILE = os.path.join(BASE_DIR, "research", "jailbreak_whale_alerts.json")
API_BASE = "https://data-api.polymarket.com/v1"
LOOKBACK_HOURS = 6  # max age for "recent" trades


def fetch_trades(wallet: str, limit: int = 10) -> list[dict]:
    """Fetch recent trades for a wallet from Polymarket data API."""
    url = f"{API_BASE}/trades?user={wallet}&limit={limit}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return [{"error": str(e)}]


def load_state() -> dict:
    """Load last-seen trade state from file."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    """Persist last-seen trade timestamps."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def timestamp_to_iso(ts: int) -> str:
    """Convert Unix timestamp to ISO string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def main():
    state = load_state()
    now = time.time()
    alerts = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "copy_signals": [],
        "fade_signals": [],
    }

    # ── Check COPY whales ──────────────────────────────────────────────
    for name, wallet in COPY_WHALES.items():
        trades = fetch_trades(wallet)
        if isinstance(trades, list) and len(trades) > 0 and "error" not in trades[0]:
            last_seen = state.get(name, 0)
            new_trades = []
            for t in trades:
                ts = t.get("timestamp", 0)
                if ts > last_seen and (now - ts) < LOOKBACK_HOURS * 3600:
                    new_trades.append({
                        "title": t.get("title", "Unknown"),
                        "slug": t.get("slug", ""),
                        "side": t.get("side", ""),
                        "price": t.get("price", 0),
                        "size": t.get("size", 0),
                        "usd_value": round(t.get("size", 0) * t.get("price", 0), 2),
                        "timestamp": timestamp_to_iso(ts),
                        "ts_unix": ts,
                    })
            if new_trades:
                # Update last seen to most recent trade
                max_ts = max(t["ts_unix"] for t in new_trades)
                state[name] = max_ts
                alerts["copy_signals"].append({
                    "whale": name,
                    "wallet": wallet,
                    "type": "COPY",
                    "trades": new_trades,
                })
        elif isinstance(trades, list) and len(trades) > 0 and "error" in trades[0]:
            # API error, don't update state
            pass
        time.sleep(0.3)  # rate limit

    # ── Check FADE whales ──────────────────────────────────────────────
    for name, wallet in FADE_WHALES.items():
        trades = fetch_trades(wallet)
        if isinstance(trades, list) and len(trades) > 0 and "error" not in trades[0]:
            last_seen = state.get(name, 0)
            new_trades = []
            for t in trades:
                ts = t.get("timestamp", 0)
                if ts > last_seen and (now - ts) < LOOKBACK_HOURS * 3600:
                    new_trades.append({
                        "title": t.get("title", "Unknown"),
                        "slug": t.get("slug", ""),
                        "side": t.get("side", ""),
                        "price": t.get("price", 0),
                        "size": t.get("size", 0),
                        "usd_value": round(t.get("size", 0) * t.get("price", 0), 2),
                        "timestamp": timestamp_to_iso(ts),
                        "ts_unix": ts,
                    })
            if new_trades:
                max_ts = max(t["ts_unix"] for t in new_trades)
                state[name] = max_ts
                alerts["fade_signals"].append({
                    "whale": name,
                    "wallet": wallet,
                    "type": "FADE",
                    "trades": new_trades,
                })
        elif isinstance(trades, list) and len(trades) > 0 and "error" in trades[0]:
            pass
        time.sleep(0.3)

    save_state(state)

    # ── Write alerts file ──────────────────────────────────────────────
    os.makedirs(os.path.dirname(ALERTS_FILE), exist_ok=True)
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

    # ── Print summary ──────────────────────────────────────────────────
    copy_count = len(alerts["copy_signals"])
    fade_count = len(alerts["fade_signals"])
    total_new = sum(len(s["trades"]) for s in alerts["copy_signals"]) + \
                sum(len(s["trades"]) for s in alerts["fade_signals"])
    print(f"[jailbreak-whale-monitor] {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"  COPY whales with new trades: {copy_count}")
    print(f"  FADE whales with new trades: {fade_count}")
    print(f"  Total new trades detected: {total_new}")

    for s in alerts["copy_signals"]:
        for t in s["trades"]:
            print(f"  COPY {s['whale']}: {t['side']} ${t['usd_value']:.0f} {t['title']} @ ${t['price']:.4f}")
    for s in alerts["fade_signals"]:
        for t in s["trades"]:
            print(f"  FADE {s['whale']}: {t['side']} ${t['usd_value']:.0f} {t['title']} @ ${t['price']:.4f}")

    return alerts


if __name__ == "__main__":
    main()
