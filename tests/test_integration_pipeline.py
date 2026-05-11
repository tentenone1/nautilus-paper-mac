"""
Integration tests: Order Execution Pipeline.
"""
import pytest
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from strategies.wf_kelly import kelly_size


class TestKellySizingIntegration:
    """Integration tests for Kelly sizing pipeline."""

    def test_pipeline_reject_no_edge(self):
        """Zero Kelly edge → no position sizing (kelly returns 0)."""
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.50,
            whale_win_rate=0.50,  # no edge: 50% WR = breakeven
            edge_score=0.0,
            available_balance=1000.0,
            market_category="politics",
            max_single_position_pct=0.02,
        )
        assert result == 0.0, "No edge → size should be 0"

    def test_pipeline_accept_positive_edge(self):
        """Positive edge → position size is positive and within caps."""
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.02,
            price=0.50,
            whale_win_rate=0.65,  # positive edge
            edge_score=0.5,
            available_balance=1000.0,
            market_category="politics",
            max_single_position_pct=0.02,
        )
        assert result > 0, f"Positive edge should produce size > 0, got {result}"
        # Max 2% of $1000 = $20
        assert result <= 20.0, f"Size {result} exceeds max single position cap $20"

    def test_pipeline_size_within_available_balance(self):
        """Kelly size respects available balance when it's lower than bankroll."""
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.10,
            price=0.50,
            whale_win_rate=0.65,
            edge_score=0.5,
            available_balance=100.0,  # depleted balance
            market_category="politics",
            max_single_position_pct=0.02,
        )
        # Should be based on min(bankroll, available) = $100
        # Cap is 2% of $100 = $2
        assert result <= 2.0, f"Size {result} should be capped at $2 for depleted balance"


class TestPositionChecksIntegration:
    """Integration tests for position checks."""

    def test_max_single_position_pct_enforced(self):
        """Kelly proposed size > max_single_position_pct → capped."""
        # Full Kelly at 0.5 price, 0.65 WR, 0.25 fraction
        # kelly = (0.5 * 0.65 - 0.35) / 0.5 = 0.30
        # raw = 1000 * 0.30 * 0.25 = $75
        # capped at 2% of 1000 = $20
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.10,
            price=0.50,
            whale_win_rate=0.65,
            edge_score=0.5,
            available_balance=1000.0,
            market_category="politics",
            max_single_position_pct=0.02,
        )
        assert result <= 20.0, f"Capped at max_single_position_pct=2%, got {result}"

    def test_sports_kelly_multiplier_reduces_size(self):
        """Sports markets get halved Kelly → smaller positions."""
        politics = kelly_size(
            bankroll=5000.0,
            kelly_fraction=0.10,
            max_position_pct=0.05,
            price=0.35,
            whale_win_rate=0.60,
            edge_score=0.1,
            available_balance=5000.0,
            market_category="politics",
            max_single_position_pct=0.02,
        )
        sports = kelly_size(
            bankroll=5000.0,
            kelly_fraction=0.10,
            max_position_pct=0.05,
            price=0.35,
            whale_win_rate=0.60,
            edge_score=0.1,
            available_balance=5000.0,
            market_category="sports",
            max_single_position_pct=0.02,
        )
        assert sports < politics, f"Sports size ({sports}) should be < politics ({politics})"
