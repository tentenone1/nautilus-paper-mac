"""
Sybil Signal Generator — converts aggregated sybil positions to fade/follow signals.

Reads: research/sybil_positions.json
Writes: research/sybil_signal_queue.json

Three strategies:
1. NO-Bias Fade: net NO >$50K across ≥5 wallets → BUY YES signal
2. Concentrated Conviction Follow: net YES >$150K on single market (≥2 wallets),
   group YES ratio >15% → BUY YES signal
3. Sentiment-Manipulation Fade: ≥10 wallets betting avg <$400 on same market → BUY NO signal
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

from scripts.sybil_config import get_config

config = get_config()

# Strategy thresholds (from centralized config)
NO_BIAS_FADE_MIN_NO_USD = config.thresholds.no_bias_fade_min_no_usd
NO_BIAS_FADE_MIN_WALLETS = config.thresholds.no_bias_fade_min_wallets

CONCENTRATED_FOLLOW_MIN_YES_USD = config.thresholds.concentrated_follow_min_yes_usd
CONCENTRATED_FOLLOW_MIN_WALLETS = config.thresholds.concentrated_follow_min_wallets
CONCENTRATED_FOLLOW_MIN_YES_RATIO = config.thresholds.concentrated_follow_min_yes_ratio

MANIPULATION_FADE_MIN_WALLETS = config.thresholds.manipulation_fade_min_wallets
MANIPULATION_FADE_MAX_AVG_BET_USD = config.thresholds.manipulation_fade_max_avg_bet_usd


@dataclass(frozen=True)
class SybilSignal:
    """A trading signal generated from sybil group analysis."""
    signal_type: str  # "no_bias_fade", "concentrated_follow", "manipulation_fade"
    group_id: str
    market_title: str
    market_slug: str
    condition_id: str
    side: str  # "BUY YES" or "BUY NO"
    confidence: float
    reason: str
    total_exposure_usd: float
    wallet_count: int
    yes_size_usd: float
    no_size_usd: float
    yes_ratio: float
    avg_bet_usd: float
    generated_at: str


def compute_no_size(market: dict) -> float:
    """Compute net NO size — all outcomes except YES count as NO."""
    total = market.get("total_size_usd", 0)
    yes = market.get("yes_size_usd", 0)
    return round(total - yes, 2)


def generate_signals(positions_data: dict) -> list[dict]:
    """Generate trading signals from sybil position data."""
    signals = []

    for group_id, group_data in positions_data.get("groups", {}).items():
        markets = group_data.get("markets", [])

        for market in markets:
            title = market.get("market_title", "")
            slug = market.get("market_slug", "")
            cond_id = market.get("condition_id", "")
            yes_size = market.get("yes_size_usd", 0)
            total_size = market.get("total_size_usd", 0)
            no_size = compute_no_size(market)
            n_wallets = len(market.get("wallets", []))

            if n_wallets == 0 or total_size == 0:
                continue

            yes_ratio = yes_size / max(yes_size + no_size, 1)
            avg_bet = total_size / n_wallets

            # Strategy 1: NO-Bias Fade
            if no_size >= NO_BIAS_FADE_MIN_NO_USD and n_wallets >= NO_BIAS_FADE_MIN_WALLETS:
                confidence = min(0.95, 0.5 + (no_size / 500_000) * 0.3)
                signals.append(asdict(SybilSignal(
                    signal_type="no_bias_fade",
                    group_id=group_id,
                    market_title=title,
                    market_slug=slug,
                    condition_id=cond_id,
                    side="BUY YES",
                    confidence=round(confidence, 2),
                    reason=f"Sybil group has ${no_size:,.0f} NO exposure across {n_wallets} wallets — fade their bearish consensus",
                    total_exposure_usd=total_size,
                    wallet_count=n_wallets,
                    yes_size_usd=yes_size,
                    no_size_usd=no_size,
                    yes_ratio=round(yes_ratio, 3),
                    avg_bet_usd=round(avg_bet, 2),
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )))

            # Strategy 2: Concentrated Conviction Follow
            if (yes_size >= CONCENTRATED_FOLLOW_MIN_YES_USD
                    and n_wallets >= CONCENTRATED_FOLLOW_MIN_WALLETS
                    and yes_ratio >= CONCENTRATED_FOLLOW_MIN_YES_RATIO):
                confidence = min(0.95, 0.5 + (yes_size / 500_000) * 0.3)
                signals.append(asdict(SybilSignal(
                    signal_type="concentrated_follow",
                    group_id=group_id,
                    market_title=title,
                    market_slug=slug,
                    condition_id=cond_id,
                    side="BUY YES",
                    confidence=round(confidence, 2),
                    reason=f"Sybil group has ${yes_size:,.0f} YES conviction across {n_wallets} wallets ({yes_ratio:.0%} YES ratio) — follow their bullish consensus",
                    total_exposure_usd=total_size,
                    wallet_count=n_wallets,
                    yes_size_usd=yes_size,
                    no_size_usd=no_size,
                    yes_ratio=round(yes_ratio, 3),
                    avg_bet_usd=round(avg_bet, 2),
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )))

            # Strategy 3: Sentiment-Manipulation Fade
            if n_wallets >= MANIPULATION_FADE_MIN_WALLETS and avg_bet <= MANIPULATION_FADE_MAX_AVG_BET_USD:
                confidence = min(0.85, 0.4 + (n_wallets / 20) * 0.3)
                signals.append(asdict(SybilSignal(
                    signal_type="manipulation_fade",
                    group_id=group_id,
                    market_title=title,
                    market_slug=slug,
                    condition_id=cond_id,
                    side="BUY NO",
                    confidence=round(confidence, 2),
                    reason=f"Sybil group spreading small bets: {n_wallets} wallets averaging ${avg_bet:,.0f}/wallet — likely sentiment manipulation, fade to NO",
                    total_exposure_usd=total_size,
                    wallet_count=n_wallets,
                    yes_size_usd=yes_size,
                    no_size_usd=no_size,
                    yes_ratio=round(yes_ratio, 3),
                    avg_bet_usd=round(avg_bet, 2),
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )))

    # Sort by confidence descending
    signals.sort(key=lambda s: s["confidence"], reverse=True)
    return signals


def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_path = os.path.join(base_dir, "research", "sybil_positions.json")
    output_path = os.path.join(base_dir, "research", config.paths.signals_file)

    if not os.path.exists(input_path):
        logger.error(f"Input file not found: {input_path}")
        return

    with open(input_path) as f:
        positions_data = json.load(f)

    logger.info(f"Loaded sybil positions from {input_path}")
    signals = generate_signals(positions_data)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signal_count": len(signals),
        "signals": signals,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print(f"\nGenerated {len(signals)} signals:")
    by_type = {}
    for s in signals:
        t = s["signal_type"]
        by_type[t] = by_type.get(t, 0) + 1
    for t, count in sorted(by_type.items()):
        print(f"  {t}: {count}")

    print(f"\nTop 5 signals:")
    for s in signals[:5]:
        print(f"  [{s['confidence']:.0%}] {s['side']:8s} | {s['market_title'][:70]} | {s['wallet_count']} wallets | ${s['total_exposure_usd']:,.0f}")

    logger.info(f"Output: {output_path}")


if __name__ == "__main__":
    main()
