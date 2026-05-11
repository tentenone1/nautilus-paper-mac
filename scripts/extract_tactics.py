#!/usr/bin/env python3
"""Extract manipulation tactics from the raw LLM output, removing chain-of-thought."""
import json
import re

with open("/Users/tentenone/workspace/nautilus-trading/research/manipulation_playbook.json") as f:
    d = json.load(f)

txt = d["llm_raw"]

# Strip the thinking process section - everything before "1. Spoofing" or "## 1." or similar
# Find where actual tactic content starts
tactic_starters = [
    "Spoofing", "Wash Trading", "Pump and Dump", "Sacrificial",
    "Front Running", "Liquidity Farming", "Oracle Manipulation",
    "Cross-Market", "Quote Stuffing", "Signal Account"
]

# Find first occurrence of a real tactic name
first_tactic = len(txt)
for s in tactic_starters:
    pos = txt.find(s)
    if pos > 0 and pos < first_tactic:
        first_tactic = pos

if first_tactic < len(txt):
    content = txt[first_tactic:]
else:
    # Split on "1. " after the first occurrence
    parts = txt.split("\n1. ")
    if len(parts) > 1:
        content = "1. " + "\n1. ".join(parts[1:])
    else:
        content = txt

# Find all tactic blocks
tactics = []
lines = content.split("\n")
current_tactic = None
current_lines = []

for line in lines:
    stripped = line.strip()
    # Check if this line starts a new tactic (number at start followed by a known tactic name)
    is_new_tactic = False
    for s in tactic_starters:
        if s.lower() in stripped.lower() and (stripped[:3].isdigit() or stripped[:2].isdigit()):
            is_new_tactic = True
            break
    
    if is_new_tactic:
        if current_tactic and current_lines:
            tactics.append({"name": current_tactic, "content": "\n".join(current_lines)})
        current_tactic = stripped[:100]
        current_lines = [stripped]
    elif current_tactic:
        current_lines.append(stripped)

if current_tactic and current_lines:
    tactics.append({"name": current_tactic, "content": "\n".join(current_lines)})

# Save extracted tactics
output = {
    "generated": d["generated"],
    "tactic_count": len(tactics),
    "tactics": [{"name": t["name"][:100], "preview": t["content"][:300]} for t in tactics],
    "full_content": content,
}

with open("/Users/tentenone/workspace/nautilus-trading/research/manipulation_playbook.json", "w") as f:
    json.dump(output, f, indent=2)

print(f"Extracted {len(tactics)} tactics")
for t in tactics:
    name = t["name"][:80]
    print(f"  {name}")
