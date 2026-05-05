"""Whale Follower — Sports market detection and exit logic.

Standalone functions for detecting sports markets and determining
whether to exit positions based on event timing.
"""

from __future__ import annotations

import json
import requests
from datetime import datetime, timezone
from pathlib import Path

from strategies.wf_constants import SPORTS_EXIT_HOURS_BEFORE_EVENT


# ── Sports Market Keywords ────────────────────────────────────────────────────

SPORTS_KEYWORDS: list[str] = [
    "nfl", "nba", "mlb", "nhl", "ncaa", "college football", "college basketball",
    "soccer", "football", "basketball", "baseball", "hockey", "tennis", "golf",
    "boxing", "mma", "ufc", "wwe", "f1", "formula 1", "nascar",
    "super bowl", "world cup", "champions league", "premier league",
    "playoffs", "stanley cup", "world series", "final four", "march madness",
    "vs.", " vs ", "eagles", "49ers", "chiefs", "lakers", "celtics",
    "warriors", "yankees", "dodgers", "red sox", "patriots",
    "trail blazers", "spurs", "penguins", "stars", "wild",
    "bucks", "thunder", "nuggets", "timberwolves", "knicks",
]

# Sport-type keyword index for faster classification
_SPORT_TYPE_KEYWORDS: dict[str, list[str]] = {
    "nba": ["nba", "lakers", "celtics", "warriors", "bucks", "thunder",
            "nuggets", "knicks", "trail blazers", "spurs", "timberwolves"],
    "nfl": ["nfl", "eagles", "49ers", "chiefs", "patriots", "cowboys",
            "commanders"],
    "nhl": ["nhl", "penguins", "stars", "wild", "hurricanes",
            "golden knights", "avalanche", "oilers", "canucks"],
    "mlb": ["mlb", "yankees", "dodgers", "red sox"],
    "soccer": ["soccer", "champions league", "premier league", "world cup"],
    "ncaa": ["ncaa", "college football", "college basketball",
             "march madness", "final four"],
}


def is_sports_market(instrument_id_str: str) -> tuple[bool, str]:
    """Check if an instrument is a sports market.

    Args:
        instrument_id_str: Full instrument ID string (e.g.
            "condition_id-token_id.POLYMARKET").

    Returns:
        (is_sports, sport_type) — sport_type is one of
        "nba", "nfl", "nhl", "mlb", "soccer", "ncaa", or "other_sports".
    """
    title = instrument_id_str.lower()

    for sport_type, keywords in _SPORT_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in title:
                return True, sport_type

    # General sports check
    for kw in SPORTS_KEYWORDS:
        if kw in title:
            return True, "other_sports"

    return False, ""


def get_market_event_time(instrument_id_str: str) -> dict:
    """Fetch event timing for a market from Polymarket API.

    First checks a local metadata cache file, then falls back to
    the Gamma API.

    Args:
        instrument_id_str: Full instrument ID string.

    Returns:
        Dict with keys: hours_until_event, is_imminent, is_in_play,
        is_past, event_date_iso, liquidity_tier, volume, liquidity.
    """
    cond_id = instrument_id_str.split("-")[0]

    # 1. Try cached metadata file
    try:
        metadata_file = Path.home() / "workspace" / "metadata" / "markets_latest.json"
        if metadata_file.exists():
            with open(metadata_file) as f:
                markets = json.load(f)
            for m in markets:
                if m.get("condition_id") == cond_id:
                    return {
                        "hours_until_event": m.get("hours_until_event"),
                        "is_imminent": m.get("is_imminent", False),
                        "is_in_play": m.get("is_in_play", False),
                        "is_past": m.get("is_past", False),
                        "event_date_iso": m.get("end_date_iso"),
                        "liquidity_tier": m.get("liquidity_tier", "tier3"),
                        "volume": m.get("volume", 0),
                        "liquidity": m.get("liquidity", 0),
                    }
    except Exception:
        pass

    # 2. Fallback: fetch from Gamma API
    try:
        resp = requests.get(
            f"https://gamma-api.polymarket.com/markets/{cond_id}",
            timeout=10,
        )
        if resp.status_code == 200:
            m = resp.json()
            end_date = m.get("endDateIso", m.get("endDate"))
            if end_date:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                hours_left = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
                return {
                    "hours_until_event": round(hours_left, 1),
                    "is_imminent": 0 < hours_left < 6,
                    "is_in_play": hours_left < 6 and hours_left > 0,
                    "is_past": hours_left < 0,
                    "event_date_iso": end_date,
                    "liquidity_tier": "tier3",
                    "volume": float(m.get("volumeNum", 0)),
                    "liquidity": float(m.get("liquidityNum", 0)),
                }
    except Exception:
        pass

    # 3. Safe default
    return {
        "hours_until_event": None,
        "is_imminent": False,
        "is_in_play": False,
        "is_past": False,
        "event_date_iso": None,
        "liquidity_tier": "tier3",
        "volume": 0,
        "liquidity": 0,
    }


def should_exit_for_sports(
    instrument_id_str: str,
    log_func=None,
) -> bool:
    """Check if a sports position should be exited.

    Exits when the game is imminent (within SPORTS_EXIT_HOURS_BEFORE_EVENT
    hours) or when the market is in-play (prices frozen).

    Args:
        instrument_id_str: Full instrument ID string.
        log_func: Optional logging callable (e.g. self.log.info).

    Returns:
        True if the position should be exited for sports reasons.
    """
    is_sports, sport_type = is_sports_market(instrument_id_str)
    if not is_sports:
        return False

    timing = get_market_event_time(instrument_id_str)

    # Exit if game is within SPORTS_EXIT_HOURS_BEFORE_EVENT hour
    # (prices will freeze during play)
    if (
        timing["hours_until_event"] is not None
        and 0 < timing["hours_until_event"] < SPORTS_EXIT_HOURS_BEFORE_EVENT
    ):
        if log_func:
            log_func(
                f"Sports exit: {sport_type} market resolving in "
                f"{timing['hours_until_event']:.1f}h"
            )
        return True

    # Exit if market is in-play (prices frozen, can't manage risk)
    if timing["is_in_play"]:
        if log_func:
            log_func(f"Sports exit: {sport_type} market is in-play (prices frozen)")
        return True

    return False
