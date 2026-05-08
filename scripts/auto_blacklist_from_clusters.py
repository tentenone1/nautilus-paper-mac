#!/usr/bin/env python3
"""Auto-blacklist: Cross-reference entity clusters with whale profiles.

For each entity cluster, if ANY member has should_fade=True or
classification = "sacrificial_account", recommend ALL cluster members
for blacklist.

Reads: research/entity_clusters.json, research/whale_profiles.json
Updates: strategies/wf_constants.py (adds to WHALE_BLACKLIST)
Output: research/auto_blacklist_report.json

Runs daily after entity_clustering.py.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLUSTERS_PATH = os.path.join(BASE_DIR, "research", "entity_clusters.json")
PROFILES_PATH = os.path.join(BASE_DIR, "research", "whale_profiles.json")
CONSTANTS_PATH = os.path.join(BASE_DIR, "strategies", "wf_constants.py")
OUTPUT_PATH = os.path.join(BASE_DIR, "research", "auto_blacklist_report.json")

# Known entity names (from sybil groups, cluster wallets) that are addresses or
# account-based labels — we filter these out and only keep human-readable whale_names
SKIP_PATTERNS = [
    r'^0x[a-fA-F0-9]{6,}',  # Ethereum addresses
    r'^p\d+-0x',             # Short whale IDs like "p233-0x5d1d9c"
]


def is_blacklistable(name: str) -> bool:
    """Only add human-readable whale names to blacklist, not raw addresses."""
    return not any(re.match(p, name) for p in SKIP_PATTERNS)


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        print(f"[auto_blacklist] File not found: {path}", flush=True)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_fade_profiles(profiles: dict) -> dict[str, str]:
    """Extract whale names marked should_fade=True or sacrificial_account.

    Returns: {whale_name: reason_string}
    """
    fade_map = {}
    for p in profiles.get("profiles", []):
        stats = p.get("stats", {})
        profile = p.get("profile", {})
        name = stats.get("name", "")
        if not name:
            continue
        classification = profile.get("classification", "")
        should_fade = profile.get("should_fade", False)

        if should_fade:
            fade_map[name] = f"should_fade=True (classification={classification})"
        elif classification and "sacrificial" in str(classification).lower():
            fade_map[name] = f"sacrificial_account (classification={classification})"

    return fade_map


def find_blacklist_candidates(
    clusters: dict, fade_map: dict[str, str]
) -> dict[str, list[str]]:
    """For each cluster, if any member is in fade_map, all members become blacklist candidates.

    Returns: {reason -> [whale_names]}
    """
    candidates: dict[str, list[str]] = {}
    seen: set[str] = set()

    # Check entity clusters
    for cluster in clusters.get("clusters", []):
        entities = cluster.get("entities", [])
        trigger_reasons = []

        for entity in entities:
            if entity in fade_map:
                trigger_reasons.append(fade_map[entity])

        if trigger_reasons:
            reason = "; ".join(sorted(set(trigger_reasons)))
            for entity in entities:
                if is_blacklistable(entity) and entity not in seen:
                    candidates.setdefault(reason, []).append(entity)
                    seen.add(entity)

    # Check sybil groups too
    for group in clusters.get("sybil_groups", []):
        wallets = group.get("wallets", [])
        trigger_reasons = []

        for entity in wallets:
            if entity in fade_map:
                trigger_reasons.append(fade_map[entity])

        if trigger_reasons:
            reason = "; ".join(sorted(set(trigger_reasons)))
            for entity in wallets:
                if is_blacklistable(entity) and entity not in seen:
                    candidates.setdefault(reason, []).append(entity)
                    seen.add(entity)

    return candidates


def update_constants_blacklist(candidates: dict[str, list[str]]) -> tuple[int, int]:
    """Append new whale names to WHALE_BLACKLIST frozenset in wf_constants.py.

    Uses patch tool semantics: finds the line before '})' and inserts new entries.
    Returns: (added_count, total_new)
    """
    all_names = set()
    for names in candidates.values():
        all_names.update(names)

    if not all_names:
        print("[auto_blacklist] No new blacklist candidates", flush=True)
        return 0, 0

    if not os.path.exists(CONSTANTS_PATH):
        print(f"[auto_blacklist] Constants file not found: {CONSTANTS_PATH}", flush=True)
        return 0, len(all_names)

    with open(CONSTANTS_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Find the WHALE_BLACKLIST frozenset closing
    bl_start = content.find("WHALE_BLACKLIST = frozenset({")
    if bl_start == -1:
        print("[auto_blacklist] Could not find WHALE_BLACKLIST in wf_constants.py", flush=True)
        return 0, len(all_names)

    # Extract existing names from between { and })
    brace_open = content.index("{", bl_start)
    brace_close = content.index("})", brace_open)

    existing_block = content[brace_open + 1:brace_close]
    existing_names = set()
    for line in existing_block.split("\n"):
        line = line.strip().rstrip(",")
        if line.startswith('"') and line.endswith('"'):
            existing_names.add(line[1:-1])
        elif line.startswith("'") and line.endswith("'"):
            existing_names.add(line[1:-1])

    new_names = all_names - existing_names
    if not new_names:
        print("[auto_blacklist] All candidates already in WHALE_BLACKLIST", flush=True)
        return 0, 0

    # Build new entries to insert before the last newline before '})'
    new_entries = []
    for name in sorted(new_names):
        reason = ""
        for r, names in candidates.items():
            if name in names:
                cls = r.split("classification=")[-1].rstrip(")") if "classification=" in r else r[:60]
                reason = f" entity cluster — {cls}"
                break
        new_entries.append(f'    "{name}",{reason}')

    # Insert before the closing "})"
    insert_pos = content.rfind("\n", bl_start, brace_close)
    if insert_pos == -1:
        insert_pos = brace_close - 1

    new_block = "\n".join(new_entries) + "\n"
    updated = content[:insert_pos + 1] + new_block + content[insert_pos + 1:]

    with open(CONSTANTS_PATH, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"[auto_blacklist] Added {len(new_names)} whales to WHALE_BLACKLIST:", flush=True)
    for name in sorted(new_names):
        print(f"  {name}", flush=True)

    return len(new_names), len(all_names)


def main():
    print(f"[auto_blacklist] Starting at {datetime.now().isoformat()}", flush=True)

    clusters = load_json(CLUSTERS_PATH)
    profiles = load_json(PROFILES_PATH)

    if not clusters:
        print("[auto_blacklist] No entity clusters data — run entity_clustering.py first", flush=True)
        sys.exit(1)

    fade_map = get_fade_profiles(profiles)
    print(f"[auto_blacklist] Found {len(fade_map)} fade/sacrificial profiles", flush=True)

    if not fade_map:
        print("[auto_blacklist] No fade candidates in profiles — nothing to blacklist", flush=True)
        # Still save a report
        report = {
            "generated": datetime.now(timezone.utc).isoformat(),
            "fade_profiles_found": 0,
            "candidates": {},
            "added_count": 0,
        }
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        return

    candidates = find_blacklist_candidates(clusters, fade_map)
    total_candidates = sum(len(v) for v in candidates.values())
    print(f"[auto_blacklist] {total_candidates} blacklist candidates from entity clusters", flush=True)

    for reason, names in candidates.items():
        print(f"  [{len(names)} whales] {reason}", flush=True)
        for n in names:
            print(f"    - {n}", flush=True)

    # Update constants file
    added, total = update_constants_blacklist(candidates)

    # Save report
    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "fade_profiles_found": len(fade_map),
        "cluster_groups_checked": len(clusters.get("clusters", [])),
        "candidates": candidates,
        "added_to_blacklist": added,
        "total_candidates": total_candidates,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"[auto_blacklist] Report: {OUTPUT_PATH}", flush=True)
    print(f"[auto_blacklist] Added {added}/{total_candidates} new whales to WHALE_BLACKLIST", flush=True)


if __name__ == "__main__":
    main()
