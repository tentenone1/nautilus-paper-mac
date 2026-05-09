"""
Sybil Position Aggregator — v2
Queries Polymarket data API for all sybil wallets, filters ACTIVE positions only,
aggregates by group into meta-whale exposures.

Output: research/sybil_positions.json
"""

import json
import logging
import os
import re
import sqlite3
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DATA_API_BASE = "https://data-api.polymarket.com"

# Paths for entity cluster integration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_DB = os.path.join(BASE_DIR, "research", "trades.db")
ENTITY_CLUSTERS_PATH = os.path.join(BASE_DIR, "research", "entity_clusters.json")

SYBIL_GROUPS = {
    "sybil_group_1": {
        "priority": "HIGH",
        "wallets": {
            "0x492442eab586f242b53bda933fd5de859c8a3782": "coordinator",
            "0xacb206b460a17382a734de8d931cc176307eb989": "AppleTime67",
            "0xe26cacfaa3f695a2a239e5918936b10d56f188cf": "Dvitaminbets",
            "0xd106952ebf30a3125affd8a23b6c1f30c35fc79c": "Herdonia",
            "0x437961a3b2684a4835da753e894d4b5cffdb2e16": "NewTeamSosed4",
            "0xf39651f0addaad0221806d828197064b97feed0d": "Pajamapants",
            "0xa71093cafc0c099b4ccab24c3cb8018d817923c4": "Talvez10",
            "0xa8e089ade142c95538e06196e09c85681112ad50": "Wannac",
            "0x0767aa79d578aead1c849fd9f0fdc6cdb50336b0": "beetlepimp",
            "0x1117eade222413335b7ec959e5b48c1d3dbc3532": "benwyatt",
            "0xa5ea13a81d2b7e8e424b182bdc1db08e756bd96a": "bossoskil1",
            "0x29b52d98ac9ef9414b04164246c95bc63d74cc6c": "loitterer",
            "0x84cfffc3f16dcc353094de30d4a45226eccd2f63": "mooseborzoi",
            "0x32ccd9015a900fde62040162a04bedf093c668b3": "pilotlady",
            "0xe48109602719f95c247fec255ffb71bab3f985a3": "trade-via-Gravia",
        },
    },
    "sybil_group_2": {
        "priority": "MODERATE",
        "wallets": {
            "0x5d1d9cfd66ee3068c2a8a57dedf1e1b006dcafd2": "coordinator",
            "0x3b5c629f114098b0dee345fb78b7a3a013c7126e": "SMCAOMCRL",
            "0xe96fb5321534971aed483029d2712917fda9ff4b": "meifei123",
        },
    },
    "sybil_group_3": {
        "priority": "LOW",
        "wallets": {
            "0x9495425feeb0c250accb89275c97587011b19a27": "LaBradfordSmith22",
            "0xba389f76b0119aed07c53c9029852664bd97e406": "joblessfinalboss",
            "0x39d3c773be30fcc73161fc6768f46d563a779ef0": "matanovik",
        },
    },
}


def load_entity_clusters() -> dict:
    """Load entity clusters from entity_clusters.json and convert to sybil groups.

    For each cluster with 3+ wallets, creates a dynamic sybil group.
    Maps readable names to addresses using trades.db.
    Returns a dict matching SYBIL_GROUPS format.
    """
    if not os.path.exists(ENTITY_CLUSTERS_PATH):
        logger.info("No entity_clusters.json found, using hardcoded groups only")
        return {}

    if not os.path.exists(TRADES_DB):
        logger.warning("trades.db not found, cannot map names to addresses")
        return {}

    try:
        with open(ENTITY_CLUSTERS_PATH, "r", encoding="utf-8") as f:
            clusters_data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load entity clusters: {e}")
        return {}

    # Build name -> address map from trades.db
    try:
        db = sqlite3.connect(TRADES_DB)
        rows = db.execute("""
            SELECT DISTINCT whale_name, whale_address FROM trades
            WHERE whale_address IS NOT NULL AND whale_address != ''
            AND whale_name NOT LIKE 'unknown%'
        """).fetchall()
        db.close()
    except sqlite3.Error as e:
        logger.warning(f"Failed to query trades.db: {e}")
        return {}

    name_to_addr: dict[str, str] = {}
    for name, addr in rows:
        clean_name = name.strip().lower()
        clean_addr = addr.strip().lower()
        if clean_name and clean_addr and clean_addr.startswith("0x"):
            name_to_addr[clean_name] = clean_addr
            name_to_addr[name.strip()] = addr.strip()

    dynamic_groups: dict = {}
    cluster_id = 0

    for cluster in clusters_data.get("clusters", []):
        entities = cluster.get("entities", [])
        if len(entities) < 3:
            continue

        cluster_id += 1
        wallets: dict[str, str] = {}
        for entity in entities:
            clean = entity.strip()
            if clean.startswith("0x"):
                # Raw address — use as-is for coordinator
                wallets[clean] = "coordinator"
            else:
                # Named entity — look up address
                addr = name_to_addr.get(clean) or name_to_addr.get(clean.lower())
                if addr:
                    wallets[addr] = clean
                else:
                    wallets[clean] = clean

        if len(wallets) >= 3:
            group_id = f"entity_cluster_{cluster_id}"
            dynamic_groups[group_id] = {
                "priority": "AUTO",
                "wallets": wallets,
            }
            logger.info(
                f"Loaded entity cluster {group_id}: {len(wallets)} wallets, "
                f"from cluster of {len(entities)} entities"
            )

    if not dynamic_groups:
        logger.info("No entity clusters with 3+ wallets found")
    else:
        logger.info(f"Loaded {len(dynamic_groups)} dynamic entity clusters")

    return dynamic_groups


def is_active_position(pos: dict) -> bool:
    """Check if a position is still open (not resolved/redeemed)."""
    if pos.get("redeemable", False):
        return False
    price = pos.get("curPrice", 0) or 0
    if price == 0 or price == 1:
        return False
    pnl = pos.get("percentPnl", 0) or 0
    if abs(pnl) >= 99.0:
        return False
    return True


def fetch_positions(address: str, timeout: int = 15) -> list[dict]:
    """Fetch positions for a wallet from Polymarket data API."""
    url = f"{DATA_API_BASE}/positions?user={address}"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warning(f"Failed to fetch positions for {address}: {e}")
        return []


def aggregate_by_group() -> dict:
    """Query all sybil wallets, filter active positions, aggregate by group.

    Merges hardcoded SYBIL_GROUPS with dynamic entity clusters from
    entity_clusters.json. Dynamic groups are loaded on each run.
    """
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "groups": {},
        "summary": {},
    }

    # Merge hardcoded groups with dynamic entity clusters
    all_groups = dict(SYBIL_GROUPS)
    dyn_groups = load_entity_clusters()
    for gid, ginfo in dyn_groups.items():
        if gid not in all_groups:
            all_groups[gid] = ginfo
            logger.info(f"Merged dynamic group {gid}: {len(ginfo['wallets'])} wallets")

    for group_id, group_info in all_groups.items():
        all_active_positions = []
        wallet_results = {}

        for addr, label in group_info["wallets"].items():
            logger.info(f"Fetching positions for {label} ({addr})")
            positions = fetch_positions(addr)
            active = [p for p in positions if is_active_position(p)]
            wallet_results[label] = {
                "address": addr,
                "total_positions": len(positions),
                "active_positions": len(active),
                "active": active,
            }
            all_active_positions.extend(active)
            time.sleep(0.5)

        # Aggregate active positions by market (conditionId)
        market_agg = {}
        for pos in all_active_positions:
            cond_id = pos.get("conditionId", "unknown")
            if cond_id not in market_agg:
                market_agg[cond_id] = {
                    "condition_id": cond_id,
                    "market_title": pos.get("title", ""),
                    "market_slug": pos.get("slug", ""),
                    "event_slug": pos.get("eventSlug", ""),
                    "end_date": pos.get("endDate", ""),
                    "wallets": [],
                    "total_size_usd": 0.0,
                    "yes_size_usd": 0.0,
                    "no_size_usd": 0.0,
                    "outcome_sizes": {},
                }
            outcome = pos.get("outcome", "unknown")
            size = float(pos.get("size", 0) or 0)
            market_agg[cond_id]["wallets"].append({
                "label": label,
                "address": addr,
                "outcome": outcome,
                "size_usd": size,
                "avg_price": pos.get("avgPrice", 0),
                "current_price": pos.get("curPrice", 0),
                "position_value": pos.get("currentValue", 0),
            })
            market_agg[cond_id]["total_size_usd"] += size
            if outcome.lower() == "yes":
                market_agg[cond_id]["yes_size_usd"] += size
            elif outcome.lower() == "no":
                market_agg[cond_id]["no_size_usd"] += size
            if outcome not in market_agg[cond_id]["outcome_sizes"]:
                market_agg[cond_id]["outcome_sizes"][outcome] = 0.0
            market_agg[cond_id]["outcome_sizes"][outcome] += size

        # Round values
        for m in market_agg.values():
            m["total_size_usd"] = round(m["total_size_usd"], 2)
            m["yes_size_usd"] = round(m["yes_size_usd"], 2)
            m["no_size_usd"] = round(m["no_size_usd"], 2)
            for k, v in m["outcome_sizes"].items():
                m["outcome_sizes"][k] = round(v, 2)

        total_exposure = sum(m["total_size_usd"] for m in market_agg.values())
        result["groups"][group_id] = {
            "priority": group_info["priority"],
            "wallet_count": len(group_info["wallets"]),
            "total_active_exposure_usd": round(total_exposure, 2),
            "active_position_count": len(all_active_positions),
            "market_count": len(market_agg),
            "markets": sorted(market_agg.values(), key=lambda x: x["total_size_usd"], reverse=True),
            "wallet_details": wallet_results,
        }

    total_all = sum(g["total_active_exposure_usd"] for g in result["groups"].values())
    result["summary"] = {
        "total_groups": len(all_groups),
        "total_wallets": sum(len(g["wallets"]) for g in all_groups.values()),
        "total_active_exposure_usd": round(total_all, 2),
    }

    return result


def main():
    output_dir = "/home/elon-1/workspace/nautilus-trading/research"
    output_path = os.path.join(output_dir, "sybil_positions.json")

    logger.info("Starting sybil position aggregation (v2)...")
    result = aggregate_by_group()

    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    for gid, gdata in result["groups"].items():
        logger.info(
            f"{gid}: {gdata['wallet_count']} wallets, "
            f"${gdata['total_active_exposure_usd']:,.0f} active exposure, "
            f"{gdata['active_position_count']} active positions, "
            f"{gdata['market_count']} markets"
        )

    logger.info(f"Output: {output_path}")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
