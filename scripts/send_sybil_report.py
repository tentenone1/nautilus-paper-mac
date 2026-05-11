#!/usr/bin/env python3
"""Send sybil signal report to Feishu Home channel."""
import json
import os
import sys
from pathlib import Path
import requests
import yaml

SIGNAL_FILE = Path("/home/elon-1/workspace/nautilus-trading/research/sybil_signal_queue.json")
CHAT_ID = "oc_9f3d634000ee97d8c71e0b81f55b1484"  # Home channel


def build_card(signals_data: dict) -> dict:
    signals = signals_data.get("signals", [])
    if not signals:
        return None

    signal_count = signals_data.get("signal_count", 0)
    types = {}
    for s in signals:
        t = s.get("signal_type", "unknown")
        types[t] = types.get(t, 0) + 1

    type_str = ", ".join(f"{v} {k}" for k, v in types.items())

    # Build signal list
    signal_lines = []
    high_confidence = []
    for s in signals:
        conf = int(s.get("confidence", 0) * 100)
        title = s.get("market_title", "Unknown")[:50]
        side = s.get("side", "?")
        wallets = s.get("wallet_count", 0)
        exposure = s.get("total_exposure_usd", 0)
        line = f"• **{conf}%** | {side} | {title} | {wallets}w | ${exposure:,.0f}"
        signal_lines.append(line)
        if conf >= 70:
            high_confidence.append(s)

    # Build card
    elements = [
        {
            "tag": "markdown",
            "content": f"**{signal_count} signals generated** ({type_str})"
        }
    ]

    # High confidence section
    if high_confidence:
        elements.append({"tag": "hr"})
        hc_lines = []
        for s in high_confidence:
            conf = int(s.get("confidence", 0) * 100)
            title = s.get("market_title", "Unknown")
            side = s.get("side", "?")
            wallets = s.get("wallet_count", 0)
            exposure = s.get("total_exposure_usd", 0)
            reason = s.get("reason", "")[:100]
            hc_lines.append(f"🔥 **{conf}%** {side} on **{title}**\n   {wallets} wallets | ${exposure:,.0f} exposure\n   {reason}")
        elements.append({
            "tag": "markdown",
            "content": "**⚠️ HIGH CONFIDENCE (>70%):**\n" + "\n".join(hc_lines)
        })

    # All signals
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": "**All Signals:**\n" + "\n".join(signal_lines)
    })

    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"🎯 Sybil Signals — {signal_count} generated"},
            "template": "blue"
        },
        "elements": elements
    }
    return card


def main():
    if not SIGNAL_FILE.exists():
        print("No sybil signal file found", file=sys.stderr)
        return

    with open(SIGNAL_FILE) as f:
        signals_data = json.load(f)

    signals = signals_data.get("signals", [])
    if not signals:
        print("No signals in file", file=sys.stderr)
        return

    card = build_card(signals_data)
    if not card:
        print("No signals to report", file=sys.stderr)
        return

    # Get credentials
    with open("/home/elon-1/.hermes/config.yaml") as f:
        cfg = yaml.safe_load(f)
    fs = cfg.get("feishu", {})
    app_id = fs.get("app_id", os.environ.get("FEISHU_APP_ID", ""))
    app_secret = fs.get("app_secret", os.environ.get("FEISHU_APP_SECRET", ""))

    # Get token
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10
    )
    token = r.json().get("tenant_access_token", "")

    if not token:
        print(f"Failed to get token: {r.text}", file=sys.stderr)
        return

    # Send message
    payload = {
        "receive_id": CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card)
    }
    r = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=10
    )
    print(f"Sent to Feishu: {r.status_code}")
    if r.status_code != 200:
        print(r.text, file=sys.stderr)


if __name__ == "__main__":
    main()