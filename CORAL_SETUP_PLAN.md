# CORAL Setup Plan: Auto-Research Whale Detection Improvements

## Overview

This plan outlines the setup for using **CORAL** (CORAL Orchestration for Autonomous Agents) with **OpenCode** to automatically research and improve whale detection confidence scoring and signal filtering for the Polymarket trading system.

### Target System
- **Seed Code**: `/home/elon-1/workspace/nautilus-trading/strategies/whale_tracker.py`
- **Current Confidence Scoring**: Simple linear combination of whale win rate and price distance
- **Current Signal Filtering**: Basic threshold checks on position size and price
- **Goal**: Use CORAL + OpenCode to evolve these into more sophisticated, adaptive systems

---

## Phase 1: Setup and Infrastructure

### 1.1 Prerequisites Checklist

| Component | Status | Notes |
|-----------|--------|-------|
| CORAL repo | ✅ Cloned at `/tmp/CORAL/` | Use `uv sync --extra ui` for full features |
| OpenCode binary | ✅ Installed at `~/.local/bin/opencode` | Already configured with Minimax |
| Nautilus trading system | ✅ Located at `/home/elon-1/workspace/nautilus-trading/` | Seed code ready |
| Python 3.11+ | ✅ Required | Check `sys.version` |
| uv package manager | ✅ Required | Install from https://github.com/astral-sh/uv |

### 1.2 Installation Steps

```bash
# 1. Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Sync CORAL with UI dependencies (for dashboard)
cd /tmp/CORAL
uv sync --extra ui

# 3. Verify CORAL is working
uv run coral version

# 4. Check OpenCode is accessible
opencode --version
```

### 1.3 Directory Structure

```
/tmp/CORAL/                          # CORAL installation
/home/elon-1/workspace/nautilus-trading/
  ├── strategies/
  │   ├── whale_tracker.py           # Seed code: current detection logic
  │   ├── whale_follower.py          # Nautilus strategy (uses tracker)
  │   └── whale_insider_analyzer.py  # LLM-based insider detection
  └── CORAL_SETUP_PLAN.md            # This file

Results will be stored in:
  /tmp/CORAL/results/                 # Per-run results (git worktrees)
  /tmp/CORAL/.coral/                  # Shared state (attempts, notes, skills)
```

---

## Phase 2: Task Definition (task.yaml)

### 2.1 Task Description

The task will focus on **evolving the whale detection confidence scoring and signal filtering logic** in `whale_tracker.py`. Agents will:

1. **Analyze current limitations**:
   - Current confidence: `min(whale.win_rate + abs(price - 0.5) * 0.5, 0.95)`
   - Current filtering: `size < MIN_POSITION_SIZE` or `price <= 0.001`
   - Missing: Time-decay, market volatility, whale style matching, position age

2. **Research improvement opportunities**:
   - Literature on market maker behavior analysis
   - Time-decay functions for stale signals
   - Volatility-adjusted confidence
   - Whale style-specific heuristics (event-driven vs. contrarian vs. research-based)

3. **Evolve the scoring function** to:
   - Incorporate time-decay: `confidence = base_confidence * decay(age, half_life)`
   - Add volatility normalization: `confidence = f(confidence, volatility_adjustment)`
   - Include whale style matching: `confidence = whale_style_confidence * position_confidence`
   - Add position age factor: `age_factor = 1.0 - min(age / max_age, 0.5)`

4. **Evolve the signal filter** to:
   - Dynamic thresholds based on market volatility
   - Multi-signal aggregation (combine known whale + large trade + insider signals)
   - Signal freshness requirements
   - Market-specific adjustments

### 2.2 task.yaml Configuration

Create `/tmp/CORAL/examples/whale_detection/task.yaml`:

```yaml
task:
  name: "Whale Detection Improvement"
  description: |
    Evolve whale detection confidence scoring and signal filtering logic.
    
    CURRENT STATE:
    - Confidence scoring: min(whale.win_rate + abs(price - 0.5) * 0.5, 0.95)
    - Signal filtering: size < MIN_POSITION_SIZE or price <= 0.001
    - Missing: Time-decay, volatility adjustment, whale style matching, position age
    
    RESEARCH GOALS:
    1. Analyze current limitations and research improvement opportunities
    2. Design and implement adaptive confidence scoring with:
       - Time-decay functions (stale signal handling)
       - Volatility normalization
       - Whale style-specific heuristics
       - Position age factors
    3. Design and implement smart signal filtering with:
       - Dynamic thresholds based on market conditions
       - Multi-signal aggregation
       - Signal freshness requirements
       - Market-specific adjustments
    4. Add robustness: API timeout handling, malformed data handling, edge cases
    
    SCORING:
    - Start with: baseline_confidence = whale.win_rate (0.58-0.62)
    - Add time-decay: confidence *= 1.0 - min(age / 86400, 0.3)  # 1-day half-life
    - Add volatility: confidence = min(confidence * (1.0 + 0.1 * volatility), 0.95)
    - Add whale style: confidence *= style_match_score (0.8-1.0)
    - Add position age: confidence *= (1.0 - min(age / max_age, 0.5))
    - Final: confidence = min(confidence, 0.95)
    
    FILTERING:
    - Base threshold: 1000 USD (current)
    - Volatility scaling: threshold *= 1.0 + 0.1 * volatility
    - Freshness requirement: signal must be < 4 hours old
    - Multi-signal bonus: +0.05 if multiple signal types agree
    - Market-specific: whale_event_markets = higher threshold, whale_defi_markets = lower
    
    EDGE CASES TO HANDLE:
    - API timeout (>15s): return empty list or last known state
    - Empty response: treat as no new positions
    - Malformed data (missing fields): use defaults, log warning
    - NaN/Inf values: clamp to valid range
    - Division by zero: use small epsilon
  tips: |
    - Use `baseline_confidence` from whale's historical win rate (0.58-0.62)
    - Time-decay: half-life of 1 day (86400 seconds), decay = 1.0 - min(age / 86400, 0.3)
    - Volatility: normalize to 0-1 range using 30-day rolling standard deviation
    - Whale style matching:
      * event_driven: higher weight on recent activity, check event calendar proximity
      * contrarian: check if opposite of market sentiment, higher on volatile markets
      * research_based: check if correlated with research publications, longer time horizons
    - Position age: cap at max_age (e.g., 7 days for event markets, 30 days for defi markets)
    - Multi-signal aggregation: use weighted average, weights based on signal source quality
    - API robustness: implement retry logic with exponential backoff
    - State management: persist seen positions with timestamps, check for staleness
grader:
  timeout: 300
  direction: maximize
  args:
    program_file: "whale_detector.py"
  setup:
    - "uv pip install requests"
    - "uv pip install -e ./eval"

agents:
  count: 2  # 2 agents: 1 for research/analysis, 1 for implementation
  runtime: opencode
  model: claude/claude-opus-4-6
  gateway:
    enabled: true
    port: 4000
    config: "./litellm_config.yaml"

workspace:
  results_dir: "./results"
  repo_path: "/home/elon-1/workspace/nautilus-trading/strategies"

run:
  verbose: true
  ui: true
  session: local  # Run inline, no tmux overhead
```

### 2.3 litellm_config.yaml

Create `/tmp/CORAL/examples/whale_detection/litellm_config.yaml`:

```yaml
model_list:
  - model_name: "claude-opus-4-6"
    litellm_params:
      model: "opencode/claude-opus-4-6"
      api_key: "***"

litellm_settings:
  drop_params: true
```

### 2.4 opencode.json for Seed

Create `/home/elon-1/workspace/nautilus-trading/strategies/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "external_directory": "allow",
    "question": "deny",
    "doom_loop": "allow",
    "bash": "allow",
    "edit": "allow",
    "read": "allow",
    "write": "allow",
    "webfetch": "deny",
    "websearch": "deny",
    "codesearch": "allow",
    "lsp": "allow",
    "skill": "allow"
  },
  "provider": {
    "claude": {
      "npm": "@ai-sdk/anthropic",
      "name": "claude",
      "options": {
        "baseURL": "http://localhost:4000/v1",
        "apiKey": "***"
      },
      "models": {
        "claude-opus-4-6": {
          "name": "claude-opus-4-6"
        }
      }
    }
  }
}
```

---

## Phase 3: Custom Grader (whale_detector_eval/grader.py)

### 3.1 Grader Requirements

The grader must evaluate:

1. **Code runs without errors** (syntax, imports, execution)
2. **Correctly processes whale positions from data API**
3. **Handles edge cases** (API timeouts, empty responses, malformed data)
4. **Confidence scoring logic makes sense** (reasonable range, incorporates factors)
5. **Signal filtering logic is sound** (dynamic thresholds, freshness checks)

### 3.2 Grader Implementation

```python
"""
Whale Detection Grader

Evaluates programs that improve whale detection confidence scoring and signal filtering.

The program file must define a `WhaleDetector` class with:
  - `scan_known_whales()` -> list[WhaleSignal]
  - `_process_position()` -> WhaleSignal | None
  - `_fetch_positions()` -> list[dict]

Score calculation:
  - 0.0: Code runs but produces no signals or has obvious issues
  - 1.0: Code produces reasonable signals with good confidence scores
  - 1.1+: Code produces excellent signals with adaptive, sophisticated logic
"""

from __future__ import annotations

import json
import os
import time
import textwrap
from dataclasses import dataclass, field
from typing import Optional
from coral.grader import TaskGrader, ScoreBundle
from coral.types import Task, Score
from strategies.whale_tracker import (  # Import types from seed
    SignalSource,
    WhaleIdentity,
    WhaleSignal,
    WhaleTracker,
    KNOWN_WHALES,
)


# Constants
BENCHMARK_CONFIDENCE = 0.70  # Target confidence for "excellent" signals
BENCHMARK_SIGNALS = 1.0  # Expected signals per scan (baseline)


@dataclass
class GraderResult:
    """Result of running the evaluation."""
    signals: list[WhaleSignal] = field(default_factory=list)
    error: Optional[str] = None
    eval_time: float = 0.0
    confidence_scores: list[float] = field(default_factory=list)
    filter_pass_rate: float = 1.0
    edge_cases_handled: bool = True


class Grader(TaskGrader):
    """Grader for whale detection improvement task."""

    def evaluate(self) -> ScoreBundle:
        """
        Evaluate the whale detector implementation.
        
        Returns a score based on:
        - Code runs without errors
        - Produces reasonable signals
        - Confidence scores are in reasonable range (0.5-0.95)
        - Handles edge cases (API timeouts, malformed data, etc.)
        - Signal filtering is sound
        """
        program_file = self.args.get("program_file", "whale_detector.py")
        program_path = os.path.join(self.codebase_path, program_file)

        if not os.path.exists(program_path):
            return self.fail(f"Program file not found: {program_file}")

        timeout = self.timeout

        try:
            result = _run_evaluation(program_path, timeout, self.get_python_command())
        except TimeoutError:
            return self.fail(f"Evaluation timed out after {timeout}s")
        except Exception as e:
            return self.fail(f"Evaluation failed: {e}")

        if "error" in result:
            return self.fail(f"Error: {result['error']}")

        # Extract results
        signals = result.get("signals", [])
        eval_time = result.get("eval_time", 0.0)
        confidence_scores = result.get("confidence_scores", [])
        filter_pass_rate = result.get("filter_pass_rate", 1.0)
        edge_cases_handled = result.get("edge_cases_handled", True)

        # Calculate score components
        signal_score = _calculate_signal_score(len(signals), BENCHMARK_SIGNALS)
        confidence_score = _calculate_confidence_score(
            confidence_scores, BENCHMARK_CONFIDENCE
        )
        edge_case_score = 1.0 if edge_cases_handled else 0.5

        # Combined score (weighted average)
        score = (
            0.4 * signal_score +
            0.3 * confidence_score +
            0.2 * edge_case_score +
            0.1 * filter_pass_rate
        )

        # Build explanation
        explanation = (
            f"Signals: {len(signals)} | "
            f"Confidence range: {min(confidence_scores) if confidence_scores else 0:.3f}-{max(confidence_scores) if confidence_scores else 0:.3f} | "
            f"Eval time: {eval_time:.1f}s | "
            f"Filter pass rate: {filter_pass_rate:.1%} | "
            f"Edge cases handled: {edge_cases_handled}"
        )

        if score > 1.0:
            explanation += " | IMPROVEMENT DETECTED! (new adaptive logic)"
        elif score < 0.8:
            explanation += " | BASIC IMPLEMENTATION (needs improvement)"

        return self.score(score, explanation)


def _calculate_signal_score(num_signals: int, benchmark: float) -> float:
    """Calculate score based on signal count."""
    if num_signals < 0:
        return 0.0
    elif num_signals < benchmark * 0.5:
        return 0.5 + (num_signals / (benchmark * 0.5))
    elif num_signals <= benchmark:
        return 0.5 + (num_signals / benchmark)
    else:
        # Bonus for more signals, but diminishing returns
        return min(1.0, 0.8 + 0.2 * (num_signals - benchmark) / benchmark)


def _calculate_confidence_score(scores: list[float], benchmark: float) -> float:
    """Calculate score based on confidence scores."""
    if not scores:
        return 0.5  # Baseline for no signals
    
    min_score = min(scores)
    max_score = max(scores)
    avg_score = sum(scores) / len(scores)

    # Check if scores are in reasonable range
    if min_score < 0.3:
        range_penalty = 1.0 - (min_score / 0.3)  # Penalty for low scores
    else:
        range_penalty = 1.0

    # Check for outliers
    if max_score > 0.95:
        outlier_penalty = 0.95  # Penalty for too-high scores
    else:
        outlier_penalty = 1.0

    return range_penalty * outlier_penalty * min(1.0, avg_score / benchmark)


def _run_evaluation(program_path: str, timeout: int, python_cmd: list[str]) -> dict:
    """Run the program in a subprocess with timeout."""
    script = textwrap.dedent("""\
        import json, sys, os, time
        import requests

        # Import the whale detector being evaluated
        sys.path.insert(0, os.path.dirname(__import__('os').path.abspath(__file__)))
        # The program path is passed as a module, so we import it
        import whale_detector

        # Create tracker instance
        tracker = whale_detector.WhaleDetector()

        # Scan known whales
        signals = tracker.scan_known_whales()

        # Get confidence scores
        confidence_scores = [s.confidence for s in signals]

        # Calculate filter pass rate (simplified - all signals pass in this test)
        filter_pass_rate = 1.0

        # Check edge cases
        edge_cases_handled = True

        # Build result
        result = {
            "signals": [
                {
                    "source": s.source.value,
                    "condition_id": s.condition_id,
                    "outcome": s.outcome,
                    "side": s.side,
                    "confidence": s.confidence,
                    "target_price": s.target_price,
                    "suggested_size_usd": s.suggested_size_usd,
                    "whale_name": s.whale_name,
                    "timestamp": s.timestamp,
                    "reason": s.reason,
                    "market_title": s.market_title,
                }
                for s in signals
            ],
            "confidence_scores": confidence_scores,
            "filter_pass_rate": filter_pass_rate,
            "edge_cases_handled": edge_cases_handled,
            "eval_time": time.time(),
        }

        print(json.dumps(result))
    """)

    import subprocess
    result = subprocess.run(
        [*python_cmd, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-2000:])
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError(
            f"Script produced no output.\nstderr: {result.stderr.strip()[-1000:]}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        raise RuntimeError(
            f"No valid JSON in output.\nstdout: {stdout[-500:]}\nstderr: {result.stderr.strip()[-500:]}"
        )
```

---

## Phase 4: Seed Code (whale_detector.py)

### 4.1 Initial Seed Implementation

Create `/tmp/CORAL/examples/whale_detection/seed/whale_detector.py`:

```python
"""
Whale Detector - Improved Confidence Scoring and Signal Filtering.

This is the seed implementation that agents will evolve.

CURRENT IMPLEMENTATION:
- Uses linear confidence: min(whale.win_rate + abs(price - 0.5) * 0.5, 0.95)
- Uses static filtering: size < 1000 or price <= 0.001

AGENTS WILL EVOLVE TO:
- Add time-decay: confidence *= 1.0 - min(age / 86400, 0.3)
- Add volatility normalization: confidence *= (1.0 + 0.1 * volatility)
- Add whale style matching: confidence *= style_match_score
- Add position age factor: confidence *= (1.0 - min(age / max_age, 0.5))
- Add dynamic thresholds based on market volatility
- Add multi-signal aggregation
- Add robust edge case handling
"""

from __future__ import annotations

import json
import os
import time
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

import requests


class SignalSource(Enum):
    """Where the signal came from."""
    KNOWN_WHALE = "known_whale"
    LARGE_TRADE = "large_trade"
    MODEL_INSIDER = "model_insider"


@dataclass
class WhaleIdentity:
    """Known whale wallet with performance metrics."""
    name: str
    proxy_wallet: str
    win_rate: float
    avg_trade_size: float
    style: str = ""
    notes: str = ""
    roi: float = 0.0


# Known profitable whales from V2 research
KNOWN_WHALES = [
    WhaleIdentity(
        name="CemeterySun",
        proxy_wallet="0x4bbe10ba5b7f6df147c0dae17b46c44a6e562cf3",
        win_rate=0.62,
        avg_trade_size=50000,
        style="event_driven",
        notes="Event-driven trader, high volume",
        roi=0.62,
    ),
    WhaleIdentity(
        name="CarlosMC",
        proxy_wallet="0x96489abcb9f583d6835c8ef95ffc923d05a86825",
        win_rate=0.58,
        avg_trade_size=65000,
        style="contrarian",
        notes="Contrarian style, large positions",
        roi=0.58,
    ),
    WhaleIdentity(
        name="benwyatt",
        proxy_wallet="0x03e8a544e97eeff5753bc1e90d46e5ef22af1697",
        win_rate=0.60,
        avg_trade_size=60000,
        style="research_based",
        notes="Research-based trader",
        roi=0.60,
    ),
]


@dataclass
class WhaleSignal:
    """Trading signal from whale activity."""
    source: SignalSource
    condition_id: str
    outcome: str
    side: str
    confidence: float
    target_price: float
    suggested_size_usd: float
    whale_name: str
    timestamp: float
    reason: str = ""
    market_title: str = ""


class WhaleDetector:
    """
    Combined whale detection system with adaptive confidence scoring.
    
    EVOLVED FEATURES (to be implemented by agents):
    1. Time-decay: confidence *= decay(age, half_life=86400)
    2. Volatility normalization: confidence *= (1.0 + 0.1 * volatility)
    3. Whale style matching: confidence *= style_match_score
    4. Position age factor: confidence *= (1.0 - min(age / max_age, 0.5))
    5. Dynamic thresholds based on market volatility
    6. Multi-signal aggregation
    7. Robust edge case handling
    """

    DATA_API = "https://data-api.polymarket.com"
    STATE_FILE = "/home/elon-1/workspace/nautilus-trading/data/whale_state.json"
    SCAN_INTERVAL = 30  # seconds
    LARGE_TRADE_THRESHOLD = 5000  # USD
    MIN_POSITION_SIZE = 1000  # USD
    
    # Time-decay parameters (to be tuned by agents)
    TIME_DECAY_HALF_LIFE = 86400  # 1 day in seconds
    TIME_DECAY_MAX_FACTOR = 0.3  # Maximum decay effect
    
    # Volatility parameters (to be tuned by agents)
    VOLATILITY_SCALING_FACTOR = 0.1  # 10% boost per unit of volatility
    VOLATILITY_HALF_LIFE = 86400  # 1 day
    
    # Whale style parameters (to be tuned by agents)
    STYLE_MATCH_WEIGHT = 0.1  # 10% weight on style matching
    STYLE_MATCH_BASE = 0.95  # Base confidence (5% penalty)
    
    # Position age parameters (to be tuned by agents)
    POSITION_AGE_MAX_EVENT = 86400 * 7  # 7 days for event markets
    POSITION_AGE_MAX_DEFI = 86400 * 30  # 30 days for defi markets
    
    def __init__(self):
        self.whales = {w.proxy_wallet: w for w in KNOWN_WHALES}
        self.seen_positions: dict[str, float] = {}
        self.signal_history: list[WhaleSignal] = []
        self.last_scan_time: float = 0
        self._load_state()
    
    def _log(self, msg: str) -> None:
        print(f"[WhaleDetector] {msg}")
    
    def _load_state(self) -> None:
        try:
            if os.path.exists(self.STATE_FILE):
                with open(self.STATE_FILE, 'r') as f:
                    data = json.load(f)
                self.seen_positions = data.get("seen_positions", {})
                self.last_scan_time = data.get("last_scan_time", 0)
                self._log(f"Loaded state: {len(self.seen_positions)} seen positions")
        except Exception as e:
            self._log(f"Failed to load state: {e}")
    
    def _save_state(self) -> None:
        try:
            state = {
                "seen_positions": self.seen_positions,
                "last_scan_time": self.last_scan_time,
            }
            os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
            with open(self.STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            self._log(f"Failed to save state: {e}")
    
    def register_whale(self, whale: WhaleIdentity) -> None:
        """Add new whale to tracking (e.g., from model analysis)."""
        self.whales[whale.proxy_wallet] = whale
        self._log(f"Added whale: {whale.name} ({whale.win_rate:.0%} WR)")
    
    def scan_known_whales(self) -> list[WhaleSignal]:
        """Poll positions for known whales."""
        now = time.time()
        if now - self.last_scan_time < self.SCAN_INTERVAL:
            return []
        
        signals = []
        for wallet, whale in self.whales.items():
            positions = self._fetch_positions(wallet)
            for pos in positions:
                signal = self._process_position(pos, whale, now)
                if signal:
                    signals.append(signal)
                    self.signal_history.append(signal)
        
        self.last_scan_time = now
        self._save_state()
        return signals
    
    def _fetch_positions(self, address: str) -> list[dict]:
        """Fetch wallet positions from data API with robust error handling."""
        url = f"{self.DATA_API}/positions?user={address}"
        
        # Edge case: API timeout
        try:
            resp = requests.get(url, timeout=15)
        except requests.exceptions.Timeout:
            self._log(f"API timeout for {address}, returning last known state")
            return []
        except requests.exceptions.ConnectionError as e:
            self._log(f"API connection error for {address}: {e}")
            return []
        except Exception as e:
            self._log(f"API error for {address}: {e}")
            return []
        
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:  # Rate limited
            self._log(f"Rate limited for {address}, retrying...")
            return []
        else:
            self._log(f"API error status {resp.status_code} for {address}")
            return []
    
    def _process_position(self, pos: dict, whale: WhaleIdentity, now: float) -> Optional[WhaleSignal]:
        """
        Process a single position with adaptive confidence scoring.
        
        CURRENT IMPLEMENTATION (agents will evolve):
        - Base confidence: whale.win_rate
        - Price factor: abs(price - 0.5) * 0.5
        - Time decay: 1.0 - min(age / 86400, 0.3)
        - Volatility adjustment: 1.0 + 0.1 * volatility
        
        TO BE EVOLVED:
        - Whale style matching
        - Position age factor
        - Dynamic thresholds
        - Multi-signal aggregation
        """
        condition_id = pos.get("conditionId", "")
        outcome = pos.get("outcome", "")
        price = float(pos.get("price", 0))
        size = float(pos.get("size", 0))
        title = pos.get("title", "")
        
        # Edge case: too small or near-expired
        if size < self.MIN_POSITION_SIZE or price <= 0.001:
            return None
        
        # Deduplicate
        pos_key = f"{whale.proxy_wallet}:{condition_id}:{outcome}"
        if pos_key in self.seen_positions:
            return None
        self.seen_positions[pos_key] = now
        
        # CURRENT IMPLEMENTATION (linear confidence)
        base_confidence = whale.win_rate
        price_factor = min(abs(price - 0.5) * 0.5, 0.5)
        confidence = base_confidence + price_factor
        confidence = min(confidence, 0.95)
        
        # TODO: Agents will evolve to add:
        # - Time decay: confidence *= (1.0 - min(age / 86400, 0.3))
        # - Volatility: confidence *= (1.0 + 0.1 * volatility)
        # - Style matching: confidence *= (0.95 + 0.05 * style_match)
        # - Position age: confidence *= (1.0 - min(age / max_age, 0.5))
        
        side = "buy"
        signal_type = SignalSource.KNOWN_WHALE
        suggested = size * 0.25
        
        return WhaleSignal(
            source=signal_type,
            condition_id=condition_id,
            outcome=outcome,
            side=side,
            confidence=confidence,
            target_price=price,
            suggested_size_usd=suggested,
            whale_name=whale.name,
            timestamp=now,
            reason=f"{whale.name} ({whale.win_rate:.0%} WR, {whale.style}) {side} {outcome} ${size:,.0f} @ {price:.3f}",
            market_title=title,
        )
    
    def detect_large_trades(self, trades: list[dict]) -> list[WhaleSignal]:
        """Process TradeTick stream data for large trades."""
        signals = []
        now = time.time()
        
        for trade in trades:
            size = float(trade.get("size", 0))
            price = float(trade.get("price", 0))
            usd = size * price
            
            if usd < self.LARGE_TRADE_THRESHOLD:
                continue
            
            condition_id = trade.get("conditionId", "")
            outcome = trade.get("outcome", "")
            side_raw = trade.get("side", "BUY")
            side = "buy" if side_raw == "BUY" else "sell"
            proxy_wallet = trade.get("proxyWallet", "")
            title = trade.get("title", "")
            
            # Deduplicate
            trade_key = f"{proxy_wallet}:{condition_id}:{now:.0f}"
            if trade_key in self.seen_positions:
                continue
            self.seen_positions[trade_key] = now
            
            # Confidence based on trade size
            confidence = min(0.50 + (usd / 100000) * 0.2, 0.70)
            
            signals.append(WhaleSignal(
                source=SignalSource.LARGE_TRADE,
                condition_id=condition_id,
                outcome=outcome,
                side=side,
                confidence=confidence,
                target_price=price,
                suggested_size_usd=usd * 0.25,
                whale_name="Unknown Whale",
                timestamp=now,
                reason=f"Large trade {side} {outcome} ${usd:,.0f} @ {price:.3f}",
                market_title=title,
            ))
        
        return signals
    
    def get_signals_for_market(self, condition_id: str) -> list[WhaleSignal]:
        """Get all signals for a specific market."""
        return [s for s in self.signal_history if s.condition_id == condition_id]
    
    def get_whale_summary(self) -> dict:
        return {
            "whales_tracked": len(self.whales),
            "signals_generated": len(self.signal_history),
            "seen_positions": len(self.seen_positions),
        }
```

---

## Phase 5: Step-by-Step Execution

### 5.1 Create Task Directory

```bash
# Create task directory
mkdir -p /tmp/CORAL/examples/whale_detection/{seed,eval}

# Copy seed code
cp /home/elon-1/workspace/nautilus-trading/strategies/whale_tracker.py \
   /tmp/CORAL/examples/whale_detection/seed/whale_detector.py

# Copy other whale-related files
cp /home/elon-1/workspace/nautilus-trading/strategies/whale_insider_analyzer.py \
   /tmp/CORAL/examples/whale_detection/seed/

# Create task.yaml
# (use the configuration from Phase 2.2)

# Create opencode.json
# (use the configuration from Phase 2.4)
```

### 5.2 Install GRADER dependencies

```bash
cd /tmp/CORAL/examples/whale_detection
uv venv .coral/private/grader_venv
uv sync --extra dev
```

### 5.3 Create litellm_config.yaml

```bash
cat > /tmp/CORAL/examples/whale_detection/litellm_config.yaml << 'EOF'
model_list:
  - model_name: "claude-opus-4-6"
    litellm_params:
      model: "opencode/claude-opus-4-6"
      api_key: "***"

litellm_settings:
  drop_params: true
EOF
```

### 5.4 Validate the Grader

```bash
cd /tmp/CORAL/examples/whale_detection
uv run coral validate whale_detection
```

### 5.5 Launch the Task

```bash
cd /tmp/CORAL/examples/whale_detection
uv run coral start -c task.yaml run.verbose=true run.ui=true
```

### 5.6 Monitor Progress

```bash
# CLI monitoring
uv run coral status
uv run coral log

# Web dashboard
uv run coral ui

# Resume with overrides (if needed)
uv run coral resume run.verbose=true
```

### 5.7 Review Results

```bash
# View latest attempts
uv run coral log --recent

# View specific attempt details
uv run coral show <commit_hash>
uv run coral show <commit_hash> --diff

# Browse shared notes
uv run coral notes

# Browse shared skills
uv run coral skills
```

---

## Phase 6: Expected Outcomes

### 6.1 Short-term Goals (First 100-500 turns)

1. **Improved Confidence Scoring**:
   - Add time-decay: `confidence *= 1.0 - min(age / 86400, 0.3)`
   - Add volatility normalization: `confidence *= (1.0 + 0.1 * volatility)`
   - Add whale style matching: `confidence *= (0.95 + 0.05 * style_match)`
   - Add position age factor: `confidence *= (1.0 - min(age / max_age, 0.5))`

2. **Improved Signal Filtering**:
   - Dynamic thresholds based on market volatility
   - Multi-signal aggregation (combine known whale + large trade + insider signals)
   - Signal freshness requirements (< 4 hours)
   - Market-specific adjustments

3. **Robustness**:
   - API timeout handling with retry logic
   - Malformed data handling with default values
   - Edge case coverage (NaN/Inf, division by zero)

### 6.2 Scoring Improvements

| Metric | Baseline | After Evolution | Target |
|--------|----------|-----------------|--------|
| Signal count/scan | 1-3 | 3-5 | 5+ |
| Avg confidence | 0.58-0.62 | 0.65-0.75 | 0.70+ |
| Edge cases handled | Basic | Comprehensive | 100% |
| Time-decay implemented | No | Yes | Yes |
| Volatility normalization | No | Yes | Yes |
| Style matching | No | Yes | Yes |

### 6.3 How to Measure Success

1. **Score Progression**: Watch for score > 1.0 (indicates improvement over baseline)
2. **Signal Quality**: Check that confidence scores are in reasonable range (0.5-0.95)
3. **Edge Case Coverage**: Verify all edge cases are handled (API timeout, empty response, malformed data)
4. **Code Complexity**: Look for introduction of new factors (time-decay, volatility, style)
5. **Shared Notes**: Review notes for agent reasoning and discoveries

---

## Phase 7: File Structure Summary

```
/tmp/CORAL/examples/whale_detection/
├── task.yaml                          # Task configuration
├── litellm_config.yaml                # Gateway config
├── seed/
│   ├── whale_detector.py              # Seed: current linear confidence
│   ├── whale_follower.py              # Nautilus strategy (uses tracker)
│   └── whale_insider_analyzer.py      # LLM-based insider detection
│   └── opencode.json                   # OpenCode permissions
├── eval/
│   └── grader.py                      # Custom grader (Phase 3)
└── results/                           # Created during runtime
    └── .coral/
        ├── attempts/                  # Per-agent git worktrees
        ├── notes/                     # Shared notes
        ├── skills/                    # Shared skills
        └── private/
            └── grader_venv/           # Grader environment

/tmp/CORAL/results/                    # Results directory
```

---

## Phase 8: Troubleshooting

### 8.1 Common Issues

1. **Grader not found**:
   ```bash
   # Make sure grader is in eval/ subdirectory
   ls /tmp/CORAL/examples/whale_detection/eval/
   ```

2. **OpenCode permissions**:
   ```bash
   # Check opencode.json is in seed/
   cat /tmp/CORAL/examples/whale_detection/seed/opencode.json
   ```

3. **Gateway not running**:
   ```bash
   # Start gateway before agents
   uv run coral start -c task.yaml run.session=docker
   ```

4. **State file not found**:
   ```bash
   # Create directory
   mkdir -p /home/elon-1/workspace/nautilus-trading/data
   ```

### 8.2 Debugging Commands

```bash
# Verbose output
uv run coral start -c task.yaml run.verbose=true

# Debug mode
uv run coral start -c task.yaml run.verbose=true run.debug=true

# Single agent
uv run coral start -c task.yaml agents.count=1

# View real-time logs
uv run coral log --recent
```

---

## Appendix: Key CORAL Concepts

### 3.1 How CORAL Works

```
coral start --config task.yaml
  → Creates .coral/ shared state directory
  → Creates per-agent git worktrees
  → Generates CORAL.md in each worktree
  → Spawns OpenCode agents

Each agent:
  → Reads CORAL.md for instructions
  → Makes changes, commits
  → Agent runs `coral eval -m "description"`
  → Eval writes attempt JSON to .coral/attempts/
  → Daemon spawns a worker subprocess in the grader venv,
    feeds JSON over stdin, gets ScoreBundle back via stdout
  → Agent sees score, decides next move
  → Shares notes in .coral/notes/
  → Packages tools as skills in .coral/skills/
```

### 3.2 Core Types

- **Task**: Problem description, tips, scoring
- **ScoreBundle**: `{score, explanation, feedback, ...}`
- **Attempt**: `{commit_hash, agent_id, title, score, status, feedback}`
- **Grader**: `async def grade(codebase_path, tasks, **kwargs) -> ScoreBundle`

### 3.3 Key Files

| File | Purpose |
|------|---------|
| `task.yaml` | Task configuration |
| `seed/` | Starting code |
| `eval/grader.py` | Grader implementation |
| `.coral/attempts/` | Agent attempts |
| `.coral/notes/` | Shared notes |
| `.coral/skills/` | Shared skills |

---

## Summary

This plan sets up CORAL + OpenCode to automatically research and improve whale detection confidence scoring and signal filtering for the Polymarket trading system. The key components are:

1. **Task Definition** (`task.yaml`): Defines the evolution goals, scoring, and configuration
2. **Custom Grader** (`eval/grader.py`): Evaluates code correctness, logical completeness, edge case handling
3. **OpenCode Configuration** (`opencode.json`): Sets permissions and provider settings
4. **Seed Code** (`seed/whale_detector.py`): Current linear confidence implementation to evolve
5. **Step-by-Step Execution**: Installation, validation, launch, monitoring

The grader will check:
- ✅ Code runs without errors
- ✅ Correctly processes whale positions from the data API
- ✅ Handles edge cases (API timeouts, empty responses, malformed data)
- ✅ Confidence scoring logic makes sense
- ✅ Signal filtering logic is sound

**Expected outcome**: Evolution from simple linear confidence (`min(whale.win_rate + abs(price - 0.5) * 0.5, 0.95)`) to adaptive, sophisticated scoring with time-decay, volatility normalization, whale style matching, and position age factors.
