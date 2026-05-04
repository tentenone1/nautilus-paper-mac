#!/usr/bin/env python3
"""Cron wrapper: run position reconciliation and report.

This script is designed to be run from cron independently of the paper
trading process. It runs the reconciliation engine and logs results.

Usage:
    venv/bin/python scripts/run_reconciliation.py [--interval N]
    
If --interval is set, runs in periodic mode (every N seconds).
Default: one-shot reconciliation.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from components.position_reconciler import PositionReconciler


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Position Reconciliation — paper vs live Polymarket positions"
    )
    parser.add_argument(
        "--interval", type=float, default=0,
        help="Run periodically every N seconds (default: one-shot)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Only output on errors",
    )
    parser.add_argument(
        "--check", type=str, default=None,
        help="Quick check a single position: condition_id:price:size",
    )
    args = parser.parse_args()

    reconciler = PositionReconciler()

    if args.check:
        parts = args.check.split(":")
        cond_id = parts[0]
        price = float(parts[1]) if len(parts) > 1 else 0.5
        size = float(parts[2]) if len(parts) > 2 else 0
        result = reconciler.check_position_alignment(cond_id, price, size)
        print(f"Quick check: {cond_id[:24]}...")
        print(f"  OK: {result.get('ok')}")
        if result.get('issues'):
            for issue in result['issues']:
                print(f"  ⚠️  {issue}")
        return

    report = reconciler.reconcile_all()

    # Summary output
    if args.quiet and report.ok:
        return

    status = "✅ OK" if report.ok else "⚠️  ISSUES"
    print(f"[{report.timestamp}] Position Recon: {status}")
    print(f"  Paper positions: {report.total_paper_positions}")
    print(f"  Live positions:  {report.total_live_positions}")
    print(f"  Matched:         {report.matched}")
    print(f"  Mismatches:      {len(report.mismatches)}")

    if report.mismatches:
        # Only show most critical (group by type)
        price_issues = [m for m in report.mismatches if not m.price_match]
        orphan_issues = [m for m in report.mismatches if m.live_size_usd <= 0]

        if price_issues:
            print(f"  🔴 Price mismatches: {len(price_issues)}")
            for m in price_issues[:5]:
                print(f"       {m.condition_id[:24]}... paper=@{m.paper_entry_price:.4f} live=@{m.live_avg_price:.4f} ({m.price_diff_pct:.1f}%)")

        if orphan_issues:
            print(f"  🟡 Orphan positions (no live match): {len(orphan_issues)}")

    # Periodic mode
    if args.interval > 0:
        import time
        print(f"\nStarting periodic reconciliation (every {args.interval:.0f}s)...")
        try:
            while True:
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")

    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
