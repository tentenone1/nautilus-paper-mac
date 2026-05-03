"""Whale Insider Edge Detection using Local Uncensored Model.

Uses the local Qwen3.5-9B-Uncensored model (localhost:8080) to analyze
whale trading patterns and identify wallets with insider edge.

Analyzes:
- Early entry timing (before public news)
- Consistent wins on specific market types
- Position size vs market liquidity ratio
- Cross-market correlation patterns
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional

import requests

from strategies.whale_tracker_new import WhaleIdentity


@dataclass
class InsiderAnalysis:
    """Result of insider edge analysis."""
    wallet_address: str
    wallet_name: str
    edge_score: float  # 0-1, how likely this is an insider
    edge_type: str  # "early_entry", "market_maker", "news_trader", "pattern"
    reasoning: str
    suggested_action: str  # "track", "ignore", "high_priority"
    markets_analyzed: int


class WhaleInsiderAnalyzer:
    """Analyzes whale wallets for insider edge using local LLM."""

    LLM_ENDPOINT = "http://localhost:8080/v1/chat/completions"
    MODEL = "Qwen3.5-9B"

    def __init__(self):
        self.analysis_history: list[InsiderAnalysis] = []
        self.known_insiders: dict[str, float] = {}  # wallet -> edge_score

    def analyze_wallet(
        self,
        wallet: str,
        positions: list[dict],
        trades: list[dict],
        wallet_name: str = "Unknown",
    ) -> Optional[InsiderAnalysis]:
        """Analyze a wallet's trading patterns for insider edge."""

        # Build analysis prompt
        prompt = self._build_analysis_prompt(wallet, positions, trades, wallet_name)

        # Query local LLM
        try:
            response = self._query_llm(prompt)
            return self._parse_analysis(response, wallet, wallet_name)
        except Exception as e:
            print(f"[InsiderAnalyzer] LLM error: {e}")
            return None

    def _build_analysis_prompt(
        self,
        wallet: str,
        positions: list[dict],
        trades: list[dict],
        wallet_name: str,
    ) -> str:
        """Build prompt for LLM analysis."""

        position_summary = []
        for pos in positions[:10]:
            position_summary.append(
                f"- {pos.get('title', 'Unknown')}: "
                f"{pos.get('outcome', '?')} @ {pos.get('price', 0):.3f}, "
                f"size={pos.get('size', 0):.0f}, "
                f"conditionId={pos.get('conditionId', '')[:20]}..."
            )

        trade_summary = []
        for trade in trades[:10]:
            trade_summary.append(
                f"- {trade.get('side', '?')} {trade.get('outcome', '?')} "
                f"@ {trade.get('price', 0):.3f}, "
                f"size={trade.get('size', 0):.0f}, "
                f"title={trade.get('title', 'Unknown')[:40]}"
            )

        prompt = f"""
You are an expert prediction market analyst. Analyze this wallet's trading patterns to determine if they have insider edge.

Wallet: {wallet_name} ({wallet})

Recent Positions:
{chr(10).join(position_summary) if position_summary else "No recent positions"}

Recent Trades:
{chr(10).join(trade_summary) if trade_summary else "No recent trades"}

Look for these insider signals:
1. EARLY ENTRY: Entered positions before major news/events were public
2. CONSISTENT WINS: High win rate (>65%) on specific market types
3. LARGE POSITIONS: Position sizes that suggest high confidence
4. PATTERN RECOGNITION: Trades that correlate with future outcomes
5. MARKET TIMING: Entering just before price movements

Respond in JSON format:
{{
  "edge_score": 0.0-1.0,
  "edge_type": "early_entry|market_maker|news_trader|pattern|none",
  "reasoning": "Brief explanation of why this wallet does/doesn't have edge",
  "suggested_action": "track|ignore|high_priority",
  "markets_analyzed": <number>
}}
"""
        return prompt

    def _query_llm(self, prompt: str) -> str:
        """Query local LLM endpoint."""
        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": "You are an expert prediction market analyst. Respond only with valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        }

        resp = requests.post(self.LLM_ENDPOINT, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _parse_analysis(
        self,
        response: str,
        wallet: str,
        wallet_name: str,
    ) -> Optional[InsiderAnalysis]:
        """Parse LLM response into InsiderAnalysis."""
        try:
            # Extract JSON from response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start == -1 or end == 0:
                return None

            json_str = response[start:end]
            data = json.loads(json_str)

            analysis = InsiderAnalysis(
                wallet_address=wallet,
                wallet_name=wallet_name,
                edge_score=float(data.get("edge_score", 0)),
                edge_type=data.get("edge_type", "none"),
                reasoning=data.get("reasoning", ""),
                suggested_action=data.get("suggested_action", "ignore"),
                markets_analyzed=int(data.get("markets_analyzed", 0)),
            )

            self.analysis_history.append(analysis)
            if analysis.edge_score > 0.6:
                self.known_insiders[wallet] = analysis.edge_score

            return analysis
        except Exception as e:
            print(f"[InsiderAnalyzer] Parse error: {e}")
            return None

    def get_top_insiders(self, limit: int = 5) -> list[InsiderAnalysis]:
        """Get wallets with highest edge scores."""
        sorted_analyses = sorted(
            self.analysis_history,
            key=lambda x: x.edge_score,
            reverse=True,
        )
        return sorted_analyses[:limit]
