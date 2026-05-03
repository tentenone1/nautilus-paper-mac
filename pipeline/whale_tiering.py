"""Whale tiering for Polymarket trading.

Assigns whales to tiers based on alpha_score thresholds from whale_tiers.yaml,
and computes position sizing parameters per tier using Kelly-inspired formulas.
"""
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import yaml

from pipeline.db import get_connection

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YAML_PATH = os.path.join(BASE_DIR, "whale_tiers.yaml")
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_PATH = os.path.join(DATA_DIR, "whale_tier_assignments.json")

# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

_tier_config: dict[str, Any] | None = None


def _load_tier_config() -> dict[str, Any]:
    """Load and cache the whale_tiers.yaml configuration."""
    global _tier_config
    if _tier_config is None:
        with open(YAML_PATH, "r") as f:
            _tier_config = yaml.safe_load(f)
    return _tier_config


def _tiers() -> list[dict[str, Any]]:
    """Return the list of tier definitions from YAML."""
    return _load_tier_config()["tiers"]


# ---------------------------------------------------------------------------
# Tier lookup
# ---------------------------------------------------------------------------


def get_tier_for_score(alpha_score: float) -> dict[str, Any]:
    """Return the matching tier dict for a given alpha_score.

    Tiers are evaluated in order; the first tier whose range contains
    *alpha_score* is returned.  Returns an empty dict if no tier matches.
    """
    for tier in _tiers():
        if tier["min_alpha"] <= alpha_score <= tier["max_alpha"]:
            return tier
    return {}


def get_position_params(alpha_score: float) -> dict[str, Any]:
    """Return {tier_name, kelly_fraction, max_position_cap, max_exposure}."""
    tier = get_tier_for_score(alpha_score)
    if not tier:
        return {
            "tier_name": "unknown",
            "kelly_fraction": 0.15,
            "max_position_cap": 1000,
            "max_exposure": 1500,
        }
    return {
        "tier_name": tier["name"],
        "kelly_fraction": tier["kelly_fraction"],
        "max_position_cap": tier["max_position_cap"],
        "max_exposure": tier["max_exposure"],
    }


# ---------------------------------------------------------------------------
# Tier assignment generation
# ---------------------------------------------------------------------------


def generate_tier_assignments() -> dict[str, Any]:
    """Assign every whale to a tier and write the result to JSON.

    Returns the full assignments dict.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT address, name, alpha_score, pnl, win_rate "
        "FROM whales ORDER BY alpha_score DESC"
    ).fetchall()
    conn.close()

    whales: list[dict[str, Any]] = []
    # Per-tier accumulation: tier_name -> {count, sum_alpha, sum_pnl, sum_wr}
    tier_agg: dict[str, dict[str, float]] = {}

    for row in rows:
        alpha: float = row["alpha_score"] if row["alpha_score"] is not None else 0.0
        tier = get_tier_for_score(alpha)
        tier_name: str = tier.get("name", "unknown")
        kelly_fraction: float = tier.get("kelly_fraction", 0.15)
        max_position_cap: float = tier.get("max_position_cap", 1000)
        max_exposure: float = tier.get("max_exposure", 1500)

        whales.append({
            "address": row["address"],
            "name": row["name"],
            "alpha_score": alpha,
            "tier_name": tier_name,
            "kelly_fraction": kelly_fraction,
            "max_position_cap": max_position_cap,
            "max_exposure": max_exposure,
        })

        pnl: float = row["pnl"] if row["pnl"] is not None else 0.0
        win_rate: float = row["win_rate"] if row["win_rate"] is not None else 0.0

        agg = tier_agg.get(tier_name, {"count": 0, "sum_alpha": 0.0, "sum_pnl": 0.0, "sum_wr": 0.0})
        agg["count"] += 1
        agg["sum_alpha"] += alpha
        agg["sum_pnl"] += pnl
        agg["sum_wr"] += win_rate
        tier_agg[tier_name] = agg

    tier_summary: dict[str, Any] = {}
    for tname, agg in tier_agg.items():
        n = agg["count"]
        tier_summary[tname] = {
            "count": n,
            "avg_alpha": round(agg["sum_alpha"] / n, 2) if n else 0.0,
            "avg_pnl": round(agg["sum_pnl"] / n, 2) if n else 0.0,
            "avg_win_rate": round(agg["sum_wr"] / n, 4) if n else 0.0,
        }

    result: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_whales": len(whales),
        "tier_summary": tier_summary,
        "whale_assignments": whales,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)

    return result


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------


def compute_sizing(
    alpha_score: float,
    bankroll: float = 10000,
    win_rate: float = 0.55,
    price: float = 0.5,
) -> dict[str, Any]:
    """Compute position sizing for a given alpha score.

    Uses a Kelly-inspired formula scaled by the tier's kelly_fraction
    and capped at the tier's max_position_cap.

    Returns:
        {tier_name, kelly_fraction, max_position_cap,
         suggested_kelly_bet, capped_bet, conservative_bet}
    """
    params = get_position_params(alpha_score)
    kelly_fraction = params["kelly_fraction"]
    max_position_cap = params["max_position_cap"]

    # Kelly edge and percentage
    # edge = win_rate - (1 - win_rate) * (price / (1 - price))
    edge = win_rate - (1 - win_rate) * (price / (1 - price)) if price < 1.0 else win_rate - 0.0

    # kelly_pct = max(0, (win_rate * (1 - price) - (1 - win_rate) * price) / price)
    if price > 0:
        kelly_pct = max(0.0, (win_rate * (1 - price) - (1 - win_rate) * price) / price)
    else:
        kelly_pct = 0.0

    # Suggested bet = bankroll * kelly_pct * tier kelly_fraction
    suggested = bankroll * kelly_pct * kelly_fraction

    # Capped bet
    capped = min(suggested, max_position_cap)

    # Conservative bet: half of the capped bet
    conservative = capped / 2

    return {
        "tier_name": params["tier_name"],
        "kelly_fraction": kelly_fraction,
        "max_position_cap": max_position_cap,
        "suggested_kelly_bet": round(suggested, 2),
        "capped_bet": round(capped, 2),
        "conservative_bet": round(conservative, 2),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_tier_summary(result: dict[str, Any]) -> None:
    """Pretty-print the tier assignment summary."""
    print(f"Timestamp : {result['timestamp']}")
    print(f"Whales    : {result['total_whales']}")
    print(f"Output    : {OUTPUT_PATH}")
    print()
    print(f"{'Tier':<12} {'Count':>6} {'Avg Alpha':>10} {'Avg PnL':>12} {'Avg WR':>9}")
    print("-" * 52)
    for tier_name, summary in sorted(
        result["tier_summary"].items(),
        key=lambda x: x[1]["avg_alpha"],
        reverse=True,
    ):
        print(
            f"{tier_name:<12} {summary['count']:>6} "
            f"{summary['avg_alpha']:>10.2f} "
            f"${summary['avg_pnl']:>10,.2f} "
            f"{summary['avg_win_rate']:>9.4f}"
        )


def _main() -> None:
    args = sys.argv[1:]

    if "--generate" in args:
        result = generate_tier_assignments()
        _print_tier_summary(result)

    elif "--sizing" in args:
        alpha = 85.0
        bankroll = 10000.0
        try:
            idx = args.index("--alpha")
            alpha = float(args[idx + 1])
        except (ValueError, IndexError):
            pass
        try:
            idx = args.index("--bankroll")
            bankroll = float(args[idx + 1])
        except (ValueError, IndexError):
            pass

        sizing = compute_sizing(alpha_score=alpha, bankroll=bankroll)
        print(f"Tier              : {sizing['tier_name']}")
        print(f"Kelly Fraction    : {sizing['kelly_fraction']}")
        print(f"Max Position Cap  : ${sizing['max_position_cap']:,.2f}")
        print(f"Suggested Kelly   : ${sizing['suggested_kelly_bet']:,.2f}")
        print(f"Capped Bet        : ${sizing['capped_bet']:,.2f}")
        print(f"Conservative Bet  : ${sizing['conservative_bet']:,.2f}")

    else:
        print("Usage:")
        print("  python whale_tiering.py --generate")
        print("  python whale_tiering.py --sizing --alpha 85 --bankroll 10000")


if __name__ == "__main__":
    _main()
