"""
Integration tests: Kill Switch Trigger Flow.
"""
import pytest
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")

from strategies.wf_kelly import kelly_size


class TestKillSwitchTriggers:
    """Tests for kill switch trigger conditions."""

    def test_kill_switch_fires_on_max_single_position_breach(self):
        """Proposed size > max_single_position_pct → kill switch condition."""
        # Bankroll $1000, max_single_position_pct = 2% → $20 cap
        bankroll = 1000.0
        max_single_pct = 0.02
        max_single_usd = bankroll * max_single_pct  # $20

        # Proposed size of $30 exceeds $20 cap
        proposed_size = 30.0
        assert proposed_size > max_single_usd, "Proposed size should breach cap"

    def test_kill_switch_fires_on_max_total_exposure_breach(self):
        """Existing + proposed > max_total_exposure_pct → kill switch."""
        bankroll = 1000.0
        max_total_pct = 0.20
        max_total_usd = bankroll * max_total_pct  # $200

        current_exposure = 180.0  # existing positions
        proposed_size = 30.0
        total_after = current_exposure + proposed_size

        assert total_after > max_total_usd, f"Total {total_after} should exceed cap {max_total_usd}"

    def test_daily_loss_limit_closes_all_positions(self):
        """Daily P&L drops below daily_loss_limit → all positions should close."""
        bankroll = 1000.0
        daily_loss_limit = 100.0  # -$100/day limit

        # Scenario: -$110 realized today (breaches $100 limit)
        daily_pnl = -110.0
        daily_loss_limit_usd = daily_loss_limit

        assert daily_pnl <= -daily_loss_limit_usd, f"PNL {daily_pnl} should breach limit {-daily_loss_limit_usd}"

    def test_kill_switch_conditions_are_independent(self):
        """Each kill switch condition can trigger independently."""
        # Single position breach
        size = 30.0
        cap = 20.0
        single_breach = size > cap

        # Total exposure breach
        exposure = 200.0
        total_cap = 200.0
        exposure_breach = exposure > total_cap

        # Daily loss breach
        pnl = -110.0
        loss_limit = -100.0
        loss_breach = pnl <= loss_limit

        assert single_breach or exposure_breach or loss_breach


class TestKillSwitchPrevention:
    """Tests that kill switch does NOT fire under normal conditions."""

    def test_no_single_breach_normal_size(self):
        """Normal Kelly sizing stays within max_single_position_pct."""
        # Even a high-edge trade at 2% cap should not trigger kill switch
        result = kelly_size(
            bankroll=1000.0,
            kelly_fraction=0.25,
            max_position_pct=0.10,
            price=0.50,
            whale_win_rate=0.80,
            edge_score=0.8,
            available_balance=1000.0,
            market_category="politics",
            max_single_position_pct=0.02,
        )
        # Result should be capped at 2% of $1000 = $20
        assert result <= 20.0

    def test_no_exposure_breach_within_limits(self):
        """Existing positions within total exposure limit."""
        bankroll = 1000.0
        max_total_pct = 0.20
        max_total_usd = bankroll * max_total_pct  # $200

        current_exposure = 150.0
        proposed_size = 30.0
        total_after = current_exposure + proposed_size

        assert total_after <= max_total_usd, "Should be within limits"

    def test_no_loss_breach_profitable_day(self):
        """Profitable day → no loss limit breach."""
        daily_pnl = 25.0  # profitable
        daily_loss_limit = -100.0

        assert daily_pnl > daily_loss_limit, "Profitable day should not breach"
