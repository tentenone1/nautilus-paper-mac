"""Wallet position tracker — polls known whales for new positions."""
import time
import requests
from datetime import datetime, timezone

from pipeline.config import DATA_API, MIN_POSITION_SIZE, MIN_ALPHA_SCORE
from pipeline.db import (
    add_signal, is_position_seen, mark_position_seen,
    get_top_whales, log_scan
)


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
        alpha_score = whale.get("alpha_score", 50)

        if alpha_score < MIN_ALPHA_SCORE:
            return []

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
            confidence = self._calculate_confidence(alpha_score, size, avg_price)

            signal_id = add_signal(
                whale_address=address,
                whale_name=name,
                alpha_score=alpha_score,
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
                    "alpha_score": alpha_score,
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

    def _calculate_confidence(self, alpha_score: float, size: float,
                              price: float) -> float:
        """Calculate signal confidence from whale metrics."""
        base = 0.50 + (alpha_score / 100) * 0.40
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
            active.append(w)
            seen.add(w["address"])

    discovered = get_top_whales(limit=20)
    for w in discovered:
        if w["address"] not in seen:
            active.append({
                "address": w["address"],
                "name": w["name"],
                "alpha_score": w["alpha_score"],
            })
            seen.add(w["address"])

    return active
