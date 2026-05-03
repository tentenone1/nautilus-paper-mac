"""Whale Tiering Configuration and Risk Limits.

Determines whale tier based on alpha_score and applies risk limits,
position sizing multipliers, and confidence thresholds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WhaleTiering:
    """Determines whale tier and applies risk limits.

    Reads tier config from JSON file and provides methods to classify
    whales, validate signals, and calculate position sizes per tier.
    """

    def __init__(self, config_path: str = "config/whale_tiers.json") -> None:
        self._config_path = Path(config_path)
        if not self._config_path.is_absolute():
            # Resolve relative to project root (this file is in strategies/)
            self._config_path = Path(__file__).resolve().parent.parent / self._config_path
        self._config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load or reload configuration from JSON file."""
        if self._config_path.exists():
            with open(self._config_path) as f:
                self._config = json.load(f)
        else:
            # Fallback defaults if config file is missing
            self._config = {
                "tiers": {
                    "speculative": {
                        "alpha_min": 0, "alpha_max": 100,
                        "kelly_multiplier": 0.5, "max_position_usd": 250,
                        "max_concurrent_positions": 5, "min_confidence": 0.30,
                    }
                },
                "defaults": {
                    "kelly_multiplier": 0.5, "max_position_usd": 250,
                    "max_concurrent_positions": 5, "min_confidence": 0.30,
                },
                "overrides": {"tag_based": {}},
            }

    def _get_tiers(self) -> dict[str, dict[str, Any]]:
        """Get tiers dict, reloading config to support hot-reload."""
        self._load_config()
        return self._config.get("tiers", {})

    def get_tier(self, alpha_score: float) -> str:
        """Return tier name based on alpha_score range."""
        tiers = self._get_tiers()
        for tier_name, cfg in sorted(tiers.items(), key=lambda x: -x[1].get("alpha_min", 0)):
            if cfg["alpha_min"] <= alpha_score <= cfg["alpha_max"]:
                return tier_name
        return "speculative"

    def get_tier_config(self, alpha_score: float) -> dict[str, Any]:
        """Return full config for a whale's tier."""
        tier_name = self.get_tier(alpha_score)
        tiers = self._get_tiers()
        return tiers.get(tier_name, self._config.get("defaults", {}))

    def apply_overrides(self, tier_config: dict[str, Any], tags: list[str] | None = None) -> dict[str, Any]:
        """Apply tag-based overrides to tier config.

        Args:
            tier_config: Base tier configuration dict.
            tags: List of whale tags (e.g., ["top_performer", "high_efficiency"]).

        Returns:
            Modified config dict with tag adjustments applied.
        """
        result = dict(tier_config)
        if not tags:
            return result

        overrides = self._config.get("overrides", {}).get("tag_based", {})
        for tag in tags:
            if tag in overrides:
                override = overrides[tag]
                if "min_confidence_reduction" in override:
                    current = result.get("min_confidence", 0.3)
                    result["min_confidence"] = round(max(0.1, current - override["min_confidence_reduction"]), 2)

        return result

    def kelly_sized_position(self, base_kelly: float, alpha_score: float, tags: list[str] | None = None) -> float:
        """Calculate position size applying tier kelly_multiplier."""
        tier_config = self.get_tier_config(alpha_score)
        tier_config = self.apply_overrides(tier_config, tags)
        kelly_mult = tier_config.get("kelly_multiplier", 0.5)
        return base_kelly * kelly_mult

    def validate_confidence(self, confidence: float, alpha_score: float, tags: list[str] | None = None) -> bool:
        """Check if signal confidence meets tier threshold (with tag overrides)."""
        tier_config = self.get_tier_config(alpha_score)
        tier_config = self.apply_overrides(tier_config, tags)
        min_conf = tier_config.get("min_confidence", 0.3)
        return confidence >= min_conf

    def validate_edge_score(self, edge_score: float, alpha_score: float) -> bool:
        """Check if signal edge_score meets tier threshold.

        Edge score is a separate quality metric from confidence.
        Returns True if edge_score >= the tier's min_edge_score.
        """
        tier_config = self.get_tier_config(alpha_score)
        min_edge = tier_config.get("min_edge_score", 0.15)
        return edge_score >= min_edge

    def get_edge_kelly(self, edge_score: float) -> float:
        """Maps edge_score to a base Kelly fraction using calibrated ranges.

        Reads from edge_kelly_mapping in config (supports hot-reload).
        Falls back to default_kelly_fraction if edge_score doesn't match a range.
        """
        self._load_config()
        mapping = self._config.get("edge_kelly_mapping", {})
        ranges = mapping.get("ranges", [])
        default_kelly = mapping.get("default_kelly_fraction", 0.10)

        for rng in ranges:
            if rng["min"] <= edge_score < rng["max"]:
                return rng["kelly_fraction"]

        return default_kelly

    def get_sanity_checks(self) -> dict[str, Any]:
        """Get position sizing sanity check bounds.

        Returns dict with max_position_pct, min_position_pct, and enabled flag.
        Falls back to sensible defaults if not configured.
        """
        self._load_config()
        sanity = self._config.get("kelly_sanity_checks", {})
        return {
            "max_position_pct": sanity.get("max_position_pct", 0.25),
            "min_position_pct": sanity.get("min_position_pct", 0.01),
            "enabled": sanity.get("enabled", True),
        }

    def get_all_tier_summary(self) -> dict[str, dict[str, Any]]:
        """Return tier names with their config for display/reporting."""
        return self._get_tiers()
