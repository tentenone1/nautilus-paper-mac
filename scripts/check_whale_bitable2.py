#!/usr/bin/env python3
"""Check full records of Whale Analysis Bitable table."""
import json, os
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

# Get token
resp = json.loads(urlopen(Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode(),
    headers={"Content-Type": "application/json"}
)).read())
token = resp["tenant_access_token"]

BASE_TOKEN = "Jwr7b4Rf2a1EsfsqvwZcFJoXnVf"
TABLE_ID = "tblSM2BBBGJbZGO3"

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Get existing records with all fields
req = Request(
    f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records?page_size=20",
    headers=headers
)
resp = json.loads(urlopen(req).read())
print(f"Records ({len(resp.get('data',{}).get('items',[]))}):")
for r in resp.get("data", {}).get("items", []):
    rid = r["record_id"]
    fields = r.get("fields", {})
    # Get Whale Name specifically
    name = fields.get("Whale Name", "N/A")
    verdict = fields.get("Jailbreak Verdict", "")[:80]
    conf = fields.get("Confidence", "N/A")
    print(f"\n  {rid}")
    print(f"    Whale Name: {name}")
    print(f"    Verdict: {verdict}")
    print(f"    Confidence: {conf}")
    print(f"    Full fields: {json.dumps(fields, ensure_ascii=False)[:200]}")
