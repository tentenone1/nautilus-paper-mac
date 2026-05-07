#!/usr/bin/env python3
"""Price Pump Tracker — monitors markets for price movements and whale follow.

Reads from data/pump_tracker_events.json. Polls CLOB midpoint every 60s.
Alerts on pump>50% or dump>20% from entry. Detects follow whales.
Writes alerts to research/pump_tracker_alerts.json.
State in research/pump_tracker_state.json.

Usage: python scripts/price_pump_tracker.py --once    # Single pass
       python scripts/price_pump_tracker.py --daemon # Continuous
"""

from __future__ import annotations
import argparse, json, logging, signal, sys, time, urllib.request, urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("price_pump_tracker")

POLL_INTERVAL_SECONDS: float = 60.0
PUMP_THRESHOLD: float = 0.50
DUMP_THRESHOLD: float = 0.20
REQUEST_TIMEOUT_SECONDS: float = 10.0
TRADES_LIMIT: int = 20
CLOB_API: str = "https://clob.polymarket.com"
DATA_API: str = "https://data-api.polymarket.com"

ROOT = Path(__file__).resolve().parent.parent
EVENTS_FILE = ROOT / "data" / "pump_tracker_events.json"
STATE_FILE = ROOT / "research" / "pump_tracker_state.json"
ALERTS_FILE = ROOT / "research" / "pump_tracker_alerts.json"


@dataclass
class AlertEvent:
    alert_id: str
    market_id: str
    market_title: str
    whale_address: str
    whale_name: str
    signal_id: str
    entry_price: float
    current_price: float
    price_change_pct: float
    alert_type: str
    timestamp: float
    follow_whales: list[str] = field(default_factory=list)


@dataclass
class SubscribedEvent:
    signal_id: str
    market_id: str
    entry_price: float
    whale_address: str
    whale_name: str
    market_title: str
    timestamp: float
    event_type: str


def _fetch(url: str) -> Optional[dict | list]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=int(REQUEST_TIMEOUT_SECONDS)) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def get_midpoint(market_id: str) -> Optional[float]:
    data = _fetch(f"{CLOB_API}/markets/{market_id}")
    if not data or not isinstance(data, dict):
        return None
    try:
        book = data.get("orderbook", {})
        bids, asks = book.get("bids", []) or [], book.get("asks", []) or []
        if not bids or not asks:
            return None
        best_bid, best_ask = (
            float(bids[0].get("price", 0)),
            float(asks[0].get("price", 1)),
        )
        return (best_bid + best_ask) / 2 if best_bid > 0 and best_ask < 1 else None
    except (ValueError, TypeError, KeyError):
        return None


def get_follow_whales(market_id: str, known: set[str]) -> list[str]:
    data = _fetch(f"{DATA_API}/trades?conditionId={market_id}&limit={TRADES_LIMIT}")
    if not data:
        return []
    trades = data.get("data", []) if isinstance(data, dict) else data
    return list(
        {
            t.get("trader") or t.get("address") or t.get("proxyWallet", "")
            for t in trades
            if (t.get("trader") or t.get("address") or t.get("proxyWallet", "")).lower()
            not in known
        }
    )[:5]


def load_events() -> list[SubscribedEvent]:
    if not EVENTS_FILE.exists():
        return []
    try:
        data = (
            json.loads(EVENTS_FILE.read_text())
            if EVENTS_FILE.read_text().strip()
            else []
        )
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        data = [data]
    return [
        SubscribedEvent(
            signal_id=e.get("signal_id", ""),
            market_id=e.get("market_id", ""),
            entry_price=float(e.get("entry_price", 0)),
            whale_address=e.get("whale_address", ""),
            whale_name=e.get("whale_name", ""),
            market_title=e.get("market_title", ""),
            timestamp=float(e.get("timestamp", 0)),
            event_type=e.get("event_type", ""),
        )
        for e in data
        if e.get("event_type") == "signal_entry"
    ]


def load_state() -> dict[str, dict]:
    if not STATE_FILE.exists():
        return {}
    try:
        return (
            json.loads(STATE_FILE.read_text()) if STATE_FILE.read_text().strip() else {}
        )
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict[str, dict]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def append_alert(alert: AlertEvent) -> None:
    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    alerts = []
    if ALERTS_FILE.exists():
        try:
            content = ALERTS_FILE.read_text()
            if content.strip():
                alerts = json.loads(content)
        except (json.JSONDecodeError, OSError):
            alerts = []
    if not isinstance(alerts, list):
        alerts = [alerts]
    alerts.append(
        {
            "alert_id": alert.alert_id,
            "market_id": alert.market_id,
            "market_title": alert.market_title,
            "whale_address": alert.whale_address,
            "whale_name": alert.whale_name,
            "signal_id": alert.signal_id,
            "entry_price": alert.entry_price,
            "current_price": alert.current_price,
            "price_change_pct": alert.price_change_pct,
            "alert_type": alert.alert_type,
            "timestamp": alert.timestamp,
            "follow_whales": alert.follow_whales,
        }
    )
    ALERTS_FILE.write_text(json.dumps(alerts[-500:], indent=2))
    logger.info(
        "Alert: %s %.1f%% entry=%.4f cur=%.4f",
        alert.alert_type,
        alert.price_change_pct * 100,
        alert.entry_price,
        alert.current_price,
    )


def run_once() -> int:
    events = load_events()
    if not events:
        logger.info("No events to check")
        return 0
    state = load_state()
    known = {e.whale_address.lower() for e in events if e.whale_address}
    alerts = 0
    seen = {e.market_id for e in events}
    for market_id in seen:
        event = next((e for e in events if e.market_id == market_id), None)
        if not event:
            continue
        price = get_midpoint(market_id)
        if price is None:
            continue
        change = (
            (price - event.entry_price) / event.entry_price
            if event.entry_price > 0
            else 0
        )
        alert_type = (
            "pump"
            if change >= PUMP_THRESHOLD
            else "dump"
            if change <= -DUMP_THRESHOLD
            else None
        )
        ts = time.time()
        whales = get_follow_whales(market_id, known) if alert_type else []
        if alert_type:
            a = AlertEvent(
                alert_id=f"{event.signal_id[:8]}-{alert_type}-{int(ts)}",
                market_id=market_id,
                market_title=event.market_title,
                whale_address=event.whale_address,
                whale_name=event.whale_name,
                signal_id=event.signal_id,
                entry_price=event.entry_price,
                current_price=price,
                price_change_pct=change,
                alert_type=alert_type,
                timestamp=ts,
                follow_whales=whales,
            )
            append_alert(a)
            alerts += 1
        state[market_id] = {"last_check_timestamp": ts, "last_price": price}
    save_state(state)
    logger.info("Pass complete: %d alerts", alerts)
    return alerts


def run_daemon() -> None:
    logger.info("Starting daemon")

    def shutdown(sig, _):
        logger.info("Shutting down")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    while True:
        run_once()
        time.sleep(int(POLL_INTERVAL_SECONDS))


def main() -> int:
    parser = argparse.ArgumentParser(description="Price Pump Tracker")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--once", action="store_true", help="Run single pass")
    args = parser.parse_args()
    logger.setLevel(logging.DEBUG)
    run_daemon() if args.daemon else run_once()
    return 0


if __name__ == "__main__":
    sys.exit(main())
