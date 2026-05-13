# Mac Failover System for Nautilus Trading — Implementation Plan

## Current State Analysis

### On 1700 (Linux Production)
- Python: 3.12.3
- nautilus-trader: 1.225.0
- Service: systemd user service (`nautilus-paper.service`, `nautilus-live.service`)
- Running path: `/home/elon-1/workspace/nautilus-trading`

### On Mac (Failover)
- Python: 3.12.13 (Homebrew) — available at `/opt/homebrew/bin/python3.12`
- nautilus-trader: Installed but with missing optional dependency
- Service: LaunchAgent (plist) — already exists at `~/Library/LaunchAgents/com.nautilus.paper.plist`
- Running path: `~/workspace/nautilus-trading`

---

## Problem Diagnosis

### Root Cause of Import Errors on Mac

The import error occurs because `nautilus_trader.adapters.polymarket.providers` imports from `py_clob_client_v2.client`, but only `py_clob_client` (v0.34.6) is installed. The polymarket extra (`py-clob-client-v2>=1.0.0,<2.0.0`) was not included when nautilus-trader was originally installed on Mac.

```
# Error traceback:
from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProviderConfig
  File ".../nautilus_trader/adapters/polymarket/providers.py", line 21, in <module>
    from py_clob_client_v2.client import ClobClient
ModuleNotFoundError: No module named 'py_clob_client_v2'
```

### Why run_paper.py fails on Mac (vs working on 1700)
- On 1700, `nautilus-trader[polymarket]` was installed correctly with the `[polymarket]` extra
- On Mac, only `pip install nautilus_trader` was used without the polymarket extras

---

## Step-by-Step Implementation Plan

### Phase 1: Fix Dependencies (Get run_paper.py running)

#### 1.1 Install missing py-clob-client-v2 dependency

```bash
cd ~/workspace/nautilus-trading/venv/bin
pip install nautilus-trader[polymarket]
# OR directly:
pip install py-clob-client-v2>=1.0.0,<2.0.0
```

#### 1.2 Verify installation

```bash
cd ~/workspace/nautilus-trading
./venv/bin/pip list | grep -i nautilus
./venv/bin/python -c "from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProviderConfig; print('OK')"
```

#### 1.3 Test run_paper.py directly (no service yet)

```bash
cd ~/workspace/nautilus-trading
./venv/bin/python run_paper.py
# Should see output connecting to Polymarket sandbox, loading whale markets
```

Expected output snippet:
- "Preloading X instruments into cache..."
- "Loaded X instruments from Y markets"
- "POLYMARKET WHALE FOLLOWER — PAPER TRADING" header
- No import errors

### Phase 2: Venv Setup to Match 1700 Environment

#### 2.1 Recreate venv from scratch (recommended)

```bash
cd ~/workspace/nautilus-trading
rm -rf venv

# Create fresh venv with Python 3.12 (Homebrew version)
python3.12 -m venv venv
source venv/bin/activate

# Install nautilus-trader WITH polymarket extra
pip install nautilus-trader[polymarket]

# Verify installation
pip list | grep nautilus-trader
```

#### 2.2 Alternative: Use Homebrew Python directly (no venv needed)

Since Homebrew provides Python 3.12.13 and no venv is strictly required for nautilus-trader (it's not a web framework that needs isolation), consider using Homebrew Python directly:

```bash
# Test if it works without venv
/opt/homebrew/bin/python3.12 -m pip install nautilus-trader[polymarket]
/opt/homebrew/bin/python3.12 -c "from nautilus_trader.adapters.polymarket import PolymarketDataClientConfig; print('OK')"
```

This approach avoids venv-specific issues and matches the 1700 setup more closely (Linux uses a dedicated Python binary too).

#### 2.3 Verify all required imports work

```bash
./venv/bin/python -c "
from nautilus_trader.adapters.polymarket import (
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
)
from nautilus_trader.config import LiveExecEngineConfig
print('All imports OK')
"
```

### Phase 3: LaunchAgent Configuration

#### 3.1 Review existing plist

The existing `~/Library/LaunchAgents/com.nautilus.paper.plist` already exists but has a few issues:

**Current problems:**
- Uses hardcoded venv path (`/Users/tentenone/workspace/nautilus-trading/venv/bin/python`) — should match the actual venv location after rebuild
- SoftResourceLimits.Memory=524288000 is too low (512MB); nautilus-trader needs more memory
- Missing `LimitNOFILE` equivalent for macOS (macOS LaunchAgent uses `<key>LimitNumberOfFiles</key>`)

#### 3.2 Create/Update the plist

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nautilus.paper</string>

    <!-- Run the Python interpreter from venv or Homebrew -->
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3.12</string>
        <string>-u</string>  <!-- Unbuffered stdout (equivalent to PYTHONUNBUFFERED=1) -->
        <string>/Users/tentenone/workspace/nautilus-trading/run_paper.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/tentenone/workspace/nautilus-trading</string>

    <key>RunAtLoad</key>
    <true/>  <!-- Start on login -->

    <key>KeepAlive</key>
    <true/>  <!-- Keep alive like systemd's Restart=always -->

    <!-- Logging to file (like systemd StandardOutput/StandardError) -->
    <key>StandardOutPath</key>
    <string>/Users/tentenone/workspace/nautilus-trading/logs/paper_trading.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/tentenone/workspace/nautilus-trading/logs/paper_trading.log</string>

    <!-- Environment variables -->
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>BANKROLL</key>
        <string>10000</string>
        <key>KELLY_FRACTION</key>
        <string>0.25</string>
    </dict>

    <!-- Resource limits (equivalent to systemd LimitNOFILE and MemoryMax) -->
    <key>LimitNumberOfFiles</key>
    <integer>65536</integer>
    <!-- Note: LaunchAgents have per-user memory limit; process typically gets ~2GB+ on modern macOS -->

    <!-- Restart on failure (equivalent to systemd Restart=always, RestartSec=30) -->
    <key>Restart</key>
    <true/>
    <key>AbandonOutOfCore</key>
    <true/>
</dict>
</plist>
```

### Phase 4: Verify Startup and Polymarket Connection

#### 4.1 Load and test the LaunchAgent

```bash
# Load the plist
launchctl load ~/Library/LaunchAgents/com.nautilus.paper.plist

# Check if it's running
launchctl list com.nautilus.paper

# View logs (should appear in the log file after a few seconds)
cat ~/workspace/nautilus-trading/logs/paper_trading.log

# If running successfully, you should see:
# - "POLYMARKET WHALE FOLLOWER — PAPER TRADING" header
# - Market loading output
# - No import errors
```

#### 4.2 Test connectivity to Polymarket

```bash
# Verify the script connects and loads markets
./venv/bin/python -c "
import sys; sys.path.insert(0, 'strategies')
from nautilus_trader.adapters.polymarket.common.parsing import parse_polymarket_instrument
print('polymarket adapter imports OK')
"
```

#### 4.3 Verify position reconciliation works

```bash
# Check that the reconciler component loads correctly
./venv/bin/python -c "
from components.position_reconciler import PositionReconciler
r = PositionReconciler()
print('Reconciler loaded:', type(r))
"
```

### Phase 5: State Sync Between Mac and 1700

When the Linux system (1700) recovers, both systems will be running simultaneously. The key considerations:

#### 5.1 Trade ID uniqueness

Each trade has a unique identifier generated by `uuid.uuid4()`. Since each instance generates its own IDs, trades from Mac and 1700 won't conflict as long as they're tracked separately (which they are — the system uses separate trader IDs: `WHALE-FOLLOWER-PAPER` vs `WHALE-FOLLOWER-LIVE`).

#### 5.2 Sync strategy

The `components/position_reconciler.py` component handles reconciling positions between paper and live trading. When both systems run simultaneously:
- The reconciler tracks all paper positions separately from live positions
- No sync is needed for trade IDs (they're unique per-instance)
- Dashboard shows both modes independently

#### 5.3 Manual switch-over procedure when 1700 recovers

```bash
# On Mac, stop the failover service:
launchctl unload ~/Library/LaunchAgents/com.nautilus.paper.plist

# Optionally restart with live mode (if needed):
cp nautilus-live.service config/...
```

#### 5.4 DB backup during transition

```bash
# Back up trades.db from Mac before stopping
cp ~/workspace/nautilus-trading/research/trades.db backups/mac-failover-backup-$(date +%Y%m%d).db
```

---

## Verification Checklist

| Test | Command | Expected Result |
|------|---------|-----------------|
| Python version | `./venv/bin/python --version` | 3.12.x |
| nautilus-trader installed | `pip list \| grep nautilus` | nautilus_trader 1.226.0+ |
| Polymarket extra installed | `pip show py-clob-client-v2` | Version ≥1.0.0 |
| Import test | `python -c "from nautilus_trader.adapters.polymarket import ..."` | No errors |
| run_paper.py execution | `./venv/bin/python run_paper.py` | Loads markets, no crashes |
| LaunchAgent loading | `launchctl list com.nautilus.paper` | Shows process running |
| Polymarket connection | Check logs for "Sandbox" and market data | Markets loaded |
| Position reconciliation | Check reconciler output in logs | No unmatched positions errors |

---

## Alternative: Use run_live.py instead of run_paper.py on Mac

If the polyclob-client-v2 dependency is a persistent issue, an alternative approach is to use `run_live.py` directly with the real Polymarket API (no sandbox). This uses a different code path that may have fewer dependencies. However, it requires:
- Real POLYMARKET_API_KEY and POLYMARKET_API_SECRET from .env
- Actual USDC balance in Polymarket wallet
- `.guard/micro-live.ok` file to be present

---

## Troubleshooting Common Issues

### If run_paper.py still fails after installing py-clob-client-v2:

1. **Check for compiled binary issues on arm64:**
   ```bash
   brew install pkg-config libomp  # Required by some dependencies
   pip uninstall nautilus-trader
   pip install nautilus-trader[polymarket]
   ```

2. **Check if polyclob-client-v2 is actually installed:**
   ```bash
   pip show py-clob-client-v2
   # Should show version >= 1.0.0
   ```

3. **If there's a platform-specific issue, try reinstalling from source:**
   ```bash
   pip install --no-binary :all: nautilus-trader[polymarket]
   ```

4. **Check Python path:**
   ```bash
   ./venv/bin/python -c "import sys; print('\\n'.join(sys.path))"
   # Ensure site-packages is in the path
   ```

### If LaunchAgent doesn't start:

1. **Check plist syntax:** Use `plutil` to validate
   ```bash
   plutil -lint ~/Library/LaunchAgents/com.nautilus.paper.plist
   ```

2. **Check launchd logs:**
   ```bash
   launchctl list com.nautilus.paper
   cat ~/workspace/nautilus-trading/logs/paper_trading.log  # Should have error details
   ```

3. **Test manually first:** Run run_paper.py directly before setting up LaunchAgent:
   ```bash
   cd ~/workspace/nautilus-trading && ./venv/bin/python run_paper.py
   ```

### If the script hangs on startup (import takes >15 seconds):

This is normal for nautilus-trader — it loads many modules on import. Wait at least 30-60 seconds before checking logs.
