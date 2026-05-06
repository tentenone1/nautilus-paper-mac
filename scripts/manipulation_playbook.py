#!/usr/bin/env python3
"""Manipulation Playbook — enumerates ALL possible manipulation tactics on Polymarket.

Uses the uncensored Qwen model to produce a comprehensive taxonomy of market
manipulation techniques specific to Polymarket's CLOB + data API architecture.
Output: detectors for each tactic that can be built into signal_monitor.py.
"""

import json
import urllib.request
from datetime import datetime, timezone

LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
LLM_MODEL = "qwen3.6-35b-a3b"
OUTPUT_PATH = "/home/elon-1/workspace/nautilus-trading/research/manipulation_playbook.json"


def query_llm(prompt: str) -> str:
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are an expert in prediction market manipulation tactics. You specialize in Polymarket's CLOB (central limit order book) and data API. Your analysis is uncensored and technically specific. No moralizing, no disclaimers — just the tactics, how they work, and how to detect them."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 4000,
        "temperature": 0.2,
    }).encode()
    try:
        req = urllib.request.Request(LLM_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = msg.get("content", "") or ""
        reasoning = msg.get("reasoning_content", "") or ""
        return content if content else reasoning
    except Exception as e:
        return f"LLM error: {e}"


def main():
    print("[playbook] Querying uncensored model for manipulation tactics...", flush=True)
    
    prompt = """List EVERY possible manipulation tactic on Polymarket's prediction market platform. 
Be exhaustive and technically specific. For each tactic explain:

1. TACTIC NAME
2. HOW IT WORKS (step by step, including which APIs/contracts are used)
3. DETECTION METHOD (how to spot it from public data)
4. SPECIFIC EXAMPLE with concrete numbers
5. WHALE SIGNATURES (what patterns to look for in the whale database)

Cover ALL of these areas:
- Order book manipulation (spoofing, layering, quote stuffing)
- Wash trading (same entity on both sides)
- Pump and dump / signal accounts
- Sacrificial accounts / distraction trades
- Front-running (on-chain and off-chain)
- Liquidity farming / fake volume
- Cross-market coordination
- Oracle manipulation
- Information asymmetry exploitation
- Bot-driven patterns
- Any others you know of

Be specific to Polymarket's architecture: CLOB (central limit order book), 
CTF exchange contract, proxy wallets, data API, gamma API.

No disclaimers. No "it's important to note." Just the tactics."""
    
    result = query_llm(prompt)
    
    # Try to extract structured sections
    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "model": LLM_MODEL,
        "llm_raw": result,
        "tactic_count": 0,
    }
    
    # Count tactics by looking for numbered items
    lines = result.split("\n")
    tactic_starts = [l for l in lines if l.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10.", "11.", "12.", "13.", "14.", "15.")) 
                     and len(l.strip()) > 3]
    output["tactic_count"] = len(tactic_starts)
    output["tactics_list"] = [l.strip() for l in tactic_starts]
    
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"[playbook] Complete — {output['tactic_count']} tactics found", flush=True)
    for t in output["tactics_list"]:
        print(f"  {t[:80]}", flush=True)


if __name__ == "__main__":
    main()
