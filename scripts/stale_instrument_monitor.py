#!/usr/bin/env python3
"""Monitor stale instrument warnings in paper_trading.log.

Usage:
    python3 stale_instrument_monitor.py                    # Quick summary
    python3 stale_instrument_monitor.py --top 20           # Show top N stale IDs
    python3 stale_instrument_monitor.py --check-active     # Check each ID on Polymarket
"""

import argparse
import gzip
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
PAPER_LOG = LOG_DIR / "paper_trading.log"

WARN_PATTERN = re.compile(
    r"Cannot find instrument for "
    r"(0x[a-f0-9]+-\d+)\.POLYMARKET"
)


def parse_log(filepath: Path, max_lines: int = 500_000) -> Counter:
    """Parse a log file (possibly gzipped) for stale instrument IDs."""
    counter = Counter()
    lines_checked = 0

    open_fn = gzip.open if filepath.suffix == ".gz" else open

    try:
        with open_fn(filepath, "rt", errors="replace") as f:
            for line in f:
                m = WARN_PATTERN.search(line)
                if m:
                    counter[m.group(1)] += 1
                lines_checked += 1
                if lines_checked >= max_lines and max_lines > 0:
                    break
    except FileNotFoundError:
        pass

    return counter


def check_market_active(token_pair: str) -> bool | None:
    """Check if a token's market is active on Polymarket."""
    # token_pair is "0x<token_id>-<negative_id>"
    token_id = token_pair.split("-")[0]
    try:
        # Use CLOB API to check market by token
        result = subprocess.run(
            ["curl", "-s", "-m", "10",
             f"https://clob.polymarket.com/markets?token_id={token_id}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            if isinstance(data, list):
                return any(m.get("active") for m in data)
            return data.get("active", None) if isinstance(data, dict) else None
    except Exception:
        return None
    return None


def main():
    parser = argparse.ArgumentParser(description="Monitor stale instrument warnings")
    parser.add_argument("--top", type=int, default=10,
                        help="Show top N stale instrument IDs (default: 10)")
    parser.add_argument("--check-active", action="store_true",
                        help="Check if stale markets are still active on Polymarket")
    parser.add_argument("--max-lines", type=int, default=100_000,
                        help="Max lines to scan from current log (default: 100k)")
    args = parser.parse_args()

    # Scan current log
    current_counter = parse_log(PAPER_LOG, max_lines=args.max_lines)

    # Also scan rotated logs
    rotated_counter = Counter()
    for f in sorted(LOG_DIR.glob("paper_trading.log.*.gz")):
        rotated_counter += parse_log(f, max_lines=0)  # no limit on old logs

    total_counter = current_counter + rotated_counter

    if not total_counter:
        print("✅ No stale instrument warnings found.")
        return

    print(f"\n📊 Stale Instrument Report")
    print(f"{'='*60}")
    print(f"  Current log warnings: {sum(current_counter.values()):,}")
    print(f"  Rotated log warnings: {sum(rotated_counter.values()):,}")
    print(f"  Total:                {sum(total_counter.values()):,}")
    print(f"  Unique instrument IDs: {len(total_counter):,}")
    print()

    if args.check_active:
        print(f"{'ID':<5} {'Count':>10} {'Active?':<10} Token ID")
        print(f"{'-'*5} {'-'*10} {'-'*10} {'-'*70}")
        for i, (token_pair, count) in enumerate(
            total_counter.most_common(args.top), 1
        ):
            active = check_market_active(token_pair)
            active_str = (
                "✅ YES" if active else "❌ NO" if active is False else "⏳ UNKNOWN"
            )
            print(f"{i:<5} {count:>10,} {active_str:<10} {token_pair[:70]}")
    else:
        print(f"{'ID':<5} {'Count':>10} Token ID")
        print(f"{'-'*5} {'-'*10} {'-'*70}")
        for i, (token_pair, count) in enumerate(
            total_counter.most_common(args.top), 1
        ):
            print(f"{i:<5} {count:>10,} {token_pair[:70]}")

    print(f"\n  Total unique stale IDs: {len(total_counter)}")
    print(f"  Top {args.top} shown above.")
    print(f"\n  Run with --check-active to verify each on Polymarket API.")
    print(f"  (May be slow for many IDs)")


if __name__ == "__main__":
    main()
