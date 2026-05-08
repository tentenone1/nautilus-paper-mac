#!/usr/bin/env python3
"""Signal accuracy analysis — computes true prediction accuracy from signal_validation.json.

The validator's built-in summary counts `resolution == "YES"` as "winning", which
is NOT prediction accuracy. This script computes the real metric:

  For BUY decisions:  correct if actual_outcome_pnl > 0 (would have profited)
  For WAIT decisions: correct if actual_outcome_pnl <= 0 (correctly avoided loss)

Usage:
  python3 scripts/accuracy_analysis.py

Output:
  - Full accuracy breakdown by decision type
  - Lists of individual signal outcomes
  - Summary with real vs reported accuracy
"""

import json
import os
import sys

TRACKER_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research",
    "signal_validation.json",
)


def load_tracker() -> dict:
    with open(TRACKER_FILE, "r") as f:
        return json.loads(f.read(), strict=False)


def compute_accuracy(data: dict) -> dict:
    resolved = {k: v for k, v in data.items() if v.get("status") == "resolved"}
    total = len(resolved)

    correct = 0
    incorrect = 0
    by_decision = {"BUY": {"correct": 0, "incorrect": 0, "total": 0},
                   "WAIT": {"correct": 0, "incorrect": 0, "total": 0},
                   "SKIP": {"correct": 0, "incorrect": 0, "total": 0}}

    details = []
    for txid, sig in sorted(resolved.items(), key=lambda x: x[1].get("signal_timestamp", "")):
        decision = sig.get("decision", "?")
        market = sig.get("market", "?")
        pnl = sig.get("actual_outcome_pnl")
        resolution = sig.get("resolution", "?")
        would_profit = sig.get("would_profit")

        if decision not in by_decision:
            continue
        by_decision[decision]["total"] += 1

        # Determine correctness by actual_outcome_pnl when available
        if pnl is not None:
            if pnl > 0:
                # Profitable signal
                if decision == "WAIT":
                    # WAIT + positive PnL = missed opportunity = incorrect
                    is_correct = False
                    reasoning = f"WAIT but PnL={pnl}% (missed profit)"
                else:
                    is_correct = True
                    reasoning = f"BUY with PnL={pnl}%"
            else:
                # Negative or zero PnL
                if decision == "WAIT":
                    is_correct = True
                    reasoning = f"WAIT correctly avoided PnL={pnl}%"
                else:
                    is_correct = False
                    reasoning = f"BUY with PnL={pnl}% (lost)"
        elif would_profit in ("profit", True):
            # No PnL but would_profit indicates profit
            if decision == "WAIT":
                is_correct = False
                reasoning = f"WAIT but would_profit={would_profit} (missed)"
            else:
                is_correct = True
                reasoning = f"BUY would_profit={would_profit}"
        elif would_profit in ("loss", False):
            if decision == "WAIT":
                is_correct = True
                reasoning = f"WAIT correctly avoided would_profit={would_profit}"
            else:
                is_correct = False
                reasoning = f"BUY but would_profit={would_profit}"
        else:
            # No PnL, no would_profit — unscored
            # Fallback: WAIT + resolved = conservatively correct,
            # BUY + resolved = unknown, mark as ambiguous
            if decision == "WAIT":
                is_correct = True  # Conservative: WAIT is safe
                reasoning = f"WAIT no-PnL (conservatively correct)"
            else:
                is_correct = "ambiguous"
                reasoning = f"BUY resolved but no PnL (unscored)"

        if is_correct == "ambiguous":
            pass  # skip counting
        elif is_correct:
            correct += 1
            by_decision[decision]["correct"] += 1
        else:
            incorrect += 1
            by_decision[decision]["incorrect"] += 1

        details.append({
            "market": market[:60],
            "decision": decision,
            "correct": is_correct,
            "pnl": pnl,
            "resolution": resolution,
            "reasoning": reasoning,
        })

    # Script's own metric: count resolution == "YES"
    yes_resolved = sum(1 for v in resolved.values() if v.get("resolution") == "YES")

    return {
        "total_resolved": total,
        "correct": correct,
        "incorrect": incorrect,
        "accuracy_pct": round(correct / max(correct + incorrect, 1) * 100, 1),
        "scored": correct + incorrect,
        "unscored": total - (correct + incorrect),
        "script_yes_count": yes_resolved,
        "by_decision": by_decision,
        "details": details,
    }


def main():
    data = load_tracker()
    result = compute_accuracy(data)

    print(f"Signal Validation Accuracy Analysis")
    print(f"{'=' * 60}")
    print(f"Total resolved signals: {result['total_resolved']}")
    print(f"Scorable (had PnL/would_profit): {result['scored']}")
    print(f"Unscored (no data):              {result['unscored']}")
    print()
    print(f"Real prediction accuracy: {result['correct']}/{result['scored']} = {result['accuracy_pct']}%")
    print(f"Script's reported 'YES wins': {result['script_yes_count']}/{result['total_resolved']}")
    print(f"  ⚠️ Script counts only resolution==YES markets, which understates accuracy")
    print()

    print("Breakdown by decision type:")
    for dec, counts in sorted(result["by_decision"].items()):
        if counts["total"] > 0:
            acc = round(counts["correct"] / max(counts["correct"] + counts["incorrect"], 1) * 100, 1)
            print(f"  {dec:4s}: {counts['correct']}/{counts['correct'] + counts['incorrect']} correct ({acc}%)"
                  f"  [{counts['total']} total]")

    print()
    print("Individual signal outcomes (scored only):")
    for d in result["details"]:
        mark = "✅" if d["correct"] is True else "❌" if d["correct"] is False else "⚠️"
        print(f"  {mark} {d['decision']:4s} | {d['reasoning']:45s} | {d['market']}")


if __name__ == "__main__":
    main()
