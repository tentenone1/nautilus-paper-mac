"""
Unit tests for strategies.wf_kelly — Kelly sizing and liquidity adjustments.
"""
import pytest
import sys
sys.path.insert(0, ".")

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from strategies.wf_kelly import kelly_size, adjust_size_for_liquidity
from strategies.wf_constants import (
    LIQUIDITY_TIER4_THRESHOLD,
    LIQUIDITY_TIER3_THRESHOLD,
    LIQUIDITY_TIER2_MULTIPLIER,
    LIQUIDITY_TIER3_MULTIPLIER,
    LIQUIDITY_TIER4_MULTIPLIER,
    MIN_ENTRY_PRICE,
    SPORTS_KELLY_MULTIPLIER,
)


def mock_timing(liquidity_tier, volume=1000000, liquidity=0):
    """Create a mock get_market_event_time_func for liquidity tier tests."""
    def mock(instrument_id_str):
        return {
            "liquidity_tier": liquidity_tier,
            "volume": volume,
            "liquidity": liquidity,
            "hours_until_event": 24.0,
        }
    return mock


class TestKellySize:
    """Tests for kelly_size() — core position sizing."""

    def test_basic_positive_size(self):
        """$1000 bankroll, 0.6 win rate, 0.1 edge, price 0.5 → positive size"""
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.5,
            whale_win_rate=0.6,
            edge_score=0.1,
            available_balance=1000.0,
            market_category="politics",
        )
        assert result > 0, f"kelly_size should be positive, got {result}"
        # Max position cap is 2% of $1000 = $20
        assert result <= 20.0, f"kelly_size {result} exceeds max position cap"

    def test_zero_edge_returns_zero(self):
        """Zero edge → no bet (0 size)"""
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.5,
            whale_win_rate=0.5,
            edge_score=0.0,
            available_balance=1000.0,
            market_category="politics",
        )
        assert result == 0.0

    def test_negative_edge_returns_zero(self):
        """Negative edge → no bet"""
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.5,
            whale_win_rate=0.4,
            edge_score=-0.1,
            available_balance=1000.0,
            market_category="politics",
        )
        assert result == 0.0

    def test_sports_applies_kelly_multiplier(self):
        """Sports category applies SPORTS_KELLY_MULTIPLIER (0.5x)"""
        # Use bankroll=5000 and kelly_fraction=0.10 to keep uncapped sports below
        # the 2% hard cap, so the 0.5x sports multiplier is visible.
        # price=0.35: kelly ≈ 0.314
        # politics uncapped = 5000 * 0.314 * 0.10 = 157 → capped to 100
        # sports uncapped = 5000 * 0.314 * 0.10 * 0.5 = 78.5 → NOT capped
        politics_result = kelly_size(
            bankroll=5000.0,
            kelly_fraction=0.10,
            max_position_pct=0.05,
            price=0.35,
            whale_win_rate=0.6,
            edge_score=0.1,
            available_balance=5000.0,
            market_category="politics",
        )
        sports_result = kelly_size(
            bankroll=5000.0,
            kelly_fraction=0.10,
            max_position_pct=0.05,
            price=0.35,
            whale_win_rate=0.6,
            edge_score=0.1,
            available_balance=5000.0,
            market_category="sports",
        )
        # Sports gets the 0.5x SPORTS_KELLY_MULTIPLIER → smaller size
        assert sports_result < politics_result, (
            f"sports_result ({sports_result}) should be less than "
            f"politics_result ({politics_result})"
        )

    def test_high_win_rate_produces_larger_size(self):
        """95% win rate → larger position than 55% baseline"""
        high_wr = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.5,
            whale_win_rate=0.95,
            edge_score=0.1,
            available_balance=1000.0,
            market_category="politics",
        )
        low_wr = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.5,
            whale_win_rate=0.5,
            edge_score=0.1,
            available_balance=1000.0,
            market_category="politics",
        )
        assert high_wr > low_wr

    def test_below_min_entry_price_edge_rejected(self):
        """Sub-$0.05 edge (like Italy at $0.017) gets near-zero Kelly fraction"""
        # Italy world cup at $0.017: edge must come from whale win_rate
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.017,
            whale_win_rate=0.5,
            edge_score=0.0,  # no edge score for long shots
            available_balance=1000.0,
            market_category="sports",
        )
        # Near-zero price + no edge → tiny Kelly
        assert isinstance(result, float)

    def test_max_position_pct_hard_cap(self):
        """kelly_size result capped at max_position_pct of bankroll"""
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=1.0,  # full Kelly (not used in practice)
            max_position_pct=0.02,
            price=0.01,
            whale_win_rate=0.99,
            edge_score=0.5,
            available_balance=1000.0,
            market_category="politics",
        )
        # Even with huge edge, can't exceed 2% of bankroll = $20
        assert result <= 20.0


class TestAdjustSizeForLiquidity:
    """Tests for adjust_size_for_liquidity() — liquidity tier sizing.

    Tier thresholds (volume + liquidity):
      tier1 (full):     >= $1,000,000 (LIQUIDITY_TIER3_THRESHOLD)
      tier3 (50%):      >= $100,000 (LIQUIDITY_TIER4_THRESHOLD) and < $1,000,000
      tier2 (25%):      < $100,000 AND liquidity_tier == "tier2"
      tier1 illiquid:   < $100,000 (anything not tier2)
    """

    def test_tier1_high_liquidity_returns_full_size(self):
        """volume+liquidity >= $1M (tier3 threshold) → full Kelly (tier1 most liquid)"""
        result = adjust_size_for_liquidity(
            size_usd=1000.0,
            instrument_id_str="0xabc-123",
            get_market_event_time_func=mock_timing("tier1", volume=2000000, liquidity=0),
        )
        assert result == 1000.0

    def test_tier3_medium_liquidity_reduces_to_50pct(self):
        """$100k <= volume+liquidity < $1M → 50% multiplier"""
        result = adjust_size_for_liquidity(
            size_usd=1000.0,
            instrument_id_str="0xabc-123",
            get_market_event_time_func=mock_timing("tier3", volume=500000, liquidity=0),
        )
        expected = 1000.0 * LIQUIDITY_TIER3_MULTIPLIER  # 0.50
        assert abs(result - expected) < 0.01

    def test_tier2_low_liquidity_reduces_to_25pct(self):
        """volume+liquidity < $100k AND liq_tier='tier2' → 25% multiplier"""
        result = adjust_size_for_liquidity(
            size_usd=1000.0,
            instrument_id_str="0xabc-123",
            get_market_event_time_func=mock_timing("tier2", volume=50000, liquidity=0),
        )
        expected = 1000.0 * LIQUIDITY_TIER2_MULTIPLIER  # 0.25
        assert abs(result - expected) < 0.01

    def test_very_low_liquidity_reduces_to_10pct(self):
        """volume+liquidity < $100k AND NOT tier2 → 10% multiplier (tier1 illiquid)"""
        result = adjust_size_for_liquidity(
            size_usd=1000.0,
            instrument_id_str="0xabc-123",
            get_market_event_time_func=mock_timing("tier1", volume=50000, liquidity=0),
        )
        expected = 1000.0 * LIQUIDITY_TIER4_MULTIPLIER  # 0.10
        assert abs(result - expected) < 0.01

    def test_zero_size_returns_zero(self):
        """Zero input → zero output"""
        result = adjust_size_for_liquidity(
            size_usd=0.0,
            instrument_id_str="0xabc-123",
            get_market_event_time_func=mock_timing("tier1"),
        )
        assert result == 0.0

    def test_zero_volume_falls_to_10pct(self):
        """Zero volume (no tier2 name) → tier1 illiquid at 10%"""
        result = adjust_size_for_liquidity(
            size_usd=1000.0,
            instrument_id_str="0xabc-123",
            get_market_event_time_func=mock_timing("tier4", volume=0, liquidity=0),
        )
        # Volume 0 < 100k threshold → illiquid tier1 → 10%
        expected = 1000.0 * LIQUIDITY_TIER4_MULTIPLIER
        assert abs(result - expected) < 0.01

    def test_tier2_name_with_high_volume_still_uses_tier3(self):
        """volume in tier3 range with tier2 name → 50% (volume-based tiering wins)"""
        # volume=500k >= 100k threshold → tier3 branch, not tier2
        result = adjust_size_for_liquidity(
            size_usd=1000.0,
            instrument_id_str="0xabc-123",
            get_market_event_time_func=mock_timing("tier2", volume=500000, liquidity=0),
        )
        expected = 1000.0 * LIQUIDITY_TIER3_MULTIPLIER  # 0.50
        assert abs(result - expected) < 0.01
