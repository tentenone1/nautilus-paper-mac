"""Signal Generator for the Whale Discovery Pipeline.

Reads discovered whales from whale_discovery.db and generates
trading signals filtered by alpha_score, position size, and win rate.
"""

import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import sqlite3


@dataclass
class WhaleSignal:
    """A trading signal generated from a whale's position."""
    whale_address: str
    market_slug: str
    condition_id: str
    token_id: str
    outcome: str
    side: str  # "buy_yes", "buy_no", "sell_yes", "sell_no"
    size: float  # USD value
    price: float  # Market price %
    confidence: float  # 0-100 confidence score
    detected_at: str  # ISO timestamp
    whale_name: Optional[str] = None
    whale_alpha_score: Optional[float] = None
    whale_win_rate: Optional[float] = None
    capital_tier: str = 'E'
    precision_tier: str = 'LOW'


class SignalGenerator:
    """Generates and filters whale signals from discovered wallets."""

    def __init__(self, db_path: str, min_alpha_score: float = 70.0, min_position_size: float = 5000.0):
        """
        Initialize the signal generator.

        Args:
            db_path: Path to the whale_discovery.db SQLite database
            min_alpha_score: Minimum alpha score to consider a whale
            min_position_size: Minimum position size in USD
        """
        self.db_path = db_path
        self.min_alpha_score = min_alpha_score
        self.min_position_size = min_position_size
        self._conn: Optional[sqlite3.Connection] = None
        self._last_signal_time: float = 0.0
        self._signal_cooldown_seconds = 10.0  # Rate limit signals

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None or not self._conn:
            self._conn = sqlite3.connect(self.db_path, timeout=30.0)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_tables(self) -> None:
        """Ensure the whales and whale_signals tables exist."""
        conn = self._get_connection()
        
        # Create whales table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whales (
                address TEXT PRIMARY KEY,
                name TEXT,
                alpha_score REAL,
                pnl REAL,
                volume REAL,
                win_rate REAL,
                total_trades INTEGER,
                last_seen TEXT,
                tags TEXT,
                notes TEXT
            )
        """)
        
        # Create whale_signals table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whale_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whale_address TEXT,
                market_slug TEXT,
                condition_id TEXT,
                token_id TEXT,
                outcome TEXT,
                side TEXT,
                size REAL,
                price REAL,
                confidence REAL,
                detected_at TEXT,
                FOREIGN KEY (whale_address) REFERENCES whales(address)
            )
        """)
        
        # Create scan_log table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT,
                wallets_found INTEGER,
                signals_found INTEGER,
                errors TEXT
            )
        """)
        
        conn.commit()

    def generate_signal(
        self,
        whale_address: str,
        market_slug: str,
        condition_id: str,
        token_id: str,
        outcome: str,
        side: str,
        size: float,
        price: float,
        whale_name: Optional[str] = None,
        whale_alpha_score: Optional[float] = None,
        whale_win_rate: Optional[float] = None,
        capital_tier: str = 'E',
        precision_tier: str = 'LOW',
    ) -> Optional[WhaleSignal]:
        """
        Generate a signal if it passes filtering criteria.

        Args:
            whale_address: The whale's Ethereum address
            market_slug: Polymarket market slug
            condition_id: Market condition ID
            token_id: Market token ID
            outcome: "Yes" or "No"
            side: "buy_yes", "buy_no", "sell_yes", "sell_no"
            size: Position size in USD
            price: Market price as decimal (e.g., 0.87 = 87%)
            whale_name: Optional whale name
            whale_alpha_score: Optional alpha score
            whale_win_rate: Optional win rate

        Returns:
            WhaleSignal if filtered, None otherwise
        """
        now = time.time()

        # Rate limit signals
        if now - self._last_signal_time < self._signal_cooldown_seconds:
            return None
        self._last_signal_time = now

        # Filter by alpha score
        alpha = whale_alpha_score if whale_alpha_score is not None else 70.0
        if alpha < self.min_alpha_score:
            return None

        # Filter by position size
        if size < self.min_position_size:
            return None

        # Filter by win rate if provided
        wr = whale_win_rate if whale_win_rate is not None else 0.50
        if wr < 0.50:
            return None

        # Calculate confidence based on alpha and size
        confidence = self._calculate_confidence(alpha, size)

        # Format side
        formatted_side = self._format_side(side, outcome)

        # Create signal
        signal = WhaleSignal(
            whale_address=whale_address,
            market_slug=market_slug,
            condition_id=condition_id,
            token_id=token_id,
            outcome=outcome,
            side=formatted_side,
            size=size,
            price=price,
            confidence=confidence,
            detected_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            whale_name=whale_name,
            whale_alpha_score=alpha,
            whale_win_rate=wr,
            capital_tier=capital_tier,
            precision_tier=precision_tier,
        )

        return signal

    def _calculate_confidence(self, alpha_score: float, size: float) -> float:
        """
        Calculate confidence score based on alpha and position size.

        Args:
            alpha_score: Whale's alpha score (0-100)
            size: Position size in USD

        Returns:
            Confidence score 0-100
        """
        # Base confidence from alpha
        alpha_confidence = alpha_score / 100.0 * 60.0  # 0-60 from alpha

        # Size-based confidence (larger positions = higher confidence)
        size_factor = min(size / 100000.0, 1.0)  # Cap at 100k
        size_confidence = size_factor * 40.0  # 0-40 from size

        # Win rate bonus
        win_rate_confidence = (0.60 - 0.50) / 0.10 * 10.0  # 0-10 from win rate

        # Combine
        total_confidence = alpha_confidence + size_confidence + win_rate_confidence
        return min(95.0, total_confidence)

    def _format_side(self, side: str, outcome: str) -> str:
        """Format side as standard notation."""
        if outcome == "Yes":
            return f"buy_yes" if side == "buy" else "sell_yes"
        elif outcome == "No":
            return f"buy_no" if side == "sell" else "sell_no"
        return side

    def store_signal(self, signal: WhaleSignal, db_path: Optional[str] = None) -> bool:
        """
        Store signal in the database.

        Args:
            signal: The WhaleSignal to store
            db_path: Optional override for database path

        Returns:
            True if stored successfully
        """
        db_path = db_path or self.db_path
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            conn.execute("""
                INSERT INTO whale_signals 
                (whale_address, market_slug, condition_id, token_id, outcome, side, 
                 size, price, confidence, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.whale_address,
                signal.market_slug,
                signal.condition_id,
                signal.token_id,
                signal.outcome,
                signal.side,
                signal.size,
                signal.price,
                signal.confidence,
                signal.detected_at,
            ))
            conn.commit()
            return True
        except Exception as e:
            print(f"Failed to store signal: {e}")
            return False

    def store_whale(self, whale: Dict[str, Any], db_path: Optional[str] = None) -> bool:
        """
        Store whale in the whales table.

        Args:
            whale: Dict with keys: address, name, alpha_score, pnl, volume, 
                   win_rate, total_trades, last_seen, tags, notes
            db_path: Optional override for database path

        Returns:
            True if stored successfully
        """
        db_path = db_path or self.db_path
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO whales
                (address, name, alpha_score, pnl, volume, win_rate, total_trades, 
                 last_seen, tags, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                whale.get("address"),
                whale.get("name"),
                whale.get("alpha_score"),
                whale.get("pnl"),
                whale.get("volume"),
                whale.get("win_rate"),
                whale.get("total_trades"),
                whale.get("last_seen"),
                whale.get("tags"),
                whale.get("notes"),
            ))
            conn.commit()
            return True
        except Exception as e:
            print(f"Failed to store whale: {e}")
            return False

    def log_scan(self, scan_time: str, wallets_found: int, signals_found: int, errors: str, 
                 db_path: Optional[str] = None) -> None:
        """
        Log a scan cycle.

        Args:
            scan_time: ISO timestamp
            wallets_found: Number of wallets discovered
            signals_found: Number of signals found
            errors: Error messages (JSON string)
            db_path: Optional override for database path
        """
        db_path = db_path or self.db_path
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            conn.execute("""
                INSERT INTO scan_log (scan_time, wallets_found, signals_found, errors)
                VALUES (?, ?, ?, ?)
            """, (scan_time, wallets_found, signals_found, errors or "[]"))
            conn.commit()
        except Exception as e:
            print(f"Failed to log scan: {e}")
        finally:
            conn.close()

    def get_recent_signals(self, limit: int = 100, db_path: Optional[str] = None) -> List[WhaleSignal]:
        """
        Get recent signals from the database.

        Args:
            limit: Maximum number of signals to return
            db_path: Optional override for database path

        Returns:
            List of WhaleSignal objects
        """
        db_path = db_path or self.db_path
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            cursor = conn.execute("""
                SELECT whale_address, market_slug, condition_id, token_id, 
                       outcome, side, size, price, confidence, detected_at,
                       whales.name, whales.alpha_score, whales.win_rate,
                       whales.capital_tier, whales.precision_tier
                FROM whale_signals
                JOIN whales ON whale_signals.whale_address = whales.address
                ORDER BY detected_at DESC
                LIMIT ?
            """, (limit,))
            
            signals = []
            for row in cursor:
                signals.append(WhaleSignal(
                    whale_address=row["whale_address"],
                    market_slug=row["market_slug"],
                    condition_id=row["condition_id"],
                    token_id=row["token_id"],
                    outcome=row["outcome"],
                    side=row["side"],
                    size=row["size"],
                    price=row["price"],
                    confidence=row["confidence"],
                    detected_at=row["detected_at"],
                    whale_name=row["name"],
                    whale_alpha_score=row["alpha_score"],
                    whale_win_rate=row["win_rate"],
                    capital_tier=row["capital_tier"],
                    precision_tier=row["precision_tier"],
                ))
            return signals
        finally:
            conn.close()

    def get_active_whales(self, min_alpha: float = 70.0, db_path: Optional[str] = None) -> List[Dict]:
        """
        Get whales with alpha score >= threshold.

        Args:
            min_alpha: Minimum alpha score
            db_path: Optional override for database path

        Returns:
            List of whale dicts
        """
        db_path = db_path or self.db_path
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            cursor = conn.execute("""
                SELECT address, name, alpha_score, pnl, volume, win_rate, 
                       total_trades, last_seen, tags, notes
                FROM whales
                WHERE alpha_score >= ?
                ORDER BY alpha_score DESC
            """, (min_alpha,))
            
            return [dict(row) for row in cursor]
        finally:
            conn.close()

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
