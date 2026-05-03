#!/usr/bin/env python3
"""Backtesting Grader — evaluates trading strategy performance.

Grades strategies on: Sharpe ratio, max drawdown, win rate, profit factor,
total return, and trade count.

Usage:
    python backtest_grader.py [--log-file PATH] [--simulated-trades N]
    python backtest_grader.py --help

Sources (in priority order):
1. Paper trading log entries (ENTER/EXIT lines)
2. Whale signal history with simulated fills
3. Simulated random trades (for testing the grader itself)
"""

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
WORKSPACE = Path.home() / "workspace"
NAUTILUS_DIR = WORKSPACE / "nautilus-trading"
DASHBOARD_LOG = NAUTILUS_DIR / "dashboard.log"
OUTPUT_DIR = NAUTILUS_DIR / "backtest_results"

# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_time: str
    exit_time: str
    side: str  # "buy" or "sell"
    entry_price: float
    exit_price: float
    size: float  # USD notional
    pnl: float
    market: str = ""

@dataclass
class GradingResult:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_return_pct: float = 0.0
    grade: str = "F"
    grade_reason: str = ""
    trades: list = field(default_factory=list)

# ── Trade extraction ─────────────────────────────────────────────────────────

def extract_trades_from_log(log_path: Path) -> list[Trade]:
    """Extract trades from Nautilus dashboard log.

    Looks for ENTER and EXIT patterns in the log.
    """
    trades = []
    if not log_path.exists():
        return trades

    try:
        text = log_path.read_text()
        # Look for trade entries - pattern varies based on how Nautilus logs them
        # Common patterns:
        # "ENTER: bought YES at 0.45, size $500"
        # "EXIT: sold YES at 0.62, PnL +$85"
        # Or JSON-like entries in the log

        # Try JSON entries first
        for line in text.split("\n"):
            if "ENTER" in line or "EXIT" in line or "FILL" in line:
                trade = _parse_trade_line(line)
                if trade:
                    trades.append(trade)

        if not trades:
            # Try to find PnL entries
            trades = _extract_pnl_entries(text)

    except Exception as e:
        print(f"Error reading log: {e}")

    return trades


def _parse_trade_line(line: str) -> Optional[Trade]:
    """Parse a single trade line from the log."""
    # Try to extract key fields
    entry_match = re.search(r'ENTER.*?(?:buy|BUY|long).*?@\s*([\d.]+)', line)
    exit_match = re.search(r'EXIT.*?(?:sell|SELL|short).*?@\s*([\d.]+)', line)
    pnl_match = re.search(r'(?:PnL|pnl|PNL|profit|loss).*?([+-]?[\d.]+)', line)
    size_match = re.search(r'(?:size|notional|amount).*?\$?([\d,]+\.?\d*)', line)

    if pnl_match:
        pnl = float(pnl_match.group(1))
        entry_price = float(entry_match.group(1)) if entry_match else 0.5
        exit_price = float(exit_match.group(1)) if exit_match else entry_price
        size = float(size_match.group(1).replace(",", "")) if size_match else 100.0

        return Trade(
            entry_time=datetime.now(timezone.utc).isoformat(),
            exit_time=datetime.now(timezone.utc).isoformat(),
            side="buy" if entry_match else "sell",
            entry_price=entry_price,
            exit_price=exit_price,
            size=size,
            pnl=pnl,
        )
    return None


def _extract_pnl_entries(text: str) -> list[Trade]:
    """Extract PnL entries from log text."""
    trades = []
    # Look for realized PnL patterns
    pnl_pattern = re.compile(
        r'(?:realized|closed|profit|loss).*?([+-]?[\d.]+)\s*(?:USD|USDT|\$)?'
    )
    for match in pnl_pattern.finditer(text):
        pnl = float(match.group(1))
        if abs(pnl) > 0.01:  # Ignore tiny values
            trades.append(Trade(
                entry_time="",
                exit_time="",
                side="buy",
                entry_price=0.5,
                exit_price=0.5,
                size=100.0,
                pnl=pnl,
            ))
    return trades[:50]  # Limit to 50 trades


def generate_simulated_trades(n: int, seed: int = 42) -> list[Trade]:
    """Generate simulated trades for testing the grader.

    Uses a simple random walk with configurable edge.
    """
    import random
    random.seed(seed)

    trades = []
    balance = 10000.0
    for i in range(n):
        # Simulate: 55% win rate, avg win 1.5x avg loss
        is_win = random.random() < 0.55
        if is_win:
            pnl = random.uniform(50, 300)
        else:
            pnl = -random.uniform(30, 150)

        entry_price = random.uniform(0.3, 0.7)
        if pnl > 0:
            exit_price = entry_price + abs(pnl) / 1000
        else:
            exit_price = entry_price - abs(pnl) / 1000

        trades.append(Trade(
            entry_time=f"2026-04-{(i % 28) + 1:02d}",
            exit_time=f"2026-04-{(i % 28) + 2:02d}",
            side="buy",
            entry_price=round(entry_price, 3),
            exit_price=round(exit_price, 3),
            size=1000.0,
            pnl=round(pnl, 2),
            market=f"simulated_market_{i % 5}",
        ))
    return trades


# ── Grading logic ─────────────────────────────────────────────────────────────

def grade_strategy(trades: list[Trade], initial_balance: float = 10000.0) -> GradingResult:
    """Grade a strategy based on trade results."""
    result = GradingResult()
    result.trades = trades

    if not trades:
        result.grade = "F"
        result.grade_reason = "No trades to evaluate"
        return result

    result.total_trades = len(trades)

    # Win/loss analysis
    winning = [t for t in trades if t.pnl > 0]
    losing = [t for t in trades if t.pnl < 0]
    result.winning_trades = len(winning)
    result.losing_trades = len(losing)

    if result.winning_trades > 0:
        result.avg_win = sum(t.pnl for t in winning) / len(winning)
    if result.losing_trades > 0:
        result.avg_loss = sum(t.pnl for t in losing) / len(losing)

    result.total_pnl = sum(t.pnl for t in trades)
    result.win_rate = result.winning_trades / result.total_trades if result.total_trades > 0 else 0

    # Profit factor = gross profit / gross loss
    gross_profit = sum(t.pnl for t in winning)
    gross_loss = abs(sum(t.pnl for t in losing))
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Total return
    result.total_return_pct = (result.total_pnl / initial_balance) * 100

    # Sharpe ratio (annualized)
    returns = [t.pnl / initial_balance for t in trades]
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std_r = math.sqrt(variance) if variance > 0 else 0
        if std_r > 0:
            # Annualize: assume ~252 trading days
            daily_sharpe = mean_r / std_r
            result.sharpe_ratio = daily_sharpe * math.sqrt(252)
        else:
            result.sharpe_ratio = 0 if mean_r == 0 else float("inf")

    # Max drawdown
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.pnl
        peak = max(peak, cumulative)
        drawdown = (peak - cumulative) / initial_balance if initial_balance > 0 else 0
        max_dd = max(max_dd, drawdown)
    result.max_drawdown = max_dd * 100

    # Grade calculation
    result.grade, result.grade_reason = _calculate_grade(result)

    return result


def _calculate_grade(r: GradingResult) -> tuple[str, str]:
    """Assign a letter grade based on performance metrics."""
    score = 0
    reasons = []

    # Win rate (max 25 points)
    if r.win_rate >= 0.60:
        score += 25
        reasons.append(f"Excellent win rate: {r.win_rate:.0%}")
    elif r.win_rate >= 0.50:
        score += 15
        reasons.append(f"Decent win rate: {r.win_rate:.0%}")
    elif r.win_rate >= 0.40:
        score += 5
        reasons.append(f"Low win rate: {r.win_rate:.0%}")
    else:
        reasons.append(f"Poor win rate: {r.win_rate:.0%}")

    # Profit factor (max 25 points)
    if r.profit_factor >= 2.0:
        score += 25
        reasons.append(f"Strong profit factor: {r.profit_factor:.2f}")
    elif r.profit_factor >= 1.5:
        score += 15
        reasons.append(f"Good profit factor: {r.profit_factor:.2f}")
    elif r.profit_factor >= 1.0:
        score += 5
        reasons.append(f"Break-even profit factor: {r.profit_factor:.2f}")
    else:
        reasons.append(f"Negative profit factor: {r.profit_factor:.2f}")

    # Sharpe ratio (max 25 points)
    sharpe = r.sharpe_ratio
    if sharpe >= 2.0:
        score += 25
        reasons.append(f"Excellent Sharpe: {sharpe:.2f}")
    elif sharpe >= 1.0:
        score += 15
        reasons.append(f"Good Sharpe: {sharpe:.2f}")
    elif sharpe >= 0.5:
        score += 5
        reasons.append(f"Moderate Sharpe: {sharpe:.2f}")
    else:
        reasons.append(f"Poor Sharpe: {sharpe:.2f}")

    # Max drawdown (max 25 points)
    dd = r.max_drawdown
    if dd <= 5:
        score += 25
        reasons.append(f"Low drawdown: {dd:.1f}%")
    elif dd <= 15:
        score += 15
        reasons.append(f"Manageable drawdown: {dd:.1f}%")
    elif dd <= 25:
        score += 5
        reasons.append(f"High drawdown: {dd:.1f}%")
    else:
        reasons.append(f"Severe drawdown: {dd:.1f}%")

    # Letter grade
    if score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    elif score >= 20:
        grade = "D"
    else:
        grade = "F"

    return grade, "; ".join(reasons)


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report(result: GradingResult, source: str) -> str:
    """Generate a markdown report."""
    lines = []
    lines.append(f"# Strategy Grading Report")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Data source: {source}")
    lines.append("")

    # Grade header
    grade_emoji = {"A": "🏆", "B": "✅", "C": "⚠️", "D": "🔴", "F": "💀"}.get(result.grade, "?")
    lines.append(f"## Overall Grade: {grade_emoji} {result.grade}")
    lines.append(f"{result.grade_reason}")
    lines.append("")

    # Metrics table
    lines.append("## Performance Metrics\n")
    lines.append("| Metric | Value | Assessment |")
    lines.append("|--------|-------|------------|")

    def assess_pct(value, good, ok):
        if value >= good:
            return "✅ Excellent"
        elif value >= ok:
            return "⚠️ Acceptable"
        else:
            return "❌ Poor"

    def assess_float(value, good, ok):
        if value >= good:
            return "✅ Excellent"
        elif value >= ok:
            return "⚠️ Acceptable"
        else:
            return "❌ Poor"

    lines.append(f"| Total Trades | {result.total_trades} | {'✅' if result.total_trades >= 20 else '⚠️ Limited sample'} |")
    lines.append(f"| Win Rate | {result.win_rate:.1%} | {assess_pct(result.win_rate, 0.60, 0.50)} |")
    lines.append(f"| Total PnL | ${result.total_pnl:,.2f} | {'✅' if result.total_pnl > 0 else '❌'} |")
    lines.append(f"| Profit Factor | {result.profit_factor:.2f} | {assess_float(result.profit_factor, 2.0, 1.5)} |")
    lines.append(f"| Sharpe Ratio | {result.sharpe_ratio:.2f} | {assess_float(result.sharpe_ratio, 2.0, 1.0)} |")
    lines.append(f"| Max Drawdown | {result.max_drawdown:.1f}% | {'✅' if result.max_drawdown <= 5 else '⚠️' if result.max_drawdown <= 15 else '❌'} |")
    lines.append(f"| Avg Win | ${result.avg_win:,.2f} | |")
    lines.append(f"| Avg Loss | ${result.avg_loss:,.2f} | |")
    lines.append(f"| Total Return | {result.total_return_pct:+.2f}% | |")
    lines.append("")

    # Trade details (last 10)
    if result.trades:
        lines.append("## Recent Trades\n")
        lines.append("| # | Side | Entry | Exit | PnL |")
        lines.append("|---|------|-------|------|-----|")
        for i, t in enumerate(result.trades[-10:], 1):
            pnl_icon = "🟢" if t.pnl > 0 else "🔴"
            lines.append(f"| {i} | {t.side} | {t.entry_price:.3f} | {t.exit_price:.3f} | {pnl_icon} ${t.pnl:+.2f} |")
        lines.append("")

    # Recommendations
    lines.append("## Recommendations\n")
    if result.grade in ["A", "B"]:
        lines.append("- Strategy is performing well. Consider paper trading with more markets.")
        lines.append("- Monitor for overfitting — validate on out-of-sample data.")
    elif result.grade == "C":
        lines.append("- Strategy is marginally profitable. Review entry/exit logic.")
        lines.append("- Consider adjusting stop-loss and take-profit levels.")
        lines.append("- Increase sample size before making conclusions.")
    else:
        lines.append("- Strategy needs significant improvement.")
        lines.append("- Review whale signal quality and timing.")
        lines.append("- Consider Kelly fraction adjustment.")
        lines.append("- Do NOT deploy to live trading.")
    lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtesting Grader for Polymarket Whale Follower")
    parser.add_argument("--log-file", type=str, default=str(DASHBOARD_LOG),
                        help="Path to trading log file")
    parser.add_argument("--simulated-trades", type=int, default=0,
                        help="Generate N simulated trades (for testing)")
    parser.add_argument("--bankroll", type=float, default=10000.0,
                        help="Initial bankroll for return calculations")
    parser.add_argument("--output", type=str, default="",
                        help="Output file path (default: auto-generated)")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Get trades
    trades = []
    source = "unknown"

    if args.simulated_trades > 0:
        trades = generate_simulated_trades(args.simulated_trades)
        source = f"Simulated ({args.simulated_trades} trades)"
    else:
        trades = extract_trades_from_log(Path(args.log_file))
        source = f"Log file: {args.log_file}"

    if not trades:
        print("No trades found in log. Generating 50 simulated trades for demonstration.")
        trades = generate_simulated_trades(50)
        source = "Simulated fallback (no trades in log)"

    # Grade
    result = grade_strategy(trades, args.bankroll)

    # Report
    report = generate_report(result, source)

    # Output
    output_path = args.output or str(OUTPUT_DIR / f"grade-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.md")
    Path(output_path).write_text(report)

    # Print summary
    print(report)
    print(f"\nReport saved to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
