#!/usr/bin/env python3
"""Check the field structure of the Whale Analysis Bitable table."""
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
print(f"Got token: {token[:20]}...")

BASE_TOKEN = "Jwr7b4Rf2a1EsfsqvwZcFJoXnVf"
TABLE_ID = "tblSM2BBBGJbZGO3"

# List records
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# Get table fields/metadata first
req = Request(
    f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/fields",
    headers=headers
)
resp = json.loads(urlopen(req).read())
print(f"\nFields:")
for f in resp.get("data", {}).get("items", []):
    print(f"  {f['field_name']:30s} | type={f['type']} | id={f['field_id']}")

# Get existing records
req2 = Request(
    f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records?page_size=20",
    headers=headers
)
resp2 = json.loads(urlopen(req2).read())
print(f"\nExisting records ({len(resp2.get('data',{}).get('items',[]))}):")
for r in resp2.get("data", {}).get("items", []):
    fields = r.get("fields", {})
    print(f"  record_id={r['record_id']} | {json.dumps(fields, ensure_ascii=False)[:150]}")
