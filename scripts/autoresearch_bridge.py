#!/usr/bin/env python3
"""Autoresearch Bridge — takes signal monitor detections and produces trade recommendations.

Pipeline:
1. Reads signal_monitor_state.json for tracked markets
2. For each promising detection, queries CLOB midpoint for live pricing
3. Feeds market data through uncensored LLM for analysis
4. Outputs trade card: BUY/WAIT/SKIP with entry, target, stop, Kelly

Output: research/trade_recommendations.json
"""

import json
import urllib.request
import time
import os
from datetime import datetime, timezone

STATE_FILE = "/home/elon-1/workspace/nautilus-trading/research/signal_monitor_state.json"
DETECTIONS_FILE = "/home/elon-1/workspace/nautilus-trading/research/signal_detections.json"
OUTPUT_FILE = "/home/elon-1/workspace/nautilus-trading/research/trade_recommendations.json"
LLM_URL = "http://192.168.50.148:1234/v1/chat/completions"
LLM_MODEL = "qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive"
CLOB_MIDPOINT_URL = "https://clob.polymarket.com/midpoint?token_id={}"
MARKETS_API = "https://data-api.polymarket.com/markets?conditionId={}&limit=1"

# Noise markets to skip
NOISE_TITLES = ["highest temperature", "Bitcoin Up or Down"]
NOISE_CONDITIONS = set()  # Populated from previous runs


def query_llm(prompt: str) -> str:
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are a Polymarket trading analyst. Output ONLY valid JSON. Do NOT include any reasoning, thinking, or explanation. Just the JSON object."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 600,
        "temperature": 0.1,
    }).encode()
    try:
        req = urllib.request.Request(LLM_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""
        return content if content else reasoning
    except Exception as e:
        return f"LLM error: {e}"


def fetch_json(url: str, timeout: int = 10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def get_market_info(condition_id: str) -> dict | None:
    """Get live market info from Polymarket API."""
    data = fetch_json(MARKETS_API.format(condition_id))
    if data and len(data) > 0:
        return data[0]
    return None


def check_midpoint(condition_id: str) -> float | None:
    """Get CLOB midpoint price for a market."""
    # For now return None — need token_id which varies per market
    # This is a placeholder for Phase 2 midpoint tracking
    return None


def analyze_market(detection: dict, market_info: dict = None) -> dict:
    """Run uncensored LLM analysis on a detection and return trade card."""
    market = detection.get("market", "Unknown")
    price = detection.get("lowest_price", 0.5)
    age = detection.get("age_seconds", 0)
    
    prompt = f"""Analyze this Polymarket market and output a trade recommendation as JSON only.

MARKET: {market}
Lowest entry: ${price:.2f}
Age: {age:.0f}s
Trades in last scan: {detection.get('trades_count', 0)}
Detection type: {detection.get('type', 'unknown')}

Evaluate: Is this a coordinated whale pump or random noise?
If actionable, what's the entry, target, stop, and Kelly size?

OUTPUT (JSON only):
{{
  "market": "name",
  "decision": "BUY | WAIT | SKIP",
  "confidence": 0.0-1.0,
  "reason": "brief reason",
  "entry_price": 0.0,
  "target_price": 0.0,
  "stop_price": 0.0,
  "kelly_fraction": 0.0,
  "hold_hours": 0
}}"""
    
    llm_out = query_llm(prompt)
    
    # Parse JSON from LLM output — must handle reasoning model's CoT prefix
    import re
    # Strip thinking process prefix
    cleaned = llm_out
    for prefix in ["Here's a thinking process:", "Let me think about this:", "I'll analyze"]:
        if prefix in cleaned:
            cleaned = cleaned.split(prefix, 1)[-1]
    # Find first { and last }
    json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass
    
    return {
        "market": market,
        "decision": "SKIP",
        "confidence": 0.0,
        "reason": f"LLM parse failed: {llm_out[:100]}",
    }


def main():
    print(f"[autoresearch] Starting at {datetime.now(timezone.utc).isoformat()}", flush=True)
    
    # Load detections
    detections = []
    if os.path.exists(DETECTIONS_FILE):
        with open(DETECTIONS_FILE) as f:
            detections = json.load(f)
    
    # Load existing recommendations
    existing = []
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE) as f:
            existing = json.load(f)
    existing_markets = {r.get("market", "") for r in existing}
    
    # Filter for new detections only
    new_detections = [d for d in detections if d.get("market", "") not in existing_markets]
    new_detections = [d for d in new_detections if not any(
        n in ((d.get("market", "") or "") + (d.get("title", "") or "")).lower() 
        for n in NOISE_TITLES + ["bitcoin", "temperature", "weather"]
    )]
    
    print(f"[autoresearch] {len(new_detections)} new detections to analyze", flush=True)
    
    recommendations = []
    for det in new_detections[-5:]:  # Max 5 per run (LLM cost)
        print(f"  Analyzing: {det.get('market', '?')[:50]}...", flush=True)
        
        # Check if market is still live
        cid = det.get("condition_id", "")
        market_info = get_market_info(cid) if cid else None
        midpoint = check_midpoint(cid) if cid else None
        
        rec = analyze_market(det, market_info)
        rec["timestamp"] = datetime.now(timezone.utc).isoformat()
        rec["condition_id"] = cid
        rec["detection_price"] = det.get("lowest_price")
        
        recommendations.append(rec)
        
        status = "🟢" if rec.get("decision") == "BUY" else ("🟡" if rec.get("decision") == "WAIT" else "⚫")
        print(f"  {status} {rec.get('decision', '?')} | {rec.get('confidence', 0):.0%} | {rec.get('reason', '')[:60]}", flush=True)
        
        time.sleep(1)  # Rate limit between LLM calls
    
    # Merge with existing
    all_recs = existing + recommendations
    # Keep last 50
    all_recs = all_recs[-50:]
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_recs, f, indent=2)
    
    # Print summary
    buys = sum(1 for r in recommendations if r.get("decision") == "BUY")
    print(f"\n[autoresearch] Complete. {len(recommendations)} analyzed → {buys} BUY signals", flush=True)


if __name__ == "__main__":
    main()
