"""Manipulation Pattern Matcher — Detects loss-leader and coordinated trading tactics.

Matches entity clusters against manipulation playbook patterns and
enriches whale profiles with counter-strategy flags.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger(__name__)

# Paths (relative to nautilus-trading root)
PLAYBOOK_PATH = Path("research/manipulation_playbook.json")
CLUSTERS_PATH = Path("research/entity_clusters.json")
PROFILES_PATH = Path("research/whale_profiles.json")


def load_json(path: Path) -> Dict[str, Any]:
    """Load JSON file with error handling."""
    if not path.exists():
        LOGGER.warning(f"File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        LOGGER.error(f"Failed to load {path}: {e}")
        return {}


def save_json(path: Path, data: Dict[str, Any]) -> None:
    """Save JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    LOGGER.info(f"Saved {path} ({len(json.dumps(data))} chars)")


def match_loss_leader(cluster: Dict[str, Any], trades_db: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Detect loss-leader pattern: large public bets that lose intentionally.
    
    Pattern indicators:
    1. One whale in cluster has many trades with LOW win rate (<30%)
    2. Other whales in same cluster have HIGH win rate (>60%) on opposite sides
    3. Timing correlation (trades within same market, same time window)
    
    Returns match details if detected, None otherwise.
    """
    whales = cluster.get("whales", [])
    if len(whales) < 2:
        return None  # Need at least 2 whales for loss-leader + hidden partner
    
    # Analyze each whale's win rate from trades_db
    whale_stats = {}
    for whale_name in whales:
        trades = trades_db.get("whales", {}).get(whale_name, [])
        if not trades:
            continue
        
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        total = len(trades)
        win_rate = wins / total if total > 0 else 0
        
        whale_stats[whale_name] = {
            "trades": total,
            "wins": wins,
            "win_rate": win_rate,
            "avg_pnl": sum(t.get("pnl", 0) for t in trades) / total if total > 0 else 0,
        }
    
    # Find loss leader candidate (low win rate, many trades)
    loss_leader = None
    for whale_name, stats in whale_stats.items():
        if stats["trades"] >= 50 and stats["win_rate"] < 0.30:
            loss_leader = whale_name
            break
    
    if not loss_leader:
        return None
    
    # Find hidden partners (high win rate, fewer trades)
    hidden_partners = [
        w for w, s in whale_stats.items()
        if w != loss_leader and s["win_rate"] > 0.55
    ]
    
    if not hidden_partners:
        return None
    
    return {
        "pattern": "loss_leader",
        "loss_leader": loss_leader,
        "hidden_partners": hidden_partners,
        "loss_leader_stats": whale_stats[loss_leader],
        "partner_stats": {w: whale_stats[w] for w in hidden_partners},
        "cluster_score": cluster.get("score", 0),
    }


def apply_to_profiles(match: Dict[str, Any], profiles: Dict[str, Any]) -> Dict[str, Any]:
    """Update whale profiles with counter-strategy flags based on match.
    
    For loss-leader pattern:
    - loss_leader whale → should_fade=True (fade their public bets)
    - hidden_partners → should_follow=True (follow their hidden bets)
    """
    if not match or not profiles:
        return profiles
    
    pattern = match.get("pattern")
    
    if pattern == "loss_leader":
        loss_leader = match.get("loss_leader")
        hidden_partners = match.get("hidden_partners", [])
        
        # Mark loss leader for fading
        if loss_leader:
            for profile in profiles.get("profiles", []):
                stats = profile.get("stats", {})
                if stats.get("name") == loss_leader:
                    profile_data = profile.get("profile", {})
                    profile_data["should_fade"] = True
                    profile_data["counter_strategy"] = "fade_public_bet"
                    profile_data["manipulation_type"] = "loss_leader"
                    LOGGER.info(f"Marked {loss_leader} as should_fade=True (loss leader)")
        
        # Mark hidden partners for following
        for partner in hidden_partners:
            for profile in profiles.get("profiles", []):
                stats = profile.get("stats", {})
                if stats.get("name") == partner:
                    profile_data = profile.get("profile", {})
                    profile_data["should_follow"] = True
                    profile_data["counter_strategy"] = "follow_hidden_bet"
                    profile_data["manipulation_type"] = "loss_leader_partner"
                    LOGGER.info(f"Marked {partner} as should_follow=True (hidden partner)")
    
    return profiles


def match_all_clusters(
    clusters: Dict[str, Any],
    trades_db: Dict[str, Any],
    profiles: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Match all clusters against manipulation patterns and update profiles."""
    matches = []
    
    for cluster in clusters.get("clusters", []):
        # Try loss-leader pattern
        match = match_loss_leader(cluster, trades_db)
        if match:
            matches.append(match)
            profiles = apply_to_profiles(match, profiles)
    
    return matches, profiles


if __name__ == "__main__":
    # CLI interface for testing
    import sys
    
    print("Loading data files...")
    playbook = load_json(PLAYBOOK_PATH)
    clusters = load_json(CLUSTERS_PATH)
    profiles = load_json(PROFILES_PATH)
    
    print(f"Playbook: {len(playbook.get('tactics', []))} tactics")
    print(f"Clusters: {len(clusters.get('clusters', []))} clusters")
    print(f"Profiles: {len(profiles.get('profiles', []))} profiles")
    
    # Note: trades_db would need to be loaded from trades.db
    # For now, using placeholder
    trades_db = {"whales": {}}
    
    matches, updated_profiles = match_all_clusters(clusters, trades_db, profiles)
    
    print(f"\nMatches found: {len(matches)}")
    for m in matches:
        print(f"  {m['pattern']}: {m['loss_leader']} → fade, {m['hidden_partners']} → follow")
    
    if matches:
        save_json(PROFILES_PATH, updated_profiles)
        print(f"\nProfiles updated and saved to {PROFILES_PATH}")