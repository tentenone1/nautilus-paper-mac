"""
Unit tests for strategies.wf_position_checks — daily loss limit and position checks.
"""
import pytest
import sys
sys.path.insert(0, ".")

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from strategies.wf_position_checks import check_daily_loss_limit


class TestCheckDailyLossLimit:
    """Tests for check_daily_loss_limit() — daily loss kill switch."""

    def _make_config(self, daily_loss_limit=500.0):
        """Create a mock config object."""
        config = MagicMock()
        config.daily_loss_limit = daily_loss_limit
        config.sports_daily_loss_limit = 2000.0
        return config

    def test_new_day_resets_tracking(self):
        """New UTC day resets P&L and breach flag."""
        config = self._make_config()
        mock_log = MagicMock()

        with patch("strategies.wf_position_checks.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            new_pnl, new_date, new_breached = check_daily_loss_limit(
                config=config,
                log=mock_log,
                daily_pnl=-300.0,  # was losing money yesterday
                daily_pnl_date="2026-05-11",  # yesterday
                daily_loss_breached=True,
                open_positions={},
                exited_positions=set(),
                last_exit_time={},
            )

        assert new_pnl == 0.0
        assert new_date == "2026-05-12"
        assert new_breached is False

    def test_already_breached_stays_breached(self):
        """Once breached, stay breached for the rest of the day."""
        config = self._make_config()
        mock_log = MagicMock()

        with patch("strategies.wf_position_checks.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 11, 14, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            new_pnl, new_date, new_breached = check_daily_loss_limit(
                config=config,
                log=mock_log,
                daily_pnl=-600.0,
                daily_pnl_date="2026-05-11",
                daily_loss_breached=True,
                open_positions={},
                exited_positions=set(),
                last_exit_time={},
            )

        assert new_breached is True
        assert new_pnl == -600.0
        mock_log.error.assert_not_called()  # no re-trigger

    def test_loss_limit_breached_calls_exit_all(self):
        """When daily loss exceeds limit, call exit_all_positions."""
        config = self._make_config(daily_loss_limit=500.0)
        mock_log = MagicMock()
        mock_cache = MagicMock()

        with patch("strategies.wf_position_checks.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 11, 14, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            with patch("strategies.wf_position_checks.exit_all_positions") as mock_exit:
                new_pnl, new_date, new_breached = check_daily_loss_limit(
                    config=config,
                    log=mock_log,
                    daily_pnl=-550.0,
                    daily_pnl_date="2026-05-11",
                    daily_loss_breached=False,
                    open_positions={"inst1": {}},
                    exited_positions=set(),
                    last_exit_time={},
                    cache=mock_cache,
                )

        mock_exit.assert_called_once()
        assert new_breached is True
        mock_log.error.assert_called()

    def test_loss_within_limit_no_exit(self):
        """Daily loss within limit → no exit triggered."""
        config = self._make_config(daily_loss_limit=500.0)
        mock_log = MagicMock()

        with patch("strategies.wf_position_checks.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 11, 14, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            with patch("strategies.wf_position_checks.exit_all_positions") as mock_exit:
                new_pnl, new_date, new_breached = check_daily_loss_limit(
                    config=config,
                    log=mock_log,
                    daily_pnl=-300.0,
                    daily_pnl_date="2026-05-11",
                    daily_loss_breached=False,
                    open_positions={},
                    exited_positions=set(),
                    last_exit_time={},
                )

        mock_exit.assert_not_called()
        assert new_breached is False
        assert new_pnl == -300.0

    def test_exactly_at_limit_also_breaches(self):
        """Exactly at the limit IS breached (<= triggers)."""
        config = self._make_config(daily_loss_limit=500.0)
        mock_log = MagicMock()
        mock_cache = MagicMock()

        with patch("strategies.wf_position_checks.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 11, 14, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            with patch("strategies.wf_position_checks.exit_all_positions") as mock_exit:
                new_pnl, new_date, new_breached = check_daily_loss_limit(
                    config=config,
                    log=mock_log,
                    daily_pnl=-500.0,  # exactly at limit
                    daily_pnl_date="2026-05-11",
                    daily_loss_breached=False,
                    open_positions={},
                    exited_positions=set(),
                    last_exit_time={},
                    cache=mock_cache,
                )

        mock_exit.assert_called_once()
        assert new_breached is True

    def test_profit_no_action(self):
        """Daily profit → no changes."""
        config = self._make_config()
        mock_log = MagicMock()

        with patch("strategies.wf_position_checks.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 11, 14, 0, 0, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            with patch("strategies.wf_position_checks.exit_all_positions") as mock_exit:
                new_pnl, new_date, new_breached = check_daily_loss_limit(
                    config=config,
                    log=mock_log,
                    daily_pnl=200.0,
                    daily_pnl_date="2026-05-11",
                    daily_loss_breached=False,
                    open_positions={},
                    exited_positions=set(),
                    last_exit_time={},
                )

        mock_exit.assert_not_called()
        assert new_breached is False
        assert new_pnl == 200.0
