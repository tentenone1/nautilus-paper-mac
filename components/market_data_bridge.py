"""Bridge between Polymarket data API and Nautilus instruments."""

from typing import Optional, List
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.adapters.polymarket.common.symbol import (
    get_polymarket_instrument_id,
    get_polymarket_condition_id,
    get_polymarket_token_id,
)


class MarketDataBridge:
    """Bridge between Polymarket data API and Nautilus instruments.
    
    Handles:
    - Loading instrument mappings for target markets
    - Converting between Polymarket IDs and Nautilus IDs
    - Scanning for whale trades in specific markets
    - Caching market data for faster lookups
    """
    
    def __init__(self):
        """Initialize bridge."""
        self._instrument_cache: dict[str, InstrumentId] = {}
        self._condition_to_instrument: dict[str, InstrumentId] = {}
        self._token_to_instrument: dict[str, InstrumentId] = {}
        self._condition_to_token: dict[str, str] = {}
    
    def load_instrument_mapping(
        self,
        condition_id: str,
        token_id: str,
    ) -> InstrumentId:
        """Load instrument mapping for a specific market.
        
        Args:
            condition_id: Polymarket condition ID
            token_id: Polymarket token ID
        
        Returns:
            Nautilus InstrumentId
        """
        instrument_id = get_polymarket_instrument_id(condition_id, token_id)
        
        # Cache mappings
        self._instrument_cache[str(instrument_id)] = instrument_id
        self._condition_to_instrument[condition_id] = instrument_id
        self._token_to_instrument[token_id] = instrument_id
        self._condition_to_token[condition_id] = token_id
        
        return instrument_id
    
    def load_multi_market_mapping(
        self,
        markets: dict[str, dict],
    ) -> None:
        """Load mappings for multiple markets at once.
        
        Args:
            markets: Dict of market_name -> {condition_id, token_id}
        """
        for market_name, market_data in markets.items():
            condition_id = market_data.get("condition_id")
            token_id = market_data.get("token_id")
            if condition_id and token_id:
                self.load_instrument_mapping(condition_id, token_id)
    
    def get_condition_id(
        self,
        instrument_id: InstrumentId,
    ) -> Optional[str]:
        """Get condition ID for an instrument."""
        try:
            cond_id = self._condition_to_instrument.get(str(instrument_id))
            if not cond_id:
                # Try reverse mapping
                for inst, c_id in self._condition_to_instrument.items():
                    if str(inst) == str(instrument_id):
                        cond_id = c_id
                        break
        except Exception:
            pass
        return cond_id
    
    def get_token_id(
        self,
        instrument_id: InstrumentId,
    ) -> Optional[str]:
        """Get token ID for an instrument."""
        try:
            token_id = self._token_to_instrument.get(str(instrument_id))
            if not token_id:
                # Try reverse mapping
                for inst, t_id in self._token_to_instrument.items():
                    if str(inst) == str(instrument_id):
                        token_id = t_id
                        break
        except Exception:
            pass
        return token_id
    
    def get_all_condition_ids(
        self,
    ) -> List[str]:
        """Get all loaded condition IDs."""
        return list(self._condition_to_instrument.keys())
    
    def get_all_token_ids(
        self,
    ) -> List[str]:
        """Get all loaded token IDs."""
        return list(self._token_to_instrument.keys())
    
    def clear_cache(self) -> None:
        """Clear all cached mappings."""
        self._instrument_cache.clear()
        self._condition_to_instrument.clear()
        self._token_to_instrument.clear()
        self._condition_to_token.clear()
    
    def scan_for_whale_trades_in_market(
        self,
        instrument_id: InstrumentId,
        whale_wallet: str,
        offset: int = 0,
        limit: int = 100,
    ) -> List[dict]:
        """Scan for whale trades in a specific market.
        
        Args:
            instrument_id: Nautilus instrument ID
            whale_wallet: Whale's proxy wallet address
            offset: Pagination offset
            limit: Trades per page
        
        Returns:
            List of whale trades
        """
        condition_id = self._condition_to_instrument.get(str(instrument_id))
        if not condition_id:
            return []
        
        token_id = self._token_to_instrument.get(str(instrument_id))
        if not token_id:
            return []
        
        # Scan API for trades
        url = "https://data-api.polymarket.com/trades"
        params = {
            "limit": limit,
            "offset": offset,
            "conditionId": condition_id,
        }
        
        try:
            import requests
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                trades = resp.json()
                # Filter for whale trades
                whale_trades = [t for t in trades if t.get("proxyWallet") == whale_wallet]
                return whale_trades
        except Exception:
            pass
        
        return []
    
    def scan_all_markets_for_whale(
        self,
        whale_wallet: str,
        offset: int = 0,
        limit: int = 100,
    ) -> List[dict]:
        """Scan all loaded markets for whale trades.
        
        Args:
            whale_wallet: Whale's proxy wallet address
            offset: Pagination offset
            limit: Trades per page
        
        Returns:
            List of whale trades across all markets
        """
        all_trades: List[dict] = []
        
        for condition_id in self._condition_to_instrument.keys():
            trades = self.scan_for_whale_trades_in_market(
                instrument_id=None,  # Use condition_id directly
                whale_wallet=whale_wallet,
                offset=offset,
                limit=limit,
            )
            all_trades.extend(trades)
        
        return all_trades
    
    def get_market_info(
        self,
        condition_id: str,
    ) -> Optional[dict]:
        """Get market info for a condition ID.
        
        Args:
            condition_id: Polymarket condition ID
        
        Returns:
            Market info dict or None
        """
        try:
            url = "https://data-api.polymarket.com/markets"
            params = {
                "conditionId": condition_id,
                "limit": 1,
            }
            
            import requests
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    return data[0]
        except Exception:
            pass
        
        return None
    
    def get_trade_by_sequence(
        self,
        whale_wallet: str,
        condition_id: str,
        timestamp_ms: int,
        sequence: int,
    ) -> Optional[dict]:
        """Get specific trade by sequence number (for reprocessing).
        
        Args:
            whale_wallet: Whale's wallet
            condition_id: Market condition ID
            timestamp_ms: Trade timestamp in ms
            sequence: Sequence number
        
        Returns:
            Trade data or None
        """
        # Try cache first
        cache_key = f"whale_trade:{whale_wallet}:{condition_id}:{timestamp_ms}:{sequence}"
        cached = self._condition_to_instrument.get(condition_id)
        
        if cached:
            # Try to fetch from API (requires scanning)
            return self.scan_for_whale_trades_in_market(
                instrument_id=InstrumentId(cached),
                whale_wallet=whale_wallet,
                offset=0,
                limit=10000,  # Scan more to find specific trade
            )
        
        return None
    
    def get_scan_range(
        self,
        estimated_trades_per_day: int = 100000,
        days_to_scan: int = 1,
    ) -> tuple[int, int]:
        """Calculate scan range based on estimated volume.
        
        Args:
            estimated_trades_per_day: Estimated trades per day (default: 100k)
            days_to_scan: Number of days to scan
        
        Returns:
            Tuple of (start_offset, end_offset)
        """
        # Each API call returns 100 trades
        # Need to scan enough to find whale trades
        total_trades = estimated_trades_per_day * days_to_scan
        pages = (total_trades + 99) // 100  # Ceiling division
        
        # Start from most recent
        start_offset = max(0, pages - 100)  # Start 100 pages back
        end_offset = start_offset + 100
        
        return int(start_offset), int(end_offset)
