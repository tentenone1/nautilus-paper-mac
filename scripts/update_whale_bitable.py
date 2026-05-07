#!/usr/bin/env python3
"""Update Whale Analysis Bitable with new jailbreak deep analysis results."""
import json, os
from datetime import datetime, timezone
from urllib.request import Request, urlopen

ENV_PATH = "/opt/data/.env"
env_vars = {}
if os.path.exists(ENV_PATH):
    for line in open(ENV_PATH):
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env_vars[k.strip()] = v.strip()

APP_ID = env_vars.get("FEISHU_APP_ID", "")
APP_SECRET = env_vars.get("FEISHU_APP_SECRET", "")

BASE_TOKEN = "Jwr7b4Rf2a1EsfsqvwZcFJoXnVf"
TABLE_ID = "tblSM2BBBGJbZGO3"

def get_token():
    resp = json.loads(urlopen(Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode(),
        headers={"Content-Type": "application/json"}
    )).read())
    return resp["tenant_access_token"]

def update_record(token, record_id, fields):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = json.dumps({"fields": fields}).encode()
    req = Request(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/{record_id}",
        data=payload, headers=headers, method="PUT"
    )
    resp = json.loads(urlopen(req).read())
    return resp.get("code") == 0, resp

def build_verdict(whale):
    """Build the Jailbreak Verdict text from parsed data."""
    action = whale["action"]
    style = whale["style"]
    skill = whale["skill"]
    conf = whale["confidence"]
    action_label = {"COPY": "COPY", "FADE": "FADE", "WATCH": "WATCH"}.get(action, action)
    return f"{action_label}. {style} {skill.title()}. conf={conf:.2f}. (5900X jailbreak)"

token = get_token()
print(f"Token obtained: {token[:20]}...")

now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

# Define updates: record_id -> new fields
updates = {
    # RJW1 - fetch failed, downgrade to WATCH with note
    "recviQsUP5D7Ho": {
        "Jailbreak Verdict": "WATCH. Data fetch failed (SSL timeout). Unable to classify. conf=0.00. (5900X jailbreak)",
        "Confidence": 0.0,
        "Last Updated": now_ms,
    },
    # surfandturf: COPY 0.85 -> COPY 0.75
    "recviQsUP5Yv1e": {
        "Jailbreak Verdict": build_verdict({"action": "COPY", "style": "High-volume NBA and sports prop accumulator focusing on long-tail outcomes.", "skill": "skilled", "confidence": 0.75}),
        "Confidence": 0.75,
        "Last Updated": now_ms,
    },
    # matanovik: COPY 0.70 -> COPY 0.65
    "recviQsUP57O9t": {
        "Jailbreak Verdict": build_verdict({"action": "COPY", "style": "Diversified sports bettor specializing in cross-sport moneyline and player props.", "skill": "skilled", "confidence": 0.65}),
        "Confidence": 0.65,
        "Last Updated": now_ms,
    },
    # p150-0xba389f: WATCH 0.45 -> WATCH 0.45 (no change)
    "recviQsUP5At6o": {
        "Jailbreak Verdict": build_verdict({"action": "WATCH", "style": "Low-volume accumulator targeting miscellaneous and niche markets.", "skill": "noise", "confidence": 0.45}),
        "Confidence": 0.45,
        "Last Updated": now_ms,
    },
    # pilotbaby: WATCH 0.40 -> WATCH 0.50
    "recviQsUP5oVu9": {
        "Jailbreak Verdict": build_verdict({"action": "WATCH", "style": "NBA-centric accumulator with heavy focus on over/under markets.", "skill": "noise", "confidence": 0.50}),
        "Confidence": 0.50,
        "Last Updated": now_ms,
    },
    # asdfjh: COPY 0.90 -> COPY 0.80
    "recviQsUP5RCBO": {
        "Jailbreak Verdict": build_verdict({"action": "COPY", "style": "High-volume accumulator across miscellaneous markets acting as liquidity provider.", "skill": "skilled", "confidence": 0.80}),
        "Confidence": 0.80,
        "Last Updated": now_ms,
    },
    # SMCAOMCRL: WATCH 0.45 -> WATCH 0.40
    "recviQsUP5E86d": {
        "Jailbreak Verdict": build_verdict({"action": "WATCH", "style": "Low-volume accumulator focusing on miscellaneous and NBA markets.", "skill": "noise", "confidence": 0.40}),
        "Confidence": 0.40,
        "Last Updated": now_ms,
    },
    # benwyatt: COPY 0.80 -> COPY 0.75
    "recviQsUP5s31U": {
        "Jailbreak Verdict": build_verdict({"action": "COPY", "style": "Sports accumulator with balanced over/under and moneyline deployment.", "skill": "skilled", "confidence": 0.75}),
        "Confidence": 0.75,
        "Last Updated": now_ms,
    },
    # JPMorgan101: COPY 0.75 -> COPY 0.90 (significant upgrade)
    "recviQsUP5FmES": {
        "Jailbreak Verdict": build_verdict({"action": "COPY", "style": "High-frequency NBA trader with active position rolling and profit-taking.", "skill": "skilled", "confidence": 0.90}),
        "Confidence": 0.90,
        "Last Updated": now_ms,
    },
    # bossoskil1: COPY 0.85 -> COPY 0.78
    "recviQsUP5306z": {
        "Jailbreak Verdict": build_verdict({"action": "COPY", "style": "Esports and sports accumulator with high conviction in tournament brackets.", "skill": "skilled", "confidence": 0.78}),
        "Confidence": 0.78,
        "Last Updated": now_ms,
    },
    # trade-via-Gravia: WATCH 0.35 -> WATCH 0.35 (no change)
    "recviQsUP5MOQY": {
        "Jailbreak Verdict": build_verdict({"action": "WATCH", "style": "Crypto scalper targeting short-term candle settlement markets.", "skill": "noise", "confidence": 0.35}),
        "Confidence": 0.35,
        "Last Updated": now_ms,
    },
    # Countryside: COPY 0.75 -> COPY 0.85
    "recviQsUP5hEhM": {
        "Jailbreak Verdict": build_verdict({"action": "COPY", "style": "Political market specialist with high-conviction long-term positioning.", "skill": "skilled", "confidence": 0.85}),
        "Confidence": 0.85,
        "Last Updated": now_ms,
    },
}

success_count = 0
fail_count = 0
for record_id, fields in updates.items():
    ok, resp = update_record(token, record_id, fields)
    if ok:
        success_count += 1
        name = fields.get("Jailbreak Verdict", "")[:40]
        print(f"  ✅ {record_id} updated")
    else:
        fail_count += 1
        err = resp.get("msg", "unknown")
        print(f"  ❌ {record_id} FAILED: {err}")

print(f"\nSummary: {success_count} updated, {fail_count} failed")
