# NautilusTrader Integration Plan

## Overview
Extend the Polymarket-only Nautilus setup to support:
1. Kronos ML prediction integration (ports 8090/8091)
2. Bybit crypto exchange adapter
3. Unified ML-driven crypto trading strategy

---

## Phase 1: Kronos ML Client Component

### File: `components/kronos_client.py`
**Purpose:** HTTP client for Kronos prediction servers

**Implementation:**
```python
import aiohttp
import asyncio
from typing import Optional

class KronosClient:
    """Async client for Kronos ML prediction servers."""
    
    BASE_URL = "http://127.0.0.1"
    BASE_PORT = 8090
    MINI_PORT = 8091
    
    # Entry/exit thresholds (matching Freqtrade KronosDualStrategy)
    ENTRY_BASE_THRESHOLD = 0.005    # 0.5%
    ENTRY_MINI_THRESHOLD = 0.003    # 0.3%
    EXIT_THRESHOLD = -0.003         # -0.3%
    
    async def predict(self, ohlcv_data: list[dict], model: str = "base") -> Optional[float]:
        """Send OHLCV data to Kronos, return predicted return %."""
        port = self.BASE_PORT if model == "base" else self.MINI_PORT
        url = f"{self.BASE_URL}:{port}/predict"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json={"data": ohlcv_data}, timeout=10) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("predicted_return", 0.0)
            except Exception as e:
                print(f"Kronos {model} prediction failed: {e}")
        return None
    
    async def dual_predict(self, ohlcv_data: list[dict]) -> dict:
        """Get predictions from both models. Returns {base, mini, entry_signal, exit_signal}."""
        base, mini = await asyncio.gather(
            self.predict(ohlcv_data, "base"),
            self.predict(ohllcv_data, "mini")
        )
        entry = (base is not None and base > self.ENTRY_BASE_THRESHOLD and
                 mini is not None and mini > self.ENTRY_MINI_THRESHOLD)
        exit_signal = ((base is not None and base < self.EXIT_THRESHOLD) or
                       (mini is not None and mini < self.EXIT_THRESHOLD))
        return {"base": base, "mini": mini, "entry": entry, "exit": exit_signal}
```

**Dependencies:** `aiohttp` (already in venv via Nautilus)

---

## Phase 2: Bybit Exchange Adapter

### File: `run_live.py` (modify)
**Add Bybit venue alongside Polymarket:**

```python
from nautilus_trader.adapters.bybit.factories import (
    BybitLiveDataClientFactory,
    BybitLiveExecClientFactory,
)
from nautilus_trader.adapters.bybit.config import (
    BybitDataClientConfig,
    BybitExecClientConfig,
)

# Register Bybit factories
node.builder.add_data_client_factory("BYBIT", BybitLiveDataClientFactory)
node.builder.add_exec_client_factory("BYBIT", BybitLiveExecClientFactory)

# Bybit config from env
bybit_data_config = BybitDataClientConfig(
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
)
bybit_exec_config = BybitExecClientConfig(
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
)

node.builder.add_data_client_config(bybit_data_config)
node.builder.add_exec_client_config(bybit_exec_config)
```

### File: `.env` (add new vars)
```
BYBIT_API_KEY=your_key_here
BYBIT_API_SECRET=your_secret_here
BYBIT_TESTNET=true  # Set false for live
```

---

## Phase 3: Crypto Trading Strategy

### File: `strategies/kronos_crypto_strategy.py`
**Nautilus Strategy class that:**
- Subscribes to Bybit OHLCV bars (5m timeframe)
- On each bar close → calls Kronos dual prediction
- If entry signal → places market buy with stop loss
- If exit signal → closes position
- Manages trailing stop

**Key structure:**
```python
from nautilus_trader.model import Strategy
from nautilus_trader.model.data import Bar
from nautilus_trader.model.order import MarketOrder

class KronosCryptoStrategy(Strategy):
    def __init__(self, kronos_client: KronosClient, config: dict):
        super().__init__()
        self.kronos = kronos_client
        self.ohlcv_buffer = []
        self.max_ohlcv = 500  # For Kronos base model context
    
    def on_start(self):
        self.subscribe_bars(self.config.instrument_id)
    
    def on_bar(self, bar: Bar):
        # Add to buffer
        self.ohlcv_buffer.append(bar)
        if len(self.ohlcv_buffer) < self.min_history:
            return
        
        # Get Kronos prediction
        signals = self.kronos.dual_predict(self._format_ohlcv())
        
        if signals["entry"] and not self.position.is_open:
            self._enter_long()
        elif signals["exit"] and self.position.is_open:
            self._exit_position()
    
    def _enter_long(self):
        # Place market order + stop loss
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self._calculate_position_size(),
        )
        self.submit_order(order)
    
    def _exit_position(self):
        # Close position
        if self.portfolio.is_net_long(self.config.instrument_id):
            order = self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.position.quantity,
            )
            self.submit_order(order)
```

---

## Phase 4: Paper Trading Support

### File: `run_paper.py` (modify)
- Add Bybit sandbox execution client factory
- Configure sandbox with test instruments (BTC/USDT, ETH/USDT)
- Kronos client works the same (it's local, not exchange-dependent)

---

## Phase 5: Automation

### File: `~/.config/systemd/user/nautilus-crypto.service`
```ini
[Unit]
Description=NautilusTrader Crypto Strategy
After=network.target

[Service]
Type=simple
User=elon-1
WorkingDirectory=/home/elon-1/workspace/nautilus-trading
Environment=PATH=/home/elon-1/workspace/nautilus-trading/venv/bin
ExecStart=/home/elon-1/workspace/nautilus-trading/venv/bin/python run_live.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

---

## Implementation Order
1. Create `components/kronos_client.py` → test with `curl localhost:8090/health`
2. Add Bybit adapter to `run_live.py` → test sandbox first
3. Create `strategies/kronos_crypto_strategy.py` → backtest
4. Add paper trading support → test with Bybit testnet
5. Create systemd service → enable auto-start

## Risk Mitigation
- **Phase 1-4 on paper/testnet only** until verified
- **Start with 1 pair** (BTC/USDT) before adding more
- **Kronos fallback:** If prediction server is down, strategy should skip trades (not crash)
- **Position limits:** Max 1 open position per pair, max total exposure configurable
