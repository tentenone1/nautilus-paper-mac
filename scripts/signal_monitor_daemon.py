"""Real-time signal account monitor for Polymarket whale tracking."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("signal_monitor")

# ─── Constants ───────────────────────────────────────────────────────────────

POLL_INTERVAL_SECS: int = 300
LOW_PRICE_THRESHOLD: float = 0.50
MAX_RETRIES: int = 3
RETRY_BACKOFF_SECS: int = 10
TRADES_API_LIMIT: int = 20
DATA_API_BASE: str = "https://data-api.polymarket.com"
STATE_FILE: str = "research/signal_monitor_state.json"
DETECTIONS_FILE: str = "research/signal_monitor_detections.json"

KNOWN_WALLETS: dict[str, str] = {
    "pilotbaby": "0x6815040a7176c958e6ff8818bfe188e80dbd9edb",
    "Herdonia": "0xd106952ebf30a3125affd8a23b6c1f30c35fc79c",
    "mooseborzoi": "",
    "beetlepimp": "",
    "surfandturf": "",
    "loitterer": "",
}


class MonitorError(Exception):
    """Error in signal monitor operations."""


@dataclass(frozen=True)
class TradeEvent:
    """A single trade event from the Polymarket Data API."""
    proxy_wallet: str
    timestamp: str
    price: float
    condition_id: str
    outcome: str
    trade_type: str
    title: str


@dataclass(frozen=True)
class DetectionResult:
    """Notable whale entry detected by the monitor."""
    whale_name: str
    wallet: str
    trade: TradeEvent
    detected_at: str
    is_low_price: bool
    alert: str


@dataclass
class MonitorState:
    """Persistent state for the signal monitor."""
    last_check: dict[str, str] = field(default_factory=dict)
    last_run: str = ""

    @classmethod
    def load(cls, path: Path) -> MonitorState:
        """Load state from JSON. Returns empty state if file missing."""
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(
                last_check=data.get("last_check", {}),
                last_run=data.get("last_run", ""),
            )
        except json.JSONDecodeError as exc:
            raise MonitorError(f"Invalid state file {path}: {exc}") from exc

    def save(self, path: Path) -> None:
        """Save state to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_check": self.last_check, "last_run": self.last_run}, indent=2))


def append_detection(detection: DetectionResult, path: Path) -> None:
    """Append detection to JSON file (append-only, never overwrite)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = json.loads(path.read_text()) if path.exists() else []
        if not isinstance(existing, list):
            existing = [existing]
    except json.JSONDecodeError:
        existing = []
    existing.append(asdict(detection))
    path.write_text(json.dumps(existing, indent=2))


def fetch_wallet_trades(wallet: str, limit: int = TRADES_API_LIMIT) -> list[dict]:
    """Fetch recent trades for a wallet. Retries with backoff on failure."""
    url = f"{DATA_API_BASE}/trades"
    params = {"proxyWallet": wallet, "limit": limit}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF_SECS * (2 ** attempt)
                logger.warning("Rate limited, retrying in %ds", wait, extra={"attempt": attempt + 1})
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                raise MonitorError(f"API error {resp.status_code} for wallet {wallet[:10]}...")
            return resp.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES - 1:
                raise MonitorError(f"Failed after {MAX_RETRIES} attempts for {wallet[:10]}...") from exc
            wait = RETRY_BACKOFF_SECS * (2 ** attempt)
            logger.warning("Request failed, retrying in %ds: %s", wait, exc, extra={"attempt": attempt + 1})
            time.sleep(wait)
    return []


def parse_trade(raw: dict) -> Optional[TradeEvent]:
    """Parse raw trade dict into TradeEvent. Returns None if required fields missing."""
    try:
        price = float(raw.get("price", 0))
    except (TypeError, ValueError):
        return None
    if not raw.get("proxyWallet") or not raw.get("conditionId"):
        return None
    return TradeEvent(
        proxy_wallet=raw["proxyWallet"],
        timestamp=raw.get("timestamp", ""),
        price=price,
        condition_id=raw["conditionId"],
        outcome=raw.get("outcome", ""),
        trade_type=raw.get("type", ""),
        title=raw.get("title", ""),
    )


class SignalMonitor:
    """Monitors known signal wallets for new trades on Polymarket."""

    def __init__(
        self,
        wallets: Optional[dict[str, str]] = None,
        state_path: Optional[Path] = None,
        detections_path: Optional[Path] = None,
        low_price_threshold: float = LOW_PRICE_THRESHOLD,
    ) -> None:
        """Initialize the signal monitor.

        Args:
            wallets: Dict mapping whale names to wallet addresses (empty = skip).
            state_path: Path to state JSON file.
            detections_path: Path to detections JSON file.
            low_price_threshold: Price below which an alert is triggered.
        """
        self.wallets = wallets or KNOWN_WALLETS
        self.low_price_threshold = low_price_threshold
        project_root = Path(__file__).resolve().parent.parent
        self.state_path = state_path or (project_root / STATE_FILE)
        self.detections_path = detections_path or (project_root / DETECTIONS_FILE)
        self.state = MonitorState.load(self.state_path)

    def check_wallet(self, whale_name: str, wallet: str) -> list[DetectionResult]:
        """Check wallet for new trades since last check. Returns detections."""
        last_check = self.state.last_check.get(wallet, "")
        trades = fetch_wallet_trades(wallet)
        detections: list[DetectionResult] = []
        for raw in trades:
            trade = parse_trade(raw)
            if trade is None:
                continue
            if last_check and trade.timestamp <= last_check:
                continue
            is_low = trade.trade_type == "BUY" and trade.price < self.low_price_threshold
            if is_low or trade.trade_type == "BUY":
                now = datetime.now(timezone.utc).isoformat()
                alert_parts = [whale_name]
                if is_low:
                    alert_parts.append(f"LOW PRICE entry at ${trade.price:.2f}")
                else:
                    alert_parts.append(f"BUY at ${trade.price:.2f}")
                alert_parts.append(f"on '{trade.title[:80]}'")
                detection = DetectionResult(
                    whale_name=whale_name, wallet=wallet, trade=trade,
                    detected_at=now, is_low_price=is_low, alert=" ".join(alert_parts),
                )
                detections.append(detection)
                append_detection(detection, self.detections_path)
        if trades:
            self.state.last_check[wallet] = trades[0].get("timestamp", "")
        return detections

    def run_once(self) -> list[DetectionResult]:
        """Single monitoring cycle across all wallets."""
        all_detections: list[DetectionResult] = []
        now = datetime.now(timezone.utc).isoformat()
        for whale_name, wallet in self.wallets.items():
            if not wallet:
                logger.warning("Skipping %s — wallet address not yet discovered", whale_name, extra={"whale": whale_name})
                continue
            logger.info("Checking %s (%s...)", whale_name, wallet[:10], extra={"whale": whale_name})
            try:
                detections = self.check_wallet(whale_name, wallet)
                all_detections.extend(detections)
                for det in detections:
                    if det.is_low_price:
                        logger.warning("ALERT: %s", det.alert, extra={"whale": det.whale_name, "price": det.trade.price})
            except MonitorError as exc:
                logger.error("Failed to check %s: %s", whale_name, exc, extra={"whale": whale_name})
        self.state.last_run = now
        self.state.save(self.state_path)
        logger.info("Cycle complete: %d detection(s)", len(all_detections), extra={"detection_count": len(all_detections)})
        return all_detections

    def run_daemon(self, interval: int = POLL_INTERVAL_SECS) -> None:
        """Run as continuous daemon with given interval."""
        logger.info("Starting signal monitor daemon (interval=%ds)", interval, extra={"interval": interval})
        try:
            while True:
                self.run_once()
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Daemon stopped by user")


def setup_logging(verbose: bool = False) -> None:
    """Configure logging. DEBUG if verbose, otherwise INFO."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")


def main() -> None:
    """CLI entry point: --once for cron, default for daemon mode."""
    parser = argparse.ArgumentParser(description="Polymarket Signal Account Monitor")
    parser.add_argument("--once", action="store_true", help="Run once and exit (for cron)")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL_SECS, help=f"Polling interval (default: {POLL_INTERVAL_SECS})")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    setup_logging(args.verbose)
    monitor = SignalMonitor()
    if args.once:
        detections = monitor.run_once()
        if detections:
            print(f"Detected {len(detections)} signal(s):")
            for det in detections:
                marker = "LOW PRICE" if det.is_low_price else "BUY"
                print(f"  [{marker}] {det.alert}")
        else:
            print("No new signals detected.")
        sys.exit(0)
    else:
        monitor.run_daemon(interval=args.interval)


if __name__ == "__main__":
    main()
