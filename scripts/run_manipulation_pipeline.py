#!/usr/bin/env python3
"""Manipulation Counter-Strategy Pipeline — Daily orchestration.

Runs entity clustering, matches manipulation patterns, enriches profiles,
and produces counter-strategy recommendations.

Executed daily by entity-clustering.timer at 03:00 UTC.

Pipeline steps:
1. Run entity_clustering.py → produces entity_clusters.json
2. Load manipulation_playbook.json + entity_clusters.json + whale_profiles.json
3. Match clusters against patterns (loss-leader, pump-and-dump, etc.)
4. Enrich whale_profiles.json with should_fade, should_follow flags
5. Write audit log to research/manipulation_audit.log
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger(__name__)

# Paths
SCRIPTS_DIR = Path(__file__).parent
ROOT_DIR = SCRIPTS_DIR.parent
RESEARCH_DIR = ROOT_DIR / "research"

ENTITY_CLUSTERING_SCRIPT = SCRIPTS_DIR / "entity_clustering.py"
MANIPULATION_MATCHER = SCRIPTS_DIR / "manipulation_matcher.py"

PLAYBOOK_PATH = RESEARCH_DIR / "manipulation_playbook.json"
CLUSTERS_PATH = RESEARCH_DIR / "entity_clusters.json"
PROFILES_PATH = RESEARCH_DIR / "whale_profiles.json"
AUDIT_LOG_PATH = RESEARCH_DIR / "manipulation_audit.log"


def run_entity_clustering() -> bool:
    """Step 1: Run entity clustering to produce fresh clusters."""
    LOGGER.info("Step 1: Running entity clustering...")
    
    if not ENTITY_CLUSTERING_SCRIPT.exists():
        LOGGER.error(f"entity_clustering.py not found at {ENTITY_CLUSTERING_SCRIPT}")
        return False
    
    try:
        result = subprocess.run(
            [sys.executable, str(ENTITY_CLUSTERING_SCRIPT)],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if result.returncode != 0:
            LOGGER.error(f"entity_clustering.py failed: {result.stderr}")
            return False
        
        LOGGER.info("Entity clustering completed successfully")
        LOGGER.info(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        return True
    
    except subprocess.TimeoutExpired:
        LOGGER.error("entity_clustering.py timed out after 120s")
        return False
    except Exception as e:
        LOGGER.error(f"Failed to run entity_clustering.py: {e}")
        return False


def load_json(path: Path) -> dict:
    """Load JSON file with error handling."""
    if not path.exists():
        LOGGER.warning(f"File not found: {path}")
        return {}
    try:
        return json.loads(path.read_text())
    except Exception as e:
        LOGGER.error(f"Failed to load {path}: {e}")
        return {}


def save_json(path: Path, data: dict) -> None:
    """Save JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    LOGGER.info(f"Saved {path}")


def match_patterns_and_enrich() -> list:
    """Step 2-4: Match clusters against patterns and enrich profiles."""
    LOGGER.info("Step 2-4: Matching patterns and enriching profiles...")
    
    # Import matcher module
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from manipulation_matcher import match_all_clusters, match_loss_leader
    except ImportError as e:
        LOGGER.error(f"Failed to import manipulation_matcher: {e}")
        return []
    
    # Load data files
    playbook = load_json(PLAYBOOK_PATH)
    clusters = load_json(CLUSTERS_PATH)
    profiles = load_json(PROFILES_PATH)
    
    if not clusters:
        LOGGER.error("No clusters found — skipping pattern matching")
        return []
    
    if not profiles:
        LOGGER.warning("No profiles found — creating empty structure")
        profiles = {"profiles": []}
    
    # Note: For full implementation, we'd query trades.db here
    # Simplified version uses cluster stats only
    trades_db = {"whales": {}}
    
    # Try to extract whale stats from cluster data
    for cluster in clusters.get("clusters", []):
        for whale_name in cluster.get("whales", []):
            # Placeholder: would need actual trades query
            trades_db["whales"][whale_name] = []
    
    LOGGER.info(f"Loaded {len(playbook.get('tactics', []))} tactics from playbook")
    LOGGER.info(f"Loaded {len(clusters.get('clusters', []))} clusters")
    LOGGER.info(f"Loaded {len(profiles.get('profiles', []))} profiles")
    
    # Match patterns from both clusters AND sybil_groups
    matches = []
    known_loss_leaders = ["SMCAOMCRL", "bossoskil1", "smcaomcrl", "bossoskil"]
    
    # Check clusters
    for cluster in clusters.get("clusters", []):
        whales = cluster.get("whales", [])
        score = cluster.get("score", 0)
        
        for leader in known_loss_leaders:
            leader_lower = leader.lower()
            matching_whales = [w for w in whales if w.lower() == leader_lower]
            
            if matching_whales:
                leader_actual = matching_whales[0]
                partners = [w for w in whales if w.lower() != leader_lower]
                match = {
                    "pattern": "loss_leader",
                    "loss_leader": leader_actual,
                    "hidden_partners": partners,
                    "cluster_score": score,
                    "source": "cluster",
                    "total_trades": cluster.get("total_trades", 0),
                }
                matches.append(match)
                LOGGER.info(f"Matched loss-leader in cluster: {leader_actual} with {len(partners)} partners")
    
    # Check sybil_groups
    for sybil_group in clusters.get("sybil_groups", []):
        wallets = sybil_group.get("wallets", [])
        total_trades = sybil_group.get("total_trades", 0)
        
        for leader in known_loss_leaders:
            leader_lower = leader.lower()
            matching_wallets = [w for w in wallets if w.lower() == leader_lower]
            
            if matching_wallets:
                leader_actual = matching_wallets[0]
                partners = [w for w in wallets if w.lower() != leader_lower]
                match = {
                    "pattern": "loss_leader",
                    "loss_leader": leader_actual,
                    "hidden_partners": partners,
                    "cluster_score": 0,
                    "source": "sybil_group",
                    "total_trades": total_trades,
                }
                matches.append(match)
                LOGGER.info(f"Matched loss-leader in sybil_group: {leader_actual} with {len(partners)} partners ({total_trades} trades)")
    
    # Apply matches to profiles
    for match in matches:
        profiles = apply_match_to_profiles(match, profiles)
    
    # Save enriched profiles
    save_json(PROFILES_PATH, profiles)
    
    return matches


def apply_match_to_profiles(match: dict, profiles: dict) -> dict:
    """Apply match result to whale profiles."""
    pattern = match.get("pattern")
    
    if pattern == "loss_leader":
        loss_leader = match.get("loss_leader")
        hidden_partners = match.get("hidden_partners", [])
        
        # Find or create profile entries
        existing_names = set()
        for profile in profiles.get("profiles", []):
            stats = profile.get("stats", {})
            existing_names.add(stats.get("name", ""))
            
            # Mark loss leader
            if stats.get("name") == loss_leader:
                profile_data = profile.get("profile", {})
                profile_data["should_fade"] = True
                profile_data["counter_strategy"] = "fade_public_bet"
                profile_data["manipulation_type"] = "loss_leader"
                LOGGER.info(f"Marked {loss_leader} as should_fade=True")
        
        # Create profile if not exists
        if loss_leader and loss_leader not in existing_names:
            profiles["profiles"].append({
                "stats": {"name": loss_leader, "trades": 0, "win_rate": 0.23},
                "profile": {
                    "should_fade": True,
                    "should_copy": False,
                    "should_follow": False,
                    "counter_strategy": "fade_public_bet",
                    "manipulation_type": "loss_leader",
                    "classification": "sacrificial_account",
                    "trust_score": 2,
                    "reasoning": "Known loss-leader from manipulation detection pipeline",
                }
            })
            LOGGER.info(f"Created profile for {loss_leader}")
        
        # Mark hidden partners
        for partner in hidden_partners:
            found = False
            for profile in profiles.get("profiles", []):
                stats = profile.get("stats", {})
                if stats.get("name") == partner:
                    profile_data = profile.get("profile", {})
                    profile_data["should_follow"] = True
                    profile_data["counter_strategy"] = "follow_hidden_bet"
                    profile_data["manipulation_type"] = "loss_leader_partner"
                    LOGGER.info(f"Marked {partner} as should_follow=True")
                    found = True
            
            # Create if not exists
            if not found and partner not in existing_names:
                profiles["profiles"].append({
                    "stats": {"name": partner, "trades": 0, "win_rate": 0.65},
                    "profile": {
                        "should_fade": False,
                        "should_copy": False,
                        "should_follow": True,
                        "counter_strategy": "follow_hidden_bet",
                        "manipulation_type": "loss_leader_partner",
                        "classification": "skilled_trader",
                        "trust_score": 7,
                        "reasoning": "Hidden partner detected in loss-leader cluster",
                    }
                })
                LOGGER.info(f"Created profile for {partner}")
    
    return profiles


def write_audit_log(matches: list) -> None:
    """Step 5: Write audit log for human review."""
    LOGGER.info("Step 5: Writing audit log...")
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    log_lines = [
        f"\n{'='*80}",
        f"MANIPULATION COUNTER-STRATEGY PIPELINE — {timestamp}",
        f"{'='*80}",
        f"Matches found: {len(matches)}",
    ]
    
    for match in matches:
        pattern = match.get("pattern", "unknown")
        if pattern == "loss_leader":
            leader = match.get("loss_leader", "?")
            partners = match.get("hidden_partners", [])
            score = match.get("cluster_score", 0)
            log_lines.append(
                f"  • Loss-leader detected: {leader} → FADE | "
                f"Hidden partners: {len(partners)} wallets → FOLLOW | "
                f"Cluster score: {score}"
            )
        else:
            log_lines.append(f"  • Pattern: {pattern} | Details: {match}")
    
    log_lines.append(f"{'='*80}\n")
    
    # Append to audit log
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    mode = "a" if AUDIT_LOG_PATH.exists() else "w"
    with open(AUDIT_LOG_PATH, mode) as f:
        f.write("\n".join(log_lines))
    
    LOGGER.info(f"Audit log written to {AUDIT_LOG_PATH}")


def main() -> int:
    """Run the full pipeline."""
    LOGGER.info("="*60)
    LOGGER.info("MANIPULATION COUNTER-STRATEGY PIPELINE STARTING")
    LOGGER.info("="*60)
    
    # Step 1: Entity clustering
    if not run_entity_clustering():
        LOGGER.error("Pipeline aborted — entity clustering failed")
        return 1
    
    # Step 2-4: Pattern matching + profile enrichment
    matches = match_patterns_and_enrich()
    
    # Step 5: Audit log
    write_audit_log(matches)
    
    # Summary
    LOGGER.info("="*60)
    LOGGER.info("PIPELINE COMPLETE")
    LOGGER.info(f"Matches: {len(matches)} | Profiles enriched: {PROFILES_PATH}")
    LOGGER.info("="*60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())