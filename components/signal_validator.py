"""Signal validation and scoring for whale trade signals."""

from enum import Enum
from dataclasses import dataclass
from typing import Optional
import time
from nautilus_trader.model.identifiers import InstrumentId


class SignalState(Enum):
    """Signal validation states."""
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass
class ValidationResult:
    """Result of signal validation."""
    state: SignalState
    confidence: float
    reason: str
    timestamp: float
    metadata: dict = None
    
    @property
    def is_valid(self) -> bool:
        return self.state == SignalState.VALIDATED
    
    @property
    def is_rejected(self) -> bool:
        return self.state in (SignalState.REJECTED, SignalState.DUPLICATE)
    
    @property
    def is_timeout(self) -> bool:
        return self.state == SignalState.TIMEOUT
    
    @property
    def is_error(self) -> bool:
        return self.state == SignalState.ERROR

    def __str__(self) -> str:
        return f"{self.state.value}: {self.reason}"


class SignalValidator:
    """Validate whale trade signals.
    
    Validates:
    - Whale is tracked and known
    - Trade size is within acceptable range
    - Price is reasonable (deviation check)
    - Signal confidence meets threshold
    - Trade hasn't been seen before
    
    Scoring:
    - Base confidence from whale's historical win rate
    - Adjusted for trade size (larger = more confidence)
    - Adjusted for whale style (event-driven = higher)
    - Penalty for time since trade (older = less confidence)
    """
    
    def __init__(
        self,
        min_confidence: float = 0.60,
        min_trade_size: float = 5000.0,
        max_trade_size: float = 200000.0,
        max_price_deviation: float = 0.05,
        time_decay_factor: float = 0.999,  # 0.1% decay per second
        min_time_since_trade: float = 0.0,
    ):
        """Initialize validator.
        
        Args:
            min_confidence: Minimum confidence to accept signal
            min_trade_size: Minimum trade size in USD
            max_trade_size: Maximum trade size in USD
            max_price_deviation: Maximum price deviation (0.05 = 5%)
            time_decay_factor: Time decay factor per second
            min_time_since_trade: Minimum time since trade for freshness
        """
        self._min_confidence = min_confidence
        self._min_trade_size = min_trade_size
        self._max_trade_size = max_trade_size
        self._max_price_deviation = max_price_deviation
        self._time_decay_factor = time_decay_factor
        self._min_time_since_trade = min_time_since_trade
    
    def validate_signal(
        self,
        whale_name: str,
        whale_wallet: str,
        condition_id: str,
        token_id: str,
        side: str,
        outcome: str,
        size: float,
        price: float,
        usd_value: float,
        timestamp: float,
        current_market_price: Optional[float] = None,
        whale_roi: Optional[float] = None,
        whale_win_rate: Optional[float] = None,
        whale_avg_trade_size: Optional[float] = None,
        whale_style: Optional[str] = None,
    ) -> ValidationResult:
        """Validate a whale trade signal.
        
        Args:
            whale_name: Whale's display name
            whale_wallet: Whale's proxy wallet address
            condition_id: Market condition ID
            token_id: Market token ID
            side: BUY or SELL
            outcome: YES or NO
            size: Number of shares
            price: Price per share
            usd_value: Total USD value (size * price)
            timestamp: Trade timestamp
            current_market_price: Current market price (for deviation check)
            whale_roi: Whale's historical ROI
            whale_win_rate: Whale's historical win rate
            whale_avg_trade_size: Whale's average trade size
            whale_style: Whale's trading style
        
        Returns:
            ValidationResult with confidence and reason
        """
        try:
            # Check trade size bounds
            if usd_value < self._min_trade_size:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=0.0,
                    reason=f"Trade too small: ${usd_value:,.0f} < ${self._min_trade_size:,.0f}",
                    timestamp=timestamp,
                )
            
            if usd_value > self._max_trade_size:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=0.0,
                    reason=f"Trade too large: ${usd_value:,.0f} > ${self._max_trade_size:,.0f}",
                    timestamp=timestamp,
                )
            
            # Check price (should be 0.01-0.99 for binary options)
            if price <= 0.01 or price >= 0.99:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=0.0,
                    reason=f"Price near resolution: {price:.3f}",
                    timestamp=timestamp,
                )
            
            # Check price deviation if we have current price
            if current_market_price:
                deviation = abs(price - current_market_price) / current_market_price
                if deviation > self._max_price_deviation:
                    return ValidationResult(
                        state=SignalState.REJECTED,
                        confidence=0.0,
                        reason=f"Price deviation: {price:.3f} vs {current_market_price:.3f} ({deviation*100:.1f}%)",
                        timestamp=timestamp,
                    )
            
            # Calculate confidence score
            confidence = self._calculate_confidence(
                whale_name=whale_name,
                whale_roi=whale_roi,
                whale_win_rate=whale_win_rate,
                whale_avg_trade_size=whale_avg_trade_size,
                whale_style=whale_style,
                usd_value=usd_value,
                timestamp=timestamp,
            )
            
            # Check confidence threshold
            if confidence < self._min_confidence:
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=confidence,
                    reason=f"Confidence below threshold: {confidence:.0%} < {self._min_confidence:.0%}",
                    timestamp=timestamp,
                    metadata={"confidence": confidence},
                )
            
            # All checks passed
            return ValidationResult(
                state=SignalState.VALIDATED,
                confidence=confidence,
                reason=f"{whale_name} ({confidence:.0%} conf) {side} {outcome}",
                timestamp=timestamp,
                metadata={
                    "whale_roi": whale_roi,
                    "whale_win_rate": whale_win_rate,
                    "trade_size_factor": usd_value / whale_avg_trade_size if whale_avg_trade_size else 1.0,
                    "price_deviation": abs(price - current_market_price) / current_market_price if current_market_price else 0.0,
                },
            )
        
        except Exception as e:
            return ValidationResult(
                state=SignalState.ERROR,
                confidence=0.0,
                reason=f"Validation error: {e}",
                timestamp=timestamp,
            )
    
    def _calculate_confidence(
        self,
        whale_name: str,
        whale_roi: Optional[float],
        whale_win_rate: Optional[float],
        whale_avg_trade_size: Optional[float],
        whale_style: Optional[str],
        usd_value: float,
        timestamp: float,
    ) -> float:
        """Calculate signal confidence score.
        
        Base formula:
        confidence = (win_rate * 0.8 + 0.2) * size_factor * style_bonus
        
        Where:
        - win_rate * 0.8 + 0.2: Base confidence from historical performance
        - size_factor: Adjust for trade size (larger = more confidence)
        - style_bonus: Bonus for certain whale styles
        """
        # Base confidence from win rate
        base_confidence = 0.5  # Default if win rate unknown
        
        if whale_win_rate is not None:
            base_confidence = whale_win_rate * 0.8 + 0.2
        
        # Adjust for trade size
        if whale_avg_trade_size and whale_avg_trade_size > 0:
            size_ratio = usd_value / whale_avg_trade_size
            size_factor = min(size_ratio / 2.0, 2.0)  # Cap at 2x
            base_confidence *= size_factor
        
        # Style bonus
        style_bonus = 0
        if whale_style == "event_driven":
            style_bonus = 0.1
        elif whale_style == "research_based":
            style_bonus = 0.05
        
        base_confidence = min(base_confidence + style_bonus, 0.95)
        
        # Time decay (older trades = less confidence)
        # Assume ~1000 trades per day average
        trades_per_day = 1000
        days_since_trade = (time.time() - timestamp) / 86400
        time_factor = self._time_decay_factor ** (days_since_trade * trades_per_day)
        base_confidence *= time_factor
        
        return min(base_confidence, 0.95)
    
    def validate_instrument_mapping(
        self,
        condition_id: str,
        token_id: str,
        instrument_id: InstrumentId,
    ) -> ValidationResult:
        """Validate that a market is properly mapped to a Nautilus instrument.
        
        Args:
            condition_id: Polymarket condition ID
            token_id: Polymarket token ID
            instrument_id: Nautilus instrument ID
        
        Returns:
            ValidationResult
        """
        try:
            # Check that condition_id and token_id match the instrument
            # This is a simplified check - in production you'd load the full mapping
            
            # Get expected instrument ID from condition_id and token_id
            from nautilus_trader.adapters.polymarket.common.symbol import (
                get_polymarket_instrument_id,
            )
            
            expected_inst_id = get_polymarket_instrument_id(condition_id, token_id)
            
            # Compare
            if str(expected_inst_id) != str(instrument_id):
                return ValidationResult(
                    state=SignalState.REJECTED,
                    confidence=0.0,
                    reason=f"Instrument ID mismatch: expected {expected_inst_id}, got {instrument_id}",
                    timestamp=time.time(),
                )
            
            return ValidationResult(
                state=SignalState.VALIDATED,
                confidence=1.0,
                reason=f"Instrument mapping verified: {instrument_id}",
                timestamp=time.time(),
                metadata={
                    "condition_id": condition_id,
                    "token_id": token_id,
                },
            )
        
        except Exception as e:
            return ValidationResult(
                state=SignalState.ERROR,
                confidence=0.0,
                reason=f"Mapping check error: {e}",
                timestamp=time.time(),
            )
    
    def get_score_distribution(
        self,
        results: list[ValidationResult],
    ) -> dict:
        """Get distribution of validation scores."""
        if not results:
            return {}
        
        states = {}
        confidences = []
        
        for result in results:
            state = result.state.value
            states[state] = states.get(state, 0) + 1
            confidences.append(result.confidence)
        
        avg_confidence = sum(confidences) / len(confidences)
        
        return {
            "total": len(results),
            "by_state": states,
            "avg_confidence": avg_confidence,
            "min_confidence": min(confidences),
            "max_confidence": max(confidences),
        }
