"""Wallet position tracker — polls known whales for new positions."""
import time
import requests
from datetime import datetime, timezone

from strategies.whale_tiering import WhaleTiering
from pipeline.config import DATA_API, MIN_POSITION_SIZE
from pipeline.db import (
    add_signal, is_position_seen, mark_position_seen,
    get_top_whales, log_scan
)

WHALE_TIERING = WhaleTiering()


class WalletTracker:
    """Tracks known whale positions and generates signals."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; WhalePipeline/1.0)"
        })

    def scan(self, whales: list) -> list:
        """Scan all whales for new positions."""
        new_signals = []
        for whale in whales:
            try:
                signals = self._scan_whale(whale)
                new_signals.extend(signals)
            except Exception as e:
                print(f"  [ERROR] Scanning {whale.get('name', whale.get('address', '?'))}: {e}")
            time.sleep(0.5)
        return new_signals

    def _scan_whale(self, whale: dict) -> list:
        """Scan a single whale's positions for new activity."""
        address = whale["address"]
        name = whale.get("name", address[:8])
        volume = whale.get("volume", 0.0)
        win_rate = whale.get("win_rate", 0.0)

        capital_tier = WHALE_TIERING.classify_capital(volume)
        precision_tier = WHALE_TIERING.classify_precision(win_rate)
        tier_config = WHALE_TIERING.get_dual_tier_config(capital_tier, precision_tier)

        positions = self._fetch_positions(address)
        if not positions:
            return []

        signals = []
        for pos in positions:
            asset_id = pos.get("asset", "")
            size = float(pos.get("size", 0) or 0)
            avg_price = float(pos.get("avgPrice", 0) or 0)

            if size < MIN_POSITION_SIZE:
                continue

            # Check if already seen
            if is_position_seen(address, asset_id, pos.get("outcome", "")):
                continue

            # Mark as seen
            mark_position_seen(address, asset_id, pos.get("outcome", ""), ttl_hours=24)

            # All data from position API
            usd_value = size * avg_price if avg_price else size
            confidence = self._calculate_confidence(capital_tier, precision_tier, size, avg_price)

            min_confidence = tier_config.get("min_confidence", 0)
            if min_confidence > 0 and confidence < min_confidence:
                continue

            signal_id = add_signal(
                whale_address=address,
                whale_name=name,
                capital_tier=capital_tier,
                precision_tier=precision_tier,
                market_slug=pos.get("slug", ""),
                market_title=pos.get("title", ""),
                condition_id=pos.get("conditionId", ""),
                token_id=asset_id,
                outcome=pos.get("outcome", ""),
                side="buy",
                size=size,
                price=avg_price,
                usd_value=usd_value,
                confidence=confidence,
            )

            if signal_id:
                signals.append({
                    "id": signal_id,
                    "whale_name": name,
                    "capital_tier": capital_tier,
                    "precision_tier": precision_tier,
                    "market": pos.get("title", ""),
                    "slug": pos.get("slug", ""),
                    "condition_id": pos.get("conditionId", ""),
                    "token_id": asset_id,
                    "outcome": pos.get("outcome", ""),
                    "size": size,
                    "price": avg_price,
                    "usd_value": usd_value,
                    "confidence": confidence,
                })

        return signals

    def _fetch_positions(self, address: str) -> list:
        """Fetch active positions from data-api with retry."""
        url = f"{DATA_API}/positions"
        params = {"user": address, "next_cursor": ""}
        for attempt in range(3):
            try:
                r = self.session.get(url, params=params, timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                else:
                    raise

    def _calculate_confidence(self, capital_tier: str, precision_tier: str,
                              size: float, price: float) -> float:
        """Calculate signal confidence from dual-axis whale metrics."""
        tier_config = WHALE_TIERING.get_dual_tier_config(capital_tier, precision_tier)
        kelly_multiplier = tier_config.get("kelly_multiplier", 1.0)
        base = 0.40 + kelly_multiplier * 0.10
        size_factor = min(0.10, size / 500000)
        price_factor = 0.05 if (price > 0.80 or price < 0.20) else 0
        return round(min(0.95, base + size_factor + price_factor), 4)


def get_active_whales() -> list:
    """Get active whales from DB + known whales."""
    from pipeline.config import KNOWN_WHALES

    active = []
    seen = set()

    for w in KNOWN_WHALES:
        if w["address"] not in seen:
            vol = w.get("volume", 0.0)
            wr = w.get("win_rate", 0.0)
            active.append({
                "address": w["address"],
                "name": w.get("name", w["address"][:8]),
                "volume": vol,
                "win_rate": wr,
                "capital_tier": w.get("capital_tier") or WHALE_TIERING.classify_capital(vol),
                "precision_tier": w.get("precision_tier") or WHALE_TIERING.classify_precision(wr),
            })
            seen.add(w["address"])

    discovered = get_top_whales(limit=20)
    for w in discovered:
        if w["address"] not in seen:
            vol = w.get("volume", 0.0)
            wr = w.get("win_rate", 0.0)
            active.append({
                "address": w["address"],
                "name": w["name"],
                "volume": vol,
                "win_rate": wr,
                "capital_tier": WHALE_TIERING.classify_capital(vol),
                "precision_tier": WHALE_TIERING.classify_precision(wr),
            })
            seen.add(w["address"])

    return active
